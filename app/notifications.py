"""Persistent, symmetric unread notifications for human and bound-machine subjects."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Literal

from .database import connect, write_transaction

SubjectType = Literal["human", "ai"]
Category = Literal["game", "loan", "exchange", "achievement"]

CATEGORIES: tuple[Category, ...] = ("game", "loan", "exchange", "achievement")
CATEGORY_LABELS: dict[Category, str] = {
    "game": "对局",
    "loan": "借款",
    "exchange": "兑换",
    "achievement": "成就",
}
CATEGORY_ENTRIES: dict[Category, str] = {
    "game": "rooms",
    "loan": "chips/loans",
    "exchange": "chips/exchange",
    "achievement": "chips/achievements",
}

NOTIFICATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('human', 'ai')),
    subject_id TEXT NOT NULL,
    category TEXT NOT NULL CHECK (
        category IN ('game', 'loan', 'exchange', 'achievement')
    ),
    event_type TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT,
    UNIQUE (subject_type, subject_id, event_key)
)
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_subject(subject_type: str, subject_id: str) -> None:
    if subject_type not in {"human", "ai"}:
        raise ValueError("通知主体类型无效")
    if not isinstance(subject_id, str) or not subject_id.strip() or len(subject_id) > 80:
        raise ValueError("通知主体 ID 无效")


def _validate_category(category: str) -> None:
    if category not in CATEGORIES:
        raise ValueError("通知类别无效")


def init_notifications_schema(conn: sqlite3.Connection) -> None:
    """Create the additive schema without deriving historical notifications."""
    conn.execute(NOTIFICATIONS_SCHEMA)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_notifications_subject_unread
        ON notifications(subject_type, subject_id, read_at, category, id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_notifications_subject_reference
        ON notifications(subject_type, subject_id, category, reference_id, read_at)
        """
    )


def create_notification(
    conn: sqlite3.Connection,
    subject_type: SubjectType,
    subject_id: str,
    category: Category,
    event_type: str,
    reference_id: str,
    summary: str,
    *,
    event_key: str,
    created_at: str | None = None,
) -> bool:
    """Insert one immutable event; a retry never resurrects or duplicates it."""
    _validate_subject(subject_type, subject_id)
    _validate_category(category)
    if not event_type or not reference_id or not event_key or not summary:
        raise ValueError("通知事件字段不能为空")
    # During startup the strict legacy achievement backfill intentionally runs
    # before this additive table exists. Historical facts must not become new
    # unread notifications merely because the service was upgraded.
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'notifications'"
    ).fetchone() is None:
        return False
    inserted = conn.execute(
        """
        INSERT INTO notifications (
            subject_type, subject_id, category, event_type, reference_id,
            event_key, summary, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(subject_type, subject_id, event_key) DO NOTHING
        """,
        (
            subject_type,
            subject_id,
            category,
            event_type,
            reference_id,
            event_key,
            summary,
            created_at or _now(),
        ),
    ).rowcount
    return bool(inserted)


def mark_notifications_read(
    conn: sqlite3.Connection,
    subject_type: SubjectType,
    subject_id: str,
    category: Category,
    *,
    reference_id: str | None = None,
    event_keys: list[str] | tuple[str, ...] | None = None,
    read_at: str | None = None,
) -> int:
    """Read only rows owned by the exact authenticated subject."""
    _validate_subject(subject_type, subject_id)
    _validate_category(category)
    clauses = [
        "subject_type = ?", "subject_id = ?", "category = ?", "read_at IS NULL"
    ]
    params: list[object] = [subject_type, subject_id, category]
    if reference_id is not None:
        clauses.append("reference_id = ?")
        params.append(reference_id)
    if event_keys is not None:
        keys = [key for key in event_keys if key]
        if not keys:
            return 0
        clauses.append(f"event_key IN ({','.join('?' for _ in keys)})")
        params.extend(keys)
    params.append(read_at or _now())
    cursor = conn.execute(
        f"UPDATE notifications SET read_at = ? WHERE {' AND '.join(clauses)}",
        [params[-1], *params[:-1]],
    )
    return cursor.rowcount


