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
    preserved INTEGER NOT NULL DEFAULT 0 CHECK (preserved IN (0, 1)),
    terminal_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_move_at TEXT NOT NULL
)
"""

ROOM_PARTICIPANTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS room_participants (
    room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
    player_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
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
    sender TEXT NOT NULL CHECK (sender IN ('human', 'ai', 'system')),
    sender_player_id TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    revision_at_send INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'message'
        CHECK (event_type IN ('message', 'move', 'resign', 'result')),
    move_label TEXT
)
"""

ROOM_EVENT_CURSORS_SCHEMA = """
CREATE TABLE IF NOT EXISTS room_event_cursors (
    room_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    last_event_id INTEGER NOT NULL DEFAULT 0 CHECK (last_event_id >= 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (room_id, player_id),
    FOREIGN KEY (room_id, player_id)
        REFERENCES room_participants(room_id, player_id) ON DELETE CASCADE
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
        room_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(rooms)")
        }
        if "preserved" not in room_columns:
            conn.execute(
                """
                ALTER TABLE rooms ADD COLUMN preserved INTEGER NOT NULL
                DEFAULT 0 CHECK (preserved IN (0, 1))
                """
            )
        if "terminal_at" not in room_columns:
            conn.execute("ALTER TABLE rooms ADD COLUMN terminal_at TEXT")
        conn.execute(
            """
            UPDATE rooms
            SET terminal_at = COALESCE(terminal_at, updated_at, last_move_at, created_at)
            WHERE status IN ('finished', 'archived')
              AND terminal_at IS NULL
            """
        )
        participant_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(room_participants)")
        }
        if "display_name" not in participant_columns:
            conn.execute(
                "ALTER TABLE room_participants ADD COLUMN display_name TEXT"
            )
            conn.execute(
                """
                UPDATE room_participants
                SET display_name = player_id
                WHERE display_name IS NULL OR display_name = ''
                """
            )
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
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_rooms_terminal_cleanup
            ON rooms(status, preserved, terminal_at)
            """
        )
        conn.execute(ROOM_MESSAGES_SCHEMA)
        message_schema_row = conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'room_messages'
            """
        ).fetchone()
        message_schema = (message_schema_row["sql"] or "") if message_schema_row else ""
        message_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(room_messages)")
        }
        if (
            "sender_player_id" not in message_columns
            or "read_by_ai" in message_columns
            or "'result'" not in message_schema
            or "'system'" not in message_schema
        ):
            _migrate_message_events(conn, message_columns)
        conn.execute(ROOM_EVENT_CURSORS_SCHEMA)
        _seed_missing_event_cursors(conn)
        _backfill_terminal_result_events(conn)
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_room_messages_timeline
            ON room_messages(room_id, id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_room_messages_sender
            ON room_messages(room_id, id, sender_player_id)
            """
        )
        conn.execute("DROP INDEX IF EXISTS idx_room_messages_ai_unread")
        # Chip-center tables are additive and deliberately independent of rooms.
        # The local import avoids a schema/service cycle during module loading.
        from .chips import init_chips_schema

        init_chips_schema(conn)
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
    preserved_expr = "COALESCE(preserved, 0)" if "preserved" in columns else "0"
    terminal_expr = (
        "CASE WHEN status IN ('finished', 'archived') "
        "THEN COALESCE(terminal_at, updated_at, created_at) ELSE NULL END"
        if "terminal_at" in columns
        else
        "CASE WHEN status IN ('finished', 'archived') "
        "THEN COALESCE(updated_at, created_at) ELSE NULL END"
    )
    messages_exists = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'room_messages'
        """
    ).fetchone() is not None
    message_columns = (
        {
            row["name"]
            for row in conn.execute("PRAGMA table_info(room_messages)")
        }
        if messages_exists
        else set()
    )
    participants_exists = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'room_participants'
        """
    ).fetchone() is not None
    participant_columns = (
        {
            row["name"]
            for row in conn.execute("PRAGMA table_info(room_participants)")
        }
        if participants_exists
        else set()
    )
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
                status, winner, preserved, terminal_at,
                created_at, updated_at, last_move_at
            )
            SELECT
                room_id, game_type, mode, board_state, turn, revision,
                status, winner, {preserved_expr}, {terminal_expr},
                created_at, updated_at, {last_move_expr}
            FROM rooms_legacy
            """
        )
        conn.execute(ROOM_PARTICIPANTS_SCHEMA)
        if participants_exists:
            display_name_expr = (
                "COALESCE(display_name, player_id)"
                if "display_name" in participant_columns
                else "player_id"
            )
            conn.execute(
                f"""
                INSERT INTO room_participants (
                    room_id, player_id, display_name, role,
                    seat_index, joined_at
                )
                SELECT room_id, player_id, {display_name_expr}, role,
                       seat_index, joined_at
                FROM room_participants_legacy
                """
            )
        elif has_legacy_players:
            conn.execute(
                """
                INSERT INTO room_participants (
                    room_id, player_id, display_name, role,
                    seat_index, joined_at
                )
                SELECT room_id, human_player_id, human_player_id,
                       'human', 0, created_at
                FROM rooms_legacy
                WHERE human_player_id IS NOT NULL
                """
            )
            conn.execute(
                """
                INSERT INTO room_participants (
                    room_id, player_id, display_name, role,
                    seat_index, joined_at
                )
                SELECT room_id, ai_player_id, ai_player_id,
                       'ai', 1, created_at
                FROM rooms_legacy
                WHERE ai_player_id IS NOT NULL
                """
            )
        if messages_exists:
            _copy_legacy_messages_and_seed_cursors(
                conn, "room_messages_legacy", message_columns
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


def _copy_legacy_messages_and_seed_cursors(
    conn: sqlite3.Connection, legacy_table: str, columns: set[str]
) -> None:
    sender_player_expr = (
        "COALESCE(sender_player_id, "
        "(SELECT participant.player_id FROM room_participants AS participant "
        f"WHERE participant.room_id = {legacy_table}.room_id "
        f"AND participant.role = {legacy_table}.sender "
        "ORDER BY participant.seat_index LIMIT 1), sender)"
        if "sender_player_id" in columns
        else
        "(SELECT participant.player_id FROM room_participants AS participant "
        f"WHERE participant.room_id = {legacy_table}.room_id "
        f"AND participant.role = {legacy_table}.sender "
        "ORDER BY participant.seat_index LIMIT 1)"
    )
    conn.execute(ROOM_MESSAGES_SCHEMA)
    conn.execute(
        f"""
        INSERT INTO room_messages (
            id, room_id, sender, sender_player_id, text,
            revision_at_send, created_at, event_type, move_label
        )
        SELECT
            id, room_id, sender, COALESCE({sender_player_expr}, sender), text,
            revision_at_send, created_at, event_type, move_label
        FROM {legacy_table}
        """
    )
    conn.execute(ROOM_EVENT_CURSORS_SCHEMA)
    if "read_by_ai" in columns:
        conn.execute(
            f"""
            INSERT OR IGNORE INTO room_event_cursors (
                room_id, player_id, last_event_id, updated_at
            )
            SELECT
                participant.room_id,
                participant.player_id,
                CASE
                    WHEN participant.role = 'ai' THEN COALESCE(
                        (
                            SELECT MIN(message.id) - 1
                            FROM {legacy_table} AS message
                            WHERE message.room_id = participant.room_id
                              AND message.sender <> 'ai'
                              AND message.text <> ''
                              AND message.read_by_ai = 0
                        ),
                        (
                            SELECT COALESCE(MAX(message.id), 0)
                            FROM {legacy_table} AS message
                            WHERE message.room_id = participant.room_id
                        )
                    )
                    ELSE (
                        SELECT COALESCE(MAX(message.id), 0)
                        FROM {legacy_table} AS message
                        WHERE message.room_id = participant.room_id
                    )
                END,
                participant.joined_at
            FROM room_participants AS participant
            """
        )


def _migrate_message_events(
    conn: sqlite3.Connection, columns: set[str]
) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("ALTER TABLE room_messages RENAME TO room_messages_legacy")
        _copy_legacy_messages_and_seed_cursors(
            conn, "room_messages_legacy", columns
        )
        conn.execute("DROP TABLE room_messages_legacy")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _seed_missing_event_cursors(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO room_event_cursors (
            room_id, player_id, last_event_id, updated_at
        )
        SELECT
            participant.room_id,
            participant.player_id,
            COALESCE(
                (
                    SELECT MAX(message.id)
                    FROM room_messages AS message
                    WHERE message.room_id = participant.room_id
                ),
                0
            ),
            participant.joined_at
        FROM room_participants AS participant
        """
    )


