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
    current_player_id TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'waiting', 'playing', 'finished', 'archived')
    ),
    winner TEXT CHECK (winner IN ('human', 'ai', 'draw') OR winner IS NULL),
    winner_player_id TEXT,
    result_json TEXT,
    stake INTEGER NOT NULL DEFAULT 0 CHECK (stake >= 0),
    initiator_player_id TEXT,
    confirmation_required INTEGER NOT NULL DEFAULT 0
        CHECK (confirmation_required IN (0, 1)),
    confirmation_expires_at TEXT,
    preserved INTEGER NOT NULL DEFAULT 0 CHECK (preserved IN (0, 1)),
    terminal_at TEXT,
    terminal_reason TEXT,
    rematch_of_room_id TEXT,
    rematch_root_room_id TEXT,
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
    participant_kind TEXT NOT NULL DEFAULT 'bound_machine'
        CHECK (participant_kind IN ('human', 'bound_machine', 'system_npc')),
    npc_persona_id TEXT,
    seat_index INTEGER NOT NULL CHECK (seat_index >= 0),
    token TEXT,
    join_status TEXT NOT NULL DEFAULT 'joined'
        CHECK (join_status IN ('invited', 'joined', 'left')),
    activity_state TEXT NOT NULL DEFAULT 'active'
        CHECK (activity_state IN ('active', 'inactive', 'eliminated', 'skipped')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
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
        CHECK (event_type IN ('message', 'move', 'resign', 'leave', 'result')),
    move_label TEXT,
    move_payload TEXT,
    visible_to_json TEXT
)
"""

ROOM_EVENT_CURSORS_SCHEMA = """
CREATE TABLE IF NOT EXISTS room_event_cursors (
    room_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    last_event_id INTEGER NOT NULL DEFAULT 0 CHECK (last_event_id >= 0),
    mcp_bootstrapped INTEGER NOT NULL DEFAULT 0
        CHECK (mcp_bootstrapped IN (0, 1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (room_id, player_id),
    FOREIGN KEY (room_id, player_id)
        REFERENCES room_participants(room_id, player_id) ON DELETE CASCADE
)
"""

ROOM_CONFIRMATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS room_confirmations (
    room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
    player_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('pending', 'accepted')),
    decided_at TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (room_id, player_id),
    FOREIGN KEY (room_id, player_id)
        REFERENCES room_participants(room_id, player_id) ON DELETE CASCADE
)
"""

NPC_DECISIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS npc_decisions (
    idempotency_key TEXT PRIMARY KEY,
    room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    npc_player_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('reserved', 'completed', 'failed')),
    decision_json TEXT,
    error_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (room_id, revision, npc_player_id),
    FOREIGN KEY (room_id, npc_player_id)
        REFERENCES room_participants(room_id, player_id) ON DELETE CASCADE
)
"""

NPC_SPEECH_STATES_SCHEMA = """
CREATE TABLE IF NOT EXISTS npc_speech_states (
    room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
    npc_player_id TEXT NOT NULL,
    silent_completed_turns INTEGER NOT NULL DEFAULT 0
        CHECK (silent_completed_turns >= 0),
    speech_pending INTEGER NOT NULL DEFAULT 0
        CHECK (speech_pending IN (0, 1)),
    active_turn_start_revision INTEGER,
    last_completed_revision INTEGER,
    last_attempt_revision INTEGER,
    last_attempt_status TEXT
        CHECK (last_attempt_status IN ('reserved', 'sent', 'failed', 'superseded')),
    speech_attempted_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (room_id, npc_player_id),
    FOREIGN KEY (room_id, npc_player_id)
        REFERENCES room_participants(room_id, player_id) ON DELETE CASCADE
)
"""

PARTICIPANT_KIND_TRIGGER_SCHEMA = """
CREATE TRIGGER IF NOT EXISTS trg_room_participants_infer_legacy_kind
AFTER INSERT ON room_participants
FOR EACH ROW
WHEN NEW.participant_kind IS NULL
  OR (NEW.role = 'human' AND NEW.participant_kind = 'bound_machine')
BEGIN
    UPDATE room_participants
    SET participant_kind = CASE
        WHEN NEW.role = 'human' THEN 'human'
        ELSE 'bound_machine'
    END
    WHERE room_id = NEW.room_id AND player_id = NEW.player_id;
END
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
                or "'pending'" not in sql
                or "last_move_at" not in columns
                or "stake" not in columns
                or "initiator_player_id" not in columns
                or "confirmation_expires_at" not in columns
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
        if "terminal_reason" not in room_columns:
            conn.execute("ALTER TABLE rooms ADD COLUMN terminal_reason TEXT")
        if "rematch_of_room_id" not in room_columns:
            conn.execute("ALTER TABLE rooms ADD COLUMN rematch_of_room_id TEXT")
        if "rematch_root_room_id" not in room_columns:
            conn.execute("ALTER TABLE rooms ADD COLUMN rematch_root_room_id TEXT")
        if "current_player_id" not in room_columns:
            conn.execute("ALTER TABLE rooms ADD COLUMN current_player_id TEXT")
        if "winner_player_id" not in room_columns:
            conn.execute("ALTER TABLE rooms ADD COLUMN winner_player_id TEXT")
        if "result_json" not in room_columns:
            conn.execute("ALTER TABLE rooms ADD COLUMN result_json TEXT")
        if "confirmation_required" not in room_columns:
            conn.execute(
                """
                ALTER TABLE rooms ADD COLUMN confirmation_required INTEGER
                NOT NULL DEFAULT 0 CHECK (confirmation_required IN (0, 1))
                """
            )
            conn.execute(
                """
                UPDATE rooms SET confirmation_required = 1
                WHERE stake > 0 OR status = 'pending'
                """
            )
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
        if "token" not in participant_columns:
            conn.execute("ALTER TABLE room_participants ADD COLUMN token TEXT")
        if "active" not in participant_columns:
            conn.execute(
                """
                ALTER TABLE room_participants ADD COLUMN active INTEGER NOT NULL
                DEFAULT 1 CHECK (active IN (0, 1))
                """
            )
        if "join_status" not in participant_columns:
            conn.execute(
                """
                ALTER TABLE room_participants ADD COLUMN join_status TEXT
                NOT NULL DEFAULT 'joined'
                CHECK (join_status IN ('invited', 'joined'))
                """
            )
        if "activity_state" not in participant_columns:
            conn.execute(
                """
                ALTER TABLE room_participants ADD COLUMN activity_state TEXT
                NOT NULL DEFAULT 'active'
                CHECK (activity_state IN ('active', 'inactive', 'eliminated', 'skipped'))
                """
            )
            conn.execute(
                """
                UPDATE room_participants
                SET activity_state = CASE WHEN active = 1 THEN 'active' ELSE 'inactive' END
                """
            )
        if "participant_kind" not in participant_columns:
            conn.execute(
                "ALTER TABLE room_participants ADD COLUMN participant_kind TEXT"
            )
            conn.execute(
                """
                UPDATE room_participants
                SET participant_kind = CASE
                    WHEN role = 'human' THEN 'human'
                    ELSE 'bound_machine'
                END
                WHERE participant_kind IS NULL OR participant_kind = ''
                """
            )
        if "npc_persona_id" not in participant_columns:
            conn.execute(
                "ALTER TABLE room_participants ADD COLUMN npc_persona_id TEXT"
            )
        participant_schema_row = conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'room_participants'
            """
        ).fetchone()
        participant_schema = (
            (participant_schema_row["sql"] or "") if participant_schema_row else ""
        )
        if "'left'" not in participant_schema:
            _migrate_participant_membership_schema(conn)
        _backfill_multiplayer_room_fields(conn)
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
            or "'leave'" not in message_schema
            or "'system'" not in message_schema
        ):
            _migrate_message_events(conn, message_columns)
        message_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(room_messages)")
        }
        if "move_payload" not in message_columns:
            conn.execute("ALTER TABLE room_messages ADD COLUMN move_payload TEXT")
        if "visible_to_json" not in message_columns:
            conn.execute("ALTER TABLE room_messages ADD COLUMN visible_to_json TEXT")
        conn.execute(ROOM_EVENT_CURSORS_SCHEMA)
        conn.execute(ROOM_CONFIRMATIONS_SCHEMA)
        conn.execute(NPC_DECISIONS_SCHEMA)
        conn.execute(NPC_SPEECH_STATES_SCHEMA)
        _repair_participant_child_foreign_keys(conn)
        cursor_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(room_event_cursors)")
        }
        if "mcp_bootstrapped" not in cursor_columns:
            conn.execute(
                """
                ALTER TABLE room_event_cursors
                ADD COLUMN mcp_bootstrapped INTEGER NOT NULL DEFAULT 0
                CHECK (mcp_bootstrapped IN (0, 1))
                """
            )
        # Compatibility for legacy direct inserts that only know role. New
        # framework writes participant_kind explicitly; this trigger prevents
        # a human row from inheriting the bound-machine column default.
        conn.execute(PARTICIPANT_KIND_TRIGGER_SCHEMA)
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_room_confirmations_pending
            ON room_confirmations(player_id, decision, room_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_rooms_pending_expiry
            ON rooms(status, confirmation_expires_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_npc_decisions_room_revision
            ON npc_decisions(room_id, revision, npc_player_id)
            """
        )
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
        # Achievement facts/rewards are additive and survive room deletion.
        # The strict legacy backfill runs as one transaction and is idempotent.
        from .achievements import (
            backfill_authoritative_matches,
            init_achievement_schema,
        )

        init_achievement_schema(conn)
        # Additive IOUs are independent of rooms and never infer old debts.
        from .loans import init_loans_schema

        init_loans_schema(conn)
        # Interaction exchanges are additive and store request terms only.
        from .exchanges import init_exchanges_schema

        init_exchanges_schema(conn)
        # The revision cursor must exist before backfills on upgrades where the
        # notification table is already present. Creating it alone cannot derive
        # historical unread rows on a first install.
        from .notifications import init_notification_subject_states_schema

        init_notification_subject_states_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            backfill_authoritative_matches(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        # Historical achievement backfill runs before the notification table
        # exists, so upgrading cannot manufacture fresh unread events.
        from .notifications import init_notifications_schema

        init_notifications_schema(conn)
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
    stake_expr = "COALESCE(stake, 0)" if "stake" in columns else "0"
    initiator_expr = (
        "initiator_player_id" if "initiator_player_id" in columns else "NULL"
    )
    confirmation_expiry_expr = (
        "confirmation_expires_at"
        if "confirmation_expires_at" in columns
        else "NULL"
    )
    confirmation_required_expr = (
        "COALESCE(confirmation_required, 0)"
        if "confirmation_required" in columns
        else "CASE WHEN status = 'pending' OR " + stake_expr + " > 0 THEN 1 ELSE 0 END"
    )
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
                current_player_id, status, winner, winner_player_id,
                result_json, stake, initiator_player_id,
                confirmation_required, confirmation_expires_at, preserved, terminal_at,
                created_at, updated_at, last_move_at
            )
            SELECT
                room_id, game_type, mode, board_state, turn, revision,
                NULL, status, winner, NULL, NULL, {stake_expr}, {initiator_expr},
                {confirmation_required_expr}, {confirmation_expiry_expr},
                {preserved_expr}, {terminal_expr},
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
                    room_id, player_id, display_name, role, participant_kind,
                    npc_persona_id,
                    seat_index, token, join_status, activity_state,
                    active, joined_at
                )
                SELECT room_id, player_id, {display_name_expr}, role,
                       {"participant_kind" if "participant_kind" in participant_columns else "CASE WHEN role = 'human' THEN 'human' ELSE 'bound_machine' END"},
                       {"npc_persona_id" if "npc_persona_id" in participant_columns else "NULL"},
                       seat_index,
                       {"token" if "token" in participant_columns else "NULL"},
                       {"join_status" if "join_status" in participant_columns else "'joined'"},
                       {"activity_state" if "activity_state" in participant_columns else "CASE WHEN active = 1 THEN 'active' ELSE 'inactive' END" if "active" in participant_columns else "'active'"},
                       {"active" if "active" in participant_columns else "1"},
                       joined_at
                FROM room_participants_legacy
                """
            )
        elif has_legacy_players:
            conn.execute(
                """
                INSERT INTO room_participants (
                    room_id, player_id, display_name, role, participant_kind,
                    npc_persona_id,
                    seat_index, token, join_status, activity_state,
                    active, joined_at
                )
                SELECT room_id, human_player_id, human_player_id,
                       'human', 'human', NULL, 0, NULL, 'joined', 'active', 1, created_at
                FROM rooms_legacy
                WHERE human_player_id IS NOT NULL
                """
            )
            conn.execute(
                """
                INSERT INTO room_participants (
                    room_id, player_id, display_name, role, participant_kind,
                    npc_persona_id,
                    seat_index, token, join_status, activity_state,
                    active, joined_at
                )
                SELECT room_id, ai_player_id, ai_player_id,
                       'ai', 'bound_machine', NULL, 1, NULL, 'joined', 'active', 1, created_at
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
        _backfill_multiplayer_room_fields(conn)
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
    move_payload_expr = "move_payload" if "move_payload" in columns else "NULL"
    visible_to_expr = "visible_to_json" if "visible_to_json" in columns else "NULL"
    conn.execute(ROOM_MESSAGES_SCHEMA)
    conn.execute(
        f"""
        INSERT INTO room_messages (
            id, room_id, sender, sender_player_id, text,
            revision_at_send, created_at, event_type, move_label,
            move_payload, visible_to_json
        )
        SELECT
            id, room_id, sender, COALESCE({sender_player_expr}, sender), text,
            revision_at_send, created_at, event_type, move_label,
            {move_payload_expr}, {visible_to_expr}
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


def _migrate_participant_membership_schema(conn: sqlite3.Connection) -> None:
    """Add the historical ``left`` membership state without losing seats."""
    columns = [
        row["name"] for row in conn.execute("PRAGMA table_info(room_participants)")
    ]
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "ALTER TABLE room_participants RENAME TO room_participants_legacy"
        )
        conn.execute(ROOM_PARTICIPANTS_SCHEMA)
        quoted = ", ".join(columns)
        conn.execute(
            f"INSERT INTO room_participants ({quoted}) "
            f"SELECT {quoted} FROM room_participants_legacy"
        )
        conn.execute("DROP TABLE room_participants_legacy")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _repair_participant_child_foreign_keys(conn: sqlite3.Connection) -> None:
    """Repair child tables whose FK target was rewritten by an older table rename.

    SQLite may rewrite REFERENCES room_participants to room_participants_legacy
    when a migration renames the participant table. If the legacy table is then
    dropped, inserts into the child table fail at startup. Rebuild only the
    affected child tables and preserve their rows.
    """
    specs = (
        ("room_event_cursors", ROOM_EVENT_CURSORS_SCHEMA),
        ("room_confirmations", ROOM_CONFIRMATIONS_SCHEMA),
    )
    for table_name, create_sql in specs:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if not exists:
            continue
        targets = {row["table"] for row in conn.execute(f"PRAGMA foreign_key_list({table_name})")}
        if not targets or "room_participants" in targets:
            continue
        legacy_name = f"{table_name}_fk_legacy"
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(f"ALTER TABLE {table_name} RENAME TO {legacy_name}")
            conn.execute(create_sql)
            columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({legacy_name})")]
            quoted = ", ".join(columns)
            conn.execute(
                f"INSERT INTO {table_name} ({quoted}) SELECT {quoted} FROM {legacy_name}"
            )
            conn.execute(f"DROP TABLE {legacy_name}")
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
                AND existing.move_payload IS NULL
          )
        """
    )


