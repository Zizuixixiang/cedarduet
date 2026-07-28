import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("DUEL_DB_PATH", PROJECT_ROOT / "data" / "duel.db"))


ROOMS_SCHEMA = """
CREATE TABLE rooms (
    room_id TEXT PRIMARY KEY,
    game_type TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('human_first', 'ai_first')),
    board_state TEXT NOT NULL,
    turn TEXT NOT NULL CHECK (turn IN ('human', 'ai')),
    revision INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (
        status IN ('waiting', 'playing', 'finished', 'archived')
    ),
    winner TEXT CHECK (winner IN ('human', 'ai', 'draw') OR winner IS NULL),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_move_at TEXT NOT NULL,
    human_player_id TEXT,
    ai_player_id TEXT
)
"""

ROOM_MESSAGES_SCHEMA = """
CREATE TABLE IF NOT EXISTS room_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
    sender TEXT NOT NULL CHECK (sender IN ('human', 'ai')),
    text TEXT NOT NULL DEFAULT '',
    revision_at_send INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'message'
        CHECK (event_type IN ('message', 'move', 'resign')),
    move_label TEXT,
    read_by_ai INTEGER NOT NULL DEFAULT 0 CHECK (read_by_ai IN (0, 1))
)
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        existing = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'rooms'"
        ).fetchone()
        if existing is None:
            conn.execute(ROOMS_SCHEMA)
        else:
            sql = existing["sql"] or ""
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(rooms)")
            }
            if "'archived'" not in sql or "last_move_at" not in columns:
                _migrate_rooms_schema(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rooms_updated_at ON rooms(updated_at)"
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_rooms_active_pair
            ON rooms(status, human_player_id, ai_player_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_rooms_last_move_at
            ON rooms(status, last_move_at)
            """
        )
        conn.execute(ROOM_MESSAGES_SCHEMA)
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_room_messages_timeline
            ON room_messages(room_id, id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_room_messages_ai_unread
            ON room_messages(room_id, sender, read_by_ai, id)
            """
        )
    finally:
        conn.close()


def _migrate_rooms_schema(conn: sqlite3.Connection) -> None:
    """Rebuild the phase-one table so SQLite CHECK constraints accept archived."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(rooms)")}
    last_move_expr = (
        "COALESCE(last_move_at, updated_at, created_at)"
        if "last_move_at" in columns
        else "COALESCE(updated_at, created_at)"
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("ALTER TABLE rooms RENAME TO rooms_phase_one")
        conn.execute(ROOMS_SCHEMA)
        conn.execute(
            f"""
            INSERT INTO rooms (
                room_id, game_type, mode, board_state, turn, revision,
                status, winner, created_at, updated_at, last_move_at,
                human_player_id, ai_player_id
            )
            SELECT
                room_id, game_type, mode, board_state, turn, revision,
                status, winner, created_at, updated_at, {last_move_expr},
                human_player_id, ai_player_id
            FROM rooms_phase_one
            """
        )
        conn.execute("DROP TABLE rooms_phase_one")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


@contextmanager
def write_transaction() -> Iterator[sqlite3.Connection]:
    """Reserve the SQLite writer before loading state for a mutation."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def decode_room(row: sqlite3.Row) -> dict:
    room = dict(row)
    room["board_state"] = json.loads(room["board_state"])
    return room
