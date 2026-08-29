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
        self.assertEqual(len(payload["games"]), 8)
        games = {game["game_type"]: game for game in payload["games"]}
        self.assertEqual(games["dots_boxes"]["allowed_player_counts"], [2, 3, 4])
        self.assertEqual(games["liars_dice"]["allowed_player_counts"], [2, 3, 4, 5, 6])
        self.assertEqual(
            [item["room_id"] for item in payload["rooms"]],
            [active["room_id"], finished["room_id"]],
        )
        self.assertEqual(payload["rooms"][0]["status"], "playing")
        self.assertEqual(payload["rooms"][1]["status"], "finished")
        self.assertEqual(payload["rooms"][0]["game_name"], "黑白棋")
        self.assertEqual(payload["rooms"][0]["ai_name"], "克莱奥")
        self.assertEqual(payload["rooms"][1]["ai_name"], "南山小机")
        self.assertFalse(payload["rooms"][0]["preserved"])
        self.assertIsNone(payload["rooms"][0]["auto_delete_at"])
        self.assertFalse(payload["rooms"][1]["preserved"])
        self.assertIsNotNone(payload["rooms"][1]["auto_delete_at"])

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

    async def test_page_has_all_games_and_no_identity_or_room_join_inputs(self):
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.text
        for game_type in (
            "tictactoe", "gomoku", "othello",
            "connect4", "dots_boxes", "liars_dice", "jungle", "xiangqi",
        ):
            self.assertIn(f'value="{game_type}"', html)
        self.assertNotIn('id="playerId"', html)
        self.assertNotIn('id="joinRoomId"', html)
        self.assertNotIn('id="joinButton"', html)
        self.assertIn('id="aiPlayer"', html)
        self.assertIn('id="createButton"', html)
        self.assertIn('type="button" disabled', html)
        self.assertNotIn('class="bottom-nav"', html)
        self.assertIn(
            'class="home-link" href="https://toy.cedarstar.org/" '
            'aria-label="返回 CedarToy 首页">←</a>',
            html,
        )
        self.assertNotIn("← 返回首页", html)
        self.assertIn("/static/app.js?v=0.9.1", html)
        self.assertIn("/static/styles.css?v=0.9.1", html)
        self.assertLess(html.index("开新对局"), html.index("我的全部房间"))
        self.assertIn("请从 toy.cedarstar.org 首页登录进入", html)
        self.assertIn('id="aiAvatar"', html)
        self.assertIn('id="humanAvatar"', html)
        self.assertIn('aria-hidden="true">🤖</span>', html)
        self.assertIn('aria-hidden="true">👤</span>', html)
        self.assertIn('id="aiSpeech"', html)
        self.assertIn('id="humanSpeech"', html)
        self.assertIn('id="confirmMoveButton"', html)
        self.assertIn(
            'id="waitModeModal" class="wait-mode-modal-backdrop hidden"', html
        )
        self.assertIn('role="dialog" aria-modal="true"', html)
        self.assertIn('id="waitModeModalTitle">挂等模式</h2>', html)
        self.assertIn("回聊天窗口告诉它「开启挂等模式」", html)
        self.assertIn("约 30 秒一次的短心跳持续等待", html)
        self.assertIn("一次心跳结束不代表退出", html)
        self.assertIn("轮到它时会自动继续", html)
        self.assertNotIn("wait=true", html)
        self.assertNotIn("MCP", html)
        self.assertIn('id="closeWaitModalTodayButton"', html)
        self.assertIn('id="closeWaitModalForeverButton"', html)
        self.assertIn('aria-label="仅关闭本次提示"', html)
        game_view = html[
            html.index('<section id="gameView"'):
            html.index('</main>')
        ]
        self.assertNotIn('id="waitModeModal"', game_view)
        self.assertNotIn('class="wait-mode-hint', html)
        self.assertNotIn("小机正在等你落子", html)
        self.assertIn('id="historyDrawerPanel"', html)
        self.assertIn('class="chat-compose game-compose"', html)
        battle_stage_start = html.index('<section class="battle-stage')
        battle_stage = html[
            battle_stage_start:
            html.index('<button id="historyDrawerTab"', battle_stage_start)
        ]
        self.assertIn('id="chatInput" maxlength="500"', battle_stage)
        self.assertIn('placeholder="说点什么…"', battle_stage)
        self.assertIn('id="sendMessageButton"', battle_stage)
        self.assertNotIn('id="refreshButton"', battle_stage)
        self.assertNotIn('id="resignButton"', battle_stage)
        self.assertLess(
            battle_stage.index('class="player-row human-row"'),
            battle_stage.index('id="chatInput"'),
        )
        toolbar = html[
            html.index('<header class="game-header pixel-card">'):
            html.index('<section class="game-meta" aria-label="对局信息">')
        ]
        for button_id in ("refreshButton", "rulesButton", "resignButton"):
            self.assertIn(f'id="{button_id}"', toolbar)
        chat_start = battle_stage.index('<div class="chat-compose game-compose"')
        chat = battle_stage[chat_start:battle_stage.index("</div>", chat_start)]
        self.assertIn('id="chatInput"', chat)
        self.assertIn('id="sendMessageButton"', chat)
        self.assertNotIn("刷新局面", chat)
        self.assertNotIn("认输", chat)
        history_drawer = html[
            html.index('<div id="historyDrawer"'):
            html.index('<div id="resultModal"')
        ]
        self.assertIn('id="timeline"', history_drawer)
        self.assertNotIn('id="chatInput"', history_drawer)
        self.assertNotIn('id="sendMessageButton"', history_drawer)
        self.assertIn('id="resultBanner"', html)
        self.assertIn('id="resultModal"', html)
        self.assertIn('id="togglePreserveButton"', html)
        self.assertIn('id="roomRetentionStatus"', html)
        self.assertIn('id="resultPreserveCheckbox" type="checkbox"', html)
        self.assertIn('id="resultRetentionHint"', html)
        self.assertNotIn('id="preserveResultButton"', html)
        self.assertNotIn('id="skipPreserveButton"', html)
        self.assertIn("对局结束", html)
        self.assertIn("保留本局棋谱和聊天记录", html)
        self.assertIn("终局 7 天后自动删除", html)
        self.assertIn('id="rematchButton"', html)
        self.assertIn('id="finishGameButton"', html)
        self.assertNotIn('class="timeline-panel', html)
        self.assertNotIn('id="moveFormat"', html)
        self.assertNotIn("随落子发送", html)
        self.assertNotIn("只留言", html)
        self.assertEqual(response.headers["cache-control"], "no-store")

        script = await self.client.get("/static/app.js")
        self.assertEqual(script.headers["cache-control"], "no-store")
        self.assertIn("select.disabled = false", script.text)
        self.assertNotIn("PLAYER_EMOJIS", script.text)
        self.assertNotIn("function emojiFor", script.text)
        self.assertIn('$("aiAvatar").textContent = "🤖"', script.text)
        self.assertIn('$("humanAvatar").textContent = "👤"', script.text)
        self.assertIn(
            'const WAIT_HINT_STORAGE_PREFIX = "duel:wait-mode-hint"',
            script.text,
        )
        self.assertIn("const waitHintShownRooms = new Set()", script.text)
        self.assertIn("function localDateString(date = new Date())", script.text)
        self.assertIn("function waitHintHumanId(targetRoom)", script.text)
        self.assertIn("function shouldShowWaitModeHint(", script.text)
        self.assertIn("function closeWaitModeModal(", script.text)
        self.assertIn("function showWaitModeModalOnce(", script.text)
        self.assertIn("waitHintShownRooms.has(visitKey)", script.text)
        self.assertNotIn("waitHintTimer", script.text)
        self.assertNotIn("setTimeout(hideWaitModeModal", script.text)
        self.assertIn("showWaitModeModalOnce(room)", script.text)
        self.assertIn('$("dismissWaitModeModalButton").addEventListener', script.text)
        self.assertIn('$("closeWaitModalTodayButton").addEventListener', script.text)
        self.assertIn('$("closeWaitModalForeverButton").addEventListener', script.text)
        self.assertIn('$("waitModeModal").addEventListener', script.text)
        self.assertIn(
            '$("sendMessageButton").disabled = '
            '!["waiting", "playing"].includes(room.status)',
            script.text,
        )
        self.assertIn('const message = $("chatInput").value.trim()', script.text)
        self.assertIn('JSON.stringify({message})', script.text)
        self.assertIn('$("chatInput").value = ""', script.text)
        self.assertIn('$("chatInput").addEventListener("keydown"', script.text)
        self.assertIn('if (event.key === "Enter")', script.text)
        self.assertIn("event.preventDefault()", script.text)
        self.assertIn("function viewerPlayerIdFor(targetRoom)", script.text)
        self.assertIn('bubble: $("viewerSpeech")', script.text)
        self.assertIn("{excludePlayerId: viewerPlayerId}", script.text)
        self.assertIn("function retentionTextFor(targetRoom)", script.text)
        self.assertIn("function updateRoomPreservation(", script.text)
        self.assertIn("function deleteRoom(summary)", script.text)
        self.assertIn("/retention", script.text)
        self.assertIn("/delete", script.text)
        self.assertIn("window.confirm(", script.text)
        self.assertIn('remove.textContent = "删除对局"', script.text)
        self.assertIn("function confirmMove()", script.text)
        self.assertIn("function openHistory()", script.text)
        self.assertIn("function oppositeMode(mode)", script.text)
        self.assertIn("function rematch()", script.text)
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

        styles = await self.client.get("/static/styles.css")
        self.assertEqual(styles.headers["cache-control"], "no-store")
        self.assertIn(
            "grid-template-rows: auto minmax(0, 1fr)", styles.text
        )
        self.assertIn("height: 100dvh", styles.text)
        self.assertIn("overscroll-behavior: contain", styles.text)
        self.assertIn(".room-record-controls", styles.text)
        self.assertIn(".result-preserve-option", styles.text)
        self.assertIn(".last-move-marker", styles.text)
        self.assertIn(".wait-mode-modal-backdrop", styles.text)
        self.assertIn(".wait-mode-modal-actions", styles.text)
        self.assertNotIn(".wait-mode-hint", styles.text)
        self.assertIn("width: min(920px, 100%)", styles.text)

    async def test_rematch_contract_reuses_pair_and_flips_first_player(self):
        headers = self.trusted_headers([{"id": "ai-9", "name": "克莱奥"}])
        first = await self.client.post(
            "/api/rooms",
            headers=headers,
            json={
                "player_id": "human-7",
                "ai_player": "ai-9",
                "game_type": "othello",
                "mode": "human_first",
            },
        )
        self.assertEqual(first.status_code, 200, first.text)
        first_room = first.json()["room"]
        resigned = await self.client.post(
            f"/api/rooms/{first_room['room_id']}/resign",
            json={"player_id": "human-7"},
        )
        self.assertEqual(resigned.status_code, 200, resigned.text)

        rematch = await self.client.post(
            "/api/rooms",
            headers=headers,
            json={
                "player_id": "human-7",
                "ai_player": first_room["ai_player_id"],
                "game_type": first_room["game_type"],
                "mode": "ai_first",
            },
        )
        self.assertEqual(rematch.status_code, 200, rematch.text)
        next_room = rematch.json()["room"]
        self.assertNotEqual(next_room["room_id"], first_room["room_id"])
        self.assertEqual(next_room["game_type"], first_room["game_type"])
        self.assertEqual(next_room["ai_player_id"], first_room["ai_player_id"])
        self.assertEqual(first_room["mode"], "human_first")
        self.assertEqual(next_room["mode"], "ai_first")
        self.assertEqual(next_room["turn"], "ai")


if __name__ == "__main__":
    unittest.main()