def _backfill_multiplayer_room_fields(conn: sqlite3.Connection) -> None:
    """Safely derive additive multiplayer fields from legacy two-seat data."""
    conn.execute(
        """
        UPDATE room_participants
        SET token = CASE
            WHEN seat_index = 0 THEN CASE
                WHEN role = 'human' AND room_id IN (
                    SELECT room_id FROM rooms WHERE mode = 'human_first'
                ) THEN 'X'
                WHEN role = 'ai' AND room_id IN (
                    SELECT room_id FROM rooms WHERE mode = 'ai_first'
                ) THEN 'X'
                ELSE 'O'
            END
            WHEN seat_index = 1 THEN CASE
                WHEN role = 'ai' AND room_id IN (
                    SELECT room_id FROM rooms WHERE mode = 'human_first'
                ) THEN 'O'
                WHEN role = 'human' AND room_id IN (
                    SELECT room_id FROM rooms WHERE mode = 'ai_first'
                ) THEN 'O'
                ELSE 'X'
            END
            ELSE 'P' || CAST(seat_index + 1 AS TEXT)
        END
        WHERE token IS NULL OR token = ''
        """
    )
    conn.execute(
        """
        UPDATE rooms
        SET current_player_id = (
            SELECT participant.player_id
            FROM room_participants AS participant
            WHERE participant.room_id = rooms.room_id
              AND participant.role = rooms.turn
              AND participant.active = 1
            ORDER BY participant.seat_index
            LIMIT 1
        )
        WHERE current_player_id IS NULL
          AND status IN ('pending', 'waiting', 'playing')
        """
    )
    conn.execute(
        """
        UPDATE rooms
        SET winner_player_id = (
            SELECT participant.player_id
            FROM room_participants AS participant
            WHERE participant.room_id = rooms.room_id
              AND participant.role = rooms.winner
            ORDER BY participant.seat_index
            LIMIT 1
        )
        WHERE winner_player_id IS NULL
          AND winner IN ('human', 'ai')
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
            SELECT player_id, display_name, role, participant_kind,
                   npc_persona_id, seat_index, token,
                   join_status, activity_state, active, joined_at
            FROM room_participants
            WHERE room_id = ?
            ORDER BY seat_index, joined_at, player_id
            """,
            (room["room_id"],),
        ).fetchall()
        confirmation_rows = conn.execute(
            """
            SELECT player_id, decision, decided_at, created_at
            FROM room_confirmations
            WHERE room_id = ?
            ORDER BY created_at, player_id
            """,
            (room["room_id"],),
        ).fetchall()
    finally:
        if owns_connection:
            conn.close()
    room["participants"] = [dict(participant) for participant in participant_rows]
    decisions = {
        item["player_id"]: item["decision"] for item in confirmation_rows
    }
    for participant in room["participants"]:
        participant["active"] = bool(participant.get("active", True))
        participant["seat"] = participant["seat_index"]
        participant["order"] = participant["seat_index"]
        participant["confirmation_status"] = decisions.get(
            participant["player_id"], "not_required"
        )
    room["confirmations"] = [dict(item) for item in confirmation_rows]
    room["confirmation_required"] = bool(room.get("confirmation_required", False))
    room["pending_for"] = [
        item["player_id"]
        for item in room["confirmations"]
        if item["decision"] == "pending"
    ]
    raw_result = room.get("result_json")
    room["result"] = json.loads(raw_result) if raw_result else None
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
