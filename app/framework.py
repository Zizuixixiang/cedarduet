import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from .database import connect, decode_room, write_transaction
from .games import get_game
from .games.base import MoveResult

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
            "SELECT 1 FROM rooms WHERE room_id = ?", (candidate,)
        ).fetchone() is None:
            return candidate
    raise DuelError("暂时无法生成房间号，请重试", 503)


def _decorate(room: dict) -> dict:
    game = get_game(room["game_type"])
    result = dict(room)
    result["rules_text"] = game.rules_text
    result["move_format"] = game.move_format
    result["game_name"] = game.display_name
    result["min_players"] = game.min_players
    result["max_players"] = game.max_players
    result["action_note"] = room["board_state"].get("last_action_note", "")
    _add_retention_metadata(result)
    return result


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
) -> None:
    conn.execute(
        """
        INSERT INTO room_messages (
            room_id, sender, sender_player_id, text, revision_at_send,
            created_at, event_type, move_label
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
    return {
        "player_id": player_id,
        "name": (
            participant.get("display_name")
            if participant and participant.get("display_name")
            else player_id
        ),
        "role": participant["role"] if participant else row["sender"],
    }


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
    elif row["event_type"] == "result":
        display_text = row["text"]
    else:
        display_text = f"{sender_name}：{row['text']}"
    return {
        "id": row["id"],
        "sequence": row["id"],
        "sender": sender,
        "sender_role": sender_role,
        "sender_name": sender_name,
        "text": row["text"],
        "event_type": row["event_type"],
        "move_label": row["move_label"],
        "display_text": display_text,
        "revision_at_send": row["revision_at_send"],
        "created_at": row["created_at"],
    }


def list_timeline(room_id: str, limit: int = 200) -> list[dict]:
    room_id = _room_id(room_id)
    conn = connect()
    try:
        room_row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if room_row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(room_row, conn)
        rows = conn.execute(
            """
            SELECT * FROM (
                SELECT * FROM room_messages
                WHERE room_id = ?
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id
            """,
            (room_id, limit),
        ).fetchall()
        return [_timeline_entry(row, room) for row in rows]
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
                   ai.player_id AS ai_player_id
            FROM rooms AS r
            JOIN room_participants AS human
              ON human.room_id = r.room_id
             AND human.role = 'human'
             AND human.player_id = ?
            LEFT JOIN room_participants AS ai
              ON ai.room_id = r.room_id AND ai.role = 'ai'
            ORDER BY
                CASE r.status
                    WHEN 'playing' THEN 0
                    WHEN 'waiting' THEN 1
                    WHEN 'finished' THEN 2
                    ELSE 3
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
        ai_player_id = item.get("ai_player_id")
        base_ai_id = ai_player_id.split(":", 1)[0] if ai_player_id else None
        item["ai_name"] = (
            ai_names.get(ai_player_id or "")
            or ai_names.get(base_ai_id or "")
            or "你的小机"
        )
        item["game_name"] = get_game(row["game_type"]).display_name
        _add_retention_metadata(item)
        result.append(item)
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
        "" if include_terminal else "AND r.status IN ('waiting', 'playing')"
    )
    with write_transaction() as conn:
        _maintain_rooms(conn)
        rows = conn.execute(
            f"""
            SELECT r.room_id, r.game_type, r.status, r.turn,
                   r.created_at, r.updated_at
            FROM rooms AS r
            JOIN room_participants AS participant
              ON participant.room_id = r.room_id
             AND participant.role = 'ai'
             AND participant.player_id = ?
            WHERE 1 = 1
              {terminal_filter}
            ORDER BY
                CASE r.status
                    WHEN 'playing' THEN 0
                    WHEN 'waiting' THEN 1
                    WHEN 'finished' THEN 2
                    ELSE 3
                END,
                r.updated_at DESC,
                r.created_at DESC,
                r.room_id DESC
            LIMIT ? OFFSET ?
            """,
            (ai_player_id, limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


def post_message(
    room_id: str,
    role: Role,
    player_id: str,
    text: str,
    opponent_id: str | None = None,
) -> dict:
    """Store speech without changing room revision or waking move waiters."""
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
        _record_event(
            conn, room_id, role, player_id, room["revision"],
            event_type="message", text=text,
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
                   created_at, event_type, move_label
            FROM room_messages
            WHERE room_id = ? AND id > ? AND sender_player_id <> ?
            ORDER BY id
            """,
            (room_id, last_event_id, player_id),
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
        return [_timeline_entry(row, room) for row in rows]


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
        WHERE room_id = ? AND event_type = 'result'
        """,
        (room["room_id"],),
    ).fetchone()
    if exists is not None:
        return
    if resigned_player_id is not None:
        result_text = (
            f"{_participant_display_name(room, resigned_player_id)} 认输"
        )
    elif room["winner"] == "draw":
        result_text = "和棋"
    elif room["winner"] in {"human", "ai"}:
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
            else (winner["player_id"] if winner else room["winner"])
        )
        result_text = f"{winner_name} 获胜"
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
    archived = _archive_stale_rooms(conn, room_id)
    deleted = _delete_expired_terminal_rooms(conn, room_id)
    return archived, deleted


def _check_global_capacity(conn) -> None:
    active_count = conn.execute(
        "SELECT COUNT(*) FROM rooms WHERE status IN ('waiting', 'playing')"
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
        JOIN room_participants AS ai
          ON ai.room_id = rooms.room_id
         AND ai.role = 'ai'
         AND ai.player_id = ?
        WHERE rooms.status IN ('waiting', 'playing')
        """,
        (human_player_id, ai_player_id),
    ).fetchone()[0]
    if active_count >= PAIR_ACTIVE_ROOM_LIMIT:
        raise DuelError(
            f"这对人类与 AI 已有 {PAIR_ACTIVE_ROOM_LIMIT} 个活跃房间；"
            "请先完成或认输其中一局再开新房。",
            409,
        )


def create_room(
    game_type: str,
    mode: str,
    role: Role,
    player_id: str,
    opponent_id: str | None = None,
    participant_names: dict[str, str] | None = None,
) -> dict:
    try:
        game = get_game(game_type)
    except ValueError as exc:
        raise DuelError(str(exc)) from exc
    if mode not in {"human_first", "ai_first"}:
        raise DuelError("mode 必须是 human_first 或 ai_first")
    player_id = _player_id(player_id)
    opponent_id = _player_id(opponent_id) if opponent_id is not None else None
    human_player_id = player_id if role == "human" else opponent_id
    ai_player_id = player_id if role == "ai" else opponent_id
    participant_names = participant_names or {}
    state = game.initial_state()
    state["marks"] = (
        {"human": "X", "ai": "O"}
        if mode == "human_first"
        else {"human": "O", "ai": "X"}
    )
    first_turn = "human" if mode == "human_first" else "ai"
    timestamp = _now()
    with write_transaction() as conn:
        _maintain_rooms(conn)
        _check_global_capacity(conn)
        _check_pair_capacity(conn, human_player_id, ai_player_id)
        room_id = _new_room_id(conn)
        status = "playing" if opponent_id is not None else "waiting"
        conn.execute(
            """
            INSERT INTO rooms (
                room_id, game_type, mode, board_state, turn, revision,
                status, winner, preserved, terminal_at,
                created_at, updated_at, last_move_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?, NULL, 0, NULL, ?, ?, ?)
            """,
            (
                room_id,
                game_type,
                mode,
                json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                first_turn,
                status,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        for seat_index, (participant_role, participant_id) in enumerate(
            (("human", human_player_id), ("ai", ai_player_id))
        ):
            if participant_id is not None:
                conn.execute(
                    """
                    INSERT INTO room_participants (
                        room_id, player_id, display_name, role,
                        seat_index, joined_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        room_id,
                        participant_id,
                        (
                            participant_names.get(participant_id)
                            or participant_id
                        )[:100],
                        participant_role,
                        seat_index,
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
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        room = decode_room(row, conn)
    return _decorate(room)


def join_room(
    room_id: str,
    role: Role,
    player_id: str,
    opponent_id: str | None = None,
    message: str | None = None,
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
        same_role = [
            participant
            for participant in room["participants"]
            if participant["role"] == role
        ]
        if row["status"] != "waiting":
            if any(
                participant["player_id"] == player_id
                for participant in same_role
            ):
                if message:
                    _record_event(
                        conn, room_id, role, player_id, row["revision"],
                        event_type="message", text=message,
                    )
                return _decorate(room)
            raise DuelError("房间已经开始或结束，不能加入", 409)
        if same_role and all(
            participant["player_id"] != player_id
            for participant in same_role
        ):
            raise DuelError("该席位已被占用", 409)
        if not same_role:
            future_human_id = (
                player_id if role == "human" else room["human_player_id"]
            )
            future_ai_id = (
                player_id if role == "ai" else room["ai_player_id"]
            )
            _check_pair_capacity(conn, future_human_id, future_ai_id)
            seat_index = conn.execute(
                """
                SELECT COALESCE(MAX(seat_index), -1) + 1
                FROM room_participants WHERE room_id = ?
                """,
                (room_id,),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO room_participants (
                    room_id, player_id, display_name, role,
                    seat_index, joined_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (room_id, player_id, player_id, role, seat_index, _now()),
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
        room = decode_room(row, conn)
        human_player_id = room["human_player_id"]
        ai_player_id = room["ai_player_id"]
        new_status = (
            "playing"
            if human_player_id is not None and ai_player_id is not None
            else "waiting"
        )
        conn.execute(
            """
            UPDATE rooms
            SET status = ?, revision = revision + 1, updated_at = ?
            WHERE room_id = ?
            """,
            (new_status, _now(), room_id),
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


def play_move(
    room_id: str,
    role: Role,
    player_id: str,
    move: dict,
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
            raise DuelError("当前房间不在对局中", 409)
        if room["turn"] != role:
            raise DuelError("还没轮到你落子", 409)
        game = get_game(room["game_type"])
        mark = room["board_state"]["marks"][role]
        try:
            move_label = game.format_move(room["board_state"], move, mark)
            game.validate_move(room["board_state"], move, mark)
            applied = game.apply_move(room["board_state"], move, mark)
        except (KeyError, TypeError, ValueError) as exc:
            raise DuelError(f"无效落子：{exc}") from exc
        if isinstance(applied, MoveResult):
            state = applied.state
            retain_turn = applied.retain_turn
            action_note = applied.note
        else:
            state = applied
            retain_turn = False
            action_note = ""
        state["last_action_note"] = action_note
        outcome = game.check_winner(state)
        next_turn: Role = (
            role if retain_turn else ("ai" if role == "human" else "human")
        )
        status = "finished" if outcome is not None else "playing"
        winner = None
        if outcome == "draw":
            winner = "draw"
        elif outcome in {"X", "O"}:
            winner = next(
                side for side, side_mark in state["marks"].items() if side_mark == outcome
            )
        timestamp = _now()
        conn.execute(
            """
            UPDATE rooms
            SET board_state = ?, turn = ?, revision = revision + 1,
                status = ?, winner = ?, updated_at = ?, last_move_at = ?,
                terminal_at = CASE WHEN ? = 'finished' THEN ? ELSE terminal_at END
            WHERE room_id = ?
            """,
            (
                json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                next_turn,
                status,
                winner,
                timestamp,
                timestamp,
                status,
                timestamp,
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
        )
        result = decode_room(updated, conn)
        if result["status"] == "finished":
            _record_result_event(conn, result)
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
        if room["status"] in {"finished", "archived"}:
            raise DuelError("对局已经结束", 409)
        winner: Role = "ai" if role == "human" else "human"
        timestamp = _now()
        conn.execute(
            """
            UPDATE rooms
            SET status = 'finished', winner = ?, revision = revision + 1,
                updated_at = ?, terminal_at = ?
            WHERE room_id = ?
            """,
            (winner, timestamp, timestamp, room_id),
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
        _record_result_event(
            conn, result, resigned_player_id=player_id
        )
    return _decorate(result)
