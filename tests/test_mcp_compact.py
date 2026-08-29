import asyncio
import json
import os
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

    def assert_full_room(self, payload, *, ai_balance=205):
        self.assertTrue(payload["bootstrap"])
        self.assertEqual(payload["status"], "playing")
        for field in ("board_state", "rules_text", "move_format", "turn", "status", "stake"):
            self.assertIn(field, payload["room"])
        self.assertEqual(payload["chip_balances"], {"ai": ai_balance, "human": 200})

    def assert_compact_delta(self, payload):
        for forbidden in (
            "room", "board_state", "rules_text", "move_format",
            "chip_balances", "your_move", "your_action", "message",
        ):
            self.assertNotIn(forbidden, payload)
        self.assertIn(payload["status"], {"playing", "finished"})
        for required in ("room_id", "revision"):
            self.assertIn(required, payload)

    @staticmethod
    def event_cursor(room_id, player_id):
        conn = database.connect()
        try:
            row = conn.execute(
                """
                SELECT last_event_id FROM room_event_cursors
                WHERE room_id = ? AND player_id = ?
                """,
                (room_id, player_id),
            ).fetchone()
            return row["last_event_id"]
        finally:
            conn.close()

    async def test_new_and_accept_return_full_bootstrap_while_pending_is_compact(self):
        started = await self.new_room()
        self.assert_full_room(started)

        pending = await self.new_room(
            "gomoku", mode="human_first", stake=9, ai="ai-p", human="human-p"
        )
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(pending["stake"], 9)
        self.assertEqual(pending["confirmation_decision"], "accepted")
        self.assertEqual(pending["chip_balances"], {"ai": 205, "human": 200})
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
        self.assert_full_room(accepted.json(), ai_balance=200)
        self.assertEqual(accepted.json()["room"]["stake"], 4)

    async def test_bootstrap_is_once_then_state_move_and_wait_are_incremental(self):
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
        self.assert_compact_delta(resumed)
        self.assertEqual(resumed["events"], [{
            "name": "human-1", "move": human_move, "message": "守中。",
        }])

        state = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "ai-1", "room_id": room_id},
        )
        delta = state.json()
        self.assert_compact_delta(delta)
        self.assertNotIn("bootstrap", delta)
        self.assertNotIn("events", delta)

    async def test_still_waiting_is_minimal(self):
        started = await self.new_room()
        room_id = started["room"]["room_id"]
        moved = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-1", "room_id": room_id,
                "move": {"row": 0, "col": 0},
            },
        )
        self.assertEqual(moved.status_code, 200, moved.text)
        sent = await self.client.post(
            f"/api/rooms/{room_id}/messages",
            json={"player_id": "human-1", "message": "仍在思考。"},
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        cursor_before = self.event_cursor(room_id, "ai-1")
        with patch.object(main_module, "MCP_WAIT_SECONDS", 0.03):
            response = await self.client.post(
                "/mcp/play",
                json={
                    "action": "state", "player_id": "ai-1", "room_id": room_id,
                    "wait": True,
                },
            )
        payload = response.json()
        self.assertEqual(
            set(payload), {
                "ok", "status", "room_id", "revision",
            }
        )
        self.assertEqual(payload["status"], "still_waiting")
        self.assertEqual(self.event_cursor(room_id, "ai-1"), cursor_before)

        human = await self.client.post(
            f"/api/rooms/{room_id}/move",
            json={
                "player_id": "human-1",
                "move": {"row": 1, "col": 1},
            },
        )
        self.assertEqual(human.status_code, 200, human.text)
        delivered = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "ai-1", "room_id": room_id},
        )
        self.assertEqual(delivered.json()["events"], [
            {"name": "human-1", "message": "仍在思考。"},
            {"name": "human-1", "move": {"row": 1, "col": 1}},
        ])

    def test_mcp_wait_heartbeat_config_defaults_and_validates(self):
        configured = os.getenv("DUEL_MCP_WAIT_SECONDS", "30")
        self.assertEqual(
            main_module.MCP_WAIT_SECONDS,
            main_module._parse_mcp_wait_seconds(configured),
        )
        if "DUEL_MCP_WAIT_SECONDS" not in os.environ:
            self.assertEqual(main_module.MCP_WAIT_SECONDS, 30.0)
        for value in ("1", "12.5", "30", "45"):
            self.assertEqual(
                main_module._parse_mcp_wait_seconds(value), float(value)
            )
        for value in (None, "", "0", "45.1", "nan", "invalid"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    main_module._parse_mcp_wait_seconds(value)

        root = Path(__file__).resolve().parents[1]
        self.assertIn(
            "DUEL_MCP_WAIT_SECONDS=30",
            (root / ".env.example").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "`still_waiting` 只含房间号",
            (root / "docs" / "MCP_GUIDE.md").read_text(encoding="utf-8"),
        )

    async def test_all_six_games_return_generic_minimal_move_ack(self):
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
                self.assert_compact_delta(response.json())

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
        self.assert_compact_delta(payload)
        self.assertEqual(payload["winner"], "ai")
        self.assertEqual(payload["result"], "win")
        self.assertEqual(payload["settlement"]["delta"], {"ai": 7, "human": -7})
        self.assertEqual(payload["settlement"]["balances"], {"ai": 242, "human": 218})

        resign_room = (await self.new_room(ai="ai-r", human="human-r"))["room"]["room_id"]
        resigned = await self.client.post(
            "/mcp/play",
            json={"action": "resign", "player_id": "ai-r", "room_id": resign_room},
        )
        self.assertNotIn("your_action", resigned.json())
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
        self.assertEqual(bankrupt.json()["wallet"]["balance"], 55)
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
        compact_size = len(json.dumps(moved.json(), ensure_ascii=False))
        full_size = len(json.dumps(started, ensure_ascii=False))
        self.assertLess(compact_size, full_size * 0.35)

    async def test_first_state_after_another_participant_starts_room_bootstraps_once(self):
        room = framework.create_room(
            "tictactoe", "human_first", "human", "human-bootstrap", "ai-bootstrap"
        )
        first = await self.client.post(
            "/mcp/play",
            json={
                "action": "state", "player_id": "ai-bootstrap",
                "room_id": room["room_id"],
            },
        )
        self.assertTrue(first.json()["bootstrap"])
        second = await self.client.post(
            "/mcp/play",
            json={
                "action": "state", "player_id": "ai-bootstrap",
                "room_id": room["room_id"],
            },
        )
        self.assertNotIn("bootstrap", second.json())
        self.assertNotIn("room", second.json())

    async def test_events_use_distinct_names_and_preserve_viewer_visibility(self):
        participants = [
            {
                "player_id": "human-names", "display_name": "南杉",
                "role": "human",
            },
            {
                "player_id": "ai-reader", "display_name": "Sirius",
                "role": "ai",
            },
            {
                "player_id": "ai-blue", "display_name": "Blue",
                "role": "ai",
            },
            {
                "player_id": "ai-hidden", "display_name": "Hidden",
                "role": "ai",
            },
        ]
        room = framework.create_room(
            "liars_dice", "human_first", "human", "human-names",
            ordered_participants=participants,
        )
        bootstrap = await self.client.post(
            "/mcp/play",
            json={
                "action": "state", "player_id": "ai-reader",
                "room_id": room["room_id"],
            },
        )
        self.assertTrue(bootstrap.json()["bootstrap"])
        framework.post_message(room["room_id"], "human", "human-names", "甲")
        framework.post_message(room["room_id"], "ai", "ai-blue", "乙")
        framework.post_message(
            room["room_id"], "ai", "ai-hidden", "不可见",
            visible_to_player_ids={"ai-blue"},
        )

        cursor_before = self.event_cursor(room["room_id"], "ai-reader")

        state = await self.client.post(
            "/mcp/play",
            json={
                "action": "state", "player_id": "ai-reader",
                "room_id": room["room_id"],
            },
        )
        self.assertNotIn("events", state.json())
        self.assertEqual(
            self.event_cursor(room["room_id"], "ai-reader"), cursor_before
        )

        bid = {"action": "bid", "quantity": 1, "face": 1}
        framework.play_move(room["room_id"], "human", "human-names", bid)
        delivered = await self.client.post(
            "/mcp/play",
            json={
                "action": "state", "player_id": "ai-reader",
                "room_id": room["room_id"],
            },
        )
        self.assertEqual(delivered.json()["events"], [
            {"name": "南杉", "message": "甲"},
            {"name": "Blue", "message": "乙"},
            {"name": "南杉", "move": bid},
        ])
        repeated = await self.client.post(
            "/mcp/play",
            json={
                "action": "state", "player_id": "ai-reader",
                "room_id": room["room_id"],
            },
        )
        self.assertNotIn("events", repeated.json())


if __name__ == "__main__":
    unittest.main()
