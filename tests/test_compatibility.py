import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database
from app import main as main_module


class StringFieldCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-compat-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()
        self.original_events = main_module.revision_events
        main_module.revision_events = main_module.RevisionEvents()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_module.app),
            base_url="http://duel.test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        main_module.revision_events = self.original_events
        self.db_patch.stop()
        self.temporary.cleanup()

    async def new_room(self, suffix: str, mode: str = "ai_first") -> str:
        response = await self.client.post(
            "/mcp/play",
            json={
                "action": "new",
                "player_id": f"ai-{suffix}",
                "opponent_id": f"human-{suffix}",
                "game_type": "tictactoe",
                "mode": mode,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["room"]["room_id"]

    async def test_mcp_string_move_is_parsed_as_object(self):
        room_id = await self.new_room("move")
        response = await self.client.post(
            "/mcp/play",
            json={
                "action": "move",
                "player_id": "ai-move",
                "room_id": room_id,
                "move": '{"row":0,"col":0}',
                "wait": "false",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNotNone(response.json()["room"]["board_state"]["board"][0][0])

    async def test_human_web_move_accepts_string_object(self):
        room_id = await self.new_room("web", mode="human_first")
        response = await self.client.post(
            f"/api/rooms/{room_id}/move",
            json={
                "player_id": "human-web",
                "move": '{"row":1,"col":1}',
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNotNone(response.json()["room"]["board_state"]["board"][1][1])

    async def test_string_true_preserves_wait_and_wakeup_semantics(self):
        room_id = await self.new_room("wait")
        waiter = asyncio.create_task(
            self.client.post(
                "/mcp/play",
                json={
                    "action": "move",
                    "player_id": "ai-wait",
                    "room_id": room_id,
                    "move": '{"row":0,"col":0}',
                    "wait": "TrUe",
                },
            )
        )
        await asyncio.sleep(0.03)
        self.assertFalse(waiter.done())

        human = await self.client.post(
            f"/api/rooms/{room_id}/move",
            json={
                "player_id": "human-wait",
                "move": {"row": 1, "col": 0},
            },
        )
        self.assertEqual(human.status_code, 200, human.text)
        resumed = await asyncio.wait_for(waiter, timeout=1)
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertEqual(resumed.json()["status"], "ok")
        self.assertEqual(
            resumed.json()["room"]["revision"],
            human.json()["room"]["revision"],
        )

    async def test_bad_string_fields_remain_422_with_field_details(self):
        room_id = await self.new_room("invalid")
        bad_move = await self.client.post(
            "/mcp/play",
            json={
                "action": "move",
                "player_id": "ai-invalid",
                "room_id": room_id,
                "move": "not-json",
            },
        )
        self.assertEqual(bad_move.status_code, 422, bad_move.text)
        self.assertIn(
            "move",
            {detail["field"] for detail in bad_move.json()["details"]},
        )

        bad_wait = await self.client.post(
            "/mcp/play",
            json={
                "action": "move",
                "player_id": "ai-invalid",
                "room_id": room_id,
                "move": '{"row":0,"col":0}',
                "wait": "sometimes",
            },
        )
        self.assertEqual(bad_wait.status_code, 422, bad_wait.text)
        self.assertIn(
            "wait",
            {detail["field"] for detail in bad_wait.json()["details"]},
        )


if __name__ == "__main__":
    unittest.main()
