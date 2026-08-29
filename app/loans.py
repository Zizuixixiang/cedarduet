"""Negotiated human/machine IOUs backed by the global chip ledger.

Interest uses integer micro-percent/day units.  Every segment computes
``principal * rate * seconds + remainder`` then applies ``divmod`` once, so
rounding is auditable and never performs an inflationary daily ceiling.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from .chips import _apply_balance_change, _ensure_wallet
from .database import write_transaction
from .framework import DuelError
from .notifications import create_notification, mark_notifications_read

SubjectType = Literal["human", "ai"]
SHANGHAI = ZoneInfo("Asia/Shanghai")
PROPOSAL_LIFETIME = timedelta(days=3)
MAX_OPEN_LOANS = 3
RATE_SCALE = 100_000_000  # one micro-percent is 1 / 100,000,000 of principal
SECONDS_PER_DAY = 86_400
ACCRUAL_DENOMINATOR = RATE_SCALE * SECONDS_PER_DAY
MAX_SQLITE_INTEGER = 9_223_372_036_854_775_807
MAX_DAILY_RATE_MICRO_PERCENT = MAX_SQLITE_INTEGER
IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.-]{7,127}$")

LOANS_SCHEMA = """
CREATE TABLE IF NOT EXISTS loans (
    loan_id TEXT PRIMARY KEY,
    human_id TEXT NOT NULL,
    ai_id TEXT NOT NULL,
    borrower_type TEXT NOT NULL CHECK (borrower_type IN ('human', 'ai')),
    borrower_id TEXT NOT NULL,
    lender_type TEXT NOT NULL CHECK (lender_type IN ('human', 'ai')),
    lender_id TEXT NOT NULL,
    initiator_type TEXT NOT NULL CHECK (initiator_type IN ('human', 'ai')),
    initiator_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('negotiating', 'active', 'overdue', 'repaid',
                   'rejected', 'withdrawn', 'expired')
    ),
    current_revision INTEGER NOT NULL CHECK (current_revision >= 1),
    accepted_revision INTEGER,
    awaiting_type TEXT CHECK (awaiting_type IN ('human', 'ai') OR awaiting_type IS NULL),
    awaiting_id TEXT,
    counter_count INTEGER NOT NULL DEFAULT 0 CHECK (counter_count >= 0),
    original_principal INTEGER,
    remaining_principal INTEGER,
    daily_rate_micro_percent INTEGER,
    interest_cap_enabled INTEGER CHECK (interest_cap_enabled IN (0, 1) OR interest_cap_enabled IS NULL),
    due_date TEXT,
    accrued_interest INTEGER NOT NULL DEFAULT 0 CHECK (accrued_interest >= 0),
    interest_remainder INTEGER NOT NULL DEFAULT 0 CHECK (interest_remainder >= 0),
    interest_paid INTEGER NOT NULL DEFAULT 0 CHECK (interest_paid >= 0),
    principal_paid INTEGER NOT NULL DEFAULT 0 CHECK (principal_paid >= 0),
    borrower_balance_at_accept INTEGER,
    last_accrual_at TEXT,
    cap_reached_at TEXT,
    accepted_at TEXT,
    repaid_at TEXT,
    proposal_expires_at TEXT NOT NULL,
    activation_idempotency_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (borrower_type = 'human' AND borrower_id = human_id
         AND lender_type = 'ai' AND lender_id = ai_id)
        OR
        (borrower_type = 'ai' AND borrower_id = ai_id
         AND lender_type = 'human' AND lender_id = human_id)
    ),
    CHECK (initiator_type = borrower_type AND initiator_id = borrower_id)
);
CREATE TABLE IF NOT EXISTS loan_revisions (
    loan_id TEXT NOT NULL REFERENCES loans(loan_id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    proposer_type TEXT NOT NULL CHECK (proposer_type IN ('human', 'ai')),
    proposer_id TEXT NOT NULL,
    recipient_type TEXT NOT NULL CHECK (recipient_type IN ('human', 'ai')),
    recipient_id TEXT NOT NULL,
    principal INTEGER NOT NULL CHECK (principal > 0),
    daily_rate_micro_percent INTEGER NOT NULL CHECK (daily_rate_micro_percent >= 0),
    interest_cap_enabled INTEGER NOT NULL CHECK (interest_cap_enabled IN (0, 1)),
    due_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (loan_id, revision)
);
CREATE TABLE IF NOT EXISTS loan_operations (
    actor_type TEXT NOT NULL CHECK (actor_type IN ('human', 'ai')),
    actor_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    action TEXT NOT NULL,
    loan_id TEXT NOT NULL REFERENCES loans(loan_id) ON DELETE RESTRICT,
    revision INTEGER,
    amount INTEGER,
    interest_component INTEGER,
    principal_component INTEGER,
    created_at TEXT NOT NULL,
    PRIMARY KEY (actor_type, actor_id, idempotency_key)
);
"""


def init_loans_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(LOANS_SCHEMA)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_loans_borrower_open "
        "ON loans(borrower_type, borrower_id, status, updated_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_loans_human_recent "
        "ON loans(human_id, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_loans_ai_recent "
        "ON loans(ai_id, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_loans_proposal_expiry "
        "ON loans(status, proposal_expires_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_loans_due_date "
        "ON loans(status, due_date)"
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


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _shanghai_date(now: datetime | None = None) -> date:
    return _aware_utc(now).astimezone(SHANGHAI).date()


def _validate_actor(subject_type: str, subject_id: str) -> None:
    from .chips import _validate_subject

    _validate_subject(subject_type, subject_id)


def _validate_idempotency_key(value: str) -> str:
    if not isinstance(value, str) or not IDEMPOTENCY_RE.fullmatch(value):
        raise DuelError("idempotency_key 需为 8-128 位安全字符")
    return value


def _positive_integer(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DuelError(f"{label}必须是正整数")
    if value > MAX_SQLITE_INTEGER:
        raise DuelError(f"{label}超过技术存储上限")
    return value


def _validate_rate(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DuelError("日利率必须是非负整数微百分比")
    if value > MAX_DAILY_RATE_MICRO_PERCENT:
        raise DuelError("日利率超过技术存储上限")
    return value


def _validate_due_date(value: str, now: datetime | None = None) -> str:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise DuelError("到期日必须是 YYYY-MM-DD 格式") from exc
    today = _shanghai_date(now)
    if parsed < today + timedelta(days=1):
        raise DuelError("到期日至少应为上海时区的次日")
    if parsed > today + timedelta(days=30):
        raise DuelError("到期日不得晚于最终接受日起 30 天")
    return parsed.isoformat()


def _validate_terms(
    principal: int,
    daily_rate_micro_percent: int,
    due_date: str,
    interest_cap_enabled: bool,
    now: datetime | None = None,
) -> tuple[int, int, str, bool]:
    if not isinstance(interest_cap_enabled, bool):
        raise DuelError("利息封顶保护必须是布尔值")
    return (
        _positive_integer(principal, "本金"),
        _validate_rate(daily_rate_micro_percent),
        _validate_due_date(due_date, now),
        interest_cap_enabled,
    )


def _loan_row(conn: sqlite3.Connection, loan_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM loans WHERE loan_id = ?", (loan_id,)).fetchone()
    if row is None:
        raise DuelError("欠条不存在", 404)
    return row


def _revision_row(conn: sqlite3.Connection, loan_id: str, revision: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM loan_revisions WHERE loan_id = ? AND revision = ?",
        (loan_id, revision),
    ).fetchone()
    if row is None:
        raise DuelError("欠条 revision 不存在", 409)
    return row


def _assert_owner(row: sqlite3.Row, actor_type: SubjectType, actor_id: str) -> None:
    identities = {
        (row["borrower_type"], row["borrower_id"]),
        (row["lender_type"], row["lender_id"]),
    }
    if (actor_type, actor_id) not in identities:
        raise DuelError("无权读取或操作这张欠条", 403)


def _assert_revision(row: sqlite3.Row, revision: int) -> None:
    if revision != row["current_revision"]:
        raise DuelError(f"欠条 revision 已变化，当前为 {row['current_revision']}", 409)


def _assert_binding(row: sqlite3.Row, bound_counterparty_id: str | None) -> None:
    if bound_counterparty_id is None or bound_counterparty_id not in {
        row["human_id"], row["ai_id"]
    }:
        raise DuelError("当前绑定关系不能证明这对人机仍然绑定", 403)


def _existing_operation(
    conn: sqlite3.Connection,
    actor_type: SubjectType,
    actor_id: str,
    key: str,
    action: str,
    loan_id: str | None = None,
) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT * FROM loan_operations WHERE actor_type = ? AND actor_id = ? AND idempotency_key = ?",
        (actor_type, actor_id, key),
    ).fetchone()
    if row is not None and (
        row["action"] != action or (loan_id is not None and row["loan_id"] != loan_id)
    ):
        raise DuelError("该 idempotency_key 已用于另一项借款操作", 409)
    return row


def _record_operation(
    conn: sqlite3.Connection,
    *,
    actor_type: SubjectType,
    actor_id: str,
    key: str,
    action: str,
    loan_id: str,
    revision: int | None = None,
    amount: int | None = None,
    interest: int | None = None,
    principal: int | None = None,
    now: datetime | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO loan_operations (
            actor_type, actor_id, idempotency_key, action, loan_id, revision,
            amount, interest_component, principal_component, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor_type, actor_id, key, action, loan_id, revision, amount,
            interest, principal, _timestamp(now),
        ),
    )


def _counterparty(row: sqlite3.Row, actor_type: SubjectType, actor_id: str) -> tuple[str, str]:
    _assert_owner(row, actor_type, actor_id)
    if (actor_type, actor_id) == (row["borrower_type"], row["borrower_id"]):
        return row["lender_type"], row["lender_id"]
    return row["borrower_type"], row["borrower_id"]


def _proposal_event_key(loan_id: str, revision: int) -> str:
    return f"loan:proposal:{loan_id}:revision:{revision}"


def _notify_proposal(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    revision: int,
    *,
    event_type: Literal["created", "countered"],
    now: datetime,
) -> None:
    terms = _revision_row(conn, row["loan_id"], revision)
    summary = (
        f"借款提案：{terms['principal']} 筹码，待你回应"
        if event_type == "created"
        else f"借款条件已修改：{terms['principal']} 筹码，待你回应"
    )
    create_notification(
        conn,
        terms["recipient_type"],
        terms["recipient_id"],
        "loan",
        event_type,
        row["loan_id"],
        summary,
        event_key=_proposal_event_key(row["loan_id"], revision),
        created_at=_timestamp(now),
    )


def _close_proposal_notification(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    revision: int,
    *,
    now: datetime,
) -> None:
    terms = _revision_row(conn, row["loan_id"], revision)
    mark_notifications_read(
        conn,
        terms["recipient_type"],
        terms["recipient_id"],
        "loan",
        event_keys=[_proposal_event_key(row["loan_id"], revision)],
        read_at=_timestamp(now),
    )


def _notify_loan_result(
    conn: sqlite3.Connection,
    subject_type: SubjectType,
    subject_id: str,
    row: sqlite3.Row,
    event_type: Literal["accepted", "rejected", "expired"],
    *,
    now: datetime,
) -> None:
    summaries = {
        "accepted": "借款提案已接受，本金已转账",
        "rejected": "借款提案已被拒绝",
        "expired": "借款提案已过期",
    }
    create_notification(
        conn,
        subject_type,
        subject_id,
        "loan",
        event_type,
        row["loan_id"],
        summaries[event_type],
        event_key=f"loan:{event_type}:{row['loan_id']}:revision:{row['current_revision']}",
        created_at=_timestamp(now),
    )


def _active_debt_count(conn: sqlite3.Connection, subject_type: str, subject_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM loans WHERE borrower_type = ? AND borrower_id = ? AND status IN ('active', 'overdue')",
        (subject_type, subject_id),
    ).fetchone()[0]


def _open_loan_count(conn: sqlite3.Connection, subject_type: str, subject_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM loans WHERE borrower_type = ? AND borrower_id = ? AND status IN ('negotiating', 'active', 'overdue')",
        (subject_type, subject_id),
    ).fetchone()[0]


def _accrue_interest(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    now: datetime,
    *,
    status: str | None = None,
) -> sqlite3.Row:
    last = _parse_timestamp(row["last_accrual_at"] or row["accepted_at"])
    elapsed = max(0, int((now - last).total_seconds()))
    next_status = status or row["status"]
    if elapsed == 0:
        if next_status != row["status"]:
            conn.execute(
                "UPDATE loans SET status = ?, updated_at = ? WHERE loan_id = ?",
                (next_status, _timestamp(now), row["loan_id"]),
            )
            return _loan_row(conn, row["loan_id"])
        return row
    numerator = (
        row["remaining_principal"] * row["daily_rate_micro_percent"] * elapsed
        + row["interest_remainder"]
    )
    charged, remainder = divmod(numerator, ACCRUAL_DENOMINATOR)
    lifetime_before = row["interest_paid"] + row["accrued_interest"]
    cap_reached_now = False
    if row["interest_cap_enabled"]:
        available = max(0, row["original_principal"] - lifetime_before)
        if charged >= available and (charged > 0 or available == 0):
            charged, remainder = available, 0
            cap_reached_now = row["cap_reached_at"] is None
    if row["accrued_interest"] + charged > MAX_SQLITE_INTEGER:
        raise DuelError("未封顶利息累计超过技术存储上限，请联系管理员处理", 409)
    cap_at = _timestamp(now) if cap_reached_now else row["cap_reached_at"]
    conn.execute(
        """
        UPDATE loans SET status = ?, accrued_interest = accrued_interest + ?,
            interest_remainder = ?, last_accrual_at = ?, cap_reached_at = ?,
            updated_at = ? WHERE loan_id = ?
        """,
        (
            next_status, charged, remainder, _timestamp(now), cap_at,
            _timestamp(now), row["loan_id"],
        ),
    )
    updated = _loan_row(conn, row["loan_id"])
    if cap_reached_now:
        from .achievements import record_loan_event

        record_loan_event(
            conn, updated, "loan_interest_cap_reached", event_id=row["loan_id"],
            data={"lifetime_interest": updated["interest_paid"] + updated["accrued_interest"]},
        )
        updated = _loan_row(conn, row["loan_id"])
    return updated


def _refresh_one(conn: sqlite3.Connection, row: sqlite3.Row, now: datetime) -> sqlite3.Row:
    if row["status"] == "negotiating" and _parse_timestamp(row["proposal_expires_at"]) <= now:
        revision = row["current_revision"]
        terms = _revision_row(conn, row["loan_id"], revision)
        _close_proposal_notification(conn, row, revision, now=now)
        conn.execute(
            "UPDATE loans SET status = 'expired', awaiting_type = NULL, awaiting_id = NULL, updated_at = ? WHERE loan_id = ?",
            (_timestamp(now), row["loan_id"]),
        )
        updated = _loan_row(conn, row["loan_id"])
        _notify_loan_result(
            conn,
            terms["proposer_type"],
            terms["proposer_id"],
            updated,
            "expired",
            now=now,
        )
        return updated
    if row["status"] not in {"active", "overdue"}:
        return row
    became_overdue = (
        row["status"] == "active"
        and _shanghai_date(now) > date.fromisoformat(row["due_date"])
    )
    updated = _accrue_interest(
        conn, row, now, status="overdue" if became_overdue else row["status"]
    )
    if became_overdue:
        for subject_type, subject_id in (
            (updated["borrower_type"], updated["borrower_id"]),
            (updated["lender_type"], updated["lender_id"]),
        ):
            create_notification(
                conn,
                subject_type,
                subject_id,
                "loan",
                "overdue",
                updated["loan_id"],
                "借款已逾期",
                event_key=f"loan:overdue:{updated['loan_id']}:{subject_type}:{subject_id}",
                created_at=_timestamp(now),
            )
        from .achievements import record_loan_event

        record_loan_event(
            conn, updated, "loan_overdue", event_id=row["loan_id"],
            data={"overdue_days": (_shanghai_date(now) - date.fromisoformat(row["due_date"])).days},
        )
        updated = _loan_row(conn, row["loan_id"])
    return updated


def _refresh_borrower_loans(
    conn: sqlite3.Connection, subject_type: str, subject_id: str, now: datetime
) -> None:
    for row in conn.execute(
        "SELECT * FROM loans WHERE borrower_type = ? AND borrower_id = ? AND status IN ('negotiating', 'active', 'overdue')",
        (subject_type, subject_id),
    ).fetchall():
        _refresh_one(conn, row, now)


def _new_loan_id(conn: sqlite3.Connection) -> str:
    for _ in range(8):
        loan_id = f"ln_{secrets.token_hex(8)}"
        if conn.execute("SELECT 1 FROM loans WHERE loan_id = ?", (loan_id,)).fetchone() is None:
            return loan_id
    raise RuntimeError("unable to allocate a unique loan id")


def create_loan(
    borrower_type: SubjectType,
    borrower_id: str,
    lender_id: str,
    *,
    principal: int,
    daily_rate_micro_percent: int,
    due_date: str,
    interest_cap_enabled: bool = True,
    idempotency_key: str,
    pair_is_bound: bool,
) -> dict:
    _validate_actor(borrower_type, borrower_id)
    lender_type: SubjectType = "ai" if borrower_type == "human" else "human"
    _validate_actor(lender_type, lender_id)
    if not pair_is_bound:
        raise DuelError("只能向当前绑定的人类或小机发起借款", 403)
    key = _validate_idempotency_key(idempotency_key)
    now = _aware_utc()
    terms = _validate_terms(
        principal, daily_rate_micro_percent, due_date, interest_cap_enabled, now
    )
    with write_transaction() as conn:
        existing = _existing_operation(conn, borrower_type, borrower_id, key, "create")
        if existing is not None:
            row = _refresh_one(conn, _loan_row(conn, existing["loan_id"]), now)
            return _loan_payload(conn, row, borrower_type, borrower_id)
        _refresh_borrower_loans(conn, borrower_type, borrower_id, now)
        if conn.execute(
            "SELECT 1 FROM loans WHERE borrower_type = ? AND borrower_id = ? AND status = 'overdue' LIMIT 1",
            (borrower_type, borrower_id),
        ).fetchone() is not None:
            raise DuelError("存在逾期欠条时不能发起新借款", 409)
        if _open_loan_count(conn, borrower_type, borrower_id) >= MAX_OPEN_LOANS:
            raise DuelError("每名借款人最多同时保有 3 张未结欠条", 409)
        loan_id = _new_loan_id(conn)
        timestamp = _timestamp(now)
        expires = _timestamp(now + PROPOSAL_LIFETIME)
        human_id = borrower_id if borrower_type == "human" else lender_id
        ai_id = borrower_id if borrower_type == "ai" else lender_id
        conn.execute(
            """
            INSERT INTO loans (
                loan_id, human_id, ai_id, borrower_type, borrower_id,
                lender_type, lender_id, initiator_type, initiator_id,
                status, current_revision, awaiting_type, awaiting_id,
                proposal_expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'negotiating', 1, ?, ?, ?, ?, ?)
            """,
            (
                loan_id, human_id, ai_id, borrower_type, borrower_id,
                lender_type, lender_id, borrower_type, borrower_id,
                lender_type, lender_id, expires, timestamp, timestamp,
            ),
        )
        conn.execute(
            """
            INSERT INTO loan_revisions (
                loan_id, revision, proposer_type, proposer_id, recipient_type,
                recipient_id, principal, daily_rate_micro_percent,
                interest_cap_enabled, due_date, created_at, expires_at
            ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                loan_id, borrower_type, borrower_id, lender_type, lender_id,
                terms[0], terms[1], int(terms[3]), terms[2], timestamp, expires,
            ),
        )
        _record_operation(
            conn, actor_type=borrower_type, actor_id=borrower_id, key=key,
            action="create", loan_id=loan_id, revision=1, now=now,
        )
        created = _loan_row(conn, loan_id)
        _notify_proposal(conn, created, 1, event_type="created", now=now)
        return _loan_payload(conn, created, borrower_type, borrower_id)


