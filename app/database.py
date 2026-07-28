import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("DUEL_DB_PATH", PROJECT_ROOT / "data" / "duel.db"))


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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rooms (
                room_id TEXT PRIMARY KEY,
                game_type TEXT NOT NULL,
                mode TEXT NOT NULL CHECK (mode IN ('human_first', 'ai_first')),
                board_state TEXT NOT NULL,
                turn TEXT NOT NULL CHECK (turn IN ('human', 'ai')),
                revision INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL CHECK (status IN ('waiting', 'playing', 'finished')),
                winner TEXT CHECK (winner IN ('human', 'ai', 'draw') OR winner IS NULL),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                human_player_id TEXT,
                ai_player_id TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rooms_updated_at ON rooms(updated_at)"
        )
    finally:
        conn.close()


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
