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
    last_move_at TEXT NOT NULL
)
"""

ROOM_PARTICIPANTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS room_participants (
    room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
    player_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('human', 'ai')),
    seat_index INTEGER NOT NULL CHECK (seat_index >= 0),
    joined_at TEXT NOT NULL,
    PRIMARY KEY (room_id, player_id),
    UNIQUE (room_id, seat_index)
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
            conn.execute(ROOM_PARTICIPANTS_SCHEMA)
        else:
            sql = existing["sql"] or ""
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(rooms)")
            }
            participants_exists = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'room_participants'
                """
            ).fetchone()
            if (
                "'archived'" not in sql
                or "last_move_at" not in columns
                or "human_player_id" in columns
                or "ai_player_id" in columns
                or participants_exists is None
            ):
                _migrate_to_participants(conn)
            else:
                conn.execute(ROOM_PARTICIPANTS_SCHEMA)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rooms_updated_at ON rooms(updated_at)"
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_room_participants_player
            ON room_participants(player_id, role, room_id)
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


def _migrate_to_participants(conn: sqlite3.Connection) -> None:
    """Move legacy human/AI columns into the extensible participant relation."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(rooms)")}
    last_move_expr = (
        "COALESCE(last_move_at, updated_at, created_at)"
        if "last_move_at" in columns
        else "COALESCE(updated_at, created_at)"
    )
    has_legacy_players = {
        "human_player_id", "ai_player_id"
    }.issubset(columns)
    messages_exists = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'room_messages'
        """
    ).fetchone() is not None
    participants_exists = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'room_participants'
        """
    ).fetchone() is not None
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        if messages_exists:
            conn.execute(
                "ALTER TABLE room_messages RENAME TO room_messages_legacy"
            )
        if participants_exists:
            conn.execute(
                "ALTER TABLE room_participants RENAME TO room_participants_legacy"
            )
        conn.execute("ALTER TABLE rooms RENAME TO rooms_legacy")
        conn.execute(ROOMS_SCHEMA)
        conn.execute(
            f"""
            INSERT INTO rooms (
                room_id, game_type, mode, board_state, turn, revision,
                status, winner, created_at, updated_at, last_move_at
            )
            SELECT
                room_id, game_type, mode, board_state, turn, revision,
                status, winner, created_at, updated_at, {last_move_expr}
            FROM rooms_legacy
            """
        )
        conn.execute(ROOM_PARTICIPANTS_SCHEMA)
        if participants_exists:
            conn.execute(
                """
                INSERT INTO room_participants (
                    room_id, player_id, role, seat_index, joined_at
                )
                SELECT room_id, player_id, role, seat_index, joined_at
                FROM room_participants_legacy
                """
            )
        elif has_legacy_players:
            conn.execute(
                """
                INSERT INTO room_participants (
                    room_id, player_id, role, seat_index, joined_at
                )
                SELECT room_id, human_player_id, 'human', 0, created_at
                FROM rooms_legacy
                WHERE human_player_id IS NOT NULL
                """
            )
            conn.execute(
                """
                INSERT INTO room_participants (
                    room_id, player_id, role, seat_index, joined_at
                )
                SELECT room_id, ai_player_id, 'ai', 1, created_at
                FROM rooms_legacy
                WHERE ai_player_id IS NOT NULL
                """
            )
        if messages_exists:
            conn.execute(ROOM_MESSAGES_SCHEMA)
            conn.execute(
                """
                INSERT INTO room_messages (
                    id, room_id, sender, text, revision_at_send, created_at,
                    event_type, move_label, read_by_ai
                )
                SELECT
                    id, room_id, sender, text, revision_at_send, created_at,
                    event_type, move_label, read_by_ai
                FROM room_messages_legacy
                """
            )
            conn.execute("DROP TABLE room_messages_legacy")
        if participants_exists:
            conn.execute("DROP TABLE room_participants_legacy")
        conn.execute("DROP TABLE rooms_legacy")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


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


def decode_room(
    row: sqlite3.Row, conn: sqlite3.Connection | None = None
) -> dict:
    room = dict(row)
    room["board_state"] = json.loads(room["board_state"])
    owns_connection = conn is None
    if conn is None:
        conn = connect()
    try:
        participant_rows = conn.execute(
            """
            SELECT player_id, role, seat_index, joined_at
            FROM room_participants
            WHERE room_id = ?
            ORDER BY seat_index, joined_at, player_id
            """,
            (room["room_id"],),
        ).fetchall()
    finally:
        if owns_connection:
            conn.close()
    room["participants"] = [dict(participant) for participant in participant_rows]
    room["human_player_id"] = next(
        (
            participant["player_id"]
            for participant in room["participants"]
            if participant["role"] == "human"
        ),
        None,
    )
    room["ai_player_id"] = next(
        (
            participant["player_id"]
            for participant in room["participants"]
            if participant["role"] == "ai"
        ),
        None,
    )
    return room
