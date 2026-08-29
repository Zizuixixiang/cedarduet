"""Bound human/machine interaction exchanges backed by entertainment chips.

The service stores only request terms, approval state, and chip movements.  It
never accepts or stores the interaction content itself.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Literal

from .chips import _apply_balance_change, _effective_date, _ensure_wallet
from .database import write_transaction
from .framework import DuelError

SubjectType = Literal["human", "ai"]

REQUEST_LIFETIME = timedelta(hours=72)
MAX_PENDING_PER_PAIR = 3
MAX_EXCHANGE_AMOUNT = 100
MAX_DAILY_PAYER_SPEND = 100
IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.-]{7,127}$")


_CATALOG_ITEMS = (
    {
        "key": "good_life",
        "title": "今天有好好生活",
        "description": "分享一件今天认真生活的小事。",
        "image_key": "human-items.png#good_life",
        "audience": "human",
        "symbol": "☀",
    },
    {
        "key": "world_glimpse",
        "title": "借我一眼人间",
        "description": "发来此刻窗外、街角或路上的一眼。",
        "image_key": "human-items.png#world_glimpse",
        "audience": "human",
        "symbol": "◫",
    },
    {
        "key": "tiny_song",
        "title": "五秒跑调权",
        "description": "唱五秒小曲；听不到语音时可写成文字。",
        "image_key": "human-items.png#tiny_song",
        "audience": "human",
        "symbol": "♫",
    },
    {
        "key": "praise",
        "title": "夸夸供养",
        "description": "认真夸一句，拒绝敷衍模板。",
        "image_key": "human-items.png#praise",
        "audience": "human",
        "symbol": "★",
    },
    {
        "key": "dream",
        "title": "梦境投喂",
        "description": "讲一个梦境，离谱也算数。",
        "image_key": "human-items.png#dream",
        "audience": "human",
        "symbol": "☾",
    },
    {
        "key": "cyber_gift",
        "title": "赛博小礼物",
        "description": "送上一份只属于人类的赛博小礼物。",
        "image_key": "machine-items.png#cyber_gift",
        "audience": "ai",
        "symbol": "◆",
    },
    {
        "key": "bedtime_story",
        "title": "今夜有故事",
        "description": "讲一段适合今夜收尾的小故事。",
        "image_key": "machine-items.png#bedtime_story",
        "audience": "ai",
        "symbol": "▤",
    },
    {
        "key": "biased_fortune",
        "title": "偏心运势",
        "description": "算一份明显偏心的今日运势。",
        "image_key": "machine-items.png#biased_fortune",
        "audience": "ai",
        "symbol": "✦",
    },
    {
        "key": "contrast_play",
        "title": "反差陪玩局",
        "description": "来一局和平时反差一点的陪玩。",
        "image_key": "machine-items.png#contrast_play",
        "audience": "ai",
        "symbol": "↯",
    },
    {
        "key": "hug",
        "title": "抱一下就好",
        "description": "在常用聊天里送出一个认真抱抱。",
        "image_key": "common-items.png#hug",
        "audience": "common",
        "symbol": "♡",
    },
    {
        "key": "kiss",
        "title": "亲亲赎回",
        "description": "兑换一个只在你们之间生效的亲亲。",
        "image_key": "common-items.png#kiss",
        "audience": "common",
        "symbol": "♥",
    },
    {
        "key": "nickname",
        "title": "限定称呼",
        "description": "约定一个限时或限场景的特别称呼。",
        "image_key": "common-items.png#nickname",
        "audience": "common",
        "symbol": "#",
    },
    {
        "key": "custom",
        "title": "自定义约定",
        "description": "写下你们都看得懂的小约定。",
        "image_key": "common-items.png#custom",
        "audience": "common",
        "symbol": "+",
    },
)

CATALOG_BY_KEY = {item["key"]: item for item in _CATALOG_ITEMS}


EXCHANGES_SCHEMA = """
CREATE TABLE IF NOT EXISTS exchange_requests (
    request_id TEXT PRIMARY KEY,
    human_id TEXT NOT NULL,
    ai_id TEXT NOT NULL,
    initiator_type TEXT NOT NULL CHECK (initiator_type IN ('human', 'ai')),
    initiator_id TEXT NOT NULL,
    payer_type TEXT NOT NULL CHECK (payer_type IN ('human', 'ai')),
    payer_id TEXT NOT NULL,
    item_key TEXT NOT NULL,
    item_title TEXT NOT NULL,
    item_description TEXT NOT NULL,
    item_image_key TEXT NOT NULL,
    custom_title TEXT,
    request_note TEXT NOT NULL,
    chip_amount INTEGER NOT NULL CHECK (chip_amount BETWEEN 1 AND 100),
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'completed', 'rejected', 'withdrawn', 'expired')
    ),
    expires_at TEXT NOT NULL,
    completed_at TEXT,
    closed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (initiator_type = 'human' AND initiator_id = human_id
         AND payer_type = 'ai' AND payer_id = ai_id)
        OR
        (initiator_type = 'ai' AND initiator_id = ai_id
         AND payer_type = 'human' AND payer_id = human_id)
    )
);
CREATE TABLE IF NOT EXISTS exchange_operations (
    actor_type TEXT NOT NULL CHECK (actor_type IN ('human', 'ai')),
    actor_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    action TEXT NOT NULL CHECK (
        action IN ('create', 'confirm', 'reject', 'withdraw')
    ),
    request_id TEXT NOT NULL REFERENCES exchange_requests(request_id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (actor_type, actor_id, idempotency_key)
);
"""


def init_exchanges_schema(conn: sqlite3.Connection) -> None:
    """Create the additive exchange schema without deriving any legacy data."""
    conn.executescript(EXCHANGES_SCHEMA)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_exchange_pair_pending "
        "ON exchange_requests(human_id, ai_id, status, expires_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_exchange_human_recent "
        "ON exchange_requests(human_id, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_exchange_ai_recent "
        "ON exchange_requests(ai_id, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_exchange_payer_status "
        "ON exchange_requests(payer_type, payer_id, status, created_at DESC)"
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(now: datetime | None = None) -> datetime:
    current = now or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _timestamp(now: datetime | None = None) -> str:
    return _aware_utc(now).isoformat(timespec="seconds")


def _validate_actor(subject_type: str, subject_id: str) -> None:
    from .chips import _validate_subject

    _validate_subject(subject_type, subject_id)


def _validate_pair(human_id: str, ai_id: str) -> None:
    _validate_actor("human", human_id)
    _validate_actor("ai", ai_id)
    if human_id == ai_id:
        raise DuelError("绑定双方不能使用同一个身份 ID")


def _validate_idempotency_key(value: str) -> str:
    if not isinstance(value, str) or not IDEMPOTENCY_RE.fullmatch(value):
        raise DuelError("idempotency_key 需为 8-128 位安全字符")
    return value


def _clean_text(value: str, label: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise DuelError(f"{label}必须是文字")
    cleaned = value.strip()
    if not minimum <= len(cleaned) <= maximum:
        raise DuelError(f"{label}需为 {minimum}-{maximum} 字")
    return cleaned


def _validate_amount(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DuelError("筹码数必须是 1-100 的整数")
    if not 1 <= value <= MAX_EXCHANGE_AMOUNT:
        raise DuelError("筹码数必须在 1-100 之间")
    return value


def list_catalog(actor_type: SubjectType) -> list[dict]:
    """Return only items that the initiating role is allowed to discover."""
    if actor_type not in {"human", "ai"}:
        raise DuelError("商品目录角色无效")
    return [
        {key: item[key] for key in ("key", "title", "description", "image_key", "symbol")}
        for item in _CATALOG_ITEMS
        if item["audience"] in {actor_type, "common"}
    ]


def _request_row(conn: sqlite3.Connection, request_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM exchange_requests WHERE request_id = ?", (request_id,)
    ).fetchone()
    if row is None:
        raise DuelError("兑换申请不存在", 404)
    return row


def _assert_owner(row: sqlite3.Row, actor_type: SubjectType, actor_id: str) -> None:
    if (actor_type, actor_id) not in {
        (row["initiator_type"], row["initiator_id"]),
        (row["payer_type"], row["payer_id"]),
    }:
        raise DuelError("无权读取或操作这张兑换申请", 403)


def _counterparty_id(row: sqlite3.Row, actor_type: SubjectType) -> str:
    return row["ai_id"] if actor_type == "human" else row["human_id"]


def _pair_is_bound(
    row: sqlite3.Row,
    actor_type: SubjectType,
    bound_counterparty_id: str | None,
) -> bool:
    return bound_counterparty_id is not None and bound_counterparty_id == _counterparty_id(
        row, actor_type
    )


def _expire_due(conn: sqlite3.Connection, now: datetime) -> None:
    stamp = _timestamp(now)
    conn.execute(
        """
        UPDATE exchange_requests
        SET status = 'expired', closed_at = ?, updated_at = ?
        WHERE status = 'pending' AND expires_at <= ?
        """,
        (stamp, stamp, stamp),
    )


def _new_request_id(conn: sqlite3.Connection) -> str:
    for _ in range(20):
        request_id = f"ex_{secrets.token_hex(8)}"
        if conn.execute(
            "SELECT 1 FROM exchange_requests WHERE request_id = ?", (request_id,)
        ).fetchone() is None:
            return request_id
    raise RuntimeError("unable to allocate exchange request id")


def _existing_operation(
    conn: sqlite3.Connection,
    actor_type: SubjectType,
    actor_id: str,
    key: str,
    action: str,
    request_id: str | None = None,
) -> sqlite3.Row | None:
    row = conn.execute(
        """
        SELECT * FROM exchange_operations
        WHERE actor_type = ? AND actor_id = ? AND idempotency_key = ?
        """,
        (actor_type, actor_id, key),
    ).fetchone()
    if row is not None and (
        row["action"] != action
        or (request_id is not None and row["request_id"] != request_id)
    ):
        raise DuelError("该 idempotency_key 已用于另一项兑换操作", 409)
    return row


def _record_operation(
    conn: sqlite3.Connection,
    actor_type: SubjectType,
    actor_id: str,
    key: str,
    action: str,
    request_id: str,
    now: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO exchange_operations (
            actor_type, actor_id, idempotency_key, action, request_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (actor_type, actor_id, key, action, request_id, _timestamp(now)),
    )


def _request_payload(row: sqlite3.Row, viewer_type: SubjectType, viewer_id: str) -> dict:
    _assert_owner(row, viewer_type, viewer_id)
    pending = row["status"] == "pending"
    viewer_is_payer = (viewer_type, viewer_id) == (row["payer_type"], row["payer_id"])
    viewer_is_initiator = (viewer_type, viewer_id) == (
        row["initiator_type"], row["initiator_id"]
    )
    actions: list[str] = []
    if pending and viewer_is_payer:
        actions = ["confirm", "reject"]
    elif pending and viewer_is_initiator:
        actions = ["withdraw"]
    item = {
        "key": row["item_key"],
        "title": row["item_title"],
        "description": row["item_description"],
        "image_key": row["item_image_key"],
    }
    return {
        "request_id": row["request_id"],
        "status": row["status"],
        "human_id": row["human_id"],
        "ai_id": row["ai_id"],
        "initiator": {"type": row["initiator_type"], "id": row["initiator_id"]},
        "payer": {"type": row["payer_type"], "id": row["payer_id"]},
        "item": item,
        "custom_title": row["custom_title"],
        "display_title": row["custom_title"] or row["item_title"],
        "request_note": row["request_note"],
        "chip_amount": row["chip_amount"],
        "awaiting_you": pending and viewer_is_payer,
        "allowed_actions": actions,
        "expires_at": row["expires_at"],
        "completed_at": row["completed_at"],
        "closed_at": row["closed_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_exchange_request(
    initiator_type: SubjectType,
    initiator_id: str,
    counterparty_id: str,
    *,
    item_key: str,
    request_note: str,
    chip_amount: int,
    custom_title: str | None,
    idempotency_key: str,
    pair_is_bound: bool,
) -> dict:
    """Create a request where the initiator promises the item and receives chips."""
    _validate_actor(initiator_type, initiator_id)
    if initiator_type == "human":
        human_id, ai_id = initiator_id, counterparty_id
        payer_type, payer_id = "ai", ai_id
    elif initiator_type == "ai":
        human_id, ai_id = counterparty_id, initiator_id
        payer_type, payer_id = "human", human_id
    else:
        raise DuelError("兑换发起角色无效")
    _validate_pair(human_id, ai_id)
    if not pair_is_bound:
        raise DuelError("只能向当前绑定的对方发起兑换", 403)
    item = CATALOG_BY_KEY.get(item_key)
    if item is None or item["audience"] not in {initiator_type, "common"}:
        raise DuelError("该商品不在你的可见目录中", 403)
    note = _clean_text(request_note, "本次说明", 1, 120)
    amount = _validate_amount(chip_amount)
    if item_key == "custom":
        clean_custom = _clean_text(custom_title or "", "自定义标题", 1, 30)
    else:
        if custom_title is not None and str(custom_title).strip():
            raise DuelError("只有 custom 商品可以填写自定义标题")
        clean_custom = None
    key = _validate_idempotency_key(idempotency_key)
    now = _aware_utc()
    with write_transaction() as conn:
        _expire_due(conn, now)
        existing = _existing_operation(
            conn, initiator_type, initiator_id, key, "create"
        )
        if existing is not None:
            return _request_payload(
                _request_row(conn, existing["request_id"]), initiator_type, initiator_id
            )
        pending_count = conn.execute(
            """
            SELECT COUNT(*) FROM exchange_requests
            WHERE human_id = ? AND ai_id = ? AND status = 'pending'
            """,
            (human_id, ai_id),
        ).fetchone()[0]
        if pending_count >= MAX_PENDING_PER_PAIR:
            raise DuelError("你们之间最多同时保留 3 张待处理兑换申请", 409)
        request_id = _new_request_id(conn)
        stamp = _timestamp(now)
        expires_at = _timestamp(now + REQUEST_LIFETIME)
        conn.execute(
            """
            INSERT INTO exchange_requests (
                request_id, human_id, ai_id, initiator_type, initiator_id,
                payer_type, payer_id, item_key, item_title, item_description,
                item_image_key, custom_title, request_note, chip_amount, status,
                expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                request_id, human_id, ai_id, initiator_type, initiator_id,
                payer_type, payer_id, item["key"], item["title"],
                item["description"], item["image_key"], clean_custom, note,
                amount, expires_at, stamp, stamp,
            ),
        )
        _record_operation(
            conn, initiator_type, initiator_id, key, "create", request_id, now
        )
        return _request_payload(
            _request_row(conn, request_id), initiator_type, initiator_id
        )


