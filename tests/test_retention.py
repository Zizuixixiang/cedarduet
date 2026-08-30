import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database, framework
from app import main as main_module


FIXED_NOW = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)
LEGACY_GRACE_DEADLINE = framework.TERMINAL_LEGACY_GRACE_DEADLINE


class FrozenDateTime(datetime):
    current = FIXED_NOW

    @classmethod
    def now(cls, tz=None):
        value = cls.current
        return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)


class RetentionFrameworkTests(unittest.TestCase):
    def setUp(self):
        FrozenDateTime.current = FIXED_NOW
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-retention-")
        self.addCleanup(self.temporary.cleanup)
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.datetime_patch = patch.object(framework, "datetime", FrozenDateTime)
        self.datetime_patch.start()
        self.addCleanup(self.datetime_patch.stop)
        database.init_db()

    def terminal_room(self, suffix: str) -> dict:
        room = framework.create_room(
            "tictactoe",
            "human_first",
            "human",
            f"human-{suffix}",
            opponent_id=f"ai-{suffix}",
        )
        return framework.resign(
            room["room_id"], "human", f"human-{suffix}"
        )

    def set_terminal_at(self, room_id: str, value: datetime) -> None:
        with database.write_transaction() as conn:
            conn.execute(
                "UPDATE rooms SET terminal_at = ? WHERE room_id = ?",
                (value.isoformat(timespec="seconds"), room_id),
            )

    def room_exists(self, room_id: str) -> bool:
        with database.connect() as conn:
            return conn.execute(
                "SELECT 1 FROM rooms WHERE room_id = ?", (room_id,)
            ).fetchone() is not None

    def test_old_schema_migration_defaults_to_unpreserved(self):
        database.DB_PATH.unlink()
        conn = sqlite3.connect(database.DB_PATH)
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE rooms (
                room_id TEXT PRIMARY KEY,
                game_type TEXT NOT NULL,
                mode TEXT NOT NULL,
                board_state TEXT NOT NULL,
                turn TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                winner TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_move_at TEXT NOT NULL
            );
            CREATE TABLE room_participants (
                room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
                player_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL,
                seat_index INTEGER NOT NULL,
                joined_at TEXT NOT NULL,
                PRIMARY KEY (room_id, player_id),
                UNIQUE (room_id, seat_index)
            );
            INSERT INTO rooms VALUES (
                'OLDROOM1', 'tictactoe', 'human_first', '{}', 'human', 3,
                'finished', 'ai',
                '2026-08-01T00:00:00+00:00',
                '2026-08-02T00:00:00+00:00',
                '2026-08-02T00:00:00+00:00'
            );
            INSERT INTO room_participants VALUES (
                'OLDROOM1', 'human-old', '旧玩家', 'human', 0,
                '2026-08-01T00:00:00+00:00'
            );
            INSERT INTO room_participants VALUES (
                'OLDROOM1', 'ai-old', '旧小机', 'ai', 1,
                '2026-08-01T00:00:00+00:00'
            );
            """
        )
        conn.close()

        database.init_db()

        with database.connect() as migrated:
            columns = {
                row["name"] for row in migrated.execute("PRAGMA table_info(rooms)")
            }
            room = migrated.execute(
                "SELECT preserved, terminal_at FROM rooms WHERE room_id = 'OLDROOM1'"
            ).fetchone()
        self.assertIn("preserved", columns)
        self.assertIn("terminal_at", columns)
        self.assertEqual(room["preserved"], 0)
        self.assertEqual(room["terminal_at"], "2026-08-02T00:00:00+00:00")

        with patch.object(framework, "datetime", FrozenDateTime):
            rooms = framework.list_human_rooms("human-old")
        self.assertEqual([item["room_id"] for item in rooms], ["OLDROOM1"])
        self.assertEqual(
            rooms[0]["auto_delete_at"],
            LEGACY_GRACE_DEADLINE.isoformat(timespec="seconds"),
        )
        self.assertTrue(self.room_exists("OLDROOM1"))

    def test_reinitialization_does_not_rewrite_existing_terminal_at(self):
        room = self.terminal_room("terminal-stable")
        original_terminal_at = datetime(
            2026, 8, 12, 3, 4, 5, tzinfo=timezone.utc
        )
        self.set_terminal_at(room["room_id"], original_terminal_at)

        database.init_db()

        with database.connect() as conn:
            stored_terminal_at = conn.execute(
                "SELECT terminal_at FROM rooms WHERE room_id = ?",
                (room["room_id"],),
            ).fetchone()["terminal_at"]
        self.assertEqual(
            stored_terminal_at,
            original_terminal_at.isoformat(timespec="seconds"),
        )

    def test_old_unpreserved_room_uses_grace_deadline_and_keeps_terminal_at(self):
        room = self.terminal_room("legacy")
        original_terminal_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        self.set_terminal_at(room["room_id"], original_terminal_at)

        FrozenDateTime.current = (
            LEGACY_GRACE_DEADLINE.astimezone(timezone.utc)
            - timedelta(seconds=1)
        )
        with patch.object(framework, "datetime", FrozenDateTime):
            listed = framework.list_human_rooms("human-legacy")

        self.assertEqual([item["room_id"] for item in listed], [room["room_id"]])
        self.assertEqual(
            listed[0]["terminal_at"],
            original_terminal_at.isoformat(timespec="seconds"),
        )
        self.assertEqual(
            listed[0]["auto_delete_at"],
            LEGACY_GRACE_DEADLINE.isoformat(timespec="seconds"),
        )

        FrozenDateTime.current = LEGACY_GRACE_DEADLINE.astimezone(timezone.utc)
        with patch.object(framework, "datetime", FrozenDateTime):
            self.assertEqual(framework.list_human_rooms("human-legacy"), [])
        self.assertFalse(self.room_exists(room["room_id"]))

    def test_old_preserved_room_survives_after_grace_deadline(self):
        room = self.terminal_room("legacy-kept")
        original_terminal_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        self.set_terminal_at(room["room_id"], original_terminal_at)
        framework.set_room_preserved(room["room_id"], "human-legacy-kept", True)

        FrozenDateTime.current = (
            LEGACY_GRACE_DEADLINE.astimezone(timezone.utc)
            + timedelta(days=30)
        )
        with patch.object(framework, "datetime", FrozenDateTime):
            listed = framework.list_human_rooms("human-legacy-kept")

        self.assertEqual([item["room_id"] for item in listed], [room["room_id"]])
        self.assertTrue(listed[0]["preserved"])
        self.assertIsNone(listed[0]["auto_delete_at"])
        self.assertEqual(
            listed[0]["terminal_at"],
            original_terminal_at.isoformat(timespec="seconds"),
        )

    def test_new_finished_room_uses_its_own_seven_day_deadline(self):
        new_terminal_at = (
            framework.TERMINAL_RETENTION_ROLLOUT_CUTOFF.astimezone(timezone.utc)
            + timedelta(hours=12)
        )
        FrozenDateTime.current = new_terminal_at
        with patch.object(framework, "datetime", FrozenDateTime):
            room = self.terminal_room("new-terminal")

        expected_deadline = new_terminal_at + timedelta(days=7)
        self.assertEqual(
            room["terminal_at"], new_terminal_at.isoformat(timespec="seconds")
        )
        self.assertEqual(
            room["auto_delete_at"], expected_deadline.isoformat(timespec="seconds")
        )

        FrozenDateTime.current = expected_deadline - timedelta(seconds=1)
        with patch.object(framework, "datetime", FrozenDateTime):
            fetched = framework.get_room(room["room_id"])
        self.assertEqual(fetched["status"], "finished")

        FrozenDateTime.current = expected_deadline
        with patch.object(framework, "datetime", FrozenDateTime):
            self.assertEqual(framework.list_human_rooms("human-new-terminal"), [])
        self.assertFalse(self.room_exists(room["room_id"]))

    def test_rollout_cutoff_is_new_room_boundary(self):
        just_before = (
            framework.TERMINAL_RETENTION_ROLLOUT_CUTOFF
            - timedelta(seconds=1)
        )
        at_cutoff = framework.TERMINAL_RETENTION_ROLLOUT_CUTOFF
        self.assertTrue(framework.TERMINAL_AUTO_DELETE_ENABLED)
        self.assertEqual(
            at_cutoff.isoformat(timespec="seconds"),
            "2026-08-31T00:00:00+08:00",
        )
        self.assertEqual(
            LEGACY_GRACE_DEADLINE.isoformat(timespec="seconds"),
            "2026-09-07T00:00:00+08:00",
        )
        self.assertEqual(
            framework._terminal_auto_delete_at(just_before.isoformat()),
            LEGACY_GRACE_DEADLINE,
        )
        self.assertEqual(
            framework._terminal_auto_delete_at(at_cutoff.isoformat()),
            at_cutoff + timedelta(days=7),
        )
        self.assertEqual(
            framework._terminal_auto_delete_at(
                just_before.astimezone(timezone.utc).isoformat()
            ),
            LEGACY_GRACE_DEADLINE,
        )
        self.assertEqual(
            framework._terminal_auto_delete_at(
                at_cutoff.astimezone(timezone.utc).isoformat()
            ),
            at_cutoff.astimezone(timezone.utc) + timedelta(days=7),
        )

    def test_cancelled_preservation_restores_legacy_deadline(self):
        room = self.terminal_room("kept")
        original_terminal_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        self.set_terminal_at(room["room_id"], original_terminal_at)
        framework.set_room_preserved(room["room_id"], "human-kept", True)

        FrozenDateTime.current = (
            LEGACY_GRACE_DEADLINE.astimezone(timezone.utc)
            - timedelta(days=1)
        )
        with patch.object(framework, "datetime", FrozenDateTime):
            cancelled = framework.set_room_preserved(
                room["room_id"], "human-kept", False
            )

        self.assertFalse(cancelled["preserved"])
        self.assertEqual(
            cancelled["auto_delete_at"],
            LEGACY_GRACE_DEADLINE.isoformat(timespec="seconds"),
        )
        self.assertEqual(
            cancelled["terminal_at"],
            original_terminal_at.isoformat(timespec="seconds"),
        )
        self.assertTrue(self.room_exists(room["room_id"]))

        FrozenDateTime.current = LEGACY_GRACE_DEADLINE.astimezone(timezone.utc)
        with patch.object(framework, "datetime", FrozenDateTime):
            self.assertEqual(framework.list_human_rooms("human-kept"), [])
        self.assertFalse(self.room_exists(room["room_id"]))

    def test_old_archived_room_uses_same_grace_boundary(self):
        room = framework.create_room(
            "gomoku", "human_first", "human", "human-archive",
            opponent_id="ai-archive",
        )
        with database.write_transaction() as conn:
            conn.execute(
                "UPDATE rooms SET last_move_at = ? WHERE room_id = ?",
                (
                    (FIXED_NOW - timedelta(days=8)).isoformat(timespec="seconds"),
                    room["room_id"],
                ),
            )

        FrozenDateTime.current = FIXED_NOW
        with patch.object(framework, "datetime", FrozenDateTime):
            archived = framework.get_room(
                room["room_id"], "human", "human-archive"
            )
        self.assertEqual(archived["status"], "archived")
        self.assertEqual(
            archived["terminal_at"], FIXED_NOW.isoformat(timespec="seconds")
        )
        self.assertEqual(
            archived["auto_delete_at"],
            LEGACY_GRACE_DEADLINE.isoformat(timespec="seconds"),
        )

        FrozenDateTime.current = (
            LEGACY_GRACE_DEADLINE.astimezone(timezone.utc)
            - timedelta(seconds=1)
        )
        with patch.object(framework, "datetime", FrozenDateTime):
            self.assertEqual(
                framework.get_room(room["room_id"])["status"], "archived"
            )
        FrozenDateTime.current = LEGACY_GRACE_DEADLINE.astimezone(timezone.utc)
        with patch.object(framework, "datetime", FrozenDateTime):
            self.assertEqual(framework.list_human_rooms("human-archive"), [])
        self.assertFalse(self.room_exists(room["room_id"]))


class RetentionApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-retention-api-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        FrozenDateTime.current = FIXED_NOW
        self.datetime_patch = patch.object(framework, "datetime", FrozenDateTime)
        self.datetime_patch.start()
        database.init_db()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_module.app),
            base_url="http://duel.test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.datetime_patch.stop()
        self.db_patch.stop()
        self.temporary.cleanup()

    @staticmethod
    def headers(player_id: str) -> dict[str, str]:
        return {"X-Duel-Human-Player": player_id}

    def new_room(self, suffix: str, *, terminal: bool = True) -> dict:
        room = framework.create_room(
            "tictactoe", "human_first", "human", f"human-{suffix}",
            opponent_id=f"ai-{suffix}",
        )
        if terminal:
            room = framework.resign(room["room_id"], "human", f"human-{suffix}")
        return room

    async def test_retention_requires_trusted_owner_and_terminal_room(self):
        terminal = self.new_room("owner")
        active = self.new_room("active", terminal=False)

        missing_identity = await self.client.post(
            f"/api/rooms/{terminal['room_id']}/retention",
            json={"preserved": True},
        )
        self.assertEqual(missing_identity.status_code, 403)

        wrong_owner = await self.client.post(
            f"/api/rooms/{terminal['room_id']}/retention",
            headers=self.headers("human-other"),
            json={"player_id": "human-owner", "preserved": True},
        )
        self.assertEqual(wrong_owner.status_code, 403)

        active_rejected = await self.client.post(
            f"/api/rooms/{active['room_id']}/retention",
            headers=self.headers("human-active"),
            json={"preserved": True},
        )
        self.assertEqual(active_rejected.status_code, 409)

        preserved = await self.client.post(
            f"/api/rooms/{terminal['room_id']}/retention",
            headers=self.headers("human-owner"),
            json={"player_id": "human-owner", "preserved": True},
        )
        self.assertEqual(preserved.status_code, 200, preserved.text)
        self.assertTrue(preserved.json()["room"]["preserved"])
        self.assertIsNone(preserved.json()["room"]["auto_delete_at"])

        cancelled = await self.client.post(
            f"/api/rooms/{terminal['room_id']}/retention",
            headers=self.headers("human-owner"),
            json={"preserved": False},
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertFalse(cancelled.json()["room"]["preserved"])
        self.assertEqual(
            cancelled.json()["room"]["auto_delete_at"],
            LEGACY_GRACE_DEADLINE.isoformat(timespec="seconds"),
        )
        self.assertIn("已恢复自动删除", cancelled.json()["message"])

    async def test_manual_delete_checks_owner_status_and_cascades(self):
        terminal = self.new_room("delete")
        active = self.new_room("live", terminal=False)
        terminal_id = terminal["room_id"]
        with database.connect() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            before = {
                table: conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE room_id = ?", (terminal_id,)
                ).fetchone()[0]
                for table in (
                    "room_participants", "room_messages", "room_event_cursors"
                )
            }
        self.assertTrue(all(count > 0 for count in before.values()))

        wrong_owner = await self.client.post(
            f"/api/rooms/{terminal_id}/delete",
            headers=self.headers("human-other"),
            json={},
        )
        self.assertEqual(wrong_owner.status_code, 403)

        active_rejected = await self.client.post(
            f"/api/rooms/{active['room_id']}/delete",
            headers=self.headers("human-live"),
            json={},
        )
        self.assertEqual(active_rejected.status_code, 409)

        deleted = await self.client.post(
            f"/api/rooms/{terminal_id}/delete",
            headers=self.headers("human-delete"),
            json={"player_id": "human-delete"},
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["room_id"], terminal_id)

        with database.connect() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM rooms WHERE room_id = ?", (terminal_id,)
                ).fetchone()[0],
                0,
            )
            for table in (
                "room_participants", "room_messages", "room_event_cursors"
            ):
                self.assertEqual(
                    conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE room_id = ?",
                        (terminal_id,),
                    ).fetchone()[0],
                    0,
                )


if __name__ == "__main__":
    unittest.main()
