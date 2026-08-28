"""Global entertainment-chip wallets and their single source-of-truth ledger."""

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from .database import write_transaction
from .framework import DuelError

SubjectType = Literal["human", "ai"]

INITIAL_BALANCE = 200
DAILY_CHECK_IN_AMOUNT = 20
BANKRUPTCY_THRESHOLD = -500
BANKRUPTCY_RESET_BALANCE = 50
BANKRUPTCY_BADGE_ID = "pixel_dirt_poor"
BANKRUPTCY_BADGE_NAME = "像素吃土中"
CHIP_CALENDAR_TIMEZONE = ZoneInfo("Asia/Shanghai")

SUBJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,79}$")

CHIP_WALLETS_SCHEMA = """
CREATE TABLE IF NOT EXISTS chip_wallets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('human', 'ai')),
    subject_id TEXT NOT NULL,
    balance INTEGER NOT NULL DEFAULT 200,
    bankruptcy_count INTEGER NOT NULL DEFAULT 0 CHECK (bankruptcy_count >= 0),
    bankruptcy_badge_active INTEGER NOT NULL DEFAULT 0
        CHECK (bankruptcy_badge_active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (subject_type, subject_id)
)
"""

CHIP_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS chip_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_id INTEGER NOT NULL REFERENCES chip_wallets(id) ON DELETE CASCADE,
    transaction_type TEXT NOT NULL,
    amount INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    idempotency_key TEXT,
    effective_date TEXT,
    reference_type TEXT,
    reference_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (wallet_id, idempotency_key)
)
"""

CHIP_SETTLEMENT_BATCHES_SCHEMA = """
CREATE TABLE IF NOT EXISTS chip_settlement_batches (
    idempotency_key TEXT PRIMARY KEY,
    reference_type TEXT,
    reference_id TEXT,
    deltas_json TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

TRANSACTION_LABELS = {
    "wallet_opened": "新钱包初始筹码",
    "daily_check_in": "每日签到",
    "bankruptcy_reset": "宣布破产",
    "duel_win": "双弈胜局",
    "duel_loss": "双弈负局",
}


def init_chips_schema(conn: sqlite3.Connection) -> None:
    """Create the additive chip-center schema; safe to run on every startup."""
    conn.execute(CHIP_WALLETS_SCHEMA)
    conn.execute(CHIP_LEDGER_SCHEMA)
    conn.execute(CHIP_SETTLEMENT_BATCHES_SCHEMA)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chip_ledger_wallet_recent
        ON chip_ledger(wallet_id, id DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chip_wallets_subject
        ON chip_wallets(subject_type, subject_id)
        """
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(now: datetime | None = None) -> str:
    return (now or _utc_now()).isoformat(timespec="seconds")


def _effective_date(now: datetime | None = None) -> str:
    current = now or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(CHIP_CALENDAR_TIMEZONE).date().isoformat()


def _validate_subject(subject_type: str, subject_id: str) -> None:
    if subject_type not in {"human", "ai"}:
        raise DuelError("筹码钱包主体类型无效")
    if not SUBJECT_ID_RE.fullmatch(subject_id):
        raise DuelError("筹码钱包主体 ID 格式无效")


def _wallet_row(
    conn: sqlite3.Connection, subject_type: SubjectType, subject_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM chip_wallets
        WHERE subject_type = ? AND subject_id = ?
        """,
        (subject_type, subject_id),
    ).fetchone()


def _ensure_wallet(
    conn: sqlite3.Connection, subject_type: SubjectType, subject_id: str
) -> sqlite3.Row:
    """Create a wallet and its opening ledger entry exactly once."""
    _validate_subject(subject_type, subject_id)
    now = _timestamp()
    inserted = conn.execute(
        """
        INSERT INTO chip_wallets (
            subject_type, subject_id, balance, bankruptcy_count,
            bankruptcy_badge_active, created_at, updated_at
        ) VALUES (?, ?, ?, 0, 0, ?, ?)
        ON CONFLICT(subject_type, subject_id) DO NOTHING
        """,
        (subject_type, subject_id, INITIAL_BALANCE, now, now),
    )
    wallet = _wallet_row(conn, subject_type, subject_id)
    if wallet is None:
        raise RuntimeError("wallet insert completed without a readable wallet")
    if inserted.rowcount == 1:
        conn.execute(
            """
            INSERT INTO chip_ledger (
                wallet_id, transaction_type, amount, balance_after,
                idempotency_key, effective_date, metadata_json, created_at
            ) VALUES (?, 'wallet_opened', ?, ?, 'wallet_opened', ?, '{}', ?)
            """,
            (
                wallet["id"],
                INITIAL_BALANCE,
                INITIAL_BALANCE,
                _effective_date(),
                now,
            ),
        )
    return wallet


def _write_ledger(
    conn: sqlite3.Connection,
    *,
    wallet_id: int,
    transaction_type: str,
    amount: int,
    balance_after: int,
    idempotency_key: str | None,
    effective_date: str | None,
    reference_type: str | None,
    reference_id: str | None,
    metadata: dict | None,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO chip_ledger (
            wallet_id, transaction_type, amount, balance_after,
            idempotency_key, effective_date, reference_type, reference_id,
            metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            wallet_id,
            transaction_type,
            amount,
            balance_after,
            idempotency_key,
            effective_date,
            reference_type,
            reference_id,
            json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
            created_at,
        ),
    )


def _apply_balance_change(
    conn: sqlite3.Connection,
    wallet: sqlite3.Row,
    amount: int,
    transaction_type: str,
    *,
    idempotency_key: str | None = None,
    effective_date: str | None = None,
    reference_type: str | None = None,
    reference_id: str | None = None,
    metadata: dict | None = None,
) -> sqlite3.Row:
    """Apply one legal delta, write the ledger, and enforce badge recovery."""
    if not isinstance(amount, int) or isinstance(amount, bool) or amount == 0:
        raise DuelError("筹码变动额必须是非零整数")
    if not transaction_type.strip():
        raise DuelError("筹码流水类型不能为空")
    balance_after = wallet["balance"] + amount
    badge_after = (
        0
        if balance_after >= INITIAL_BALANCE
        else wallet["bankruptcy_badge_active"]
    )
    now = _timestamp()
    conn.execute(
        """
        UPDATE chip_wallets
        SET balance = ?, bankruptcy_badge_active = ?, updated_at = ?
        WHERE id = ?
        """,
        (balance_after, badge_after, now, wallet["id"]),
    )
    _write_ledger(
        conn,
        wallet_id=wallet["id"],
        transaction_type=transaction_type,
        amount=amount,
        balance_after=balance_after,
        idempotency_key=idempotency_key,
        effective_date=effective_date,
        reference_type=reference_type,
        reference_id=reference_id,
        metadata=metadata,
        created_at=now,
    )
    updated = conn.execute(
        "SELECT * FROM chip_wallets WHERE id = ?", (wallet["id"],)
    ).fetchone()
    if updated is None:
        raise RuntimeError("updated wallet disappeared")
    return updated


def change_balance(
    subject_type: SubjectType,
    subject_id: str,
    amount: int,
    transaction_type: str,
    *,
    idempotency_key: str | None = None,
    effective_date: str | None = None,
    reference_type: str | None = None,
    reference_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Reusable entry point for future game settlement and other chip changes."""
    with write_transaction() as conn:
        wallet = _ensure_wallet(conn, subject_type, subject_id)
        if idempotency_key is not None:
            existing = conn.execute(
                """
                SELECT 1 FROM chip_ledger
                WHERE wallet_id = ? AND idempotency_key = ?
                """,
                (wallet["id"], idempotency_key),
            ).fetchone()
            if existing is not None:
                return _wallet_payload(conn, wallet)
        wallet = _apply_balance_change(
            conn,
            wallet,
            amount,
            transaction_type,
            idempotency_key=idempotency_key,
            effective_date=effective_date,
            reference_type=reference_type,
            reference_id=reference_id,
            metadata=metadata,
        )
        return _wallet_payload(conn, wallet)


def apply_participant_deltas(
    conn: sqlite3.Connection,
    participants: list[dict],
    deltas: dict[str, int],
    *,
    idempotency_key: str,
    reference_type: str,
    reference_id: str,
    require_zero_sum: bool = False,
    metadata: dict | None = None,
) -> bool:
    """Atomically apply one idempotent N-participant settlement batch."""
    if not idempotency_key.strip():
        raise DuelError("结算幂等键不能为空")
    existing_batch = conn.execute(
        """
        SELECT 1 FROM chip_settlement_batches WHERE idempotency_key = ?
        """,
        (idempotency_key,),
    ).fetchone()
    if existing_batch is not None:
        return False
    participant_by_id = {item["player_id"]: item for item in participants}
    if not deltas:
        raise DuelError("结算 delta 不能为空")
    unknown = set(deltas) - set(participant_by_id)
    if unknown:
        raise DuelError("结算包含不属于房间的参与者")
    if any(isinstance(delta, bool) or not isinstance(delta, int) for delta in deltas.values()):
        raise DuelError("每名参与者的筹码 delta 必须是整数")
    if require_zero_sum and sum(deltas.values()) != 0:
        raise DuelError("本次结算 delta 总和必须为 0")

    wallet_participants = {
        player_id: participant
        for player_id, participant in participant_by_id.items()
        if participant.get("participant_kind")
        in {"human", "bound_machine"}
        or (
            participant.get("participant_kind") is None
            and participant.get("role") in {"human", "ai"}
        )
    }
    wallets = {
        player_id: _ensure_wallet(
            conn, wallet_participants[player_id]["role"], player_id
        )
        for player_id in deltas if player_id in wallet_participants
    }
    existing_ledger = {
        player_id: conn.execute(
            """
            SELECT 1 FROM chip_ledger
            WHERE wallet_id = ? AND idempotency_key = ?
            """,
            (wallet["id"], idempotency_key),
        ).fetchone() is not None
        for player_id, wallet in wallets.items()
        if deltas[player_id] != 0
    }
    if existing_ledger and all(existing_ledger.values()):
        conn.execute(
            """
            INSERT INTO chip_settlement_batches (
                idempotency_key, reference_type, reference_id,
                deltas_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                idempotency_key, reference_type, reference_id,
                json.dumps(deltas, ensure_ascii=False, separators=(",", ":")),
                _timestamp(),
            ),
        )
        return False
    if any(existing_ledger.values()):
        raise RuntimeError("participant settlement ledger is only partially present")

    for player_id, delta in deltas.items():
        if delta == 0 or player_id not in wallets:
            continue
        _apply_balance_change(
            conn,
            wallets[player_id],
            delta,
            "duel_win" if delta > 0 else "duel_loss",
            idempotency_key=idempotency_key,
            reference_type=reference_type,
            reference_id=reference_id,
            metadata=metadata,
        )
    conn.execute(
        """
        INSERT INTO chip_settlement_batches (
            idempotency_key, reference_type, reference_id,
            deltas_json, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            idempotency_key, reference_type, reference_id,
            json.dumps(deltas, ensure_ascii=False, separators=(",", ":")),
            _timestamp(),
        ),
    )
    return True


def settle_duel_room(conn: sqlite3.Connection, room: dict) -> bool:
    """Resolve plugin deltas or preserve the exact legacy two-player payout."""
    stake = room.get("stake", 0)
    if isinstance(stake, bool) or not isinstance(stake, int) or stake < 0:
        raise DuelError("房间本局筹码必须是大于等于 0 的整数")
    result = room.get("result") or {}
    explicit_deltas = result.get("settlement_deltas")
    # A zero-stake room never moves chips. Multiplayer plugins may provide an
    # explicit payout map only after their own non-zero stake policy is enabled.
    if stake == 0:
        return False
    if room.get("status") not in {"finished", "archived"}:
        raise DuelError("只有终局房间可以结算筹码", 409)
    participants = room.get("participants", [])
    settlement_key = f"duel_settlement:{room['room_id']}"
    if explicit_deltas is not None:
        if not isinstance(explicit_deltas, dict):
            raise DuelError("插件 settlement_deltas 必须是 player_id 到整数的映射")
        participant_ids = {item["player_id"] for item in participants}
        if set(explicit_deltas) != participant_ids:
            raise DuelError("插件 settlement_deltas 必须完整覆盖房间每名参与者")
        deltas = explicit_deltas
        require_zero_sum = True
    else:
        if room.get("winner") == "draw" or result.get("draw"):
            return False
        if len(participants) != 2:
            # Multiplayer payout rules belong to each concrete game plugin.
            return False
        winner_player_id = room.get("winner_player_id")
        if winner_player_id is None and room.get("winner") in {"human", "ai"}:
            winner_player_id = next(
                (
                    item["player_id"] for item in participants
                    if item["role"] == room["winner"]
                ),
                None,
            )
        if winner_player_id not in {item["player_id"] for item in participants}:
            raise DuelError("终局赢家无效，无法结算筹码")
        deltas = {
            item["player_id"]: (
                stake if item["player_id"] == winner_player_id else -stake
            )
            for item in participants
        }
        require_zero_sum = True
    metadata = {
        "game_type": room["game_type"],
        "stake": stake,
        "winner": room.get("winner"),
        "winner_player_id": room.get("winner_player_id"),
        "settlement_deltas": dict(deltas),
        "npc_deltas": {
            item["player_id"]: deltas[item["player_id"]]
            for item in participants
            if item.get("participant_kind") == "system_npc"
        },
    }
    return apply_participant_deltas(
        conn,
        participants,
        deltas,
        idempotency_key=settlement_key,
        reference_type="duel_room",
        reference_id=room["room_id"],
        require_zero_sum=require_zero_sum,
        metadata=metadata,
    )


def claim_daily_check_in(subject_type: SubjectType, subject_id: str) -> dict:
    """Claim one +20 grant per Asia/Shanghai calendar day."""
    today = _effective_date()
    key = f"daily_check_in:{today}"
    with write_transaction() as conn:
        wallet = _ensure_wallet(conn, subject_type, subject_id)
        existing = conn.execute(
            """
            SELECT 1 FROM chip_ledger
            WHERE wallet_id = ? AND idempotency_key = ?
            """,
            (wallet["id"], key),
        ).fetchone()
        if existing is not None:
            return {
                "claimed": False,
                "wallet": _wallet_payload(conn, wallet),
            }
        wallet = _apply_balance_change(
            conn,
            wallet,
            DAILY_CHECK_IN_AMOUNT,
            "daily_check_in",
            idempotency_key=key,
            effective_date=today,
        )
        return {"claimed": True, "wallet": _wallet_payload(conn, wallet)}


def declare_bankruptcy(subject_type: SubjectType, subject_id: str) -> dict:
    """Atomically validate, reset, count, badge, and ledger a bankruptcy."""
    with write_transaction() as conn:
        wallet = _ensure_wallet(conn, subject_type, subject_id)
        if wallet["balance"] > BANKRUPTCY_THRESHOLD:
            raise DuelError(
                f"余额需低于或等于 {BANKRUPTCY_THRESHOLD} 才能宣布破产"
            )
        old_balance = wallet["balance"]
        amount = BANKRUPTCY_RESET_BALANCE - old_balance
        now = _timestamp()
        conn.execute(
            """
            UPDATE chip_wallets
            SET balance = ?, bankruptcy_count = bankruptcy_count + 1,
                bankruptcy_badge_active = 1, updated_at = ?
            WHERE id = ?
            """,
            (BANKRUPTCY_RESET_BALANCE, now, wallet["id"]),
        )
        _write_ledger(
            conn,
            wallet_id=wallet["id"],
            transaction_type="bankruptcy_reset",
            amount=amount,
            balance_after=BANKRUPTCY_RESET_BALANCE,
            idempotency_key=None,
            effective_date=_effective_date(),
            reference_type=None,
            reference_id=None,
            metadata={"balance_before": old_balance},
            created_at=now,
        )
        updated = conn.execute(
            "SELECT * FROM chip_wallets WHERE id = ?", (wallet["id"],)
        ).fetchone()
        if updated is None:
            raise RuntimeError("bankrupt wallet disappeared")
        return _wallet_payload(conn, updated)


def _wallet_payload(conn: sqlite3.Connection, wallet: sqlite3.Row) -> dict:
    today = _effective_date()
    checked_in = conn.execute(
        """
        SELECT 1 FROM chip_ledger
        WHERE wallet_id = ? AND idempotency_key = ?
        """,
        (wallet["id"], f"daily_check_in:{today}"),
    ).fetchone() is not None
    badge_active = bool(wallet["bankruptcy_badge_active"])
    return {
        "subject_type": wallet["subject_type"],
        "balance": wallet["balance"],
        "bankruptcy_count": wallet["bankruptcy_count"],
        "bankruptcy_active": badge_active,
        "bankruptcy_badge": (
            {"id": BANKRUPTCY_BADGE_ID, "name": BANKRUPTCY_BADGE_NAME}
            if badge_active
            else None
        ),
        "can_declare_bankruptcy": wallet["balance"] <= BANKRUPTCY_THRESHOLD,
        "checked_in_today": checked_in,
        "check_in_date": today,
        "created_at": wallet["created_at"],
        "updated_at": wallet["updated_at"],
    }


def get_wallet(subject_type: SubjectType, subject_id: str) -> dict:
    with write_transaction() as conn:
        wallet = _ensure_wallet(conn, subject_type, subject_id)
        return _wallet_payload(conn, wallet)


def list_ledger(
    subject_type: SubjectType, subject_id: str, *, limit: int = 30
) -> list[dict]:
    safe_limit = max(1, min(limit, 100))
    with write_transaction() as conn:
        wallet = _ensure_wallet(conn, subject_type, subject_id)
        rows = conn.execute(
            """
            SELECT id, transaction_type, amount, balance_after,
                   effective_date, reference_type, reference_id,
                   metadata_json, created_at
            FROM chip_ledger
            WHERE wallet_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (wallet["id"], safe_limit),
        ).fetchall()
    return [
        {
            **{key: row[key] for key in row.keys() if key != "metadata_json"},
            "label": TRANSACTION_LABELS.get(
                row["transaction_type"], row["transaction_type"]
            ),
            "metadata": json.loads(row["metadata_json"]),
        }
        for row in rows
    ]
