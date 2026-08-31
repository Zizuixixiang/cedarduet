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

    @staticmethod
    def _participants(*entries: tuple[str, str]) -> list[dict]:
        return [
            {
                "player_id": player_id,
                "role": "human" if kind == "human" else "ai",
                "participant_kind": kind,
            }
            for player_id, kind in entries
        ]

    def test_same_pair_has_at_most_ten_active_rooms(self):
        for _ in range(10):
            framework.create_room(
                "tictactoe",
                "human_first",
                "ai",
                "ai-pair",
                opponent_id="human-pair",
            )
        with self.assertRaisesRegex(framework.DuelError, "已有 10 个活跃房间"):
            framework.create_room(
                "tictactoe",
                "human_first",
                "ai",
                "ai-pair",
                opponent_id="human-pair",
            )

    def test_join_can_fill_tenth_pair_room_but_not_eleventh(self):
        for _ in range(9):
            framework.create_room(
                "tictactoe",
                "human_first",
                "ai",
                "ai-join-pair",
                opponent_id="human-join-pair",
            )
        tenth = framework.create_room(
            "tictactoe", "human_first", "ai", "ai-join-pair"
        )
        joined = framework.join_room(
            tenth["room_id"], "human", "human-join-pair"
        )
        self.assertEqual(joined["status"], "playing")
        self.assertEqual(joined["current_player_id"], "human-join-pair")
        self.assertEqual(
            next(
                item["token"] for item in joined["participants"]
                if item["player_id"] == "human-join-pair"
            ),
            "X",
        )

        eleventh = framework.create_room(
            "tictactoe", "human_first", "ai", "ai-join-pair"
        )
        with self.assertRaisesRegex(framework.DuelError, "已有 10 个活跃房间"):
            framework.join_room(
                eleventh["room_id"], "human", "human-join-pair"
            )

    def test_ten_pair_rooms_still_allow_creating_and_joining_multiplayer_rooms(self):
        human_id = "human-multiplayer-after-limit"
        ai_id = "ai-multiplayer-after-limit"
        for _ in range(10):
            framework.create_room(
                "tictactoe", "human_first", "human", human_id, ai_id
            )

        created = framework.create_room(
            "doudizhu",
            "human_first",
            "human",
            human_id,
            ordered_participants=self._participants(
                (human_id, "human"),
                (ai_id, "bound_machine"),
                ("human-created-third", "human"),
            ),
        )
        self.assertEqual(created["status"], "playing")

        waiting = framework.create_room(
            "doudizhu", "human_first", "ai", ai_id
        )
        waiting = framework.join_room(waiting["room_id"], "human", human_id)
        self.assertEqual(waiting["status"], "waiting")
        joined = framework.join_room(
            waiting["room_id"], "ai", "ai-joined-third"
        )
        self.assertEqual(joined["status"], "playing")
        self.assertEqual(joined["participant_count"], 3)

    def test_multiplayer_room_does_not_accumulate_pair_limit(self):
        human_id = "human-multiplayer-quota"
        ai_id = "ai-multiplayer-quota"
        with patch.object(framework, "PAIR_ACTIVE_ROOM_LIMIT", 1):
            framework.create_room(
                "doudizhu",
                "human_first",
                "human",
                human_id,
                ordered_participants=self._participants(
                    (human_id, "human"),
                    (ai_id, "bound_machine"),
                    ("human-multiplayer-third", "human"),
                ),
            )
            framework.create_room(
                "tictactoe", "human_first", "human", human_id, ai_id
            )
            with self.assertRaisesRegex(
                framework.DuelError, "已有 1 个活跃房间"
            ):
                framework.create_room(
                    "tictactoe", "human_first", "human", human_id, ai_id
                )

    def test_multiple_bound_machines_in_multiplayer_room_use_no_pair_quotas(self):
        human_id = "human-many-machines"
        first_ai_id = "ai-many-machines-one"
        second_ai_id = "ai-many-machines-two"
        with patch.object(framework, "PAIR_ACTIVE_ROOM_LIMIT", 1):
            framework.create_room(
                "doudizhu",
                "human_first",
                "human",
                human_id,
                ordered_participants=self._participants(
                    (human_id, "human"),
                    (first_ai_id, "bound_machine"),
                    (second_ai_id, "bound_machine"),
                ),
            )
            first_pair = framework.create_room(
                "tictactoe", "human_first", "human", human_id, first_ai_id
            )
            second_pair = framework.create_room(
                "tictactoe", "human_first", "human", human_id, second_ai_id
            )
        self.assertEqual(first_pair["status"], "playing")
        self.assertEqual(second_pair["status"], "playing")

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
        result_events = [
            event
            for event in framework.list_timeline(room["room_id"])
            if event["event_type"] == "result"
        ]
        self.assertEqual(len(result_events), 1)
        self.assertEqual(result_events[0]["display_text"], "和棋")
        ai_events = framework.read_new_room_events(
            room["room_id"], "ai-stale"
        )
        self.assertEqual(
            [event["event_type"] for event in ai_events], ["result"]
        )

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
                '{"size":3,"board":[[null,null,null],[null,null,null],[null,null,null]]}',
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
        with database.write_transaction() as writable:
            writable.execute(
                "UPDATE rooms SET last_move_at = ? WHERE room_id = 'MIGRATE1'",
                (framework._now(),),
            )
        migrated_room = framework.get_room(
            "MIGRATE1", "human", "human-old"
        )
        self.assertEqual(migrated_room["current_player_id"], "human-old")
        self.assertEqual(
            [item["join_status"] for item in migrated_room["participants"]],
            ["joined", "joined"],
        )
        continued = framework.play_move(
            "MIGRATE1", "human", "human-old", {"row": 0, "col": 0}
        )
        self.assertEqual(continued["current_player_id"], "ai-old")

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
            message_schema = migrated.execute(
                """
                SELECT sql FROM sqlite_master
                WHERE type = 'table' AND name = 'room_messages'
                """
            ).fetchone()[0]
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
        self.assertIn("'result'", message_schema)
        self.assertIn("'system'", message_schema)
        self.assertEqual(
            participants, [("human-two", "human"), ("ai-two", "ai")]
        )
        self.assertEqual(cursors, [("ai-two", 0), ("human-two", 1)])


class WaitCapacityConfigTests(unittest.TestCase):
    def test_wait_capacity_parser_accepts_gateway_scale_and_rejects_bad_values(self):
        self.assertEqual(main_module._parse_max_concurrent_waits("20"), 20)
        self.assertEqual(main_module._parse_max_concurrent_waits("200"), 200)
        for value in ("0", "501", "abc", "2.5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                main_module._parse_max_concurrent_waits(value)


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
        self.assertEqual(payload["status"], "playing")
        self.assertNotIn("room", payload)

        human_move = await self.client.post(
            f"/api/rooms/{first_room}/move",
            json={"player_id": "human-wait-1", "row": 1, "col": 0},
        )
        self.assertEqual(human_move.status_code, 200)
        resumed = await asyncio.wait_for(first_waiter, timeout=1)
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(main_module.revision_events.waiting_count, 0)

    async def test_new_returns_friendly_pair_limit_error(self):
        for _ in range(10):
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
        self.assertIn("已有 10 个活跃房间", payload["message"])


if __name__ == "__main__":
    unittest.main()