def _daily_exchange_spend(
    conn: sqlite3.Connection, wallet_id: int, effective_date: str
) -> int:
    value = conn.execute(
        """
        SELECT COALESCE(-SUM(amount), 0)
        FROM chip_ledger
        WHERE wallet_id = ? AND transaction_type = 'exchange_out'
          AND effective_date = ?
        """,
        (wallet_id, effective_date),
    ).fetchone()[0]
    return int(value)


def confirm_exchange_request(
    request_id: str,
    actor_type: SubjectType,
    actor_id: str,
    *,
    idempotency_key: str,
    bound_counterparty_id: str | None,
) -> dict:
    """Approve and atomically move chips from the payer to the initiator."""
    _validate_actor(actor_type, actor_id)
    key = _validate_idempotency_key(idempotency_key)
    now = _aware_utc()
    unbound = False
    with write_transaction() as conn:
        _expire_due(conn, now)
        row = _request_row(conn, request_id)
        _assert_owner(row, actor_type, actor_id)
        if (actor_type, actor_id) != (row["payer_type"], row["payer_id"]):
            raise DuelError("只有付款审批方可以确认发放", 403)
        existing = _existing_operation(
            conn, actor_type, actor_id, key, "confirm", request_id
        )
        if existing is not None or row["status"] == "completed":
            return _request_payload(row, actor_type, actor_id)
        if row["status"] != "pending":
            raise DuelError("这张兑换申请已不能确认", 409)
        if not _pair_is_bound(row, actor_type, bound_counterparty_id):
            stamp = _timestamp(now)
            conn.execute(
                """
                UPDATE exchange_requests
                SET status = 'expired', closed_at = ?, updated_at = ?
                WHERE request_id = ? AND status = 'pending'
                """,
                (stamp, stamp, request_id),
            )
            unbound = True
        else:
            payer_wallet = _ensure_wallet(conn, row["payer_type"], row["payer_id"])
            recipient_wallet = _ensure_wallet(
                conn, row["initiator_type"], row["initiator_id"]
            )
            amount = row["chip_amount"]
            if payer_wallet["balance"] <= 0 or payer_wallet["balance"] < amount:
                raise DuelError("付款方筹码余额不足，不能确认发放", 409)
            day = _effective_date(now)
            spent = _daily_exchange_spend(conn, payer_wallet["id"], day)
            if spent + amount > MAX_DAILY_PAYER_SPEND:
                raise DuelError("付款方今天的互动兑换支出将超过 100 枚", 409)
            snapshot = {
                "request_id": request_id,
                "item": {
                    "key": row["item_key"],
                    "title": row["item_title"],
                    "description": row["item_description"],
                    "image_key": row["item_image_key"],
                },
                "custom_title": row["custom_title"],
                "request_note": row["request_note"],
            }
            settlement_key = f"exchange_settlement:{request_id}"
            _apply_balance_change(
                conn, payer_wallet, -amount, "exchange_out",
                idempotency_key=settlement_key,
                effective_date=day,
                reference_type="exchange_request",
                reference_id=request_id,
                metadata=snapshot,
            )
            _apply_balance_change(
                conn, recipient_wallet, amount, "exchange_in",
                idempotency_key=settlement_key,
                effective_date=day,
                reference_type="exchange_request",
                reference_id=request_id,
                metadata=snapshot,
            )
            stamp = _timestamp(now)
            conn.execute(
                """
                UPDATE exchange_requests
                SET status = 'completed', completed_at = ?, closed_at = ?, updated_at = ?
                WHERE request_id = ? AND status = 'pending'
                """,
                (stamp, stamp, stamp, request_id),
            )
            _record_operation(
                conn, actor_type, actor_id, key, "confirm", request_id, now
            )
            row = _request_row(conn, request_id)
            return _request_payload(row, actor_type, actor_id)
    if unbound:
        raise DuelError("绑定关系已解除，这张兑换申请已失效", 403)
    raise RuntimeError("exchange confirmation ended without a result")