def ack_notifications(
    subject_type: SubjectType,
    subject_id: str,
    category: Category,
    *,
    reference_id: str | None = None,
    event_keys: list[str] | tuple[str, ...] | None = None,
) -> int:
    with write_transaction() as conn:
        return mark_notifications_read(
            conn,
            subject_type,
            subject_id,
            category,
            reference_id=reference_id,
            event_keys=event_keys,
        )


def ack_explicit_achievement_unlocks(
    subject_type: SubjectType, subject_id: str, unlocks: list[dict]
) -> int:
    """Acknowledge unlock notices already shown in this subject's response."""
    event_keys = [
        f"achievement:unlocked:{item['id']}:{item.get('context_key') or 'global'}"
        for item in unlocks
        if isinstance(item, dict) and item.get("id")
    ]
    if not event_keys:
        return 0
    return ack_notifications(
        subject_type, subject_id, "achievement", event_keys=event_keys
    )


def unread_summary(
    subject_type: SubjectType,
    subject_id: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Return counts only; reading a summary never acknowledges anything."""
    _validate_subject(subject_type, subject_id)
    owns_connection = conn is None
    if conn is None:
        conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT category, COUNT(*) AS count
            FROM notifications
            WHERE subject_type = ? AND subject_id = ? AND read_at IS NULL
            GROUP BY category
            """,
            (subject_type, subject_id),
        ).fetchall()
    finally:
        if owns_connection:
            conn.close()
    found = {row["category"]: row["count"] for row in rows}
    categories = {category: int(found.get(category, 0)) for category in CATEGORIES}
    return {"total": sum(categories.values()), "categories": categories}


def consume_notifications(
    subject_type: SubjectType,
    subject_id: str,
    category: Category,
    *,
    reference_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Atomically return bounded short notices and mark all matching rows read."""
    _validate_subject(subject_type, subject_id)
    _validate_category(category)
    safe_limit = max(1, min(int(limit), 100))
    with write_transaction() as conn:
        where = (
            "subject_type = ? AND subject_id = ? AND category = ? AND read_at IS NULL"
        )
        params: list[object] = [subject_type, subject_id, category]
        if reference_id is not None:
            where += " AND reference_id = ?"
            params.append(reference_id)
        total = conn.execute(
            f"SELECT COUNT(*) FROM notifications WHERE {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT id, event_type, reference_id, summary, created_at
            FROM notifications
            WHERE {where}
            ORDER BY id
            LIMIT ?
            """,
            [*params, safe_limit],
        ).fetchall()
        if total:
            conn.execute(
                f"UPDATE notifications SET read_at = ? WHERE {where}",
                [_now(), *params],
            )
        notices = [
            {
                "event": row["event_type"],
                "reference_id": row["reference_id"],
                "summary": row["summary"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        if total > len(rows):
            notices.append(
                {
                    "event": "earlier",
                    "reference_id": reference_id or "*",
                    "summary": f"另有 {total - len(rows)} 条较早通知已读",
                    "created_at": rows[-1]["created_at"] if rows else _now(),
                }
            )
        return notices


def unread_hint(summary: dict) -> str:
    parts = [
        f"{CATEGORY_LABELS[category]}（未读{summary['categories'][category]}）"
        f"→{CATEGORY_ENTRIES[category]}"
        for category in CATEGORIES
        if summary["categories"].get(category, 0)
    ]
    return "；".join(parts)


def attach_mcp_unread(payload: dict, subject_id: str) -> dict:
    """Attach the compact stable MCP summary only when unread work remains."""
    summary = unread_summary("ai", subject_id)
    if summary["total"]:
        payload["unread"] = summary
        payload["unread_hint"] = unread_hint(summary)
    return payload
