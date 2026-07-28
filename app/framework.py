import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from .database import decode_room, write_transaction
from .games import get_game

Role = Literal["human", "ai"]
ROOM_ID_RE = re.compile(r"^[A-Z0-9]{8}$")
PLAYER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,79}$")
PAIR_ACTIVE_ROOM_LIMIT = 3
GLOBAL_ACTIVE_ROOM_LIMIT = 500
STALE_ROOM_DAYS = 7


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
    return result


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
        SELECT COUNT(*) FROM rooms
        WHERE status IN ('waiting', 'playing')
          AND human_player_id = ?
          AND ai_player_id = ?
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
                status, winner, created_at, updated_at, last_move_at,
                human_player_id, ai_player_id
            ) VALUES (?, ?, ?, ?, ?, 0, ?, NULL, ?, ?, ?, ?, ?)
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
                human_player_id,
                ai_player_id,
            ),
        )
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
    return _decorate(decode_room(row))


def join_room(
    room_id: str,
    role: Role,
    player_id: str,
    opponent_id: str | None = None,
) -> dict:
    room_id = _room_id(room_id)
    player_id = _player_id(player_id)
    opponent_id = _player_id(opponent_id) if opponent_id is not None else None
    column = "human_player_id" if role == "human" else "ai_player_id"
    other = "ai_player_id" if role == "human" else "human_player_id"
    with write_transaction() as conn:
        _archive_stale_rooms(conn, room_id)
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if row is None:
            raise DuelError("房间不存在", 404)
        if opponent_id is not None and row[other] not in {None, opponent_id}:
            raise DuelError("房间不属于当前绑定的人机对", 403)
        if row["status"] != "waiting":
            if row[column] == player_id:
                return _decorate(decode_room(row))
            raise DuelError("房间已经开始或结束，不能加入", 409)
        if row[column] is not None and row[column] != player_id:
            raise DuelError("该席位已被占用", 409)
        new_status = "playing" if row[other] is not None else "waiting"
        human_player_id = (
            player_id if role == "human" else row["human_player_id"]
        )
        ai_player_id = player_id if role == "ai" else row["ai_player_id"]
        if new_status == "playing":
            _check_pair_capacity(conn, human_player_id, ai_player_id)
        conn.execute(
            f"""
            UPDATE rooms
            SET {column} = ?, status = ?, revision = revision + 1, updated_at = ?
            WHERE room_id = ?
            """,
            (player_id, new_status, _now(), room_id),
        )
        updated = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
    return _decorate(decode_room(updated))


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
    room = decode_room(row)
    if role is not None:
        _assert_player(room, role, _player_id(player_id or ""))
        _assert_opponent(room, role, opponent_id)
    return _decorate(room)


def _assert_player(room: dict, role: Role, player_id: str) -> None:
    expected = room[f"{role}_player_id"]
    if expected is None:
        raise DuelError(f"{role} 席位尚未加入", 409)
    if expected != player_id:
        raise DuelError("player_id 与该房间席位不匹配", 403)


def _assert_opponent(room: dict, role: Role, opponent_id: str | None) -> None:
    if opponent_id is None:
        return
    opponent_id = _player_id(opponent_id)
    other: Role = "ai" if role == "human" else "human"
    if room[f"{other}_player_id"] != opponent_id:
        raise DuelError("房间不属于当前绑定的人机对", 403)


def play_move(
    room_id: str,
    role: Role,
    player_id: str,
    move: dict,
    opponent_id: str | None = None,
) -> dict:
    room_id = _room_id(room_id)
    player_id = _player_id(player_id)
    with write_transaction() as conn:
        _archive_stale_rooms(conn, room_id)
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(row)
        _assert_player(room, role, player_id)
        _assert_opponent(room, role, opponent_id)
        if room["status"] != "playing":
            raise DuelError("当前房间不在对局中", 409)
        if room["turn"] != role:
            raise DuelError("还没轮到你落子", 409)
        game = get_game(room["game_type"])
        mark = room["board_state"]["marks"][role]
        try:
            game.validate_move(room["board_state"], move, mark)
            state = game.apply_move(room["board_state"], move, mark)
        except (KeyError, TypeError, ValueError) as exc:
            raise DuelError(f"无效落子：{exc}") from exc
        outcome = game.check_winner(state)
        next_turn: Role = "ai" if role == "human" else "human"
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
    return _decorate(decode_room(updated))


def resign(
    room_id: str,
    role: Role,
    player_id: str,
    opponent_id: str | None = None,
) -> dict:
    room_id = _room_id(room_id)
    player_id = _player_id(player_id)
    with write_transaction() as conn:
        _archive_stale_rooms(conn, room_id)
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(row)
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
    return _decorate(decode_room(updated))