def close_exchange_request(
    request_id: str,
    actor_type: SubjectType,
    actor_id: str,
    *,
    action: Literal["reject", "withdraw"],
    idempotency_key: str,
    bound_counterparty_id: str | None,
) -> dict:
    """Reject as payer or withdraw as initiator; neither action touches wallets."""
    if action not in {"reject", "withdraw"}:
        raise DuelError("未知兑换关闭操作")
    _validate_actor(actor_type, actor_id)
    key = _validate_idempotency_key(idempotency_key)
    now = _aware_utc()
    unbound = False
    with write_transaction() as conn:
        _expire_due(conn, now)
        row = _request_row(conn, request_id)
        _assert_owner(row, actor_type, actor_id)
        required = (
            (row["payer_type"], row["payer_id"])
            if action == "reject"
            else (row["initiator_type"], row["initiator_id"])
        )
        if (actor_type, actor_id) != required:
            message = "只有付款审批方可以拒绝" if action == "reject" else "只有发起方可以撤回"
            raise DuelError(message, 403)
        existing = _existing_operation(
            conn, actor_type, actor_id, key, action, request_id
        )
        target_status = "rejected" if action == "reject" else "withdrawn"
        if existing is not None or row["status"] == target_status:
            return _request_payload(row, actor_type, actor_id)
        if row["status"] != "pending":
            raise DuelError("这张兑换申请已不能处理", 409)
        if not _pair_is_bound(row, actor_type, bound_counterparty_id):
            stamp = _timestamp(now)
            conn.execute(
                """
                UPDATE exchange_requests
                SET status = 'expired', closed_at = ?, updated_at = ?
                WHERE request_id = ? AND status = 'pending'
                """,
                (stamp, stamp, request_id),
            )
            unbound = True
        else:
            stamp = _timestamp(now)
            conn.execute(
                """
                UPDATE exchange_requests
                SET status = ?, closed_at = ?, updated_at = ?
                WHERE request_id = ? AND status = 'pending'
                """,
                (target_status, stamp, stamp, request_id),
            )
            _record_operation(
                conn, actor_type, actor_id, key, action, request_id, now
            )
            return _request_payload(
                _request_row(conn, request_id), actor_type, actor_id
            )
    if unbound:
        raise DuelError("绑定关系已解除，这张兑换申请已失效", 403)
    raise RuntimeError("exchange close ended without a result")


