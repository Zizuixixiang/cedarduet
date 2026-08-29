import json
import re
import secrets
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Literal

from .database import connect, decode_room, write_transaction
from .games import get_game
from .games.base import MoveResult
from .notifications import create_notification, mark_notifications_read
from .npc_personas import PersonaConfigError, load_personas

Role = Literal["human", "ai"]
ROOM_ID_RE = re.compile(r"^[A-Z0-9]{8}$")
PLAYER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,79}$")
PAIR_ACTIVE_ROOM_LIMIT = 3
GLOBAL_ACTIVE_ROOM_LIMIT = 500
STALE_ROOM_DAYS = 7
TERMINAL_RETENTION_DAYS = 7
# Temporarily keep every terminal room until the 0.9.0 retention rollout is announced.
TERMINAL_AUTO_DELETE_ENABLED = False
MAX_MESSAGE_LENGTH = 500
AI_ROOM_LIST_DEFAULT_LIMIT = 50
AI_ROOM_LIST_MAX_LIMIT = 100
INVITATION_EXPIRY_HOURS = 24
ABSOLUTE_MAX_PLAYERS = 6
PARTICIPANT_KINDS = {"human", "bound_machine", "system_npc"}
MAX_NPCS_PER_ROOM = 4
GLOBAL_ROOM_CHAT_RULE = (
    "【聊天说明】\n"
    "小机聊天时不得主动逐项泄露自己的真实未公开手牌、骰子、暗子等私密状态；"
    "公开以系统结果为准，正常诈唬不受限。"
)


class DuelError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _player_id(value: str) -> str:
    if not PLAYER_ID_RE.fullmatch(value):
        raise DuelError("player_id 只能包含字母、数字、冒号、下划线和连字符，最长 80 位")
    return value


def _room_id(value: str | None) -> str:
    normalized = (value or "").strip().upper()
    if not ROOM_ID_RE.fullmatch(normalized):
        raise DuelError("room_id 必须是 8 位大写字母或数字")
    return normalized


def _new_room_id(conn) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(20):
        candidate = "".join(secrets.choice(alphabet) for _ in range(8))
        if conn.execute(
            """
            SELECT 1 FROM rooms WHERE room_id = ?
            UNION ALL
            SELECT 1 FROM achievement_matches WHERE room_id = ?
            UNION ALL
            SELECT 1 FROM achievement_room_openings WHERE room_id = ?
            LIMIT 1
            """,
            (candidate, candidate, candidate),
        ).fetchone() is None:
            return candidate
    raise DuelError("暂时无法生成房间号，请重试", 503)


def _room_rules_text(plugin_rules: str) -> str:
    rules = plugin_rules.rstrip()
    if GLOBAL_ROOM_CHAT_RULE in rules:
        return rules
    separator = "\n\n" if rules else ""
    return f"{rules}{separator}{GLOBAL_ROOM_CHAT_RULE}"


def _decorate(room: dict) -> dict:
    game = get_game(room["game_type"])
    allowed_counts = game.resolved_allowed_player_counts()
    result = dict(room)
    result["rules_text"] = _room_rules_text(game.rules_text)
    result["move_format"] = game.move_format
    result["game_name"] = game.display_name
    result["min_players"] = allowed_counts[0]
    result["max_players"] = allowed_counts[-1]
    result["allowed_player_counts"] = list(allowed_counts)
    result["participants"] = sorted(
        result.get("participants", []), key=lambda item: item["seat_index"]
    )
    result["turn_order"] = [
        participant["player_id"] for participant in result["participants"]
    ]
    result["participant_count"] = len(result["participants"])
    result["active_player_ids"] = [
        participant["player_id"]
        for participant in result["participants"]
        if participant.get("join_status") == "joined"
        and participant.get("active", True)
        and participant.get("activity_state", "active") == "active"
    ]
    result["active_participant_count"] = len(result["active_player_ids"])
    current = _participant_by_id(result, result.get("current_player_id"))
    if current is not None:
        result["turn"] = current["role"]
        result["current_player"] = {
            key: current[key]
            for key in (
                "player_id", "display_name", "role", "participant_kind",
                "seat_index", "token",
            )
        }
        result["current_actor"] = {
            **result["current_player"],
            "seat": current["seat_index"],
        }
        result["current_actor_seat"] = current["seat_index"]
    else:
        result["current_player"] = None
        result["current_actor"] = None
        result["current_actor_seat"] = None
    result["action_note"] = room["board_state"].get("last_action_note", "")
    result["stake_label"] = (
        f"🪙{result.get('stake', 0)}/人"
        if result.get("stake", 0) > 0
        else "娱乐局"
    )
    settlement_deltas = (result.get("result") or {}).get("settlement_deltas", {})
    avatar_urls: dict[str, str | None] = {}
    if any(
        item.get("participant_kind") == "system_npc"
        for item in result["participants"]
    ):
        try:
            avatar_urls = {
                persona.id: persona.public_identity()["avatar_url"]
                for persona in load_personas()
            }
        except PersonaConfigError:
            # Existing rooms remain readable if an administrator temporarily
            # removes or repairs the external persona/avatar inventory.
            avatar_urls = {}
    for participant in result["participants"]:
        is_npc = participant.get("participant_kind") == "system_npc"
        participant["controller"] = participant.get("participant_kind")
        participant["wallet_label"] = "???" if is_npc else None
        participant["avatar_url"] = (
            avatar_urls.get(participant.get("npc_persona_id")) if is_npc else None
        )
        participant["settlement_delta"] = settlement_deltas.get(
            participant["player_id"]
        )
    _add_retention_metadata(result)
    return result


def _participant_by_id(room: dict, player_id: str | None) -> dict | None:
    if player_id is None:
        return None
    return next(
        (
            participant
            for participant in room.get("participants", [])
            if participant["player_id"] == player_id
        ),
        None,
    )


def _participant_subject(participant: dict) -> tuple[Role, str] | None:
    kind = participant.get("participant_kind")
    if kind == "human":
        return "human", participant["player_id"]
    if kind == "bound_machine":
        return "ai", participant["player_id"]
    return None


def _notify_game_participants(
    conn,
    room: dict,
    *,
    event_type: str,
    summary: str,
    event_key: str,
    exclude_player_ids: set[str] | None = None,
    only_player_ids: set[str] | None = None,
    created_at: str | None = None,
) -> None:
    excluded = exclude_player_ids or set()
    for participant in room.get("participants", []):
        player_id = participant["player_id"]
        if player_id in excluded:
            continue
        if only_player_ids is not None and player_id not in only_player_ids:
            continue
        subject = _participant_subject(participant)
        if subject is None:
            continue
        create_notification(
            conn,
            subject[0],
            subject[1],
            "game",
            event_type,
            room["room_id"],
            summary,
            event_key=event_key,
            created_at=created_at,
        )


def _close_room_invitation_notifications(
    conn, room: dict, *, player_ids: set[str] | None = None, read_at: str | None = None
) -> None:
    for participant in room.get("participants", []):
        if player_ids is not None and participant["player_id"] not in player_ids:
            continue
        subject = _participant_subject(participant)
        if subject is None:
            continue
        mark_notifications_read(
            conn,
            subject[0],
            subject[1],
            "game",
            event_keys=[f"game:created:{room['room_id']}"],
            read_at=read_at,
        )


