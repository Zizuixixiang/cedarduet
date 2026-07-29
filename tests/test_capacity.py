import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database, framework
from app import main as main_module


class CapacityFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-capacity-")
        self.addCleanup(self.temporary.cleanup)
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()

    def test_same_pair_has_at_most_three_active_rooms(self):
        for _ in range(3):
            framework.create_room(
                "tictactoe",
                "human_first",
                "ai",
                "ai-pair",
                opponent_id="human-pair",
            )
        with self.assertRaisesRegex(framework.DuelError, "已有 3 个活跃房间"):
            framework.create_room(
                "tictactoe",
                "human_first",
                "ai",
                "ai-pair",
                opponent_id="human-pair",
            )

    def test_join_can_fill_third_pair_room_but_not_fourth(self):
        for _ in range(2):
            framework.create_room(
                "tictactoe",
                "human_first",
                "ai",
                "ai-join-pair",
                opponent_id="human-join-pair",
            )
        third = framework.create_room(
            "tictactoe", "human_first", "ai", "ai-join-pair"
        )
        joined = framework.join_room(
            third["room_id"], "human", "human-join-pair"
        )
        self.assertEqual(joined["status"], "playing")

        fourth = framework.create_room(
            "tictactoe", "human_first", "ai", "ai-join-pair"
        )
        with self.assertRaisesRegex(framework.DuelError, "已有 3 个活跃房间"):
            framework.join_room(
                fourth["room_id"], "human", "human-join-pair"
            )

    def test_global_active_room_limit(self):
        with patch.object(framework, "GLOBAL_ACTIVE_ROOM_LIMIT", 2):
            framework.create_room("tictactoe", "human_first", "ai", "ai-one")
            framework.create_room("tictactoe", "human_first", "ai", "ai-two")
            with self.assertRaisesRegex(framework.DuelError, "容量已满"):
                framework.create_room(
                    "tictactoe", "human_first", "ai", "ai-three"
                )

    def test_stale_active_room_is_archived_as_draw_on_read(self):
        room = framework.create_room(
            "gomoku",
            "human_first",
            "human",
            "human-stale",
            opponent_id="ai-stale",
        )
        stale_at = (
            datetime.now(timezone.utc) - timedelta(days=8)
        ).isoformat(timespec="seconds")
        with database.write_transaction() as conn:
            conn.execute(
                "UPDATE rooms SET last_move_at = ? WHERE room_id = ?",
                (stale_at, room["room_id"]),
            )

        archived = framework.get_room(
            room["room_id"], "human", "human-stale"
        )
        self.assertEqual(archived["status"], "archived")
        self.assertEqual(archived["winner"], "draw")
        self.assertEqual(archived["revision"], room["revision"] + 1)

    def test_stale_room_is_archived_before_global_capacity_check(self):
        with patch.object(framework, "GLOBAL_ACTIVE_ROOM_LIMIT", 1):
            stale = framework.create_room(
                "tictactoe", "human_first", "ai", "ai-old"
            )
            stale_at = (
                datetime.now(timezone.utc) - timedelta(days=8)
            ).isoformat(timespec="seconds")
            with database.write_transaction() as conn:
                conn.execute(
                    "UPDATE rooms SET last_move_at = ? WHERE room_id = ?",
                    (stale_at, stale["room_id"]),
                )
            fresh = framework.create_room(
                "tictactoe", "human_first", "ai", "ai-new"
            )
        self.assertEqual(fresh["status"], "waiting")
        self.assertEqual(
            framework.get_room(stale["room_id"])["status"], "archived"
        )

    def test_phase_one_schema_migrates_to_archived_and_last_move(self):
        database.DB_PATH.unlink()
        conn = sqlite3.connect(database.DB_PATH)
        conn.execute(
            """
            CREATE TABLE rooms (
                room_id TEXT PRIMARY KEY,
                game_type TEXT NOT NULL,
                mode TEXT NOT NULL,
                board_state TEXT NOT NULL,
                turn TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL CHECK (
                    status IN ('waiting', 'playing', 'finished')
                ),
                winner TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                human_player_id TEXT,
                ai_player_id TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO rooms (
                room_id, game_type, mode, board_state, turn, revision,
                status, winner, created_at, updated_at,
                human_player_id, ai_player_id
            ) VALUES (
                'MIGRATE1', 'tictactoe', 'human_first',
                '{}',
                'human', 0, 'playing', NULL,
                '2026-07-01T00:00:00+00:00',
                '2026-07-01T00:00:00+00:00',
                'human-old', 'ai-old'
            )
            """
        )
        conn.commit()
        conn.close()

        database.init_db()

        conn = sqlite3.connect(database.DB_PATH)
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(rooms)").fetchall()
        }
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'rooms'"
        ).fetchone()[0]
        participants = conn.execute(
            """
            SELECT player_id, role, seat_index
            FROM room_participants
            WHERE room_id = 'MIGRATE1'
            ORDER BY seat_index
            """
        ).fetchall()
        conn.close()
        self.assertIn("last_move_at", columns)
        self.assertIn("'archived'", sql)
        self.assertNotIn("human_player_id", columns)
        self.assertNotIn("ai_player_id", columns)
        self.assertEqual(
            participants,
            [("human-old", "human", 0), ("ai-old", "ai", 1)],
        )

    def test_current_schema_migrates_messages_with_participants(self):
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
                status TEXT NOT NULL CHECK (
                    status IN ('waiting', 'playing', 'finished', 'archived')
                ),
                winner TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_move_at TEXT NOT NULL,
                human_player_id TEXT,
                ai_player_id TEXT
            );
            CREATE TABLE room_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
                sender TEXT NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                revision_at_send INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL DEFAULT 'message',
                move_label TEXT,
                read_by_ai INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO rooms VALUES (
                'MIGRATE2', 'tictactoe', 'human_first',
                '{}',
                'human', 1, 'playing', NULL,
                '2026-07-02T00:00:00+00:00',
                '2026-07-02T00:00:00+00:00',
                '2026-07-02T00:00:00+00:00',
                'human-two', 'ai-two'
            );
            INSERT INTO room_messages (
                room_id, sender, text, revision_at_send, created_at,
                event_type, move_label, read_by_ai
            ) VALUES (
                'MIGRATE2', 'human', '还在吗', 1,
                '2026-07-02T00:00:01+00:00', 'message', NULL, 0
            );
            """
        )
        conn.close()

        database.init_db()

        with sqlite3.connect(database.DB_PATH) as migrated:
            message = migrated.execute(
                """
                SELECT room_id, sender, sender_player_id, text
                FROM room_messages
                """
            ).fetchone()
            participants = migrated.execute(
                """
                SELECT player_id, role FROM room_participants
                WHERE room_id = 'MIGRATE2' ORDER BY seat_index
                """
            ).fetchall()
            cursors = migrated.execute(
                """
                SELECT player_id, last_event_id
                FROM room_event_cursors
                WHERE room_id = 'MIGRATE2'
                ORDER BY player_id
                """
            ).fetchall()
        self.assertEqual(
            message, ("MIGRATE2", "human", "human-two", "还在吗")
        )
        self.assertEqual(
            participants, [("human-two", "human"), ("ai-two", "ai")]
        )
        self.assertEqual(cursors, [("ai-two", 0), ("human-two", 1)])


class WaitCapacityApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-wait-capacity-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()
        self.original_events = main_module.revision_events
        main_module.revision_events = main_module.RevisionEvents(max_waiters=1)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_module.app),
            base_url="http://duel.test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        main_module.revision_events = self.original_events
        self.db_patch.stop()
        self.temporary.cleanup()

    async def _new_paired_room(self, ai_id: str, human_id: str) -> str:
        response = await self.client.post(
            "/mcp/play",
            json={
                "action": "new",
                "player_id": ai_id,
                "opponent_id": human_id,
                "game_type": "tictactoe",
                "mode": "ai_first",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["room"]["room_id"]

    async def test_wait_over_capacity_is_downgraded_without_hanging(self):
        self.assertEqual(main_module.MAX_CONCURRENT_WAITS, 20)
        first_room = await self._new_paired_room("ai-wait-1", "human-wait-1")
        first_waiter = asyncio.create_task(
            self.client.post(
                "/mcp/play",
                json={
                    "action": "move",
                    "player_id": "ai-wait-1",
                    "room_id": first_room,
                    "move": {"row": 0, "col": 0},
                    "wait": True,
                },
            )
        )
        await asyncio.sleep(0.05)
        self.assertFalse(first_waiter.done())
        self.assertEqual(main_module.revision_events.waiting_count, 1)

        second_room = await self._new_paired_room("ai-wait-2", "human-wait-2")
        downgraded = await asyncio.wait_for(
            self.client.post(
                "/mcp/play",
                json={
                    "action": "move",
                    "player_id": "ai-wait-2",
                    "room_id": second_room,
                    "move": {"row": 0, "col": 0},
                    "wait": True,
                },
            ),
            timeout=1,
        )
        payload = downgraded.json()
        self.assertEqual(downgraded.status_code, 200)
        self.assertTrue(payload["wait_downgraded"])
        self.assertEqual(payload["status"], "ok")

        human_move = await self.client.post(
            f"/api/rooms/{first_room}/move",
            json={"player_id": "human-wait-1", "row": 1, "col": 0},
        )
        self.assertEqual(human_move.status_code, 200)
        resumed = await asyncio.wait_for(first_waiter, timeout=1)
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(main_module.revision_events.waiting_count, 0)

    async def test_new_returns_friendly_pair_limit_error(self):
        for _ in range(3):
            response = await self.client.post(
                "/mcp/play",
                json={
                    "action": "new",
                    "player_id": "ai-pair-api",
                    "opponent_id": "human-pair-api",
                    "game_type": "gomoku",
                    "mode": "human_first",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
        rejected = await self.client.post(
            "/mcp/play",
            json={
                "action": "new",
                "player_id": "ai-pair-api",
                "opponent_id": "human-pair-api",
                "game_type": "gomoku",
                "mode": "human_first",
            },
        )
        payload = rejected.json()
        self.assertEqual(rejected.status_code, 409)
        self.assertFalse(payload["ok"])
        self.assertIn("已有 3 个活跃房间", payload["message"])


if __name__ == "__main__":
    unittest.main()
