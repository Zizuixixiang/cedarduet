import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database
from app import main as main_module
from app.framework import create_room, resign


class HumanIdentityApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-identity-")
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

    async def test_unbound_direct_access_only_receives_login_guidance(self):
        response = await self.client.get("/api/whoami")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["bound"])
        self.assertEqual(payload["rooms"], [])
        self.assertEqual(
            payload["message"], "请从 toy.cedarstar.org 首页登录进入"
        )

    async def test_trusted_pair_name_and_rooms_are_returned_active_first(self):
        active = create_room(
            "othello", "human_first", "human", "human-7", "ai-9"
        )
        finished = create_room(
            "connect4", "ai_first", "human", "human-7", "ai-9"
        )
        resign(finished["room_id"], "human", "human-7", opponent_id="ai-9")
        create_room(
            "jungle", "human_first", "human", "other-human", "other-ai"
        )

        response = await self.client.get(
            "/api/whoami",
            headers={
                "X-Duel-Human-Player": "human-7",
                "X-Duel-Ai-Player": "ai-9",
                "X-Duel-Ai-Name": "%E5%85%8B%E8%8E%B1%E5%A5%A5",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["bound"])
        self.assertEqual(payload["ai_name"], "克莱奥")
        self.assertEqual(payload["pair_label"], "你 × 克莱奥")
        self.assertEqual(
            [item["room_id"] for item in payload["rooms"]],
            [active["room_id"], finished["room_id"]],
        )
        self.assertEqual(payload["rooms"][0]["status"], "playing")
        self.assertEqual(payload["rooms"][1]["status"], "finished")
        self.assertEqual(payload["rooms"][0]["game_name"], "黑白棋")

    async def test_page_has_six_games_and_no_identity_or_room_join_inputs(self):
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.text
        for game_type in (
            "tictactoe", "gomoku", "othello",
            "connect4", "dots_boxes", "jungle",
        ):
            self.assertIn(f'value="{game_type}"', html)
        self.assertNotIn('id="playerId"', html)
        self.assertNotIn('id="joinRoomId"', html)
        self.assertNotIn('id="joinButton"', html)
        self.assertIn("请从 toy.cedarstar.org 首页登录进入", html)


if __name__ == "__main__":
    unittest.main()
