import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import chips, database, framework
from app import main as main_module


class McpCompactProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-mcp-compact-")
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

    async def new_room(
        self, game_type="tictactoe", *, mode="ai_first", stake=0,
        ai="ai-1", human="human-1",
    ):
        response = await self.client.post(
            "/mcp/play",
            json={
                "action": "new", "player_id": ai, "opponent_id": human,
                "game_type": game_type, "mode": mode, "stake": stake,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def assert_full_room(self, payload):
        self.assertTrue(payload["bootstrap"])
        self.assertEqual(payload["status"], "playing")
        for field in ("board_state", "rules_text", "move_format", "turn", "status", "stake"):
            self.assertIn(field, payload["room"])
        self.assertEqual(payload["chip_balances"], {"ai": 200, "human": 200})

    def assert_compact_delta(self, payload, move):
        self.assertEqual(payload["your_move"], move)
        for forbidden in ("room", "board_state", "rules_text", "move_format", "chip_balances"):
            self.assertNotIn(forbidden, payload)
        self.assertIn(payload["status"], {"playing", "finished"})
        for required in ("room_id", "revision", "turn"):
            self.assertIn(required, payload)

    async def test_new_and_accept_return_full_bootstrap_while_pending_is_compact(self):
        started = await self.new_room()
        self.assert_full_room(started)

        pending = await self.new_room(
            "gomoku", mode="human_first", stake=9, ai="ai-p", human="human-p"
        )
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(pending["stake"], 9)
        self.assertEqual(pending["confirmation_decision"], "accepted")
        self.assertEqual(pending["chip_balances"], {"ai": 200, "human": 200})
        for forbidden in ("room", "board_state", "rules_text", "move_format"):
            self.assertNotIn(forbidden, pending)

        invited = framework.create_room(
            "connect4", "ai_first", "human", "human-a", "ai-a", stake=4
        )
        accepted = await self.client.post(
            "/mcp/play",
            json={"action": "accept", "player_id": "ai-a", "room_id": invited["room_id"]},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assert_full_room(accepted.json())
        self.assertEqual(accepted.json()["room"]["stake"], 4)

    async def test_state_is_full_but_move_and_wait_wakeup_are_compact_raw_deltas(self):
        started = await self.new_room()
        room_id = started["room"]["room_id"]
        waiter = asyncio.create_task(
            self.client.post(
                "/mcp/play",
                json={
                    "action": "move", "player_id": "ai-1", "room_id": room_id,
                    "move": {"row": 0, "col": 0}, "wait": True,
                },
            )
        )
        await asyncio.sleep(0.03)
        human_move = {"row": 1, "col": 1}
        human = await self.client.post(
            f"/api/rooms/{room_id}/move",
            json={"player_id": "human-1", "move": human_move, "message": "守中。"},
        )
        self.assertEqual(human.status_code, 200, human.text)
        resumed = (await asyncio.wait_for(waiter, timeout=1)).json()
        self.assert_compact_delta(resumed, {"row": 0, "col": 0})
        self.assertEqual(resumed["new_messages"][0]["move"], human_move)
        self.assertEqual(resumed["new_messages"][0]["message"], "守中。")

        state = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "ai-1", "room_id": room_id},
        )
        full = state.json()
        self.assertIn("room", full)
        for field in ("board_state", "rules_text", "move_format", "turn", "status", "stake"):
            self.assertIn(field, full["room"])
        self.assertNotIn("chip_balances", full)

    async def test_still_waiting_is_minimal(self):
        started = await self.new_room()
        room_id = started["room"]["room_id"]
        with patch.object(main_module, "MAX_WAIT_SECONDS", 0.03):
            response = await self.client.post(
                "/mcp/play",
                json={
                    "action": "move", "player_id": "ai-1", "room_id": room_id,
                    "move": {"row": 0, "col": 0}, "wait": True,
                },
            )
        payload = response.json()
        self.assertEqual(
            set(payload), {
                "ok", "status", "room_id", "revision", "turn",
                "current_actor_id", "current_actor_seat",
            }
        )
        self.assertEqual(payload["status"], "still_waiting")

    async def test_all_six_games_return_their_raw_move_delta(self):
        cases = {
            "tictactoe": {"row": 0, "col": 0},
            "gomoku": {"row": 7, "col": 7},
            "othello": {"row": 2, "col": 3},
            "connect4": {"col": 3},
            "dots_boxes": {"orientation": "h", "row": 0, "col": 0},
            "jungle": {"from_row": 6, "from_col": 0, "to_row": 5, "to_col": 0},
        }
        for index, (game_type, move) in enumerate(cases.items()):
            with self.subTest(game_type=game_type):
                ai, human = f"ai-{index}", f"human-{index}"
                started = await self.new_room(game_type, ai=ai, human=human)
                response = await self.client.post(
                    "/mcp/play",
                    json={
                        "action": "move", "player_id": ai,
                        "room_id": started["room"]["room_id"], "move": move,
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assert_compact_delta(response.json(), move)

    async def test_terminal_move_and_resign_include_settlement_and_balances(self):
        invited = framework.create_room(
            "tictactoe", "human_first", "human", "human-t", "ai-t", stake=7
        )
        accepted = await self.client.post(
            "/mcp/play",
            json={"action": "accept", "player_id": "ai-t", "room_id": invited["room_id"]},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        room_id = invited["room_id"]
        for human_move, ai_move in (
            ({"row": 1, "col": 0}, {"row": 0, "col": 0}),
            ({"row": 1, "col": 1}, {"row": 0, "col": 1}),
            ({"row": 2, "col": 2}, {"row": 0, "col": 2}),
        ):
            framework.play_move(room_id, "human", "human-t", human_move)
            terminal = await self.client.post(
                "/mcp/play",
                json={
                    "action": "move", "player_id": "ai-t",
                    "room_id": room_id, "move": ai_move,
                },
            )
        payload = terminal.json()
        self.assert_compact_delta(payload, {"row": 0, "col": 2})
        self.assertEqual(payload["winner"], "ai")
        self.assertEqual(payload["result"], "win")
        self.assertEqual(payload["settlement"]["delta"], {"ai": 7, "human": -7})
        self.assertEqual(payload["settlement"]["balances"], {"ai": 207, "human": 193})

        resign_room = (await self.new_room(ai="ai-r", human="human-r"))["room"]["room_id"]
        resigned = await self.client.post(
            "/mcp/play",
            json={"action": "resign", "player_id": "ai-r", "room_id": resign_room},
        )
        self.assertEqual(resigned.json()["your_action"], "resign")
        self.assertIn("settlement", resigned.json())
        self.assertNotIn("room", resigned.json())

    async def test_chips_ops_are_ai_owned_human_read_only_and_ledger_is_bounded(self):
        status = await self.client.post(
            "/mcp/play",
            json={
                "action": "chips", "op": "status", "player_id": "ai-chip",
                "opponent_id": "human-chip",
            },
        )
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["wallet"]["balance"], 200)
        self.assertEqual(status.json()["bound_human_balance"], 200)
        self.assertNotIn("ledger", status.json())

        checked = await self.client.post(
            "/mcp/play",
            json={
                "action": "chips", "op": "check_in", "player_id": "ai-chip",
                "opponent_id": "human-chip",
            },
        )
        self.assertTrue(checked.json()["claimed"])
        self.assertEqual(checked.json()["wallet"]["balance"], 220)
        self.assertEqual(chips.get_wallet("human", "human-chip")["balance"], 200)

        chips.change_balance("ai", "ai-chip", -720, "test_loss")
        bankrupt = await self.client.post(
            "/mcp/play",
            json={
                "action": "chips", "op": "bankruptcy", "player_id": "ai-chip",
                "opponent_id": "human-chip",
            },
        )
        self.assertEqual(bankrupt.json()["wallet"]["balance"], 50)
        self.assertEqual(chips.get_wallet("human", "human-chip")["balance"], 200)

        ledger = await self.client.post(
            "/mcp/play",
            json={
                "action": "chips", "op": "ledger", "player_id": "ai-chip",
                "opponent_id": "human-chip",
            },
        )
        self.assertLessEqual(len(ledger.json()["ledger"]), 5)
        too_many = await self.client.post(
            "/mcp/play",
            json={
                "action": "chips", "op": "ledger", "limit": 11,
                "player_id": "ai-chip", "opponent_id": "human-chip",
            },
        )
        self.assertEqual(too_many.status_code, 400)

    async def test_gomoku_delta_serialization_is_much_smaller_than_full_state(self):
        started = await self.new_room("gomoku", ai="ai-size", human="human-size")
        room_id = started["room"]["room_id"]
        moved = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-size", "room_id": room_id,
                "move": {"row": 7, "col": 7},
            },
        )
        state = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "ai-size", "room_id": room_id},
        )
        compact_size = len(json.dumps(moved.json(), ensure_ascii=False))
        full_size = len(json.dumps(state.json(), ensure_ascii=False))
        self.assertLess(compact_size, full_size * 0.35)


if __name__ == "__main__":
    unittest.main()