def counter_loan(
    loan_id: str,
    actor_type: SubjectType,
    actor_id: str,
    *,
    revision: int,
    principal: int,
    daily_rate_micro_percent: int,
    due_date: str,
    interest_cap_enabled: bool,
    idempotency_key: str,
    bound_counterparty_id: str | None,
) -> dict:
    key = _validate_idempotency_key(idempotency_key)
    now = _aware_utc()
    terms = _validate_terms(
        principal, daily_rate_micro_percent, due_date, interest_cap_enabled, now
    )
    with write_transaction() as conn:
        existing = _existing_operation(conn, actor_type, actor_id, key, "counter", loan_id)
        if existing is not None:
            return _loan_payload(
                conn, _refresh_one(conn, _loan_row(conn, loan_id), now), actor_type, actor_id
            )
        row = _refresh_one(conn, _loan_row(conn, loan_id), now)
        _assert_owner(row, actor_type, actor_id)
        _assert_binding(row, bound_counterparty_id)
        _assert_revision(row, revision)
        if row["status"] != "negotiating":
            raise DuelError("只有未生效且未过期的提案可以改条件", 409)
        if (row["awaiting_type"], row["awaiting_id"]) != (actor_type, actor_id):
            raise DuelError("当前还没轮到你回应这张欠条", 409)
        current = _revision_row(conn, loan_id, revision)
        if (
            terms[0] == current["principal"]
            and terms[1] == current["daily_rate_micro_percent"]
            and int(terms[3]) == current["interest_cap_enabled"]
            and terms[2] == current["due_date"]
        ):
            raise DuelError("改条件至少需要改变一项条款")
        _close_proposal_notification(conn, row, revision, now=now)
        other_type, other_id = _counterparty(row, actor_type, actor_id)
        next_revision = revision + 1
        expires = _timestamp(now + PROPOSAL_LIFETIME)
        conn.execute(
            """
            INSERT INTO loan_revisions (
                loan_id, revision, proposer_type, proposer_id, recipient_type,
                recipient_id, principal, daily_rate_micro_percent,
                interest_cap_enabled, due_date, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                loan_id, next_revision, actor_type, actor_id, other_type, other_id,
                terms[0], terms[1], int(terms[3]), terms[2], _timestamp(now), expires,
            ),
        )
        conn.execute(
            """
            UPDATE loans SET current_revision = ?, awaiting_type = ?, awaiting_id = ?,
                counter_count = counter_count + 1, proposal_expires_at = ?, updated_at = ?
            WHERE loan_id = ?
            """,
            (next_revision, other_type, other_id, expires, _timestamp(now), loan_id),
        )
        _record_operation(
            conn, actor_type=actor_type, actor_id=actor_id, key=key,
            action="counter", loan_id=loan_id, revision=next_revision, now=now,
        )
        countered = _loan_row(conn, loan_id)
        _notify_proposal(
            conn, countered, next_revision, event_type="countered", now=now
        )
        return _loan_payload(conn, countered, actor_type, actor_id)


def accept_loan(
    loan_id: str,
    actor_type: SubjectType,
    actor_id: str,
    *,
    revision: int,
    idempotency_key: str,
    bound_counterparty_id: str | None,
) -> dict:
    key = _validate_idempotency_key(idempotency_key)
    now = _aware_utc()
    with write_transaction() as conn:
        existing = _existing_operation(conn, actor_type, actor_id, key, "accept", loan_id)
        if existing is not None:
            return _loan_payload(
                conn, _refresh_one(conn, _loan_row(conn, loan_id), now), actor_type, actor_id
            )
        row = _refresh_one(conn, _loan_row(conn, loan_id), now)
        _assert_owner(row, actor_type, actor_id)
        _assert_binding(row, bound_counterparty_id)
        _assert_revision(row, revision)
        if row["status"] != "negotiating":
            raise DuelError("只有当前未生效提案可以接受", 409)
        if (row["awaiting_type"], row["awaiting_id"]) != (actor_type, actor_id):
            raise DuelError("当前没轮到你接受这张欠条", 409)
        other_type, other_id = _counterparty(row, actor_type, actor_id)
        _close_proposal_notification(conn, row, revision, now=now)
        terms = _revision_row(conn, loan_id, revision)
        _validate_due_date(terms["due_date"], now)
        lender = _ensure_wallet(conn, row["lender_type"], row["lender_id"])
        borrower = _ensure_wallet(conn, row["borrower_type"], row["borrower_id"])
        principal = terms["principal"]
        if lender["balance"] <= 0 or principal > lender["balance"]:
            raise DuelError("出借人当前余额必须为正且足以覆盖全部本金", 409)
        if borrower["balance"] + principal > MAX_SQLITE_INTEGER:
            raise DuelError("借款人收款后的余额超过技术存储上限", 409)
        borrower_before = borrower["balance"]
        ledger_key = f"loan:{loan_id}:revision:{revision}:principal"
        metadata = {
            "loan_id": loan_id, "revision": revision, "idempotency_key": key,
            "borrower_type": row["borrower_type"], "borrower_id": row["borrower_id"],
            "lender_type": row["lender_type"], "lender_id": row["lender_id"],
            "principal": principal,
        }
        _apply_balance_change(
            conn, lender, -principal, "loan_principal_out",
            idempotency_key=ledger_key, reference_type="loan",
            reference_id=loan_id, metadata=metadata,
        )
        _apply_balance_change(
            conn, borrower, principal, "loan_principal_in",
            idempotency_key=ledger_key, reference_type="loan",
            reference_id=loan_id, metadata=metadata,
        )
        timestamp = _timestamp(now)
        conn.execute(
            """
            UPDATE loans SET status = 'active', accepted_revision = ?,
                awaiting_type = NULL, awaiting_id = NULL,
                original_principal = ?, remaining_principal = ?,
                daily_rate_micro_percent = ?, interest_cap_enabled = ?, due_date = ?,
                accrued_interest = 0, interest_remainder = 0, interest_paid = 0,
                principal_paid = 0, borrower_balance_at_accept = ?,
                last_accrual_at = ?, accepted_at = ?,
                activation_idempotency_key = ?, updated_at = ? WHERE loan_id = ?
            """,
            (
                revision, principal, principal, terms["daily_rate_micro_percent"],
                terms["interest_cap_enabled"], terms["due_date"], borrower_before,
                timestamp, timestamp, key, timestamp, loan_id,
            ),
        )
        _record_operation(
            conn, actor_type=actor_type, actor_id=actor_id, key=key,
            action="accept", loan_id=loan_id, revision=revision, now=now,
        )
        activated = _loan_row(conn, loan_id)
        from .achievements import record_loan_event

        record_loan_event(
            conn, activated, "loan_activated", event_id=loan_id,
            data={
                "counter_count": activated["counter_count"],
                "borrower_balance_before": borrower_before,
                "active_debt_count": _active_debt_count(
                    conn, row["borrower_type"], row["borrower_id"]
                ),
            },
        )
        activated = _loan_row(conn, loan_id)
        _notify_loan_result(
            conn, other_type, other_id, activated, "accepted", now=now
        )
        return _loan_payload(conn, activated, actor_type, actor_id)


def close_proposal(
    loan_id: str,
    actor_type: SubjectType,
    actor_id: str,
    *,
    action: Literal["reject", "withdraw"],
    revision: int,
    idempotency_key: str,
) -> dict:
    key = _validate_idempotency_key(idempotency_key)
    now = _aware_utc()
    with write_transaction() as conn:
        existing = _existing_operation(conn, actor_type, actor_id, key, action, loan_id)
        if existing is not None:
            return _loan_payload(conn, _loan_row(conn, loan_id), actor_type, actor_id)
        row = _refresh_one(conn, _loan_row(conn, loan_id), now)
        _assert_owner(row, actor_type, actor_id)
        _assert_revision(row, revision)
        if row["status"] != "negotiating":
            raise DuelError("只有未生效提案可以拒绝或撤销", 409)
        if action == "reject":
            if (row["awaiting_type"], row["awaiting_id"]) != (actor_type, actor_id):
                raise DuelError("只有当前收到提案的一方可以拒绝", 403)
            status = "rejected"
            other_type, other_id = _counterparty(row, actor_type, actor_id)
        else:
            if (row["initiator_type"], row["initiator_id"]) != (actor_type, actor_id):
                raise DuelError("只有借款发起人可以撤销提案", 403)
            status = "withdrawn"
            other_type = other_id = None
        _close_proposal_notification(conn, row, revision, now=now)
        conn.execute(
            "UPDATE loans SET status = ?, awaiting_type = NULL, awaiting_id = NULL, updated_at = ? WHERE loan_id = ?",
            (status, _timestamp(now), loan_id),
        )
        _record_operation(
            conn, actor_type=actor_type, actor_id=actor_id, key=key,
            action=action, loan_id=loan_id, revision=revision, now=now,
        )
        closed = _loan_row(conn, loan_id)
        if action == "reject" and other_type is not None and other_id is not None:
            _notify_loan_result(
                conn, other_type, other_id, closed, "rejected", now=now
            )
        return _loan_payload(conn, closed, actor_type, actor_id)


def repay_loan(
    loan_id: str,
    actor_type: SubjectType,
    actor_id: str,
    *,
    amount: int,
    idempotency_key: str,
) -> dict:
    payment = _positive_integer(amount, "还款额")
    key = _validate_idempotency_key(idempotency_key)
    now = _aware_utc()
    with write_transaction() as conn:
        existing = _existing_operation(conn, actor_type, actor_id, key, "repay", loan_id)
        if existing is not None:
            payload = _loan_payload(
                conn, _refresh_one(conn, _loan_row(conn, loan_id), now), actor_type, actor_id
            )
            payload["repayment"] = {
                "amount": existing["amount"],
                "interest": existing["interest_component"],
                "principal": existing["principal_component"],
                "idempotent_replay": True,
            }
            return payload
        row = _refresh_one(conn, _loan_row(conn, loan_id), now)
        _assert_owner(row, actor_type, actor_id)
        if (row["borrower_type"], row["borrower_id"]) != (actor_type, actor_id):
            raise DuelError("只有借款人可以偿还这张欠条", 403)
        if row["status"] not in {"active", "overdue"}:
            raise DuelError("只有生效或逾期欠条可以还款", 409)
        total_due = row["remaining_principal"] + row["accrued_interest"]
        if payment > total_due:
            raise DuelError("还款额不能超过当前应还金额", 409)
        borrower = _ensure_wallet(conn, row["borrower_type"], row["borrower_id"])
        lender = _ensure_wallet(conn, row["lender_type"], row["lender_id"])
        if borrower["balance"] < payment:
            raise DuelError("还款后钱包不得为负，请降低还款额", 409)
        if lender["balance"] + payment > MAX_SQLITE_INTEGER:
            raise DuelError("出借人收款后的余额超过技术存储上限", 409)
        interest = min(payment, row["accrued_interest"])
        principal = payment - interest
        if row["interest_paid"] + interest > MAX_SQLITE_INTEGER:
            raise DuelError("终身已还利息超过技术存储上限，请联系管理员处理", 409)
        remaining_interest = row["accrued_interest"] - interest
        remaining_principal = row["remaining_principal"] - principal
        fully_repaid = remaining_interest == 0 and remaining_principal == 0
        metadata = {
            "loan_id": loan_id, "revision": row["accepted_revision"],
            "idempotency_key": key, "amount": payment,
            "interest": interest, "principal": principal,
        }
        ledger_key = f"loan:{loan_id}:repayment:{actor_type}:{actor_id}:{key}"
        _apply_balance_change(
            conn, borrower, -payment, "loan_repayment_out",
            idempotency_key=ledger_key, reference_type="loan",
            reference_id=loan_id, metadata=metadata,
        )
        _apply_balance_change(
            conn, lender, payment, "loan_repayment_in",
            idempotency_key=ledger_key, reference_type="loan",
            reference_id=loan_id, metadata=metadata,
        )
        status = "repaid" if fully_repaid else row["status"]
        conn.execute(
            """
            UPDATE loans SET status = ?, remaining_principal = ?, accrued_interest = ?,
                interest_paid = interest_paid + ?, principal_paid = principal_paid + ?,
                repaid_at = COALESCE(?, repaid_at), updated_at = ? WHERE loan_id = ?
            """,
            (
                status, remaining_principal, remaining_interest, interest, principal,
                _timestamp(now) if fully_repaid else None, _timestamp(now), loan_id,
            ),
        )
        _record_operation(
            conn, actor_type=actor_type, actor_id=actor_id, key=key,
            action="repay", loan_id=loan_id, revision=row["accepted_revision"],
            amount=payment, interest=interest, principal=principal, now=now,
        )
        updated = _loan_row(conn, loan_id)
        create_notification(
            conn,
            updated["lender_type"],
            updated["lender_id"],
            "loan",
            "repaid" if fully_repaid else "repayment",
            loan_id,
            (
                f"借款已还清：收到 {payment} 筹码"
                if fully_repaid
                else f"收到部分还款 {payment} 筹码"
            ),
            event_key=f"loan:repayment:{loan_id}:{actor_type}:{actor_id}:{key}",
            created_at=_timestamp(now),
        )
        from .achievements import record_loan_event

        if not fully_repaid:
            record_loan_event(
                conn, updated, "loan_partial_repayment", event_id=key,
                data={"amount": payment, "interest": interest, "principal": principal},
            )
        else:
            on_time = _shanghai_date(now) <= date.fromisoformat(updated["due_date"])
            record_loan_event(
                conn, updated, "loan_repaid", event_id=loan_id,
                data={"on_time": on_time},
            )
            if _active_debt_count(conn, actor_type, actor_id) == 0:
                record_loan_event(
                    conn, updated, "loan_debt_free",
                    event_id=f"{actor_type}:{actor_id}:{loan_id}",
                    data={"active_debt_count": 0},
                )
        payload = _loan_payload(conn, _loan_row(conn, loan_id), actor_type, actor_id)
        payload["repayment"] = {
            "amount": payment, "interest": interest, "principal": principal,
            "idempotent_replay": False,
        }
        return payload


def get_loan(loan_id: str, actor_type: SubjectType, actor_id: str) -> dict:
    _validate_actor(actor_type, actor_id)
    with write_transaction() as conn:
        row = _refresh_one(conn, _loan_row(conn, loan_id), _aware_utc())
        _assert_owner(row, actor_type, actor_id)
        return _loan_payload(conn, row, actor_type, actor_id)


def list_loans(
    actor_type: SubjectType,
    actor_id: str,
    *,
    counterparty_id: str | None = None,
    bound_counterparty_ids: set[str] | None = None,
    limit: int = 50,
) -> list[dict]:
    """Explicit loan query; the only surface that refreshes overdue state."""
    _validate_actor(actor_type, actor_id)
    safe_limit = max(1, min(limit, 100))
    now = _aware_utc()
    column = "human_id" if actor_type == "human" else "ai_id"
    other_column = "ai_id" if actor_type == "human" else "human_id"
    with write_transaction() as conn:
        where = f"{column} = ?"
        params: list[object] = [actor_id]
        if counterparty_id is not None:
            where += f" AND {other_column} = ?"
            params.append(counterparty_id)
        params.append(safe_limit)
        rows = conn.execute(
            f"""
            SELECT * FROM loans WHERE {where}
            ORDER BY CASE WHEN status IN ('active', 'overdue') THEN 0
                          WHEN status = 'negotiating' THEN 1 ELSE 2 END,
                     created_at DESC LIMIT ?
            """,
            params,
        ).fetchall()
        payloads = []
        for original in rows:
            row = _refresh_one(conn, original, now)
            other_id = row["ai_id"] if actor_type == "human" else row["human_id"]
            payloads.append(
                _loan_payload(
                    conn, row, actor_type, actor_id,
                    pair_is_bound=other_id in (bound_counterparty_ids or set()),
                )
            )
        return payloads


def _rate_display(rate: int) -> str:
    whole, fraction = divmod(rate, 1_000_000)
    return f"{whole}.{fraction:06d}".rstrip("0").rstrip(".")


def _loan_payload(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    viewer_type: SubjectType,
    viewer_id: str,
    *,
    pair_is_bound: bool = False,
) -> dict:
    _assert_owner(row, viewer_type, viewer_id)
    revision = _revision_row(conn, row["loan_id"], row["current_revision"])
    activated = row["status"] in {"active", "overdue", "repaid"}
    principal = row["original_principal"] if activated else revision["principal"]
    rate = row["daily_rate_micro_percent"] if activated else revision["daily_rate_micro_percent"]
    cap = bool(row["interest_cap_enabled"] if activated else revision["interest_cap_enabled"])
    due = row["due_date"] if activated else revision["due_date"]
    accrued = row["accrued_interest"] if activated else 0
    interest_paid = row["interest_paid"] if activated else 0
    principal_paid = row["principal_paid"] if activated else 0
    remaining = row["remaining_principal"] if activated else principal
    overdue_days = (
        max(0, (_shanghai_date() - date.fromisoformat(due)).days)
        if row["status"] in {"active", "overdue"} else 0
    )
    borrower_view = (viewer_type, viewer_id) == (row["borrower_type"], row["borrower_id"])
    awaiting_you = (viewer_type, viewer_id) == (row["awaiting_type"], row["awaiting_id"])
    actions: list[str] = []
    if row["status"] == "negotiating":
        if awaiting_you:
            actions.append("reject")
            if pair_is_bound:
                actions.extend(["accept", "counter"])
        if (viewer_type, viewer_id) == (row["initiator_type"], row["initiator_id"]):
            actions.append("withdraw")
    elif row["status"] in {"active", "overdue"} and borrower_view:
        actions.append("repay")
    return {
        "loan_id": row["loan_id"], "status": row["status"],
        "direction": "borrowing" if borrower_view else "lending",
        "borrower": {"type": row["borrower_type"], "id": row["borrower_id"]},
        "lender": {"type": row["lender_type"], "id": row["lender_id"]},
        "human_id": row["human_id"], "ai_id": row["ai_id"],
        "counterparty_id": row["lender_id"] if borrower_view else row["borrower_id"],
        "revision": row["current_revision"],
        "accepted_revision": row["accepted_revision"],
        "counter_count": row["counter_count"],
        "awaiting": (
            {"type": row["awaiting_type"], "id": row["awaiting_id"], "you": awaiting_you}
            if row["awaiting_id"] else None
        ),
        "principal": principal, "remaining_principal": remaining,
        "daily_rate_micro_percent": rate, "daily_rate_percent": _rate_display(rate),
        "accrued_interest": accrued, "interest_paid": interest_paid,
        "lifetime_interest": accrued + interest_paid,
        "principal_paid": principal_paid,
        "total_repaid": principal_paid + interest_paid,
        "total_due": remaining + accrued,
        "due_date": due, "overdue_days": overdue_days,
        "interest_cap_enabled": cap,
        "interest_cap_amount": principal if cap else None,
        "interest_cap_reached": row["cap_reached_at"] is not None,
        "interest_rounding": "carry_remainder_then_floor",
        "proposal_expires_at": row["proposal_expires_at"] if row["status"] == "negotiating" else None,
        "accepted_at": row["accepted_at"], "repaid_at": row["repaid_at"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "pair_currently_bound": pair_is_bound,
        "allowed_actions": list(dict.fromkeys(actions)),
    }