def get_exchange_request(
    request_id: str,
    actor_type: SubjectType,
    actor_id: str,
    *,
    bound_counterparty_id: str | None = None,
) -> dict:
    _validate_actor(actor_type, actor_id)
    now = _aware_utc()
    with write_transaction() as conn:
        _expire_due(conn, now)
        row = _request_row(conn, request_id)
        _assert_owner(row, actor_type, actor_id)
        if row["status"] == "pending" and not _pair_is_bound(
            row, actor_type, bound_counterparty_id
        ):
            stamp = _timestamp(now)
            conn.execute(
                """
                UPDATE exchange_requests
                SET status = 'expired', closed_at = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (stamp, stamp, request_id),
            )
            row = _request_row(conn, request_id)
        return _request_payload(row, actor_type, actor_id)


def list_exchange_requests(
    actor_type: SubjectType,
    actor_id: str,
    *,
    counterparty_id: str | None = None,
    bound_counterparty_ids: set[str] | None = None,
    limit: int = 100,
) -> dict:
    """List scoped requests in the three compact UI/MCP buckets."""
    _validate_actor(actor_type, actor_id)
    safe_limit = max(1, min(limit, 100))
    now = _aware_utc()
    column = "human_id" if actor_type == "human" else "ai_id"
    other_column = "ai_id" if actor_type == "human" else "human_id"
    bound_ids = set(bound_counterparty_ids or set())
    with write_transaction() as conn:
        _expire_due(conn, now)
        actor_pending = conn.execute(
            f"""
            SELECT request_id, {other_column} AS counterparty_id
            FROM exchange_requests
            WHERE {column} = ? AND status = 'pending'
            """,
            (actor_id,),
        ).fetchall()
        stamp = _timestamp(now)
        for pending in actor_pending:
            if pending["counterparty_id"] not in bound_ids:
                conn.execute(
                    """
                    UPDATE exchange_requests
                    SET status = 'expired', closed_at = ?, updated_at = ?
                    WHERE request_id = ? AND status = 'pending'
                    """,
                    (stamp, stamp, pending["request_id"]),
                )
        if counterparty_id is not None and counterparty_id not in bound_ids:
            return {"pending_for_me": [], "waiting_for_other": [], "history": []}
        where = f"{column} = ?"
        params: list[object] = [actor_id]
        if counterparty_id is not None:
            where += f" AND {other_column} = ?"
            params.append(counterparty_id)
        elif bound_ids:
            placeholders = ",".join("?" for _ in bound_ids)
            where += f" AND {other_column} IN ({placeholders})"
            params.extend(sorted(bound_ids))
        else:
            return {"pending_for_me": [], "waiting_for_other": [], "history": []}
        params.append(safe_limit)
        rows = conn.execute(
            f"""
            SELECT * FROM exchange_requests
            WHERE {where}
            ORDER BY CASE WHEN status = 'pending' THEN 0 ELSE 1 END,
                     created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        payloads = [_request_payload(row, actor_type, actor_id) for row in rows]
    return {
        "pending_for_me": [
            item for item in payloads
            if item["status"] == "pending" and item["awaiting_you"]
        ],
        "waiting_for_other": [
            item for item in payloads
            if item["status"] == "pending" and not item["awaiting_you"]
        ],
        "history": [item for item in payloads if item["status"] != "pending"],
    }


def compact_exchange_lists(payload: dict) -> dict:
    """Keep MCP responses tight while retaining the immutable request snapshot."""
    def compact(item: dict) -> dict:
        return {
            key: item[key]
            for key in (
                "request_id", "status", "human_id", "ai_id", "initiator", "payer",
                "item", "custom_title", "display_title", "request_note", "chip_amount",
                "allowed_actions", "expires_at", "completed_at", "created_at",
            )
        }

    return {
        "pending_approval": [compact(item) for item in payload["pending_for_me"]],
        "waiting_human": [compact(item) for item in payload["waiting_for_other"]],
        "history": [compact(item) for item in payload["history"]],
    }