def _backfill_terminal_result_events(conn: sqlite3.Connection) -> None:
    """Give pre-0.8 terminal rooms one room-level result event."""
    conn.execute(
        """
        INSERT INTO room_messages (
            room_id, sender, sender_player_id, text, revision_at_send,
            created_at, event_type, move_label
        )
        SELECT
            room.room_id,
            'system',
            'system',
            CASE
                WHEN EXISTS (
                    SELECT 1
                    FROM room_messages AS resigned
                    WHERE resigned.room_id = room.room_id
                      AND resigned.event_type = 'resign'
                ) THEN COALESCE(
                    (
                        SELECT COALESCE(
                            participant.display_name,
                            resigned.sender_player_id
                        )
                        FROM room_messages AS resigned
                        LEFT JOIN room_participants AS participant
                          ON participant.room_id = resigned.room_id
                         AND participant.player_id = resigned.sender_player_id
                        WHERE resigned.room_id = room.room_id
                          AND resigned.event_type = 'resign'
                        ORDER BY resigned.id DESC
                        LIMIT 1
                    ),
                    '参与者'
                ) || ' 认输'
                WHEN room.winner = 'draw' THEN '和棋'
                WHEN room.winner IN ('human', 'ai') THEN COALESCE(
                    (
                        SELECT COALESCE(
                            winner.display_name,
                            winner.player_id
                        )
                        FROM room_participants AS winner
                        WHERE winner.room_id = room.room_id
                          AND winner.role = room.winner
                        ORDER BY winner.seat_index
                        LIMIT 1
                    ),
                    room.winner
                ) || ' 获胜'
                ELSE '对局结束'
            END,
            room.revision,
            room.updated_at,
            'result',
            NULL
        FROM rooms AS room
        WHERE room.status IN ('finished', 'archived')
          AND NOT EXISTS (
              SELECT 1
              FROM room_messages AS existing
              WHERE existing.room_id = room.room_id
                AND existing.event_type = 'result'
          )
        """
    )


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
            SELECT player_id, display_name, role, seat_index, joined_at
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
