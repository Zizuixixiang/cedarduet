import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database
from app.local_config import LOCAL_PERSONA_DIR
from app.local_gateway import NPC_API_REQUIRED_MESSAGE, app as local_app


class LocalNpcAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-local-npc-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "local.db"
        )
        self.db_patch.start()
        self.env_patch = patch.dict(
            os.environ,
            {
                "DUEL_NPC_PROVIDER": "disabled",
                "DUEL_NPC_PERSONAS_DIR": str(LOCAL_PERSONA_DIR),
            },
        )
        self.env_patch.start()
        database.init_db()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=local_app, client=("127.0.0.1", 45678)
            ),
            base_url="http://127.0.0.1",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.env_patch.stop()
        self.db_patch.stop()
        self.temporary.cleanup()

    def room_count(self) -> int:
        conn = database.connect()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0])
        finally:
            conn.close()

    async def test_no_missing_seat_never_requires_provider(self):
        response = await self.client.post(
            "/api/rooms",
            json={"game_type": "tictactoe", "mode": "human_first"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.room_count(), 1)

    async def test_existing_local_strategy_never_requires_provider(self):
        response = await self.client.post(
            "/api/rooms",
            json={
                "game_type": "train_cards",
                "mode": "human_first",
                "target_player_count": 6,
                "fill_with_npcs": True,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        participants = response.json()["room"]["participants"]
        self.assertEqual(len(participants), 6)
        self.assertEqual(
            {item["display_name"] for item in participants[2:]},
            {"下棋助手 1", "下棋助手 2", "下棋助手 3", "下棋助手 4"},
        )

    async def test_nonlocal_strategy_is_rejected_before_room_write(self):
        response = await self.client.post(
            "/api/rooms",
            json={
                "game_type": "doudizhu",
                "target_player_count": 3,
                "fill_with_npcs": True,
            },
        )
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["message"], NPC_API_REQUIRED_MESSAGE)
        self.assertEqual(self.room_count(), 0)

        mcp = await self.client.post(
            "/mcp/play",
            json={
                "action": "new",
                "game_type": "doudizhu",
                "target_player_count": 3,
                "fill_with_npcs": True,
            },
        )
        self.assertEqual(mcp.status_code, 503, mcp.text)
        self.assertEqual(mcp.json()["message"], NPC_API_REQUIRED_MESSAGE)
        self.assertEqual(self.room_count(), 0)

    async def test_configured_provider_preserves_existing_multiplayer_shape(self):
        with patch.dict(
            os.environ,
            {
                "DUEL_NPC_PROVIDER": "openai_compatible",
                "DUEL_NPC_API_BASE": "https://provider.invalid/v1",
                "DUEL_NPC_API_KEY": "test-key",
                "DUEL_NPC_MODEL": "test-model",
            },
        ):
            response = await self.client.post(
                "/api/rooms",
                json={
                    "game_type": "doudizhu",
                    "mode": "human_first",
                    "target_player_count": 3,
                    "fill_with_npcs": True,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["room"]["participants"]), 3)


if __name__ == "__main__":
    unittest.main()
