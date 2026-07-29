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
MAX_MESSAGE_LENGTH = 500


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
    result["action_note"] = room["board_state"].get("last_action_note", "")
    return result


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
    sender: Role,
    revision: int,
    *,
    event_type: str,
    text: str = "",
    move_label: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO room_messages (
            room_id, sender, text, revision_at_send, created_at,
            event_type, move_label, read_by_ai
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            room_id,
            sender,
            text,
            revision,
            _now(),
            event_type,
            move_label,
            0 if sender == "human" and text else 1,
        ),
    )


def _timeline_entry(row, room: dict) -> dict:
    sender = row["sender"]
    sender_name = room.get(f"{sender}_player_id") or (
        "人类" if sender == "human" else "AI"
    )
    if row["event_type"] == "move":
        display_text = f"{sender_name} 落 {row['move_label']}"
        if row["text"]:
            display_text += f"：{row['text']}"
    elif row["event_type"] == "resign":
        display_text = f"{sender_name} 认输"
        if row["text"]:
            display_text += f"：{row['text']}"
    else:
        display_text = f"{sender_name}：{row['text']}"
    return {
        "id": row["id"],
        "sender": sender,
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
        _archive_stale_rooms(conn)
        rows = conn.execute(
            """
            SELECT r.room_id, r.game_type, r.mode, r.turn, r.revision,
                   r.status, r.winner, r.created_at, r.updated_at,
                   r.last_move_at, ai.player_id AS ai_player_id
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
        result.append(item)
    return result


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
        _archive_stale_rooms(conn, room_id)
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
            conn, room_id, role, room["revision"],
            event_type="message", text=text,
        )
    return _decorate(room)


def read_new_human_messages(room_id: str, ai_player_id: str) -> list[dict]:
    """Atomically consume human speech for the room's bound AI."""
    room_id = _room_id(room_id)
    ai_player_id = _player_id(ai_player_id)
    with write_transaction() as conn:
        room_row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if room_row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(room_row, conn)
        _assert_player(room, "ai", ai_player_id)
        rows = conn.execute(
            """
            SELECT id, text, revision_at_send, created_at
            FROM room_messages
            WHERE room_id = ? AND sender = 'human'
              AND text <> '' AND read_by_ai = 0
            ORDER BY id
            """,
            (room_id,),
        ).fetchall()
        if rows:
            conn.executemany(
                "UPDATE room_messages SET read_by_ai = 1 WHERE id = ?",
                [(row["id"],) for row in rows],
            )
    return [
        {
            "text": row["text"],
            "revision_at_send": row["revision_at_send"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def _archive_stale_rooms(conn, room_id: str | None = None) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=STALE_ROOM_DAYS)).isoformat(
        timespec="seconds"
    )
    params: list[str] = [cutoff]
    room_clause = ""
    if room_id is not None:
        room_clause = " AND room_id = ?"
        params.append(room_id)
    cursor = conn.execute(
        f"""
        UPDATE rooms
        SET status = 'archived', winner = 'draw',
            revision = revision + 1, updated_at = ?
        WHERE status IN ('waiting', 'playing')
          AND last_move_at < ?
          {room_clause}
        """,
        [_now(), *params],
    )
    return cursor.rowcount


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
    state = game.initial_state()
    state["marks"] = (
        {"human": "X", "ai": "O"}
        if mode == "human_first"
        else {"human": "O", "ai": "X"}
    )
    first_turn = "human" if mode == "human_first" else "ai"
    timestamp = _now()
    with write_transaction() as conn:
        _archive_stale_rooms(conn)
        _check_global_capacity(conn)
        _check_pair_capacity(conn, human_player_id, ai_player_id)
        room_id = _new_room_id(conn)
        status = "playing" if opponent_id is not None else "waiting"
        conn.execute(
            """
            INSERT INTO rooms (
                room_id, game_type, mode, board_state, turn, revision,
                status, winner, created_at, updated_at, last_move_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?, NULL, ?, ?, ?)
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
                        room_id, player_id, role, seat_index, joined_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        room_id,
                        participant_id,
                        participant_role,
                        seat_index,
                        timestamp,
                    ),
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
        _archive_stale_rooms(conn, room_id)
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
                        conn, room_id, role, row["revision"],
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
                    room_id, player_id, role, seat_index, joined_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (room_id, player_id, role, seat_index, _now()),
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
                conn, room_id, role, updated["revision"],
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
    with write_transaction() as conn:
        _archive_stale_rooms(conn, room_id)
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(row, conn)
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
        _archive_stale_rooms(conn, room_id)
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
        conn.execute(
            """
            UPDATE rooms
            SET board_state = ?, turn = ?, revision = revision + 1,
                status = ?, winner = ?, updated_at = ?, last_move_at = ?
            WHERE room_id = ?
            """,
            (
                json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                next_turn,
                status,
                winner,
                _now(),
                _now(),
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
            updated["revision"],
            event_type="move",
            text=message,
            move_label=move_label,
        )
        result = decode_room(updated, conn)
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
        _archive_stale_rooms(conn, room_id)
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
        conn.execute(
            """
            UPDATE rooms
            SET status = 'finished', winner = ?, revision = revision + 1,
                updated_at = ?
            WHERE room_id = ?
            """,
            (winner, _now(), room_id),
        )
        updated = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        _record_event(
            conn,
            room_id,
            role,
            updated["revision"],
            event_type="resign",
            text=message,
        )
        result = decode_room(updated, conn)
    return _decorate(result)
