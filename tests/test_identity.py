import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database
from app import main as main_module
from app.framework import create_room, resign
from app.games import GAMES


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

    async def test_legacy_single_machine_headers_work_until_proxy_restart(self):
        response = await self.client.get(
            "/api/whoami",
            headers={
                "X-Duel-Human-Player": "human-7",
                "X-Duel-Ai-Player": "ai-9",
                "X-Duel-Ai-Name": "%E5%85%8B%E8%8E%B1%E5%A5%A5",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["machines"],
            [{"id": "ai-9", "name": "克莱奥"}],
        )

    @staticmethod
    def trusted_headers(machines):
        encoded = base64.urlsafe_b64encode(
            json.dumps(machines, ensure_ascii=False).encode("utf-8")
        ).decode("ascii").rstrip("=")
        return {
            "X-Duel-Human-Player": "human-7",
            "X-Duel-Human-Name": "%E5%8D%97%E5%B1%B1%E5%90%9B",
            "X-Duel-Bound-Ais": encoded,
        }

    async def test_trusted_human_sees_all_machine_rooms_active_first(self):
        active = create_room(
            "othello", "human_first", "human", "human-7", "ai-9"
        )
        finished = create_room(
            "connect4", "ai_first", "human", "human-7", "ai-10"
        )
        resign(finished["room_id"], "human", "human-7", opponent_id="ai-10")
        create_room(
            "jungle", "human_first", "human", "other-human", "other-ai"
        )

        response = await self.client.get(
            "/api/whoami",
            headers=self.trusted_headers([
                {"id": "ai-9", "name": "克莱奥"},
                {"id": "ai-10", "name": "南山小机"},
            ]),
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["bound"])
        self.assertEqual(payload["human_name"], "南山君")
        self.assertEqual(
            payload["machines"],
            [
                {"id": "ai-9", "name": "克莱奥"},
                {"id": "ai-10", "name": "南山小机"},
            ],
        )
        self.assertEqual(payload["identity_label"], "南山君 · 2 只已绑定小机")
        self.assertEqual(len(payload["games"]), 6)
        self.assertTrue(all(
            game["min_players"] == game["max_players"] == 2
            for game in payload["games"]
        ))
        self.assertEqual(
            [item["room_id"] for item in payload["rooms"]],
            [active["room_id"], finished["room_id"]],
        )
        self.assertEqual(payload["rooms"][0]["status"], "playing")
        self.assertEqual(payload["rooms"][1]["status"], "finished")
        self.assertEqual(payload["rooms"][0]["game_name"], "黑白棋")
        self.assertEqual(payload["rooms"][0]["ai_name"], "克莱奥")
        self.assertEqual(payload["rooms"][1]["ai_name"], "南山小机")

    async def test_human_create_accepts_only_an_injected_bound_machine(self):
        headers = self.trusted_headers([{"id": "ai-9", "name": "克莱奥"}])
        created = await self.client.post(
            "/api/rooms",
            headers=headers,
            json={
                "player_id": "human-7",
                "ai_player": "ai-9",
                "game_type": "tictactoe",
                "mode": "human_first",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        room = created.json()["room"]
        self.assertEqual(room["human_player_id"], "human-7")
        self.assertEqual(room["ai_player_id"], "ai-9")
        self.assertEqual(
            [(item["role"], item["player_id"]) for item in room["participants"]],
            [("human", "human-7"), ("ai", "ai-9")],
        )

        rejected = await self.client.post(
            "/api/rooms",
            headers=headers,
            json={
                "player_id": "human-7",
                "ai_player": "unrelated-ai",
                "game_type": "gomoku",
                "mode": "human_first",
            },
        )
        self.assertEqual(rejected.status_code, 403)
        self.assertIn("不在当前账号的绑定清单", rejected.json()["message"])

        with (
            patch.object(GAMES["tictactoe"], "min_players", 3),
            patch.object(GAMES["tictactoe"], "max_players", 3),
        ):
            wrong_count = await self.client.post(
                "/api/rooms",
                headers=headers,
                json={
                    "player_id": "human-7",
                    "ai_player": "ai-9",
                    "game_type": "tictactoe",
                    "mode": "human_first",
                },
            )
        self.assertEqual(wrong_count.status_code, 400)
        self.assertIn("需要 3 名参与者", wrong_count.json()["message"])

    async def test_ai_actions_require_room_participation(self):
        room = create_room(
            "tictactoe", "ai_first", "human", "human-7", "ai-9"
        )
        rejected = await self.client.post(
            "/mcp/play",
            json={
                "action": "move",
                "player_id": "unrelated-ai",
                "room_id": room["room_id"],
                "move": {"row": 0, "col": 0},
            },
        )
        self.assertEqual(rejected.status_code, 403)
        self.assertIn("席位不匹配", rejected.json()["message"])

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
        self.assertIn('id="aiPlayer"', html)
        self.assertIn('id="createButton"', html)
        self.assertIn('type="button" disabled', html)
        self.assertNotIn('class="bottom-nav"', html)
        self.assertIn("← 返回首页", html)
        self.assertIn("/static/app.js?v=0.7.0", html)
        self.assertLess(html.index("开新对局"), html.index("我的全部房间"))
        self.assertIn("请从 toy.cedarstar.org 首页登录进入", html)
        self.assertIn('id="aiAvatar"', html)
        self.assertIn('id="humanAvatar"', html)
        self.assertIn('id="aiSpeech"', html)
        self.assertIn('id="humanSpeech"', html)
        self.assertIn('id="confirmMoveButton"', html)
        self.assertIn('id="historyDrawerPanel"', html)
        self.assertNotIn('class="timeline-panel', html)
        self.assertNotIn('id="moveFormat"', html)
        self.assertNotIn("随落子发送", html)
        self.assertNotIn("只留言", html)
        self.assertEqual(response.headers["cache-control"], "no-store")

        script = await self.client.get("/static/app.js")
        self.assertEqual(script.headers["cache-control"], "no-store")
        self.assertIn("select.disabled = false", script.text)
        self.assertIn("function emojiFor(name)", script.text)
        self.assertIn("function confirmMove()", script.text)
        self.assertIn("function openHistory()", script.text)
        self.assertIn(
            "setInterval(() => refreshRoom({quiet: true}), 3000)",
            script.text,
        )
        self.assertNotIn(
            "setInterval(() => refreshRoom({quiet: true}), 1500)",
            script.text,
        )
        self.assertNotIn(
            "select.disabled = machines.length === 1", script.text
        )


if __name__ == "__main__":
    unittest.main()
