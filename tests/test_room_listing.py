import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database, framework
from app import main as main_module


class AiRoomListingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-room-list-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_module.app),
            base_url="http://duel.test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.db_patch.stop()
        self.temporary.cleanup()

    @staticmethod
    def create_for(ai_player_id: str, suffix: str) -> dict:
        return framework.create_room(
            "tictactoe",
            "human_first",
            "human",
            f"human-{suffix}",
            opponent_id=ai_player_id,
        )

    def test_ai_lists_only_own_rooms_and_filters_terminal_by_default(self):
        active_a = self.create_for("ai-a", "active-a")
        waiting_a = framework.create_room(
            "gomoku", "ai_first", "ai", "ai-a"
        )
        finished_a = self.create_for("ai-a", "finished-a")
        framework.resign(
            finished_a["room_id"], "human", "human-finished-a"
        )
        archived_a = self.create_for("ai-a", "archived-a")
        with database.write_transaction() as conn:
            conn.execute(
                """
                UPDATE rooms
                SET status = 'archived', winner = 'draw', terminal_at = updated_at
                WHERE room_id = ?
                """,
                (archived_a["room_id"],),
            )
        active_b = self.create_for("ai-b", "active-b")

        default_a = framework.list_ai_rooms("ai-a")
        self.assertEqual(
            [item["room_id"] for item in default_a],
            [active_a["room_id"], waiting_a["room_id"]],
        )
        self.assertEqual(
            {item["status"] for item in default_a}, {"playing", "waiting"}
        )
        self.assertTrue(all(
            set(item) == {
                "room_id", "game_type", "status", "turn",
                "revision", "current_player_id", "current_actor_seat",
                "own_seat", "participant_count",
                "created_at", "updated_at",
            }
            for item in default_a
        ))

        all_a = framework.list_ai_rooms("ai-a", include_terminal=True)
        self.assertEqual(
            {item["room_id"] for item in all_a},
            {
                active_a["room_id"], waiting_a["room_id"],
                finished_a["room_id"], archived_a["room_id"],
            },
        )
        self.assertNotIn(active_b["room_id"], {item["room_id"] for item in all_a})
        self.assertEqual(
            [item["room_id"] for item in framework.list_ai_rooms("ai-b")],
            [active_b["room_id"]],
        )

    def test_limit_and_offset_are_applied_after_private_filtering(self):
        own_rooms = [self.create_for("ai-a", f"page-{index}") for index in range(3)]
        self.create_for("ai-b", "page-b")
        with database.write_transaction() as conn:
            for index, room in enumerate(own_rooms, start=1):
                timestamp = f"2026-08-20T00:00:0{index}+00:00"
                conn.execute(
                    "UPDATE rooms SET updated_at = ? WHERE room_id = ?",
                    (timestamp, room["room_id"]),
                )

        first_page = framework.list_ai_rooms("ai-a", limit=2, offset=0)
        second_page = framework.list_ai_rooms("ai-a", limit=2, offset=2)
        self.assertEqual(
            [item["room_id"] for item in first_page],
            [own_rooms[2]["room_id"], own_rooms[1]["room_id"]],
        )
        self.assertEqual(
            [item["room_id"] for item in second_page],
            [own_rooms[0]["room_id"]],
        )

    def test_existing_human_room_list_contract_is_unchanged(self):
        active = framework.create_room(
            "tictactoe", "human_first", "human", "human-owner", "ai-a"
        )
        finished = framework.create_room(
            "gomoku", "ai_first", "human", "human-owner", "ai-b"
        )
        framework.resign(finished["room_id"], "human", "human-owner")

        rooms = framework.list_human_rooms(
            "human-owner", {"ai-a": "甲机", "ai-b": "乙机"}
        )
        self.assertEqual(
            [item["room_id"] for item in rooms],
            [active["room_id"], finished["room_id"]],
        )
        self.assertEqual([item["ai_name"] for item in rooms], ["甲机", "乙机"])
        self.assertIn("game_name", rooms[0])
        self.assertIn("ai_player_id", rooms[0])

    async def test_mcp_rooms_action_returns_compact_private_summaries(self):
        own = self.create_for("ai-a", "mcp-a")
        other = self.create_for("ai-b", "mcp-b")

        response = await self.client.post(
            "/mcp/play",
            json={
                "action": "rooms",
                "player_id": "ai-a",
                "include_terminal": False,
                "limit": 10,
                "offset": 0,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual([item["room_id"] for item in payload["rooms"]], [own["room_id"]])
        self.assertNotIn(other["room_id"], response.text)
        self.assertEqual(
            payload["pagination"],
            {
                "include_terminal": False,
                "limit": 10,
                "offset": 0,
                "returned": 1,
            },
        )

    async def test_mcp_rooms_validates_limit_and_offset(self):
        too_large = await self.client.post(
            "/mcp/play",
            json={"action": "rooms", "player_id": "ai-a", "limit": 101},
        )
        negative_offset = await self.client.post(
            "/mcp/play",
            json={"action": "rooms", "player_id": "ai-a", "offset": -1},
        )
        self.assertEqual(too_large.status_code, 422)
        self.assertEqual(negative_offset.status_code, 422)


if __name__ == "__main__":
    unittest.main()
