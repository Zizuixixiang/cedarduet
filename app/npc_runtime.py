"""Persistence-only NPC decision contract.

This module deliberately does not import or call an LLM. A future concrete game
supplies its authoritative legal-action list, reserves the current room
revision, asks its controller for one choice, then records only a member of that
list. The unique key makes retries safe across workers and process restarts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .database import connect, decode_room, write_transaction
from .framework import DuelError, _now, _player_id, _room_id


MAX_NPC_MESSAGE_LENGTH = 500
# Two provider attempts can each use the configured 60-second maximum timeout.
# A stale reservation is recovered locally after this lease and never causes a
# second provider request for the same revision.
NPC_DECISION_LEASE_SECONDS = 130


def list_active_npc_turn_room_ids() -> list[str]:
    """Return playing rooms whose current actor is an active system NPC."""
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT room.room_id
            FROM rooms AS room
            JOIN room_participants AS actor
              ON actor.room_id = room.room_id
             AND actor.player_id = room.current_player_id
            WHERE room.status = 'playing'
              AND actor.participant_kind = 'system_npc'
              AND actor.join_status = 'joined'
              AND actor.activity_state = 'active'
              AND actor.active = 1
            ORDER BY room.updated_at, room.room_id
            """
        ).fetchall()
    finally:
        conn.close()
    return [row["room_id"] for row in rows]


@dataclass(frozen=True)
class NpcDecisionTicket:
    idempotency_key: str
    room_id: str
    revision: int
    npc_player_id: str
    created: bool
    status: str
    decision: dict[str, Any] | None = None
    error: str | None = None
    stale_recovery: bool = False


def _reservation_is_stale(updated_at: str | None) -> bool:
    if not updated_at:
        return True
    try:
        parsed = datetime.fromisoformat(updated_at)
    except (TypeError, ValueError):
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (
        datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    ).total_seconds() >= NPC_DECISION_LEASE_SECONDS


def npc_decision_key(room_id: str, revision: int, npc_player_id: str) -> str:
    room_id = _room_id(room_id)
    npc_player_id = _player_id(npc_player_id)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise DuelError("NPC revision 必须是非负整数")
    return f"npc_decision:{room_id}:{revision}:{npc_player_id}"


def reserve_npc_decision(
    room_id: str, revision: int, npc_player_id: str
) -> NpcDecisionTicket:
    """Reserve exactly one decision for the current NPC actor and revision."""
    key = npc_decision_key(room_id, revision, npc_player_id)
    with write_transaction() as conn:
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (_room_id(room_id),)
        ).fetchone()
        if row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(row, conn)
        if room["status"] != "playing":
            raise DuelError("只有进行中的房间可以生成 NPC 决策", 409)
        if room["revision"] != revision:
            raise DuelError("NPC 决策 revision 已过期", 409)
        if room.get("current_player_id") != npc_player_id:
            raise DuelError("当前行动者不是该 NPC", 409)
        participant = next(
            (
                item for item in room["participants"]
                if item["player_id"] == npc_player_id
            ),
            None,
        )
        if participant is None or participant.get("participant_kind") != "system_npc":
            raise DuelError("npc_player_id 不是系统 NPC", 403)
        timestamp = _now()
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO npc_decisions (
                idempotency_key, room_id, revision, npc_player_id,
                status, decision_json, error_text, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'reserved', NULL, NULL, ?, ?)
            """,
            (key, room_id, revision, npc_player_id, timestamp, timestamp),
        )
        stored = conn.execute(
            """
            SELECT status, decision_json, error_text, updated_at
            FROM npc_decisions WHERE idempotency_key = ?
            """,
            (key,),
        ).fetchone()
        created = cursor.rowcount == 1
        stale_recovery = False
        if not created and stored["status"] == "failed":
            retry = conn.execute(
                """
                UPDATE npc_decisions
                SET status = 'reserved', error_text = NULL, updated_at = ?
                WHERE idempotency_key = ? AND status = 'failed'
                """,
                (timestamp, key),
            )
            created = retry.rowcount == 1
        elif (
            not created
            and stored["status"] == "reserved"
            and _reservation_is_stale(stored["updated_at"])
        ):
            recovery = conn.execute(
                """
                UPDATE npc_decisions
                SET error_text = 'stale reservation recovered locally',
                    updated_at = ?
                WHERE idempotency_key = ? AND status = 'reserved'
                  AND updated_at = ?
                """,
                (timestamp, key, stored["updated_at"]),
            )
            created = recovery.rowcount == 1
            stale_recovery = created
        if created and cursor.rowcount != 1:
            stored = conn.execute(
                """
                SELECT status, decision_json, error_text, updated_at
                FROM npc_decisions WHERE idempotency_key = ?
                """,
                (key,),
            ).fetchone()
    decision = json.loads(stored["decision_json"]) if stored["decision_json"] else None
    return NpcDecisionTicket(
        key, room_id, revision, npc_player_id,
        created, stored["status"], decision, stored["error_text"], stale_recovery,
    )


def complete_npc_decision(
    ticket: NpcDecisionTicket,
    selected_action: dict[str, Any],
    legal_actions: list[dict[str, Any]],
    *,
    message: str | None = None,
) -> bool:
    """Persist a model choice only when it exactly matches a legal action."""
    if not isinstance(selected_action, dict) or not isinstance(legal_actions, list):
        raise DuelError("NPC 行动与合法行动列表格式无效")
    canonical_choice = json.dumps(
        selected_action, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    legal = {
        json.dumps(action, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for action in legal_actions if isinstance(action, dict)
    }
    if canonical_choice not in legal:
        raise DuelError("NPC 只能选择规则引擎提供的合法行动")
    if message is not None:
        if not isinstance(message, str) or len(message.strip()) > MAX_NPC_MESSAGE_LENGTH:
            raise DuelError("NPC 短消息格式无效")
        message = message.strip() or None
    payload = {"action": selected_action, "message": message}
    with write_transaction() as conn:
        row = conn.execute(
            "SELECT status FROM npc_decisions WHERE idempotency_key = ?",
            (ticket.idempotency_key,),
        ).fetchone()
        if row is None:
            raise DuelError("NPC 决策预留不存在", 404)
        if row["status"] == "completed":
            return False
        if row["status"] != "reserved":
            raise DuelError("NPC 决策已经失败，不能完成", 409)
        conn.execute(
            """
            UPDATE npc_decisions
            SET status = 'completed', decision_json = ?, error_text = NULL,
                updated_at = ?
            WHERE idempotency_key = ? AND status = 'reserved'
            """,
            (
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                _now(), ticket.idempotency_key,
            ),
        )
    return True


def fail_npc_decision(ticket: NpcDecisionTicket, error: str) -> bool:
    text = str(error).strip()[:500] or "unknown error"
    with write_transaction() as conn:
        cursor = conn.execute(
            """
            UPDATE npc_decisions
            SET status = 'failed', error_text = ?, updated_at = ?
            WHERE idempotency_key = ? AND status = 'reserved'
            """,
            (text, _now(), ticket.idempotency_key),
        )
    return cursor.rowcount == 1
