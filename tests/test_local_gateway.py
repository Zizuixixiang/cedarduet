import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database
from app import main as main_module
from app.local_config import local_identity_headers


_saved_local_env = {
    name: os.environ.get(name)
    for name in ("DUEL_DB_PATH", "DUEL_NPC_PERSONAS_DIR")
}
from app.local_gateway import app as local_app
for _name, _value in _saved_local_env.items():
    if _value is None:
        os.environ.pop(_name, None)
    else:
        os.environ[_name] = _value


class LocalGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-local-gateway-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "local.db"
        )
        self.db_patch.start()
        database.init_db()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=local_app, client=("127.0.0.1", 45678)
            ),
            base_url="http://127.0.0.1",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.db_patch.stop()
        self.temporary.cleanup()

    async def test_gateway_replaces_forged_headers_and_room_body_identities(self):
        whoami = await self.client.get(
            "/api/whoami",
            headers={
                "X-Duel-Human-Player": "evil-human",
                "X-Duel-Ai-Player": "evil-ai",
                "X-Duel-Bound-Ais": "invalid",
            },
        )
        self.assertEqual(whoami.status_code, 200, whoami.text)
        self.assertTrue(whoami.json()["bound"])
        self.assertEqual(whoami.json()["human_name"], "本地玩家")
        self.assertEqual(
            whoami.json()["machines"], [{"id": "local-ai", "name": "本地小机"}]
        )
        self.assertTrue(whoami.json()["npc_provider"]["available"])
        self.assertTrue(
            whoami.json()["npc_provider"]["local_admission_deferred"]
        )

        created = await self.client.post(
            "/api/rooms",
            json={
                "player_id": "evil-human",
                "opponent_id": "evil-ai",
                "ai_player": "evil-ai",
                "ai_players": ["evil-ai"],
                "game_type": "tictactoe",
                "mode": "human_first",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        room = created.json()["room"]
        self.assertEqual(
            [item["player_id"] for item in room["participants"]],
            ["local-human", "local-ai"],
        )
        self.assertEqual(
            [item["participant_kind"] for item in room["participants"]],
            ["human", "bound_machine"],
        )

        state = await self.client.get(
            f"/api/rooms/{room['room_id']}?player_id=evil-human&opponent_id=evil-ai"
        )
        self.assertEqual(state.status_code, 200, state.text)
        self.assertEqual(state.json()["room"]["viewer"]["player_id"], "local-human")

    async def test_gateway_forces_direct_http_mcp_identity_too(self):
        catalog = await self.client.post(
            "/mcp/play",
            json={
                "action": "catalog",
                "player_id": "npc:forged",
                "opponent_id": "evil-human",
                "participant_ids": ["evil-human", "evil-ai"],
            },
        )
        self.assertEqual(catalog.status_code, 200, catalog.text)
        self.assertEqual(len(catalog.json()["games"]), 25)

        room = await self.client.post(
            "/mcp/play",
            json={
                "action": "new",
                "player_id": "evil-ai",
                "opponent_id": "evil-human",
                "participant_ids": ["evil-human", "evil-ai"],
                "game_type": "gomoku",
                "mode": "ai_first",
            },
        )
        self.assertEqual(room.status_code, 200, room.text)
        self.assertEqual(
            [item["player_id"] for item in room.json()["room"]["participants"]],
            ["local-human", "local-ai"],
        )

    async def test_gateway_does_not_pollute_strict_notification_or_chip_bodies(self):
        notification = await self.client.post(
            "/api/notifications/read", json={"category": "game"}
        )
        self.assertEqual(notification.status_code, 200, notification.text)
        check_in = await self.client.post("/api/chips/check-in", json={})
        self.assertEqual(check_in.status_code, 200, check_in.text)

    async def test_gateway_rejects_non_loopback_client_and_nonlocal_host(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=local_app, client=("192.0.2.10", 1234)
            ),
            base_url="http://127.0.0.1",
        ) as remote:
            response = await remote.get("/health")
        self.assertEqual(response.status_code, 403)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=local_app, client=("127.0.0.1", 1234)
            ),
            base_url="http://evil.example",
        ) as rebound:
            response = await rebound.get("/health")
        self.assertEqual(response.status_code, 403)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=local_app, client=("127.0.0.1", 1234)
            ),
            base_url="http://127.0.0.1",
            headers={"Host": "[invalid"},
        ) as malformed:
            response = await malformed.get("/health")
        self.assertEqual(response.status_code, 403)

    async def test_production_app_remains_unbound_without_trusted_proxy(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_module.app),
            base_url="http://duel.test",
        ) as production:
            response = await production.get("/api/whoami")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["bound"])

    async def test_local_web_defers_admission_without_changing_production_capability(self):
        headers = {
            name.decode("ascii"): value.decode("ascii")
            for name, value in local_identity_headers()
        }
        with patch.dict(os.environ, {"DUEL_NPC_PROVIDER": "disabled"}):
            local = await self.client.get("/api/whoami")
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main_module.app),
                base_url="http://duel.test",
            ) as production:
                remote = await production.get("/api/whoami", headers=headers)
        self.assertTrue(local.json()["npc_provider"]["available"])
        self.assertTrue(
            local.json()["npc_provider"]["local_admission_deferred"]
        )
        self.assertFalse(remote.json()["npc_provider"]["available"])


if __name__ == "__main__":
    unittest.main()
