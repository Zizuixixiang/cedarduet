import json
import re
import secrets
from datetime import datetime, timezone
from typing import Literal

from .database import connect, decode_room, write_transaction
from .games import get_game

Role = Literal["human", "ai"]
ROOM_ID_RE = re.compile(r"^[A-Z0-9]{8}$")
PLAYER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,79}$")


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


def create_room(
    game_type: str, mode: str, role: Role, player_id: str
) -> dict:
    try:
        game = get_game(game_type)
    except ValueError as exc:
        raise DuelError(str(exc)) from exc
    if mode not in {"human_first", "ai_first"}:
        raise DuelError("mode 必须是 human_first 或 ai_first")
    player_id = _player_id(player_id)
    state = game.initial_state()
    state["marks"] = (
        {"human": "X", "ai": "O"}
        if mode == "human_first"
        else {"human": "O", "ai": "X"}
    )
    first_turn = "human" if mode == "human_first" else "ai"
    timestamp = _now()
    with write_transaction() as conn:
        room_id = _new_room_id(conn)
        conn.execute(
            """
            INSERT INTO rooms (
                room_id, game_type, mode, board_state, turn, revision,
                status, winner, created_at, updated_at,
                human_player_id, ai_player_id
            ) VALUES (?, ?, ?, ?, ?, 0, 'waiting', NULL, ?, ?, ?, ?)
            """,
            (
                room_id,
                game_type,
                mode,
                json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                first_turn,
                timestamp,
                timestamp,
                player_id if role == "human" else None,
                player_id if role == "ai" else None,
            ),
        )
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
    return _decorate(decode_room(row))


def join_room(room_id: str, role: Role, player_id: str) -> dict:
    room_id = _room_id(room_id)
    player_id = _player_id(player_id)
    column = "human_player_id" if role == "human" else "ai_player_id"
    other = "ai_player_id" if role == "human" else "human_player_id"
    with write_transaction() as conn:
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if row is None:
            raise DuelError("房间不存在", 404)
        if row["status"] != "waiting":
            if row[column] == player_id:
                return _decorate(decode_room(row))
            raise DuelError("房间已经开始或结束，不能加入", 409)
        if row[column] is not None and row[column] != player_id:
            raise DuelError("该席位已被占用", 409)
        new_status = "playing" if row[other] is not None else "waiting"
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


def get_room(room_id: str, role: Role | None = None, player_id: str | None = None) -> dict:
    room_id = _room_id(room_id)
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise DuelError("房间不存在", 404)
    room = decode_room(row)
    if role is not None:
        _assert_player(room, role, _player_id(player_id or ""))
    return _decorate(room)


def _assert_player(room: dict, role: Role, player_id: str) -> None:
    expected = room[f"{role}_player_id"]
    if expected is None:
        raise DuelError(f"{role} 席位尚未加入", 409)
    if expected != player_id:
        raise DuelError("player_id 与该房间席位不匹配", 403)


def play_move(
    room_id: str, role: Role, player_id: str, move: dict
) -> dict:
    room_id = _room_id(room_id)
    player_id = _player_id(player_id)
    with write_transaction() as conn:
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(row)
        _assert_player(room, role, player_id)
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
                status = ?, winner = ?, updated_at = ?
            WHERE room_id = ?
            """,
            (
                json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                next_turn,
                status,
                winner,
                _now(),
                room_id,
            ),
        )
        updated = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
    return _decorate(decode_room(updated))


def resign(room_id: str, role: Role, player_id: str) -> dict:
    room_id = _room_id(room_id)
    player_id = _player_id(player_id)
    with write_transaction() as conn:
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        if row is None:
            raise DuelError("房间不存在", 404)
        room = decode_room(row)
        _assert_player(room, role, player_id)
        if room["status"] == "finished":
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
