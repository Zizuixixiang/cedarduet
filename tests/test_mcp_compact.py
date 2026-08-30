import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import chips, database, framework
from app.games import GAMES, get_game
from app import main as main_module


class ConstantDiceRng:
    def __init__(self, value):
        self.value = value

    def randint(self, minimum, maximum):
        if not minimum <= self.value <= maximum:
            raise AssertionError("测试骰点越界")
        return self.value


class StackedShuffleRng:
    def __init__(self, draw_ranks):
        self.draw_ranks = list(draw_ranks)

    def shuffle(self, cards):
        selected = []
        remaining = list(cards)
        for rank in self.draw_ranks:
            index = next(
                index for index, card in enumerate(remaining)
                if card["rank"] == rank
            )
            selected.append(remaining.pop(index))
        cards[:] = remaining + list(reversed(selected))


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

    def assert_full_room(self, payload, *, ai_balance=205, human_balance=200):
        self.assertTrue(payload["bootstrap"])
        self.assertEqual(payload["status"], "playing")
        for field in ("board_state", "rules_text", "move_format", "turn", "status", "stake"):
            self.assertIn(field, payload["room"])
        rules_text = payload["room"]["rules_text"]
        plugin_rules = get_game(payload["room"]["game_type"]).rules_text
        self.assertTrue(rules_text.startswith(plugin_rules))
        self.assertEqual(rules_text.count(framework.GLOBAL_ROOM_CHAT_RULE), 1)
        self.assertIn("不得主动逐项泄露自己的真实未公开", rules_text)
        self.assertIn("公开以系统结果为准", rules_text)
        self.assertIn("正常诈唬不受限", rules_text)
        self.assertEqual(
            payload["chip_balances"],
            {"ai": ai_balance, "human": human_balance},
        )

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

    @staticmethod
    def bootstrap_claimed(room_id, player_id):
        conn = database.connect()
        try:
            row = conn.execute(
                """
                SELECT mcp_bootstrapped FROM room_event_cursors
                WHERE room_id = ? AND player_id = ?
                """,
                (room_id, player_id),
            ).fetchone()
            return bool(row["mcp_bootstrapped"])
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

    async def test_join_liars_dice_bootstrap_keeps_rules_and_appends_chat_rule_once(self):
        waiting = framework.create_room(
            "liars_dice", "human_first", "human", "human-join"
        )
        self.assertEqual(waiting["status"], "waiting")
        joined = await self.client.post(
            "/mcp/play",
            json={
                "action": "join",
                "player_id": "ai-join",
                "opponent_id": "human-join",
                "room_id": waiting["room_id"],
            },
        )
        self.assertEqual(joined.status_code, 200, joined.text)
        self.assert_full_room(joined.json(), ai_balance=200)
        self.assertIn("每人初始 5 枚六面骰", joined.json()["room"]["rules_text"])

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

    async def test_full_state_is_repeatable_compact_and_complete_for_all_games(self):
        layout_keys = {
            "aeroplane_chess": "planes",
            "banqi": "board",
            "blackjack": "players",
            "texas_holdem": "players",
            "tictactoe": "board",
            "gomoku": "board",
            "go": "board",
            "gandengyan": "current_trick",
            "train_cards": "table_cards",
            "othello": "board",
            "connect4": "board",
            "checkers": "board",
            "chess": "board",
            "chinese_checkers": "pieces",
            "dots_boxes": "horizontal_edges",
            "liars_dice": "dice_counts",
            "yahtzee": "scorecards",
            "uno": "hand_counts",
            "jungle": "board",
            "xiangqi": "board",
            "zhajinhua": "players",
        }
        for index, (game_type, layout_key) in enumerate(layout_keys.items()):
            with self.subTest(game_type=game_type):
                ai, human = f"ai-snapshot-{index}", f"human-snapshot-{index}"
                bootstrap = await self.new_room(game_type, ai=ai, human=human)
                room = bootstrap["room"]
                request = {
                    "action": "state", "player_id": ai,
                    "room_id": room["room_id"], "full_state": True,
                }
                first = await self.client.post("/mcp/play", json=request)
                second = await self.client.post("/mcp/play", json=request)
                self.assertEqual(first.status_code, 200, first.text)
                self.assertEqual(second.status_code, 200, second.text)
                payload = first.json()
                self.assertTrue(payload["full_state"])
                self.assertNotIn("bootstrap", payload)
                self.assertEqual(payload["snapshot"], second.json()["snapshot"])
                snapshot = payload["snapshot"]
                self.assertEqual(snapshot["room_id"], room["room_id"])
                self.assertEqual(snapshot["game"], game_type)
                self.assertEqual(snapshot["revision"], room["revision"])
                self.assertEqual(snapshot["status"], room["status"])
                self.assertEqual(
                    snapshot["current_actor"]["player_id"],
                    room["current_actor"]["player_id"],
                )
                self.assertEqual(len(snapshot["participants"]), 2)
                self.assertEqual(
                    snapshot["board_state"][layout_key],
                    room["board_state"][layout_key],
                )
                self.assertEqual(snapshot["private_state"], room["private_state"])
                for key in (
                    "rules_text", "move_format", "chip_balances",
                    "action_history", "move_history", "dice_rolls",
                ):
                    self.assertNotIn(key, snapshot)
                    self.assertNotIn(key, snapshot["board_state"])
                snapshot_size = len(json.dumps(payload, ensure_ascii=False))
                bootstrap_size = len(json.dumps(bootstrap, ensure_ascii=False))
                self.assertLess(snapshot_size, bootstrap_size * 0.65)

                board = snapshot["board_state"]
                if game_type == "uno":
                    self.assertNotIn("cards", board)
                    self.assertIn("hand", snapshot["private_state"])
                elif game_type == "gandengyan":
                    self.assertNotIn("cards", board)
                    self.assertIn("hand", snapshot["private_state"])
                elif game_type == "train_cards":
                    self.assertNotIn("cards", board)
                    self.assertNotIn("participant_order", board)
                    self.assertNotIn("seen_position_hashes", board)
                    self.assertEqual(
                        snapshot["private_state"],
                        {"legal_actions": [{"action": "flip"}]},
                    )
                elif game_type == "liars_dice":
                    self.assertNotIn("dice_by_player", board)
                    self.assertIn("dice", snapshot["private_state"])
                elif game_type == "blackjack":
                    self.assertNotIn("card_id", json.dumps(board))
                    self.assertEqual(board["dealer"]["hand"][1], {"hidden": True})
                elif game_type == "texas_holdem":
                    self.assertNotIn("engine_state", board)
                    self.assertEqual(len(snapshot["private_state"]["hand"]), 2)
                    self.assertEqual(board["showdown"], {})
                elif game_type == "banqi":
                    self.assertEqual(
                        {cell for row in board["board"] for cell in row},
                        {"hidden"},
                    )
                elif game_type == "aeroplane_chess":
                    self.assertNotIn("path_mappings", board)
                    self.assertIn("legal_actions", board)
                elif game_type == "chess":
                    self.assertNotIn("legal_moves", board)
                    self.assertEqual(
                        board["legal_actions"],
                        room["board_state"]["legal_actions"],
                    )
                elif game_type == "go":
                    self.assertNotIn("engine_history", board)
                    self.assertNotIn("position_identity", board)
                    self.assertNotIn("legal_actions", board)
                    self.assertIn("legal_actions", snapshot["private_state"])
                elif game_type == "chinese_checkers":
                    self.assertNotIn("nodes", board)
                    self.assertNotIn("camps", board)
                    self.assertTrue(all(
                        set(move) <= {"from", "to", "kind"}
                        for move in board["legal_moves"]
                    ))
                elif game_type == "xiangqi":
                    self.assertTrue(all(
                        set(move) == {
                            "from_row", "from_col", "to_row", "to_col",
                        }
                        for move in board["legal_moves"]
                    ))
                elif game_type == "zhajinhua":
                    self.assertNotIn("cards", board)
                    self.assertFalse(board["revealed_hands"])
                    self.assertEqual(
                        snapshot["private_state"]["hand"],
                        [{"hidden": True}] * 3,
                    )

    async def test_full_state_does_not_claim_bootstrap_or_consume_events(self):
        room = framework.create_room(
            "uno", "human_first", "human", "human-resync", "ai-resync"
        )
        framework.post_message(room["room_id"], "human", "human-resync", "尚未读")
        cursor_before = self.event_cursor(room["room_id"], "ai-resync")
        self.assertFalse(self.bootstrap_claimed(room["room_id"], "ai-resync"))
        request = {
            "action": "state", "player_id": "ai-resync",
            "room_id": room["room_id"], "full_state": True,
        }
        for repeat in range(2):
            response = await self.client.post(
                "/mcp/play", json={**request, "wait": bool(repeat)}
            )
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertTrue(payload["full_state"])
            self.assertNotIn("bootstrap", payload)
            self.assertEqual(len(payload["snapshot"]["private_state"]["hand"]), 7)
            self.assertNotIn("cards", payload["snapshot"]["board_state"])
            self.assertEqual(
                self.event_cursor(room["room_id"], "ai-resync"), cursor_before
            )
            self.assertFalse(self.bootstrap_claimed(room["room_id"], "ai-resync"))

        bootstrap = await self.client.post(
            "/mcp/play",
            json={
                "action": "state", "player_id": "ai-resync",
                "room_id": room["room_id"],
            },
        )
        self.assertTrue(bootstrap.json()["bootstrap"])
        self.assertEqual(bootstrap.json()["events"], [{
            "name": "human-resync", "message": "尚未读",
        }])
        self.assertTrue(self.bootstrap_claimed(room["room_id"], "ai-resync"))

        after = await self.client.post("/mcp/play", json=request)
        self.assertTrue(after.json()["full_state"])
        self.assertTrue(self.bootstrap_claimed(room["room_id"], "ai-resync"))
        compact = await self.client.post(
            "/mcp/play",
            json={
                "action": "state", "player_id": "ai-resync",
                "room_id": room["room_id"],
            },
        )
        self.assert_compact_delta(compact.json())
        self.assertNotIn("bootstrap", compact.json())

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

    async def test_all_deterministic_board_games_return_generic_minimal_move_ack(self):
        cases = {
            "tictactoe": {"row": 0, "col": 0},
            "gomoku": {"row": 7, "col": 7},
            "othello": {"row": 2, "col": 3},
            "connect4": {"col": 3},
            "checkers": {
                "from_row": 5, "from_col": 0, "to_row": 4, "to_col": 1
            },
            "dots_boxes": {"orientation": "h", "row": 0, "col": 0},
            "jungle": {"from_row": 6, "from_col": 0, "to_row": 5, "to_col": 0},
            "chess": {
                "from_row": 6, "from_col": 4, "to_row": 4, "to_col": 4
            },
            "chinese_checkers": None,
            "xiangqi": {
                "from_row": 9, "from_col": 0, "to_row": 8, "to_col": 0
            },
        }
        for index, (game_type, move) in enumerate(cases.items()):
            with self.subTest(game_type=game_type):
                ai, human = f"ai-{index}", f"human-{index}"
                started = await self.new_room(game_type, ai=ai, human=human)
                if move is None:
                    legal = started["room"]["board_state"]["legal_moves"][0]
                    move = {
                        key: legal[key] for key in ("from", "to", "kind")
                    }
                response = await self.client.post(
                    "/mcp/play",
                    json={
                        "action": "move", "player_id": ai,
                        "room_id": started["room"]["room_id"], "move": move,
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assert_compact_delta(response.json())

    async def test_random_and_automatic_results_are_immediate_public_deltas(self):
        banqi = await self.new_room(
            "banqi", ai="ai-banqi-delta", human="human-banqi-delta"
        )
        flip = banqi["room"]["board_state"]["legal_actions"][0]
        response = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-banqi-delta",
                "room_id": banqi["room"]["room_id"], "move": flip,
            },
        )
        banqi_delta = response.json()["events"][0]["banqi_delta"]
        self.assertEqual(banqi_delta["action"], "flip")
        self.assertRegex(banqi_delta["piece"], r"^[rb]:[kabrncp]$")
        self.assertNotEqual(banqi_delta["piece"], "hidden")

        aeroplane_game = GAMES["aeroplane_chess"]
        with patch.object(aeroplane_game, "_rng", ConstantDiceRng(6)):
            aeroplane = await self.new_room(
                "aeroplane_chess",
                ai="ai-aeroplane-delta", human="human-aeroplane-delta",
            )
            rolled = await self.client.post(
                "/mcp/play",
                json={
                    "action": "move", "player_id": "ai-aeroplane-delta",
                    "room_id": aeroplane["room"]["room_id"],
                    "move": {"action": "roll"},
                },
            )
            roll_delta = rolled.json()["events"][0]["aeroplane_delta"]
            self.assertEqual(roll_delta["value"], 6)
            self.assertEqual(roll_delta["consecutive_sixes"], 1)
            self.assertFalse(roll_delta["auto_pass"])
            self.assertEqual(len(roll_delta["movable_plane_ids"]), 4)
            moved = await self.client.post(
                "/mcp/play",
                json={
                    "action": "move", "player_id": "ai-aeroplane-delta",
                    "room_id": aeroplane["room"]["room_id"],
                    "move": {
                        "action": "move",
                        "plane_id": roll_delta["movable_plane_ids"][0],
                        "plane_index": 0,
                    },
                },
            )
            plane_delta = moved.json()["events"][0]["aeroplane_delta"]
            self.assertEqual(plane_delta["from"]["zone"], "airport")
            self.assertEqual(plane_delta["to"]["zone"], "launch")
            self.assertIn("capture_events", plane_delta)
            self.assertIn("reached_home", plane_delta)

        yahtzee_game = GAMES["yahtzee"]
        with patch.object(yahtzee_game, "_rng", ConstantDiceRng(6)):
            yahtzee = await self.new_room(
                "yahtzee", ai="ai-yahtzee-delta", human="human-yahtzee-delta"
            )
            rolled = await self.client.post(
                "/mcp/play",
                json={
                    "action": "move", "player_id": "ai-yahtzee-delta",
                    "room_id": yahtzee["room"]["room_id"],
                    "move": {"action": "roll"},
                },
            )
            roll_delta = rolled.json()["events"][0]["yahtzee_delta"]
            self.assertEqual(roll_delta["dice"], [6] * 5)
            self.assertEqual(roll_delta["roll_number"], 1)
            scored = await self.client.post(
                "/mcp/play",
                json={
                    "action": "move", "player_id": "ai-yahtzee-delta",
                    "room_id": yahtzee["room"]["room_id"],
                    "move": {"action": "score", "category": "yahtzee"},
                },
            )
            score_delta = scored.json()["events"][0]["yahtzee_delta"]
            self.assertEqual(score_delta["score"], 50)
            self.assertEqual(score_delta["yahtzee_bonus"], 0)

        uno = await self.new_room(
            "uno", ai="ai-uno-delta", human="human-uno-delta"
        )
        own_uno_ids = {
            card["id"] for card in uno["room"]["private_state"]["hand"]
        }
        drawn = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-uno-delta",
                "room_id": uno["room"]["room_id"],
                "move": {"action": "draw"},
            },
        )
        uno_delta = drawn.json()["events"][0]["uno_delta"]
        self.assertEqual(uno_delta["action"], "draw")
        self.assertIn("hand_counts", uno_delta)
        self.assertIn("deck_count", uno_delta)
        encoded_uno = json.dumps(uno_delta, ensure_ascii=False)
        self.assertNotIn("card_id", encoded_uno)
        self.assertTrue(all(card_id not in encoded_uno for card_id in own_uno_ids))

        gandengyan = await self.new_room(
            "gandengyan",
            ai="ai-gdy-delta", human="human-gdy-delta",
        )
        hand = gandengyan["room"]["private_state"]["hand"]
        play = next(
            action
            for action in gandengyan["room"]["private_state"]["legal_actions"]
            if action["action"] == "play"
        )
        played = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-gdy-delta",
                "room_id": gandengyan["room"]["room_id"], "move": play,
            },
        )
        gdy_delta = played.json()["events"][0]["gandengyan_delta"]
        self.assertEqual(gdy_delta["action"], "play")
        self.assertEqual(
            {card["id"] for card in gdy_delta["cards"]}, set(play["card_ids"])
        )
        self.assertIn("multiplier", gdy_delta)
        unplayed_ids = {card["id"] for card in hand} - set(play["card_ids"])
        encoded_gdy = json.dumps(gdy_delta, ensure_ascii=False)
        self.assertTrue(all(card_id not in encoded_gdy for card_id in unplayed_ids))

        blackjack_game = GAMES["blackjack"]
        scripted = StackedShuffleRng(["9", "5", "10", "8", "6", "6", "2"])
        with patch.object(blackjack_game, "_rng", scripted):
            blackjack = await self.new_room(
                "blackjack", ai="ai-bj-delta", human="human-bj-delta"
            )
            hit = await self.client.post(
                "/mcp/play",
                json={
                    "action": "move", "player_id": "ai-bj-delta",
                    "room_id": blackjack["room"]["room_id"],
                    "move": {"action": "hit"},
                },
            )
            hit_delta = hit.json()["events"][0]["blackjack_delta"]
            self.assertEqual(hit_delta["new_card"]["rank"], "2")
            self.assertNotIn("dealer", hit_delta)
            stood = await self.client.post(
                "/mcp/play",
                json={
                    "action": "move", "player_id": "ai-bj-delta",
                    "room_id": blackjack["room"]["room_id"],
                    "move": {"action": "stand"},
                },
            )
            stand_delta = stood.json()["events"][0]["blackjack_delta"]
            self.assertNotIn("dealer", stand_delta)
            framework.play_move(
                blackjack["room"]["room_id"],
                "human", "human-bj-delta", {"action": "stand"},
            )
            terminal = await self.client.post(
                "/mcp/play",
                json={
                    "action": "state", "player_id": "ai-bj-delta",
                    "room_id": blackjack["room"]["room_id"],
                },
            )
            terminal_delta = next(
                event["blackjack_delta"]
                for event in terminal.json()["events"]
                if "blackjack_delta" in event
            )
            self.assertFalse(terminal_delta["dealer"]["hole_hidden"])
            self.assertGreaterEqual(len(terminal_delta["dealer"]["hand"]), 2)
            self.assertEqual(
                set(terminal_delta["outcomes_by_player"]),
                {"ai-bj-delta", "human-bj-delta"},
            )

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
        self.assert_full_room(first.json(), ai_balance=200)
        redecorated = framework._decorate(first.json()["room"])
        self.assertEqual(
            redecorated["rules_text"].count(framework.GLOBAL_ROOM_CHAT_RULE),
            1,
        )
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