def project_room_for_viewer(room: dict, viewer_player_id: str) -> dict:
    """Return a canonical participant-specific room projection.

    Raw ``board_state`` remains the persistence source of truth and never leaves
    the service through full-state responses. Plugins split it into a shared
    public state and a per-viewer private state; public-board legacy games inherit
    an identity public projection and therefore keep their existing JSON shape.
    """
    viewer_player_id = _player_id(viewer_player_id)
    viewer = _participant_by_id(room, viewer_player_id)
    if viewer is None:
        raise DuelError("viewer 不是该房间参与者", 403)
    if viewer.get("join_status") == "left":
        raise DuelError("当前参与者已经离开房间", 403)
    game = get_game(room["game_type"])
    participants = deepcopy(room.get("participants", []))
    try:
        public_state = game.public_state(
            deepcopy(room["board_state"]), participants
        )
        private_state = game.private_state(
            deepcopy(room["board_state"]), deepcopy(viewer), participants
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DuelError(f"游戏插件状态投影无效：{exc}") from exc
    if not isinstance(public_state, dict) or not isinstance(private_state, dict):
        raise DuelError("游戏插件 public_state/private_state 必须返回对象")
    projected = deepcopy(room)
    # Unlocks can include several participants because evaluation is atomic.
    # Callers project only the authenticated viewer's compact list at top level.
    projected.pop("achievement_unlocks", None)
    projected_participants = deepcopy(participants)
    for participant in projected_participants:
        try:
            summary = game.participant_summary(
                deepcopy(public_state), deepcopy(participant), deepcopy(participants)
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DuelError(f"游戏插件 participant_summary 无效：{exc}") from exc
        if not isinstance(summary, dict) or len(summary) > 4:
            raise DuelError("游戏插件 participant_summary 必须是至多 4 项的对象")
        if any(
            not isinstance(value, (str, int, bool, type(None)))
            for value in summary.values()
        ):
            raise DuelError("游戏插件 participant_summary 只能包含简短标量")
        participant["game_metadata"] = summary
    projected["participants"] = projected_participants
    projected["board_state"] = public_state
    projected["private_state"] = private_state
    projected["viewer"] = {
        "player_id": viewer["player_id"],
        "role": viewer["role"],
        "participant_kind": viewer.get("participant_kind"),
        "seat": viewer["seat_index"],
    }
    projected["action_note"] = public_state.get("last_action_note", "")
    return projected


def _project_event_for_viewer(
    room: dict, event: dict, viewer_player_id: str
) -> dict | None:
    viewer = _participant_by_id(room, viewer_player_id)
    if viewer is None:
        raise DuelError("viewer 不是该房间参与者", 403)
    game = get_game(room["game_type"])
    try:
        projected = game.project_event(
            deepcopy(event), deepcopy(viewer), deepcopy(room["participants"])
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DuelError(f"游戏插件事件投影无效：{exc}") from exc
    if projected is not None and not isinstance(projected, dict):
        raise DuelError("游戏插件 project_event 必须返回对象或 None")
    return projected


def advance_turn(
    participants: list[dict],
    current_player_id: str,
    *,
    retain_turn: bool = False,
    skip_player_ids: set[str] | None = None,
    next_player_id: str | None = None,
) -> str:
    """Return the next active seat in stable seat order.

    Plugins can retain the current seat, nominate an explicit next seat, or mark
    players inactive/eliminated. Generic pass/skip logic is represented by the
    skip set and never depends on the human/AI role label.
    """
    ordered = sorted(participants, key=lambda item: item["seat_index"])
    skipped = set(skip_player_ids or ())
    eligible = [
        item for item in ordered
        if item.get("active", True)
        and item.get("activity_state", "active") == "active"
        and item.get("join_status", "joined") == "joined"
        and item["player_id"] not in skipped
    ]
    if not eligible:
        raise DuelError("没有可继续行动的参与者", 409)
    eligible_ids = {item["player_id"] for item in eligible}
    if next_player_id is not None:
        if next_player_id not in eligible_ids:
            raise DuelError("插件指定的下一行动者不在可行动参与者中")
        return next_player_id
    if retain_turn and current_player_id in eligible_ids:
        return current_player_id
    current_index = next(
        (
            index for index, item in enumerate(ordered)
            if item["player_id"] == current_player_id
        ),
        -1,
    )
    for offset in range(1, len(ordered) + 1):
        candidate = ordered[(current_index + offset) % len(ordered)]
        if candidate["player_id"] in eligible_ids:
            return candidate["player_id"]
    raise DuelError("没有可继续行动的参与者", 409)


def _add_retention_metadata(room: dict) -> None:
    room["preserved"] = bool(room.get("preserved", False))
    room["auto_delete_at"] = None
    terminal_at = room.get("terminal_at")
    if (
        room.get("status") not in {"finished", "archived"}
        or room["preserved"]
        or not terminal_at
    ):
        return
    try:
        terminal_time = datetime.fromisoformat(
            str(terminal_at).replace("Z", "+00:00")
        )
    except ValueError:
        return
    if terminal_time.tzinfo is None:
        terminal_time = terminal_time.replace(tzinfo=timezone.utc)
    room["auto_delete_at"] = (
        terminal_time + timedelta(days=TERMINAL_RETENTION_DAYS)
    ).isoformat(timespec="seconds")


def _message_text(value: str | None, *, required: bool = False) -> str:
    text = (value or "").strip()
    if required and not text:
        raise DuelError("留言内容不能为空")
    if len(text) > MAX_MESSAGE_LENGTH:
        raise DuelError(f"message 最长 {MAX_MESSAGE_LENGTH} 字")
    return text


def _stake(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DuelError("stake 必须是大于等于 0 的整数")
    return value


def _record_event(
    conn,
    room_id: str,
    sender: str,
    sender_player_id: str,
    revision: int,
    *,
    event_type: str,
    text: str = "",
    move_label: str | None = None,
    move_payload: dict | None = None,
    visible_to_player_ids: set[str] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO room_messages (
            room_id, sender, sender_player_id, text, revision_at_send,
            created_at, event_type, move_label, move_payload
            , visible_to_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            room_id,
            sender,
            sender_player_id,
            text,
            revision,
            _now(),
            event_type,
            move_label,
            (
                json.dumps(move_payload, ensure_ascii=False, separators=(",", ":"))
                if move_payload is not None
                else None
            ),
            (
                json.dumps(sorted(visible_to_player_ids), separators=(",", ":"))
                if visible_to_player_ids is not None
                else None
            ),
        ),
    )


def _event_sender(row, room: dict) -> dict:
    player_id = row["sender_player_id"]
    if row["sender"] == "system":
        return {
            "player_id": "system",
            "name": "双弈裁判",
            "role": "system",
        }
    participant = next(
        (
            item
            for item in room["participants"]
            if item["player_id"] == player_id
        ),
        None,
    )
    sender = {
        "player_id": player_id,
        "name": (
            participant.get("display_name")
            if participant and participant.get("display_name")
            else player_id
        ),
        "role": participant["role"] if participant else row["sender"],
        "seat": participant["seat_index"] if participant else None,
    }
    if participant and participant.get("participant_kind") == "system_npc":
        sender["participant_kind"] = "system_npc"
    return sender


def _timeline_entry(row, room: dict) -> dict:
    sender = _event_sender(row, room)
    sender_role = sender["role"]
    sender_name = sender["name"]
    if row["event_type"] == "move":
        display_text = f"{sender_name} 落 {row['move_label']}"
        if row["text"]:
            display_text += f"：{row['text']}"
    elif row["event_type"] == "resign":
        display_text = f"{sender_name} 认输"
        if row["text"]:
            display_text += f"：{row['text']}"
    elif row["event_type"] == "leave":
        display_text = f"{sender_name} 离开房间"
        if row["text"]:
            display_text += f"：{row['text']}"
    elif row["event_type"] == "result":
        display_text = row["text"]
    else:
        display_text = f"{sender_name}：{row['text']}"
    entry = {
        "id": row["id"],
        "sequence": row["id"],
        "sender": sender,
        "sender_player_id": row["sender_player_id"],
        "sender_role": sender_role,
        "sender_name": sender_name,
        "is_public": (
            "visible_to_json" not in row.keys()
            or row["visible_to_json"] is None
        ),
        "text": row["text"],
        "event_type": row["event_type"],
        "move_label": row["move_label"],
        "display_text": display_text,
        "revision_at_send": row["revision_at_send"],
        "created_at": row["created_at"],
    }
    move_payload = row["move_payload"] if "move_payload" in row.keys() else None
    if move_payload:
        entry["move"] = json.loads(move_payload)
    return entry


def list_timeline(
    room_id: str,
    limit: int = 200,
    viewer_player_id: str | None = None,
    *,
    public_only: bool = False,
) -> list[dict]:
    room_id = _room_id(room_id)
    conn = connect()
    try:
        room_row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if room_row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(room_row, conn)
        if viewer_player_id is not None:
            viewer_player_id = _player_id(viewer_player_id)
            _assert_participant(room, viewer_player_id)
        if public_only and viewer_player_id is not None:
            rows = conn.execute(
                """
                SELECT * FROM room_messages
                WHERE room_id = ? AND visible_to_json IS NULL
                ORDER BY id DESC
                """,
                (room_id,),
            )
            projected = []
            for row in rows:
                entry = _project_event_for_viewer(
                    room, _timeline_entry(row, room), viewer_player_id
                )
                if entry is not None:
                    projected.append(entry)
                    if len(projected) >= limit:
                        break
            return list(reversed(projected))
        visibility_clause = (
            "AND visible_to_json IS NULL"
            if public_only or viewer_player_id is None
            else "AND (visible_to_json IS NULL OR EXISTS ("
            "SELECT 1 FROM json_each(visible_to_json) WHERE json_each.value = ?))"
        )
        params: tuple = (
            (room_id, limit)
            if public_only or viewer_player_id is None
            else (room_id, _player_id(viewer_player_id), limit)
        )
        rows = conn.execute(
            f"""
            SELECT * FROM (
                SELECT * FROM room_messages
                WHERE room_id = ?
                  {visibility_clause}
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id
            """,
            params,
        ).fetchall()
        entries = [_timeline_entry(row, room) for row in rows]
        if viewer_player_id is None:
            return entries
        projected = [
            _project_event_for_viewer(room, entry, viewer_player_id)
            for entry in entries
        ]
        return [entry for entry in projected if entry is not None]
    finally:
        conn.close()


def list_human_rooms(
    human_player_id: str, ai_names: dict[str, str] | None = None
) -> list[dict]:
    """Return every room owned by a trusted human, active rooms first."""
    human_player_id = _player_id(human_player_id)
    ai_names = ai_names or {}
    with write_transaction() as conn:
        _maintain_rooms(conn)
        rows = conn.execute(
            """
            SELECT r.room_id, r.game_type, r.mode, r.turn, r.revision,
                   r.status, r.winner, r.created_at, r.updated_at,
                   r.last_move_at, r.preserved, r.terminal_at,
                   r.stake, r.initiator_player_id, r.current_player_id,
                   r.confirmation_expires_at,
                   (
                       SELECT GROUP_CONCAT(confirmation.player_id)
                       FROM room_confirmations AS confirmation
                       WHERE confirmation.room_id = r.room_id
                         AND confirmation.decision = 'pending'
                   ) AS pending_for_csv
            FROM rooms AS r
            JOIN room_participants AS human
              ON human.room_id = r.room_id
             AND human.role = 'human'
             AND human.participant_kind = 'human'
             AND human.player_id = ?
             AND human.join_status <> 'left'
            ORDER BY
                CASE r.status
                    WHEN 'playing' THEN 0
                    WHEN 'pending' THEN 1
                    WHEN 'waiting' THEN 2
                    WHEN 'finished' THEN 3
                    ELSE 4
                END,
                r.updated_at DESC,
                r.created_at DESC
            LIMIT 100
            """,
            (human_player_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            participant_rows = conn.execute(
                """
                SELECT player_id, display_name, role, participant_kind,
                       npc_persona_id, seat_index, active,
                       join_status, activity_state
                FROM room_participants
                WHERE room_id = ? ORDER BY seat_index
                """,
                (row["room_id"],),
            ).fetchall()
            participants = [dict(participant) for participant in participant_rows]
            for participant in participants:
                participant["active"] = bool(participant["active"])
                participant["seat"] = participant["seat_index"]
            item["participants"] = participants
            current = next(
                (
                    participant for participant in participants
                    if participant["player_id"] == row["current_player_id"]
                ),
                None,
            )
            item["current_actor"] = current
            ai_ids = [
                participant["player_id"] for participant in participants
                if participant.get("participant_kind") == "bound_machine"
            ]
            item["ai_player_ids"] = ai_ids
            item["ai_player_id"] = ai_ids[0] if ai_ids else None
            result.append(item)
    for item in result:
        ai_player_id = item.get("ai_player_id")
        base_ai_id = ai_player_id.split(":", 1)[0] if ai_player_id else None
        item["ai_name"] = (
            ai_names.get(ai_player_id or "")
            or ai_names.get(base_ai_id or "")
            or "你的小机"
        )
        ai_labels = []
        for participant_id in item["ai_player_ids"]:
            base_id = participant_id.split(":", 1)[0]
            ai_labels.append(
                ai_names.get(participant_id)
                or ai_names.get(base_id)
                or next(
                    (
                        participant["display_name"]
                        for participant in item["participants"]
                        if participant["player_id"] == participant_id
                    ),
                    "你的小机",
                )
            )
        item["participant_names"] = [
            participant["display_name"] for participant in item["participants"]
        ]
        item["ai_names"] = ai_labels
        item["game_name"] = get_game(item["game_type"]).display_name
        pending_csv = item.pop("pending_for_csv", None)
        item["pending_for"] = pending_csv.split(",") if pending_csv else []
        item["stake_label"] = (
            f"🪙{item['stake']}/人" if item["stake"] > 0 else "娱乐局"
        )
        _add_retention_metadata(item)
    return result


def list_ai_rooms(
    ai_player_id: str,
    *,
    include_terminal: bool = False,
    limit: int = AI_ROOM_LIST_DEFAULT_LIMIT,
    offset: int = 0,
) -> list[dict]:
    """Return compact summaries only for rooms containing this exact AI seat."""
    ai_player_id = _player_id(ai_player_id)
    if not isinstance(include_terminal, bool):
        raise DuelError("include_terminal 必须是布尔值")
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise DuelError("limit 必须是整数")
    if limit < 1 or limit > AI_ROOM_LIST_MAX_LIMIT:
        raise DuelError(f"limit 必须在 1-{AI_ROOM_LIST_MAX_LIMIT} 之间")
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise DuelError("offset 必须是整数")
    if offset < 0:
        raise DuelError("offset 不能小于 0")

    terminal_filter = (
        "" if include_terminal else "AND r.status IN ('pending', 'waiting', 'playing')"
    )
    with write_transaction() as conn:
        _maintain_rooms(conn)
        rows = conn.execute(
            f"""
            SELECT r.room_id, r.game_type, r.status, r.turn, r.stake,
                   r.revision, r.current_player_id,
                   r.initiator_player_id, r.confirmation_expires_at,
                   confirmation.decision AS confirmation_decision,
                   participant.seat_index AS own_seat,
                   (
                       SELECT COUNT(*) FROM room_participants AS member
                       WHERE member.room_id = r.room_id
                   ) AS participant_count,
                   (
                       SELECT COUNT(*) FROM room_participants AS member
                       WHERE member.room_id = r.room_id
                         AND member.participant_kind = 'system_npc'
                   ) AS npc_count,
                   (
                       SELECT actor.seat_index FROM room_participants AS actor
                       WHERE actor.room_id = r.room_id
                         AND actor.player_id = r.current_player_id
                   ) AS current_actor_seat,
                   r.created_at, r.updated_at
            FROM rooms AS r
            JOIN room_participants AS participant
              ON participant.room_id = r.room_id
             AND participant.role = 'ai'
             AND participant.participant_kind = 'bound_machine'
             AND participant.player_id = ?
             AND participant.join_status <> 'left'
            LEFT JOIN room_confirmations AS confirmation
              ON confirmation.room_id = r.room_id
             AND confirmation.player_id = participant.player_id
            WHERE 1 = 1
              {terminal_filter}
            ORDER BY
                CASE r.status
                    WHEN 'playing' THEN 0
                    WHEN 'pending' THEN 1
                    WHEN 'waiting' THEN 2
                    WHEN 'finished' THEN 3
                    ELSE 4
                END,
                r.updated_at DESC,
                r.created_at DESC,
                r.room_id DESC
            LIMIT ? OFFSET ?
            """,
            (ai_player_id, limit, offset),
        ).fetchall()
    result = []
    base_keys = (
        "room_id", "game_type", "status", "turn", "revision",
        "current_player_id", "current_actor_seat", "own_seat",
        "participant_count", "created_at", "updated_at"
    )
    for row in rows:
        item = {key: row[key] for key in base_keys}
        if row["npc_count"]:
            item["npc_count"] = row["npc_count"]
        if row["status"] == "pending":
            item.update(
                stake=row["stake"],
                stake_label=(
                    f"🪙{row['stake']}/人" if row["stake"] > 0 else "娱乐局"
                ),
                initiator_player_id=row["initiator_player_id"],
                confirmation_expires_at=row["confirmation_expires_at"],
                confirmation_decision=row["confirmation_decision"],
            )
        result.append(item)
    return result


def list_human_pending_invitations(human_player_id: str) -> list[dict]:
    """Return invitations still awaiting this trusted human."""
    human_player_id = _player_id(human_player_id)
    with write_transaction() as conn:
        _maintain_rooms(conn)
        rows = conn.execute(
            """
            SELECT r.room_id, r.game_type, r.stake, r.initiator_player_id,
                   r.confirmation_expires_at, r.created_at,
                   initiator.display_name AS initiator_name
            FROM rooms AS r
            JOIN room_confirmations AS confirmation
              ON confirmation.room_id = r.room_id
             AND confirmation.player_id = ?
             AND confirmation.decision = 'pending'
            LEFT JOIN room_participants AS initiator
              ON initiator.room_id = r.room_id
             AND initiator.player_id = r.initiator_player_id
            WHERE r.status = 'pending' AND r.confirmation_required = 1
            ORDER BY r.created_at DESC, r.room_id DESC
            """,
            (human_player_id,),
        ).fetchall()
    return [
        {
            **dict(row),
            "game_name": get_game(row["game_type"]).display_name,
            "initiator_name": row["initiator_name"] or row["initiator_player_id"],
            "stake_label": (
                f"🪙{row['stake']}/人" if row["stake"] > 0 else "娱乐局"
            ),
        }
        for row in rows
    ]


def post_message(
    room_id: str,
    role: Role,
    player_id: str,
    text: str,
    opponent_id: str | None = None,
    visible_to_player_ids: set[str] | None = None,
) -> dict:
    """Store speech without changing room revision.

    Visibility is immutable per event. ``None`` is room-public; a player-id set
    is the extension point for team/private plugin events.
    """
    room_id = _room_id(room_id)
    player_id = _player_id(player_id)
    text = _message_text(text, required=True)
    with write_transaction() as conn:
        _maintain_rooms(conn, room_id)
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(row, conn)
        _assert_player(room, role, player_id)
        _assert_opponent(room, role, opponent_id)
        if room["status"] not in {"waiting", "playing"}:
            raise DuelError("对局已经结束，不能继续留言", 409)
        participant = _participant_by_id(room, player_id)
        if participant is None or participant.get("join_status") != "joined":
            raise DuelError("当前参与者已经离开房间", 409)
        if visible_to_player_ids is not None:
            unknown = set(visible_to_player_ids) - {
                participant["player_id"] for participant in room["participants"]
            }
            if unknown:
                raise DuelError("消息可见参与者不属于该房间")
        _record_event(
            conn, room_id, role, player_id, room["revision"],
            event_type="message", text=text,
            visible_to_player_ids=visible_to_player_ids,
        )
    return _decorate(room)


def read_new_room_events(room_id: str, player_id: str) -> list[dict]:
    """Atomically consume other participants' events using one cursor per reader."""
    room_id = _room_id(room_id)
    player_id = _player_id(player_id)
    with write_transaction() as conn:
        _maintain_rooms(conn, room_id)
        room_row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if room_row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(room_row, conn)
        _assert_participant(room, player_id)
        cursor_row = conn.execute(
            """
            SELECT last_event_id
            FROM room_event_cursors
            WHERE room_id = ? AND player_id = ?
            """,
            (room_id, player_id),
        ).fetchone()
        last_event_id = cursor_row["last_event_id"] if cursor_row else 0
        rows = conn.execute(
            """
            SELECT id, sender, sender_player_id, text, revision_at_send,
                   created_at, event_type, move_label, move_payload,
                   visible_to_json
            FROM room_messages
            WHERE room_id = ? AND id > ? AND sender_player_id <> ?
              AND (
                  visible_to_json IS NULL
                  OR EXISTS (
                      SELECT 1 FROM json_each(visible_to_json)
                      WHERE json_each.value = ?
                  )
              )
            ORDER BY id
            """,
            (room_id, last_event_id, player_id, player_id),
        ).fetchall()
        newest_event_id = conn.execute(
            """
            SELECT COALESCE(MAX(id), 0)
            FROM room_messages WHERE room_id = ?
            """,
            (room_id,),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO room_event_cursors (
                room_id, player_id, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(room_id, player_id) DO UPDATE SET
                last_event_id = excluded.last_event_id,
                updated_at = excluded.updated_at
            """,
            (room_id, player_id, newest_event_id, _now()),
        )
        projected = [
            _project_event_for_viewer(
                room, _timeline_entry(row, room), player_id
            )
            for row in rows
        ]
        return [event for event in projected if event is not None]


def claim_mcp_bootstrap(room_id: str, player_id: str) -> bool:
    """Atomically reserve the one full MCP context for one room participant."""
    room_id = _room_id(room_id)
    player_id = _player_id(player_id)
    with write_transaction() as conn:
        room_row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if room_row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(room_row, conn)
        _assert_participant(room, player_id)
        changed = conn.execute(
            """
            UPDATE room_event_cursors
            SET mcp_bootstrapped = 1, updated_at = ?
            WHERE room_id = ? AND player_id = ? AND mcp_bootstrapped = 0
            """,
            (_now(), room_id, player_id),
        )
        return changed.rowcount == 1


def has_new_room_events(room_id: str, player_id: str) -> bool:
    """Check one participant's unread visible events without consuming them."""
    room_id = _room_id(room_id)
    player_id = _player_id(player_id)
    conn = connect()
    try:
        room_row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if room_row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(room_row, conn)
        _assert_participant(room, player_id)
        cursor = conn.execute(
            """
            SELECT last_event_id FROM room_event_cursors
            WHERE room_id = ? AND player_id = ?
            """,
            (room_id, player_id),
        ).fetchone()
        last_event_id = cursor["last_event_id"] if cursor else 0
        rows = conn.execute(
            """
            SELECT id, sender, sender_player_id, text, revision_at_send,
                   created_at, event_type, move_label, move_payload,
                   visible_to_json
            FROM room_messages
            WHERE room_id = ? AND id > ? AND sender_player_id <> ?
              AND (
                  visible_to_json IS NULL
                  OR EXISTS (
                      SELECT 1 FROM json_each(visible_to_json)
                      WHERE json_each.value = ?
                  )
              )
            ORDER BY id
            """,
            (room_id, last_event_id, player_id, player_id),
        ).fetchall()
        return any(
            _project_event_for_viewer(
                room, _timeline_entry(row, room), player_id
            ) is not None
            for row in rows
        )
    finally:
        conn.close()


def update_participant_display_names(
    human_player_id: str, participant_names: dict[str, str]
) -> None:
    """Refresh trusted display names for every room visible to one human."""
    human_player_id = _player_id(human_player_id)
    normalized = {
        _player_id(player_id): (name or player_id).strip()[:100]
        for player_id, name in participant_names.items()
    }
    with write_transaction() as conn:
        for player_id, display_name in normalized.items():
            conn.execute(
                """
                UPDATE room_participants
                SET display_name = ?
                WHERE player_id = ?
                  AND room_id IN (
                      SELECT human.room_id
                      FROM room_participants AS human
                      WHERE human.role = 'human'
                        AND human.player_id = ?
                  )
                """,
                (display_name or player_id, player_id, human_player_id),
            )


def _participant_display_name(room: dict, player_id: str) -> str:
    participant = next(
        (
            item
            for item in room["participants"]
            if item["player_id"] == player_id
        ),
        None,
    )
    return (
        participant.get("display_name")
        if participant and participant.get("display_name")
        else player_id
    )


def _record_result_event(
    conn, room: dict, *, resigned_player_id: str | None = None
) -> None:
    """Append exactly one room-level result event after terminal state is stored."""
    exists = conn.execute(
        """
        SELECT 1 FROM room_messages
        WHERE room_id = ? AND event_type = 'result' AND move_payload IS NULL
        """,
        (room["room_id"],),
    ).fetchone()
    if exists is not None:
        return
    if resigned_player_id is not None:
        result_text = (
            f"{_participant_display_name(room, resigned_player_id)} 认输"
        )
    elif room.get("winner") == "draw" or (room.get("result") or {}).get("draw"):
        result_text = "和棋"
    elif room.get("winner_player_id") or room.get("winner") in {"human", "ai"}:
        winner = _participant_by_id(room, room.get("winner_player_id"))
        if winner is None:
            winner = next(
                (
                    participant
                    for participant in room["participants"]
                    if participant["role"] == room["winner"]
                ),
                None,
            )
        winner_name = (
            winner.get("display_name")
            if winner and winner.get("display_name")
            else (winner["player_id"] if winner else room.get("winner"))
        )
        result_text = f"{winner_name} 获胜"
    elif (room.get("result") or {}).get("placements"):
        result_text = "对局结束，排名已生成"
    else:
        result_text = "对局结束"
    _record_event(
        conn,
        room["room_id"],
        "system",
        "system",
        room["revision"],
        event_type="result",
        text=result_text,
    )


def _settle_terminal_room(conn, room: dict) -> bool:
    # Local import avoids the chips -> framework DuelError import cycle.
    from .chips import settle_duel_room

    return settle_duel_room(conn, room)


def _attach_multiplayer_settlement(
    game,
    room: dict,
    state: dict,
    game_result: dict,
) -> dict:
    """Require and validate an opted-in plugin's exact multiplayer payout."""
    if room.get("stake", 0) <= 0 or len(room.get("participants", [])) <= 2:
        return game_result
    if not game.supports_multiplayer_stakes:
        raise DuelError("多人房间尚未定义筹码结算规则")
    result = dict(game_result)
    deltas = result.get("settlement_deltas")
    if deltas is None:
        try:
            deltas = game.settlement_deltas(
                deepcopy(state),
                deepcopy(result),
                deepcopy(room["participants"]),
                room["stake"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DuelError(f"游戏插件多人结算无效：{exc}") from exc
    if not isinstance(deltas, dict):
        raise DuelError("多人筹码终局必须由插件提供明确 settlement_deltas")
    participant_ids = {
        participant["player_id"] for participant in room["participants"]
    }
    if set(deltas) != participant_ids:
        raise DuelError("多人 settlement_deltas 必须完整覆盖房间每名参与者")
    if any(
        isinstance(delta, bool) or not isinstance(delta, int)
        for delta in deltas.values()
    ):
        raise DuelError("多人 settlement_deltas 的每项必须是整数")
    if sum(deltas.values()) != 0:
        raise DuelError("多人 settlement_deltas 总和必须为 0")
    result["settlement_deltas"] = dict(deltas)
    result["settlement_zero_sum"] = True
    return result


def _expire_pending_invitations(conn, room_id: str | None = None) -> int:
    params: list[str] = [_now()]
    room_clause = ""
    if room_id is not None:
        room_clause = " AND room_id = ?"
        params.append(room_id)
    rows = conn.execute(
        f"""
        SELECT * FROM rooms
        WHERE status = 'pending'
          AND confirmation_expires_at IS NOT NULL
          AND datetime(confirmation_expires_at) <= datetime(?)
          {room_clause}
        """,
        params,
    ).fetchall()
    timestamp = _now()
    for row in rows:
        room = decode_room(row, conn)
        pending_ids = {
            item["player_id"]
            for item in room.get("confirmations", [])
            if item["decision"] == "pending"
        }
        _close_room_invitation_notifications(
            conn, room, player_ids=pending_ids, read_at=timestamp
        )
        _notify_game_participants(
            conn,
            room,
            event_type="invitation_expired",
            summary=f"{get_game(room['game_type']).display_name}邀请已过期",
            event_key=f"game:invitation_expired:{room['room_id']}",
            only_player_ids={room["initiator_player_id"]},
            created_at=timestamp,
        )
        conn.execute("DELETE FROM rooms WHERE room_id = ?", (room["room_id"],))
    return len(rows)


def _archive_stale_rooms(conn, room_id: str | None = None) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=STALE_ROOM_DAYS)).isoformat(
        timespec="seconds"
    )
    params: list[str] = [cutoff]
    room_clause = ""
    if room_id is not None:
        room_clause = " AND room_id = ?"
        params.append(room_id)
    stale_rows = conn.execute(
        f"""
        SELECT room_id
        FROM rooms
        WHERE status IN ('waiting', 'playing')
          AND last_move_at < ?
          {room_clause}
        """,
        params,
    ).fetchall()
    timestamp = _now()
    for stale in stale_rows:
        conn.execute(
            """
            UPDATE rooms
            SET status = 'archived', winner = 'draw',
                winner_player_id = NULL, result_json = '{"draw":true}',
                terminal_reason = 'stale_archive',
                current_player_id = NULL,
                revision = revision + 1, updated_at = ?, terminal_at = ?
            WHERE room_id = ?
              AND status IN ('waiting', 'playing')
            """,
            (timestamp, timestamp, stale["room_id"]),
        )
        updated = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?",
            (stale["room_id"],),
        ).fetchone()
        terminal_room = decode_room(updated, conn)
        _record_result_event(conn, terminal_room)
        _settle_terminal_room(conn, terminal_room)
        from .achievements import record_terminal_room

        record_terminal_room(
            conn, terminal_room, "stale_archive", normal=False
        )
        _notify_game_participants(
            conn,
            terminal_room,
            event_type="finished",
            summary=f"{get_game(terminal_room['game_type']).display_name}房间已结束",
            event_key=f"game:finished:{terminal_room['room_id']}",
            created_at=timestamp,
        )
    return len(stale_rows)


def _delete_expired_terminal_rooms(conn, room_id: str | None = None) -> int:
    if not TERMINAL_AUTO_DELETE_ENABLED:
        return 0

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=TERMINAL_RETENTION_DAYS)
    ).isoformat(timespec="seconds")
    params: list[str] = [cutoff]
    room_clause = ""
    if room_id is not None:
        room_clause = " AND room_id = ?"
        params.append(room_id)
    cursor = conn.execute(
        f"""
        DELETE FROM rooms
        WHERE status IN ('finished', 'archived')
          AND preserved = 0
          AND terminal_at IS NOT NULL
          AND datetime(terminal_at) <= datetime(?)
          {room_clause}
        """,
        params,
    )
    return cursor.rowcount


def _maintain_rooms(conn, room_id: str | None = None) -> tuple[int, int]:
    """Archive stale active rooms and run gated terminal-room retention."""
    _expire_pending_invitations(conn, room_id)
    archived = _archive_stale_rooms(conn, room_id)
    deleted = _delete_expired_terminal_rooms(conn, room_id)
    return archived, deleted


def _check_global_capacity(conn) -> None:
    active_count = conn.execute(
        "SELECT COUNT(*) FROM rooms WHERE status IN ('pending', 'waiting', 'playing')"
    ).fetchone()[0]
    if active_count >= GLOBAL_ACTIVE_ROOM_LIMIT:
        raise DuelError(
            f"对弈厅当前已有 {GLOBAL_ACTIVE_ROOM_LIMIT} 个活跃房间，"
            "容量已满，请稍后再试。",
            503,
        )


def _check_pair_capacity(
    conn, human_player_id: str | None, ai_player_id: str | None
) -> None:
    if human_player_id is None or ai_player_id is None:
        return
    active_count = conn.execute(
        """
        SELECT COUNT(DISTINCT rooms.room_id)
        FROM rooms
        JOIN room_participants AS human
          ON human.room_id = rooms.room_id
         AND human.role = 'human'
         AND human.player_id = ?
         AND human.join_status <> 'left'
        JOIN room_participants AS ai
          ON ai.room_id = rooms.room_id
         AND ai.role = 'ai'
         AND ai.player_id = ?
         AND ai.join_status <> 'left'
        WHERE rooms.status IN ('pending', 'waiting', 'playing')
        """,
        (human_player_id, ai_player_id),
    ).fetchone()[0]
    if active_count >= PAIR_ACTIVE_ROOM_LIMIT:
        raise DuelError(
            f"这对人类与 AI 已有 {PAIR_ACTIVE_ROOM_LIMIT} 个活跃房间；"
            "请先完成或认输其中一局再开新房。",
            409,
        )


def _normalize_ordered_participants(
    *,
    role: Role,
    player_id: str,
    opponent_id: str | None,
    ordered_participants: list[dict] | None,
    participant_names: dict[str, str],
) -> list[dict]:
    if role not in {"human", "ai"}:
        raise DuelError("role 必须是 human 或 ai")
    if ordered_participants is None:
        human_player_id = player_id if role == "human" else opponent_id
        ai_player_id = player_id if role == "ai" else opponent_id
        source = [
            {"player_id": human_player_id, "role": "human"},
            {"player_id": ai_player_id, "role": "ai"},
        ]
    else:
        source = ordered_participants
    participants: list[dict] = []
    seen: set[str] = set()
    for seat_index, raw in enumerate(source):
        if not isinstance(raw, dict):
            raise DuelError("participants 每一项必须是参与者对象")
        raw_player_id = raw.get("player_id")
        if raw_player_id is None:
            continue
        participant_id = _player_id(str(raw_player_id))
        participant_role = raw.get("role")
        if participant_role not in {"human", "ai"}:
            raise DuelError("参与者 role 必须是 human 或 ai")
        participant_kind = raw.get("participant_kind") or (
            "human" if participant_role == "human" else "bound_machine"
        )
        if participant_kind not in PARTICIPANT_KINDS:
            raise DuelError("参与者 participant_kind 无效")
        if participant_kind == "human" and participant_role != "human":
            raise DuelError("human participant_kind 必须使用 human role")
        if participant_kind != "human" and participant_role != "ai":
            raise DuelError("小机和 NPC 必须使用 ai role 兼容标识")
        npc_persona_id = raw.get("npc_persona_id")
        if participant_kind == "system_npc":
            if not isinstance(npc_persona_id, str) or not npc_persona_id.strip():
                raise DuelError("system_npc 必须绑定 npc_persona_id")
            npc_persona_id = npc_persona_id.strip()
        elif npc_persona_id is not None:
            raise DuelError("只有 system_npc 可以绑定 npc_persona_id")
        if participant_id in seen:
            raise DuelError("同一 player_id 不能重复加入房间")
        seen.add(participant_id)
        display_name = str(
            raw.get("display_name")
            or participant_names.get(participant_id)
            or participant_id
        ).strip()[:100]
        participants.append(
            {
                "player_id": participant_id,
                "display_name": display_name or participant_id,
                "role": participant_role,
                "participant_kind": participant_kind,
                "npc_persona_id": npc_persona_id,
                "seat_index": len(participants),
                "active": bool(raw.get("active", True)),
            }
        )
    if player_id not in seen:
        raise DuelError("发起者必须包含在 participants 中")
    if not participants:
        raise DuelError("房间至少需要一名参与者")
    if len(participants) > ABSOLUTE_MAX_PLAYERS:
        raise DuelError(f"房间参与者不能超过 {ABSOLUTE_MAX_PLAYERS} 人")
    return participants


def _room_can_start(room: dict, game) -> bool:
    participants = room.get("participants", [])
    if not game.accepts_player_count(len(participants)):
        return False
    if not all(
        participant.get("active", True)
        and participant.get("activity_state", "active") == "active"
        and participant.get("join_status", "joined") == "joined"
        for participant in participants
    ):
        return False
    if room.get("confirmation_required", False):
        decisions = {
            item["player_id"]: item["decision"]
            for item in room.get("confirmations", [])
        }
        return all(
            decisions.get(participant["player_id"]) == "accepted"
            for participant in participants
        )
    return True


def create_room(
    game_type: str,
    mode: str,
    role: Role,
    player_id: str,
    opponent_id: str | None = None,
    participant_names: dict[str, str] | None = None,
    stake: int = 0,
    ordered_participants: list[dict] | None = None,
    require_confirmations: bool | None = None,
    enforce_trusted_pair: bool = False,
    first_player_id: str | None = None,
    rematch_of_room_id: str | None = None,
) -> dict:
    try:
        game = get_game(game_type)
    except ValueError as exc:
        raise DuelError(str(exc)) from exc
    if mode not in {"human_first", "ai_first"}:
        raise DuelError("mode 必须是 human_first 或 ai_first")
    player_id = _player_id(player_id)
    opponent_id = _player_id(opponent_id) if opponent_id is not None else None
    stake = _stake(stake)
    participant_names = participant_names or {}
    participants = _normalize_ordered_participants(
        role=role,
        player_id=player_id,
        opponent_id=opponent_id,
        ordered_participants=ordered_participants,
        participant_names=participant_names,
    )
    explicit_opener = None
    if first_player_id is not None:
        first_player_id = _player_id(first_player_id)
        explicit_opener = next(
            (
                item for item in participants
                if item["player_id"] == first_player_id
                and item.get("active", True)
            ),
            None,
        )
        if explicit_opener is None:
            raise DuelError("指定先手必须是本房间可行动参与者")
    allowed_counts = game.resolved_allowed_player_counts()
    if len(participants) > allowed_counts[-1]:
        raise DuelError(
            f"{game.display_name}最多允许 {allowed_counts[-1]} 名参与者"
        )
    if len(participants) >= allowed_counts[0] and not game.accepts_player_count(
        len(participants)
    ):
        raise DuelError(
            f"{game.display_name}只允许 {', '.join(map(str, allowed_counts))} 人桌"
        )
    npc_count = sum(
        item["participant_kind"] == "system_npc" for item in participants
    )
    if npc_count > MAX_NPCS_PER_ROOM:
        raise DuelError(f"每局最多补入 {MAX_NPCS_PER_ROOM} 名 NPC")
    if npc_count and not game.supports_npcs:
        raise DuelError(f"{game.display_name}未启用 NPC 补位")
    if enforce_trusted_pair:
        kinds = {item["participant_kind"] for item in participants}
        if "human" not in kinds or "bound_machine" not in kinds:
            raise DuelError("生产对局至少需要 1 个人类和 1 只真实绑定小机")
    if stake > 0:
        if not game.supports_stakes:
            raise DuelError(f"{game.display_name}尚未定义筹码结算规则")
        if len(participants) != 2 and not game.supports_multiplayer_stakes:
            raise DuelError("多人房间尚未定义筹码结算规则，只能创建娱乐局")
    confirmation_required = (
        stake > 0 if require_confirmations is None else bool(require_confirmations)
    )
    if stake > 0:
        confirmation_required = True
    for participant in participants:
        participant["join_status"] = (
            "invited"
            if confirmation_required
            and participant["player_id"] != player_id
            and participant["participant_kind"] != "system_npc"
            else "joined"
        )
        participant["activity_state"] = (
            "active" if participant.get("active", True) else "inactive"
        )
    tokens = game.tokens_for(participants)
    if len(tokens) != len(participants) or len(set(tokens)) != len(tokens):
        raise DuelError("游戏插件必须为每个座位分配唯一 token")
    for participant, token in zip(participants, tokens):
        participant["token"] = str(token)
    # The six legacy games use X for the mode-selected opening side.
    if allowed_counts == (2,) and len(participants) == 2:
        if explicit_opener is not None:
            opener = explicit_opener
        else:
            opening_role = "human" if mode == "human_first" else "ai"
            opener = next(
                (item for item in participants if item["role"] == opening_role),
                None,
            )
        if opener is not None and opener["token"] != "X":
            other = next(item for item in participants if item is not opener)
            opener["token"], other["token"] = other["token"], opener["token"]
    try:
        if first_player_id is None:
            first_player_id = game.first_player_id(participants, mode)
        state = game.initialize(participants)
    except (KeyError, TypeError, ValueError) as exc:
        raise DuelError(f"游戏插件初始化失败：{exc}") from exc
    state["marks_by_player"] = {
        item["player_id"]: item["token"] for item in participants
    }
    role_counts = {
        participant_role: sum(
            item["role"] == participant_role for item in participants
        )
        for participant_role in ("human", "ai")
    }
    if role_counts == {"human": 1, "ai": 1}:
        state["marks"] = {
            item["role"]: item["token"] for item in participants
        }
    first_participant = next(
        item for item in participants if item["player_id"] == first_player_id
    )
    first_turn = first_participant["role"]
    timestamp = _now()
    confirmation_expires_at = (
        (datetime.now(timezone.utc) + timedelta(hours=INVITATION_EXPIRY_HOURS))
        .isoformat(timespec="seconds")
        if confirmation_required
        else None
    )
    with write_transaction() as conn:
        _maintain_rooms(conn)
        _check_global_capacity(conn)
        humans = [
            item["player_id"] for item in participants
            if item["participant_kind"] == "human"
        ]
        ais = [
            item["player_id"] for item in participants
            if item["participant_kind"] == "bound_machine"
        ]
        for human_player_id in humans:
            for ai_player_id in ais:
                _check_pair_capacity(conn, human_player_id, ai_player_id)
        rematch_root_room_id = None
        if rematch_of_room_id is not None:
            rematch_of_room_id = _room_id(rematch_of_room_id)
            from .achievements import validate_rematch

            try:
                rematch_of_room_id, rematch_root_room_id = validate_rematch(
                    conn,
                    rematch_of_room_id,
                    (item["player_id"] for item in participants),
                    game_type,
                )
            except ValueError as exc:
                raise DuelError(str(exc), 409) from exc
        room_id = _new_room_id(conn)
        enough_players = game.accepts_player_count(len(participants))
        status = (
            "pending"
            if confirmation_required
            else "playing" if enough_players else "waiting"
        )
        conn.execute(
            """
            INSERT INTO rooms (
                room_id, game_type, mode, board_state, turn, current_player_id,
                revision, status, winner, winner_player_id, result_json,
                stake, initiator_player_id,
                confirmation_required, confirmation_expires_at,
                preserved, terminal_at,
                terminal_reason, rematch_of_room_id, rematch_root_room_id,
                created_at, updated_at, last_move_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, NULL, NULL, NULL, ?, ?, ?, ?, 0, NULL, NULL, ?, ?, ?, ?, ?)
            """,
            (
                room_id,
                game_type,
                mode,
                json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                first_turn,
                first_player_id,
                status,
                stake,
                player_id,
                int(confirmation_required),
                confirmation_expires_at,
                rematch_of_room_id,
                rematch_root_room_id,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        for participant in participants:
            participant_id = participant["player_id"]
            conn.execute(
                    """
                    INSERT INTO room_participants (
                        room_id, player_id, display_name, role,
                        participant_kind, npc_persona_id,
                        seat_index, token, join_status, activity_state,
                        active, joined_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        room_id,
                        participant_id,
                        participant["display_name"],
                        participant["role"],
                        participant["participant_kind"],
                        participant["npc_persona_id"],
                        participant["seat_index"],
                        participant["token"],
                        participant["join_status"],
                        participant["activity_state"],
                        int(participant["active"]),
                        timestamp,
                    ),
                )
            conn.execute(
                    """
                    INSERT INTO room_event_cursors (
                        room_id, player_id, last_event_id, updated_at
                    ) VALUES (?, ?, 0, ?)
                    """,
                    (room_id, participant_id, timestamp),
                )
            if confirmation_required:
                accepted = (
                    participant_id == player_id
                    or participant["participant_kind"] == "system_npc"
                )
                conn.execute(
                        """
                        INSERT INTO room_confirmations (
                            room_id, player_id, decision, decided_at, created_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            room_id,
                            participant_id,
                            "accepted" if accepted else "pending",
                            timestamp if accepted else None,
                            timestamp,
                        ),
                )
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        room = decode_room(row, conn)
        from .achievements import record_room_created

        room["achievement_unlocks"] = record_room_created(conn, room)
        game_name = game.display_name
        _notify_game_participants(
            conn,
            room,
            event_type="created",
            summary=(
                f"{game_name}邀请：需确认 {stake} 筹码"
                if confirmation_required
                else f"对方新建了{game_name}房间"
            ),
            event_key=f"game:created:{room_id}",
            exclude_player_ids={player_id},
            created_at=timestamp,
        )
    return _decorate(room)


def respond_to_invitation(
    room_id: str,
    role: Role,
    player_id: str,
    decision: Literal["accept", "reject"],
) -> dict:
    """Accept or reject only the authenticated participant's pending invite."""
    room_id = _room_id(room_id)
    player_id = _player_id(player_id)
    if decision not in {"accept", "reject"}:
        raise DuelError("decision 必须是 accept 或 reject")
    with write_transaction() as conn:
        _maintain_rooms(conn, room_id)
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if row is None:
            raise DuelError("邀请不存在或已过期取消", 404)
        room = decode_room(row, conn)
        _assert_player(room, role, player_id)
        confirmation = conn.execute(
            """
            SELECT decision FROM room_confirmations
            WHERE room_id = ? AND player_id = ?
            """,
            (room_id, player_id),
        ).fetchone()
        if confirmation is None:
            raise DuelError("当前参与者不在该局确认清单中", 403)
        if (
            decision == "accept"
            and confirmation["decision"] == "accepted"
            and room["status"] in {"pending", "waiting", "playing"}
        ):
            return _decorate(room)
        if room["status"] != "pending" or not room.get("confirmation_required"):
            raise DuelError("该房间没有待确认邀请", 409)
        if confirmation["decision"] == "accepted":
            raise DuelError("已同意的发起方不能再拒绝本次邀请", 409)
        if decision == "reject":
            cancelled = {
                "room_id": room_id,
                "status": "cancelled",
                "stake": room["stake"],
                "stake_label": room.get("stake_label") or (
                    f"🪙{room['stake']}/人" if room["stake"] > 0 else "娱乐局"
                ),
            }
            timestamp = _now()
            _close_room_invitation_notifications(
                conn, room, player_ids={player_id}, read_at=timestamp
            )
            _notify_game_participants(
                conn,
                room,
                event_type="invitation_rejected",
                summary=f"对方拒绝了 {room['stake']} 筹码邀请",
                event_key=f"game:invitation_rejected:{room_id}:{player_id}",
                exclude_player_ids={player_id},
                created_at=timestamp,
            )
            conn.execute("DELETE FROM rooms WHERE room_id = ?", (room_id,))
            return cancelled

        timestamp = _now()
        _close_room_invitation_notifications(
            conn, room, player_ids={player_id}, read_at=timestamp
        )
        conn.execute(
            """
            UPDATE room_confirmations
            SET decision = 'accepted', decided_at = ?
            WHERE room_id = ? AND player_id = ? AND decision = 'pending'
            """,
            (timestamp, room_id, player_id),
        )
        conn.execute(
            """
            UPDATE room_participants SET join_status = 'joined'
            WHERE room_id = ? AND player_id = ?
            """,
            (room_id, player_id),
        )
        pending_count = conn.execute(
            """
            SELECT COUNT(*) FROM room_confirmations
            WHERE room_id = ? AND decision = 'pending'
            """,
            (room_id,),
        ).fetchone()[0]
        refreshed_row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        refreshed = decode_room(refreshed_row, conn)
        game = get_game(refreshed["game_type"])
        new_status = (
            "pending" if pending_count > 0
            else "playing" if _room_can_start(refreshed, game)
            else "waiting"
        )
        conn.execute(
            """
            UPDATE rooms
            SET status = ?, revision = revision + 1,
                updated_at = ?,
                confirmation_expires_at = CASE
                    WHEN ? = 'pending' THEN confirmation_expires_at ELSE NULL
                END
            WHERE room_id = ? AND status = 'pending'
            """,
            (new_status, timestamp, new_status, room_id),
        )
        updated = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        result = decode_room(updated, conn)
        _notify_game_participants(
            conn,
            result,
            event_type="invitation_accepted",
            summary=f"对方已接受 {room['stake']} 筹码邀请",
            event_key=f"game:invitation_accepted:{room_id}:{player_id}",
            exclude_player_ids={player_id},
            created_at=timestamp,
        )
    return _decorate(result)


def join_room(
    room_id: str,
    role: Role,
    player_id: str,
    opponent_id: str | None = None,
    message: str | None = None,
    display_name: str | None = None,
) -> dict:
    room_id = _room_id(room_id)
    player_id = _player_id(player_id)
    opponent_id = _player_id(opponent_id) if opponent_id is not None else None
    message = _message_text(message)
    with write_transaction() as conn:
        _maintain_rooms(conn, room_id)
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(row, conn)
        _assert_opponent(room, role, opponent_id)
        existing = _participant_by_id(room, player_id)
        if existing is not None:
            if existing["role"] != role:
                raise DuelError("player_id 已用另一身份类型加入该房间", 409)
            if existing.get("join_status") == "left":
                raise DuelError("当前参与者已经离开该局，不能重新加入", 409)
            if message:
                _record_event(
                    conn, room_id, role, player_id, row["revision"],
                    event_type="message", text=message,
                )
            return _decorate(room)
        if row["status"] not in {"waiting", "pending"}:
            raise DuelError("房间已经开始或结束，不能加入", 409)
        game = get_game(room["game_type"])
        allowed_counts = game.resolved_allowed_player_counts()
        capacity = allowed_counts[-1]
        if len(room["participants"]) >= capacity:
            raise DuelError(f"房间已满，最多允许 {capacity} 名参与者", 409)
        humans = [
            item["player_id"] for item in room["participants"]
            if item.get("participant_kind") == "human"
        ]
        ais = [
            item["player_id"] for item in room["participants"]
            if item.get("participant_kind") == "bound_machine"
        ]
        if role == "human":
            humans.append(player_id)
        else:
            ais.append(player_id)
        for human_player_id in humans:
            for ai_player_id in ais:
                _check_pair_capacity(conn, human_player_id, ai_player_id)
        occupied_seats = {item["seat_index"] for item in room["participants"]}
        seat_index = next(
            seat for seat in range(capacity) if seat not in occupied_seats
        )
        prospective = sorted([
            *room["participants"],
            {
                "player_id": player_id,
                "display_name": display_name or player_id,
                "role": role,
                "participant_kind": "human" if role == "human" else "bound_machine",
                "npc_persona_id": None,
                "seat_index": seat_index,
                "active": True,
            },
        ], key=lambda item: item["seat_index"])
        tokens = game.tokens_for(prospective)
        if len(tokens) != len(prospective) or len(set(tokens)) != len(tokens):
            raise DuelError("游戏插件必须为每个座位分配唯一 token")
        token_by_player = {
            participant["player_id"]: str(prospective_token)
            for participant, prospective_token in zip(prospective, tokens)
        }
        if allowed_counts == (2,) and len(prospective) == 2:
            opening_role = "human" if room["mode"] == "human_first" else "ai"
            opener = next(
                item for item in prospective if item["role"] == opening_role
            )
            if token_by_player[opener["player_id"]] != "X":
                other = next(item for item in prospective if item is not opener)
                opener_token = token_by_player[opener["player_id"]]
                token_by_player[opener["player_id"]] = token_by_player[
                    other["player_id"]
                ]
                token_by_player[other["player_id"]] = opener_token
        for participant in prospective:
            participant["token"] = token_by_player[participant["player_id"]]
            participant.setdefault("join_status", "joined")
            participant.setdefault("activity_state", "active")
        token = token_by_player[player_id]
        conn.executemany(
            """
            UPDATE room_participants SET token = ?
            WHERE room_id = ? AND player_id = ?
            """,
            [
                (token_by_player[item["player_id"]], room_id, item["player_id"])
                for item in room["participants"]
            ],
        )
        conn.execute(
            """
            INSERT INTO room_participants (
                room_id, player_id, display_name, role,
                participant_kind, npc_persona_id,
                seat_index, token, join_status, activity_state,
                active, joined_at
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, 'joined', 'active', 1, ?)
            """,
            (
                room_id, player_id, (display_name or player_id)[:100], role,
                "human" if role == "human" else "bound_machine",
                seat_index, token, _now(),
            ),
        )
        latest_event_id = conn.execute(
            """
            SELECT COALESCE(MAX(id), 0)
            FROM room_messages WHERE room_id = ?
            """,
            (room_id,),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO room_event_cursors (
                room_id, player_id, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?)
            """,
            (room_id, player_id, latest_event_id, _now()),
        )
        try:
            first_player_id = game.first_player_id(prospective, room["mode"])
            state = game.initialize(prospective)
        except (KeyError, TypeError, ValueError) as exc:
            raise DuelError(f"游戏插件初始化失败：{exc}") from exc
        state["marks_by_player"] = token_by_player
        role_counts = {
            participant_role: sum(
                item["role"] == participant_role for item in prospective
            )
            for participant_role in ("human", "ai")
        }
        if role_counts == {"human": 1, "ai": 1}:
            state["marks"] = {
                item["role"]: item["token"] for item in prospective
            }
        first_participant = next(
            item for item in prospective
            if item["player_id"] == first_player_id
        )
        if row["confirmation_required"]:
            conn.execute(
                """
                INSERT INTO room_confirmations (
                    room_id, player_id, decision, decided_at, created_at
                ) VALUES (?, ?, 'accepted', ?, ?)
                """,
                (room_id, player_id, _now(), _now()),
            )
        room = decode_room(row, conn)
        room["board_state"] = state
        confirmation_rows = conn.execute(
            """
            SELECT player_id, decision, decided_at, created_at
            FROM room_confirmations WHERE room_id = ?
            """,
            (room_id,),
        ).fetchall()
        room["confirmations"] = [dict(item) for item in confirmation_rows]
        pending_count = sum(
            item["decision"] == "pending" for item in room["confirmations"]
        )
        prospective_count = len(prospective)
        if (
            prospective_count not in allowed_counts
            and not any(count > prospective_count for count in allowed_counts)
        ):
            raise DuelError(
                f"{game.display_name}只允许 {', '.join(map(str, allowed_counts))} 人桌"
            )
        new_status = (
            "pending" if row["confirmation_required"] and pending_count > 0
            else "playing" if _room_can_start(room, game)
            else "waiting"
        )
        timestamp = _now()
        conn.execute(
            """
            UPDATE rooms
            SET board_state = ?, status = ?, turn = ?, current_player_id = ?,
                revision = revision + 1,
                updated_at = ?,
                confirmation_expires_at = CASE
                    WHEN ? = 'pending' THEN confirmation_expires_at ELSE NULL
                END
            WHERE room_id = ?
            """,
            (
                json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                new_status, first_participant["role"], first_player_id,
                timestamp, new_status, room_id,
            ),
        )
        updated = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if message:
            _record_event(
                conn, room_id, role, player_id, updated["revision"],
                event_type="message", text=message,
            )
        result = decode_room(updated, conn)
    return _decorate(result)


def get_room(
    room_id: str,
    role: Role | None = None,
    player_id: str | None = None,
    opponent_id: str | None = None,
) -> dict:
    room_id = _room_id(room_id)
    room = None
    with write_transaction() as conn:
        _maintain_rooms(conn, room_id)
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if row is not None:
            room = decode_room(row, conn)
    if room is None:
        raise DuelError("房间不存在", 404)
    if role is not None:
        _assert_player(room, role, _player_id(player_id or ""))
        _assert_opponent(room, role, opponent_id)
    return _decorate(room)


def _assert_player(room: dict, role: Role, player_id: str) -> None:
    role_participants = [
        participant["player_id"]
        for participant in room["participants"]
        if participant["role"] == role
    ]
    if not role_participants:
        raise DuelError(f"{role} 席位尚未加入", 409)
    if player_id not in role_participants:
        raise DuelError("player_id 与该房间席位不匹配", 403)


def _assert_participant(room: dict, player_id: str) -> None:
    if player_id not in {
        participant["player_id"] for participant in room["participants"]
    }:
        raise DuelError("player_id 不是该房间参与者", 403)


def _assert_opponent(room: dict, role: Role, opponent_id: str | None) -> None:
    if opponent_id is None:
        return
    opponent_id = _player_id(opponent_id)
    other: Role = "ai" if role == "human" else "human"
    other_players = {
        participant["player_id"]
        for participant in room["participants"]
        if participant["role"] == other
    }
    if other_players and opponent_id not in other_players:
        raise DuelError("房间不属于当前绑定的人机对", 403)


def set_room_preserved(
    room_id: str, human_player_id: str, preserved: bool
) -> dict:
    room_id = _room_id(room_id)
    human_player_id = _player_id(human_player_id)
    result = None
    with write_transaction() as conn:
        _maintain_rooms(conn, room_id)
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if row is not None:
            room = decode_room(row, conn)
            _assert_player(room, "human", human_player_id)
            if room["status"] not in {"finished", "archived"}:
                raise DuelError("只有已结束或已归档的对局可以设置保留", 409)
            conn.execute(
                "UPDATE rooms SET preserved = ? WHERE room_id = ?",
                (int(preserved), room_id),
            )
            updated = conn.execute(
                "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
            ).fetchone()
            result = decode_room(updated, conn)
            if preserved:
                from .achievements import record_preserved_loss

                result["achievement_unlocks"] = record_preserved_loss(
                    conn, human_player_id, room_id
                )
    if result is None:
        raise DuelError("房间不存在", 404)
    return _decorate(result)


def delete_terminal_room(room_id: str, human_player_id: str) -> str:
    room_id = _room_id(room_id)
    human_player_id = _player_id(human_player_id)
    found = False
    with write_transaction() as conn:
        _maintain_rooms(conn, room_id)
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if row is not None:
            found = True
            room = decode_room(row, conn)
            _assert_player(room, "human", human_player_id)
            if room["status"] not in {"finished", "archived"}:
                raise DuelError("进行中或等待中的对局不能直接删除", 409)
            conn.execute("DELETE FROM rooms WHERE room_id = ?", (room_id,))
    if not found:
        raise DuelError("房间不存在", 404)
    return room_id


def _assert_expected_revision(room: dict, expected_revision: int | None) -> None:
    if expected_revision is None:
        return
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        raise DuelError("revision 必须是非负整数")
    if room["revision"] != expected_revision:
        raise DuelError(
            f"局面 revision 已变化（期望 {expected_revision}，当前 {room['revision']}），请刷新后重试",
            409,
        )


def acknowledge_liars_dice_round(
    room_id: str,
    human_player_id: str,
    expected_revision: int | None,
) -> dict:
    room_id = _room_id(room_id)
    human_player_id = _player_id(human_player_id)
    if expected_revision is None:
        raise DuelError("确认下一轮必须携带 revision")
    with write_transaction() as conn:
        _maintain_rooms(conn, room_id)
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(row, conn)
        _assert_expected_revision(room, expected_revision)
        _assert_player(room, "human", human_player_id)
        if room["status"] != "playing":
            raise DuelError("当前房间不在对局中", 409)
        if room["game_type"] != "liars_dice":
            raise DuelError("只有吹牛骰子可以确认下一轮", 409)
        game = get_game(room["game_type"])
        try:
            applied = game.acknowledge_round(room["board_state"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DuelError(f"无法开始下一轮：{exc}", 409) from exc
        if not isinstance(applied, MoveResult) or not applied.next_player_id:
            raise DuelError("吹牛骰子下一轮推进结果无效")
        next_player_id = advance_turn(
            room["participants"],
            human_player_id,
            next_player_id=applied.next_player_id,
        )
        next_participant = _participant_by_id(room, next_player_id)
        if next_participant is None:
            raise DuelError("下一行动者不属于房间")
        state = applied.state
        state["last_action_note"] = applied.note
        timestamp = _now()
        conn.execute(
            """
            UPDATE rooms
            SET board_state = ?, turn = ?, current_player_id = ?,
                revision = revision + 1, updated_at = ?, last_move_at = ?
            WHERE room_id = ?
            """,
            (
                json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                next_participant["role"],
                next_player_id,
                timestamp,
                timestamp,
                room_id,
            ),
        )
        updated = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        result = decode_room(updated, conn)
    return _decorate(result)


def play_move(
    room_id: str,
    role: Role,
    player_id: str,
    move: dict,
    opponent_id: str | None = None,
    message: str | None = None,
    expected_revision: int | None = None,
) -> dict:
    room_id = _room_id(room_id)
    player_id = _player_id(player_id)
    message = _message_text(message)
    with write_transaction() as conn:
        _maintain_rooms(conn, room_id)
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(row, conn)
        _assert_expected_revision(room, expected_revision)
        _assert_player(room, role, player_id)
        _assert_opponent(room, role, opponent_id)
        if room["status"] != "playing":
            raise DuelError("当前房间不在对局中", 409)
        if room.get("current_player_id") != player_id:
            raise DuelError("还没轮到你落子", 409)
        game = get_game(room["game_type"])
        actor = _participant_by_id(room, player_id)
        if actor is None or not actor.get("active", True):
            raise DuelError("当前参与者已不可行动", 409)
        try:
            move_label = game.format_action(room["board_state"], move, actor)
            game.validate_action(room["board_state"], move, actor)
            applied = game.apply_action(room["board_state"], move, actor)
            applied = game.progress_after_action(
                room["board_state"], move, actor, room["participants"], applied
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DuelError(f"无效落子：{exc}") from exc
        if not isinstance(applied, (dict, MoveResult)):
            raise DuelError("游戏插件推进钩子必须返回 state 对象或 MoveResult")
        if isinstance(applied, MoveResult):
            state = applied.state
            retain_turn = applied.retain_turn
            pause_turn = applied.pause_turn
            action_note = applied.note
            inactive_player_ids = set(applied.inactive_player_ids)
            skipped_player_ids = set(applied.skipped_player_ids)
            participant_activity = dict(applied.participant_activity)
            explicit_next_player_id = applied.next_player_id
            game_result = applied.result
            if applied.settlement_deltas is not None:
                game_result = dict(game_result or {})
                game_result["settlement_deltas"] = applied.settlement_deltas
            event_visible_to_player_ids = (
                set(applied.event_visible_to_player_ids)
                if applied.event_visible_to_player_ids is not None
                else None
            )
            public_event = deepcopy(applied.public_event)
        else:
            state = applied
            retain_turn = False
            pause_turn = False
            action_note = ""
            inactive_player_ids = set()
            skipped_player_ids = set()
            participant_activity = {}
            explicit_next_player_id = None
            game_result = None
            event_visible_to_player_ids = None
            public_event = None
        if not isinstance(state, dict):
            raise DuelError("游戏插件返回的 state 必须是对象")
        state["last_action_note"] = action_note
        participant_ids = {
            item["player_id"] for item in room["participants"]
        }
        unknown_inactive = inactive_player_ids - participant_ids
        if unknown_inactive:
            raise DuelError("插件尝试淘汰不属于房间的参与者")
        unknown_skipped = skipped_player_ids - participant_ids
        if unknown_skipped:
            raise DuelError("插件尝试跳过不属于房间的参与者")
        unknown_activity = set(participant_activity) - participant_ids
        if unknown_activity:
            raise DuelError("插件尝试更新不属于房间的参与者状态")
        if (
            event_visible_to_player_ids is not None
            and event_visible_to_player_ids - participant_ids
        ):
            raise DuelError("插件事件可见范围包含不属于房间的参与者")
        if public_event is not None and (
            not isinstance(public_event, dict)
            or not public_event
            or set(public_event) & {
                "name", "message", "move", "sequence", "revision",
                "revision_at_send", "event_type", "actor", "actor_id",
                "actor_seat", "actor_kind", "seat", "kind", "sender",
                "sender_player_id",
            }
        ):
            raise DuelError("插件公开增量事件必须是非空对象且不能覆盖保留字段")
        allowed_activity = {"active", "inactive", "eliminated", "skipped"}
        if set(participant_activity.values()) - allowed_activity:
            raise DuelError("插件返回了无效的参与者活动状态")
        participant_activity.update(
            {inactive_id: "inactive" for inactive_id in inactive_player_ids}
        )
        if participant_activity:
            conn.executemany(
                """
                UPDATE room_participants
                SET activity_state = ?, active = ?
                WHERE room_id = ? AND player_id = ?
                """,
                [
                    (
                        activity_state,
                        int(activity_state == "active"),
                        room_id,
                        participant_id,
                    )
                    for participant_id, activity_state
                    in participant_activity.items()
                ],
            )
            for participant in room["participants"]:
                activity_state = participant_activity.get(participant["player_id"])
                if activity_state is not None:
                    participant["activity_state"] = activity_state
                    participant["active"] = activity_state == "active"
        state["last_skipped_player_ids"] = sorted(skipped_player_ids)
        if game_result is None:
            try:
                game_result = game.result_for(state, room["participants"])
            except (KeyError, TypeError, ValueError) as exc:
                raise DuelError(f"游戏插件终局结果无效：{exc}") from exc
        status = "finished" if game_result is not None else "playing"
        if status == "finished":
            game_result = _attach_multiplayer_settlement(
                game, room, state, game_result
            )
        winner_player_id = (
            game_result.get("winner_player_id") if game_result else None
        )
        if winner_player_id is not None:
            winner_participant = _participant_by_id(room, winner_player_id)
            if winner_participant is None:
                raise DuelError("插件返回的赢家不属于房间")
            winner = winner_participant["role"]
        elif game_result and game_result.get("draw"):
            winner = "draw"
        else:
            winner = None
        next_player_id = None
        next_turn: Role = role
        if status == "playing":
            if pause_turn and (retain_turn or explicit_next_player_id is not None):
                raise DuelError("暂停行动权时不能同时指定下一行动者")
            if not pause_turn:
                next_player_id = advance_turn(
                    room["participants"],
                    player_id,
                    retain_turn=retain_turn,
                    skip_player_ids=skipped_player_ids,
                    next_player_id=explicit_next_player_id,
                )
                next_participant = _participant_by_id(room, next_player_id)
                if next_participant is None:
                    raise DuelError("下一行动者不属于房间")
                next_turn = next_participant["role"]
        timestamp = _now()
        conn.execute(
            """
            UPDATE rooms
            SET board_state = ?, turn = ?, current_player_id = ?,
                revision = revision + 1, status = ?, winner = ?,
                winner_player_id = ?, result_json = ?,
                updated_at = ?, last_move_at = ?,
                terminal_at = CASE WHEN ? = 'finished' THEN ? ELSE terminal_at END,
                terminal_reason = CASE WHEN ? = 'finished' THEN 'game_result' ELSE terminal_reason END
            WHERE room_id = ?
            """,
            (
                json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                next_turn,
                next_player_id,
                status,
                winner,
                winner_player_id,
                (
                    json.dumps(game_result, ensure_ascii=False, separators=(",", ":"))
                    if game_result is not None else None
                ),
                timestamp,
                timestamp,
                status,
                timestamp,
                status,
                room_id,
            ),
        )
        updated = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        _record_event(
            conn,
            room_id,
            role,
            player_id,
            updated["revision"],
            event_type="move",
            text=message,
            move_label=move_label,
            move_payload=move,
            visible_to_player_ids=event_visible_to_player_ids,
        )
        if public_event is not None:
            _record_event(
                conn,
                room_id,
                "system",
                "system",
                updated["revision"],
                event_type="result",
                text=action_note,
                move_payload=public_event,
            )
        result = decode_room(updated, conn)
        achievement_unlocks: list[dict] = []
        if result["status"] == "finished":
            _record_result_event(conn, result)
            _settle_terminal_room(conn, result)
            from .achievements import record_terminal_room

            achievement_unlocks.extend(
                record_terminal_room(conn, result, "game_result", normal=True)
            )
        from .achievements import record_special_move

        achievement_unlocks.extend(
            record_special_move(conn, result, actor, move)
        )
        result["achievement_unlocks"] = achievement_unlocks
        if result["status"] == "finished":
            _notify_game_participants(
                conn,
                result,
                event_type="finished",
                summary=f"{game.display_name}对局已结束",
                event_key=f"game:finished:{room_id}",
                exclude_player_ids={player_id},
                created_at=timestamp,
            )
    return _decorate(result)


def leave_room(
    room_id: str,
    role: Role,
    player_id: str,
    opponent_id: str | None = None,
    message: str | None = None,
) -> dict:
    """Atomically leave an invitation, lobby, or active multiplayer room."""
    room_id = _room_id(room_id)
    player_id = _player_id(player_id)
    message = _message_text(message)
    with write_transaction() as conn:
        _maintain_rooms(conn, room_id)
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(row, conn)
        participant = _participant_by_id(room, player_id)
        if participant is None or participant["role"] != role:
            raise DuelError("player_id 与该房间席位不匹配", 403)
        _assert_opponent(room, role, opponent_id)
        if participant.get("join_status") == "left":
            return _decorate(room)
        if room["status"] == "pending":
            cancelled = {
                "room_id": room_id,
                "status": "cancelled",
                "stake": room.get("stake", 0),
            }
            timestamp = _now()
            _close_room_invitation_notifications(conn, room, read_at=timestamp)
            _notify_game_participants(
                conn,
                room,
                event_type="left",
                summary=f"对方离开了{get_game(room['game_type']).display_name}房间",
                event_key=f"game:left:{room_id}:{player_id}",
                exclude_player_ids={player_id},
                created_at=timestamp,
            )
            conn.execute("DELETE FROM rooms WHERE room_id = ?", (room_id,))
            return cancelled
        if room["status"] not in {"waiting", "playing"}:
            raise DuelError("已经结束的房间不能离开", 409)

        game = get_game(room["game_type"])
        timestamp = _now()
        if room["status"] == "waiting":
            conn.execute(
                "DELETE FROM room_participants WHERE room_id = ? AND player_id = ?",
                (room_id, player_id),
            )
            remaining = [
                item for item in room["participants"]
                if item["player_id"] != player_id
            ]
            if not remaining:
                conn.execute("DELETE FROM rooms WHERE room_id = ?", (room_id,))
                return {"room_id": room_id, "status": "cancelled", "stake": 0}
            try:
                first_player_id = game.first_player_id(remaining, room["mode"])
                state = game.initialize(remaining)
            except (KeyError, TypeError, ValueError) as exc:
                raise DuelError(f"游戏插件初始化失败：{exc}") from exc
            state["marks_by_player"] = {
                item["player_id"]: item["token"] for item in remaining
            }
            role_counts = {
                participant_role: sum(
                    item["role"] == participant_role for item in remaining
                )
                for participant_role in ("human", "ai")
            }
            if role_counts == {"human": 1, "ai": 1}:
                state["marks"] = {
                    item["role"]: item["token"] for item in remaining
                }
            first = next(
                item for item in remaining
                if item["player_id"] == first_player_id
            )
            conn.execute(
                """
                UPDATE rooms
                SET board_state = ?, turn = ?, current_player_id = ?,
                    initiator_player_id = CASE
                        WHEN initiator_player_id = ? THEN ? ELSE initiator_player_id
                    END,
                    revision = revision + 1, updated_at = ?
                WHERE room_id = ? AND status = 'waiting'
                """,
                (
                    json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                    first["role"], first_player_id, player_id,
                    remaining[0]["player_id"], timestamp, room_id,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
            ).fetchone()
            _record_event(
                conn, room_id, role, player_id, updated["revision"],
                event_type="leave", text=message,
            )
            result = decode_room(updated, conn)
            _notify_game_participants(
                conn,
                result,
                event_type="left",
                summary=f"对方离开了{game.display_name}房间",
                event_key=f"game:left:{room_id}:{player_id}",
                exclude_player_ids={player_id},
                created_at=timestamp,
            )
            return _decorate(result)

        conn.execute(
            """
            UPDATE room_participants
            SET join_status = 'left', activity_state = 'inactive', active = 0
            WHERE room_id = ? AND player_id = ? AND join_status <> 'left'
            """,
            (room_id, player_id),
        )
        for item in room["participants"]:
            if item["player_id"] == player_id:
                item["join_status"] = "left"
                item["activity_state"] = "inactive"
                item["active"] = False
        remaining = [
            item for item in room["participants"]
            if item.get("join_status") == "joined" and item.get("active", True)
        ]
        terminal = not game.accepts_player_count(len(remaining))
        winner_player_id = remaining[0]["player_id"] if len(remaining) == 1 else None
        winner = remaining[0]["role"] if winner_player_id else (
            "draw" if terminal else None
        )
        game_result = (
            {"winner_player_id": winner_player_id, "draw": False,
             "reason": "participant_left"}
            if winner_player_id else {
                "draw": True,
                "reason": "insufficient_players",
                "remaining_player_ids": [item["player_id"] for item in remaining],
            }
            if terminal else None
        )
        if terminal:
            game_result = _attach_multiplayer_settlement(
                game, room, room["board_state"], game_result
            )
        next_player_id = None
        next_turn: Role = role
        if not terminal:
            next_player_id = (
                advance_turn(room["participants"], player_id)
                if room.get("current_player_id") == player_id
                else room.get("current_player_id")
            )
            next_participant = _participant_by_id(room, next_player_id)
            if next_participant is None:
                raise DuelError("下一行动者不属于房间")
            next_turn = next_participant["role"]
        conn.execute(
            """
            UPDATE rooms
            SET status = ?, winner = ?, winner_player_id = ?, result_json = ?,
                turn = ?, current_player_id = ?, revision = revision + 1,
                updated_at = ?, terminal_at = CASE WHEN ? THEN ? ELSE terminal_at END,
                terminal_reason = CASE WHEN ? THEN 'participant_left' ELSE terminal_reason END
            WHERE room_id = ? AND status = 'playing'
            """,
            (
                "finished" if terminal else "playing",
                winner,
                winner_player_id,
                (
                    json.dumps(game_result, ensure_ascii=False, separators=(",", ":"))
                    if game_result is not None else None
                ),
                next_turn,
                next_player_id,
                timestamp,
                int(terminal),
                timestamp,
                int(terminal),
                room_id,
            ),
        )
        updated = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        _record_event(
            conn, room_id, role, player_id, updated["revision"],
            event_type="leave", text=message,
        )
        result = decode_room(updated, conn)
        if terminal:
            _record_result_event(conn, result)
            _settle_terminal_room(conn, result)
            from .achievements import record_terminal_room

            result["achievement_unlocks"] = record_terminal_room(
                conn, result, "participant_left", normal=False
            )
        _notify_game_participants(
            conn,
            result,
            event_type="left",
            summary=f"对方离开了{game.display_name}房间",
            event_key=f"game:left:{room_id}:{player_id}",
            exclude_player_ids={player_id},
            created_at=timestamp,
        )
    return _decorate(result)


def resign(
    room_id: str,
    role: Role,
    player_id: str,
    opponent_id: str | None = None,
    message: str | None = None,
) -> dict:
    room_id = _room_id(room_id)
    player_id = _player_id(player_id)
    message = _message_text(message)
    with write_transaction() as conn:
        _maintain_rooms(conn, room_id)
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(row, conn)
        _assert_player(room, role, player_id)
        _assert_opponent(room, role, opponent_id)
        if room["status"] != "playing":
            raise DuelError("只有已开始的对局可以认输", 409)
        participant = _participant_by_id(room, player_id)
        if (
            participant is None
            or participant.get("join_status") != "joined"
            or not participant.get("active", True)
        ):
            raise DuelError("当前参与者已经退出或不可行动", 409)
        conn.execute(
            """
            UPDATE room_participants
            SET active = 0, activity_state = 'inactive'
            WHERE room_id = ? AND player_id = ?
            """,
            (room_id, player_id),
        )
        for participant in room["participants"]:
            if participant["player_id"] == player_id:
                participant["active"] = False
                participant["activity_state"] = "inactive"
        remaining = [
            participant for participant in room["participants"]
            if participant.get("join_status") == "joined"
            and participant.get("active", True)
        ]
        game = get_game(room["game_type"])
        terminal = not game.accepts_player_count(len(remaining))
        winner_player_id = remaining[0]["player_id"] if len(remaining) == 1 else None
        winner = remaining[0]["role"] if len(remaining) == 1 else (
            "draw" if terminal else None
        )
        game_result = (
            {"winner_player_id": winner_player_id, "draw": False}
            if winner_player_id else {
                "draw": True,
                "reason": "insufficient_players",
                "remaining_player_ids": [
                    participant["player_id"] for participant in remaining
                ],
            }
            if terminal else None
        )
        if terminal:
            game_result = _attach_multiplayer_settlement(
                game, room, room["board_state"], game_result
            )
        next_player_id = None
        next_turn: Role = role
        if not terminal:
            next_player_id = (
                advance_turn(room["participants"], player_id)
                if room.get("current_player_id") == player_id
                else room.get("current_player_id")
            )
            next_participant = _participant_by_id(room, next_player_id)
            if next_participant is None:
                raise DuelError("下一行动者不属于房间")
            next_turn = next_participant["role"]
        timestamp = _now()
        conn.execute(
            """
            UPDATE rooms
            SET status = ?, winner = ?, winner_player_id = ?, result_json = ?,
                turn = ?, current_player_id = ?, revision = revision + 1,
                updated_at = ?, terminal_at = CASE WHEN ? THEN ? ELSE terminal_at END,
                terminal_reason = CASE WHEN ? THEN 'resignation' ELSE terminal_reason END
            WHERE room_id = ?
            """,
            (
                "finished" if terminal else "playing",
                winner,
                winner_player_id,
                (
                    json.dumps(game_result, ensure_ascii=False, separators=(",", ":"))
                    if game_result is not None else None
                ),
                next_turn,
                next_player_id,
                timestamp,
                int(terminal),
                timestamp,
                int(terminal),
                room_id,
            ),
        )
        updated = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        _record_event(
            conn,
            room_id,
            role,
            player_id,
            updated["revision"],
            event_type="resign",
            text=message,
        )
        result = decode_room(updated, conn)
        if terminal:
            _record_result_event(
                conn, result, resigned_player_id=player_id
            )
            _settle_terminal_room(conn, result)
            from .achievements import record_terminal_room

            result["achievement_unlocks"] = record_terminal_room(
                conn,
                result,
                "resignation",
                normal=result.get("winner_player_id") is not None,
            )
        _notify_game_participants(
            conn,
            result,
            event_type="resigned",
            summary=f"对方在{get_game(result['game_type']).display_name}中认输",
            event_key=f"game:resigned:{room_id}:{player_id}",
            exclude_player_ids={player_id},
            created_at=timestamp,
        )
    return _decorate(result)
