import asyncio
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import httpx

from app import chips, database, framework
from app import main as main_module
from app.games import game_catalog, get_game
from app.games.junqi import Junqi
from app.games.junqi_engine import (
    JunqiEngineError,
    engine_apply,
    engine_initial,
    engine_moves,
    engine_shuffle,
    engine_swap,
    engine_validate,
)
from app.npc_controller import run_current_npc_turn


PARTICIPANTS = [
    {
        "player_id": "human-1",
        "display_name": "人类一号",
        "role": "human",
        "participant_kind": "human",
        "seat_index": 0,
        "token": "X",
        "active": True,
    },
    {
        "player_id": "ai-1",
        "display_name": "小机一号",
        "role": "ai",
        "participant_kind": "bound_machine",
        "seat_index": 1,
        "token": "O",
        "active": True,
    },
]


class JunqiUpstreamEngineTests(unittest.TestCase):
    def empty_board(self):
        return {square: None for square in engine_initial()["board"]}

    def test_standard_inventory_and_setup_constraints(self):
        initial = engine_initial()
        board = initial["board"]
        expected = {
            0: 2, 1: 1, 2: 1, 3: 2, 4: 2, 5: 2,
            6: 2, 7: 3, 8: 3, 9: 3, 10: 3, 11: 1,
        }
        self.assertEqual(len(board), 60)
        self.assertEqual(len(initial["bunkers"]), 10)
        for color in ("b", "r"):
            counts = {rank: 0 for rank in expected}
            for piece in board.values():
                if piece and piece["color"] == color:
                    counts[piece["rank"]] += 1
            self.assertEqual(counts, expected)
            self.assertEqual(sum(counts.values()), 25)
        self.assertEqual(
            engine_validate(board),
            {"validPlacement": True, "validInventory": True},
        )
        with self.assertRaises(JunqiEngineError):
            engine_swap(board, "b", "a1", "a6")
        shuffled = engine_shuffle(board, "b")
        self.assertEqual(
            engine_validate(shuffled),
            {"validPlacement": True, "validInventory": True},
        )
        self.assertEqual(
            {square: piece for square, piece in shuffled.items() if piece and piece["color"] == "r"},
            {square: piece for square, piece in board.items() if piece and piece["color"] == "r"},
        )

    def test_railroad_blocking_and_engineer_turning(self):
        board = self.empty_board()
        board["e2"] = {"color": "b", "rank": 9}
        board["a5"] = {"color": "r", "rank": 11}
        board["e5"] = {"color": "r", "rank": 8}
        engineer_targets = {
            item["to"] for item in engine_moves(board, "b") if item["from"] == "e2"
        }
        self.assertIn("a3", engineer_targets)
        self.assertIn("a4", engineer_targets)
        self.assertIn("a5", engineer_targets)
        self.assertNotIn("a6", engineer_targets)

        board["e2"] = {"color": "b", "rank": 8}
        ordinary_targets = {
            item["to"] for item in engine_moves(board, "b") if item["from"] == "e2"
        }
        self.assertIn("a2", ordinary_targets)
        self.assertNotIn("a3", ordinary_targets)

    def test_collision_bomb_landmine_equal_rank_and_flag(self):
        cases = (
            (0, 10, "equal"),
            (9, 10, "capture"),
            (1, 10, "dies"),
            (7, 7, "equal"),
            (9, 11, "capture"),
        )
        for attacker, defender, outcome in cases:
            with self.subTest(attacker=attacker, defender=defender):
                board = self.empty_board()
                board["a6"] = {"color": "b", "rank": attacker}
                board["a7"] = {"color": "r", "rank": defender}
                result = engine_apply(board, "b", "a6", "a7")
                self.assertEqual(result["move"]["result_type"], outcome)
                self.assertEqual(result["move"]["attacker"]["rank"], attacker)
                self.assertEqual(result["move"]["defender"]["rank"], defender)
                if outcome == "capture":
                    self.assertEqual(result["board"]["a7"], {"color": "b", "rank": attacker})
                elif outcome == "dies":
                    self.assertIsNone(result["board"]["a6"])
                    self.assertEqual(result["board"]["a7"], {"color": "r", "rank": defender})
                else:
                    self.assertIsNone(result["board"]["a6"])
                    self.assertIsNone(result["board"]["a7"])

    def test_headquarters_piece_is_immobile(self):
        board = self.empty_board()
        board["b1"] = {"color": "b", "rank": 8}
        self.assertFalse(any(item["from"] == "b1" for item in engine_moves(board, "b")))


class JunqiPluginTests(unittest.TestCase):
    def setUp(self):
        self.game = Junqi()

    def state(self):
        return self.game.initialize_for_first_player(deepcopy(PARTICIPANTS), "human-1")

    def test_catalog_contract_and_standard_stakes(self):
        item = next(item for item in game_catalog() if item["game_type"] == "junqi")
        self.assertEqual(item["allowed_player_counts"], [2])
        self.assertTrue(item["supports_npcs"])
        self.assertTrue(item["uses_local_npc_strategy"])
        self.assertTrue(item["supports_stakes"])
        self.assertFalse(self.game.uses_custom_stake_settlement)
        self.assertIn("赢家获得一份房间底注", self.game.rules_text)
        self.assertIn("不设棋子、回合或终局原因倍率", self.game.rules_text)

    def test_privacy_projection_never_exposes_opponent_ranks_or_public_legal_actions(self):
        state = self.state()
        public = self.game.public_state(deepcopy(state), deepcopy(PARTICIPANTS))
        human = self.game.private_state(
            deepcopy(state), deepcopy(PARTICIPANTS[0]), deepcopy(PARTICIPANTS)
        )
        ai = self.game.private_state(
            deepcopy(state), deepcopy(PARTICIPANTS[1]), deepcopy(PARTICIPANTS)
        )
        self.assertEqual(len(human["pieces"]), 25)
        self.assertEqual(len(ai["pieces"]), 25)
        self.assertTrue(human["legal_actions"])
        self.assertFalse(ai["legal_actions"])
        self.assertNotIn("legal_actions", public)
        self.assertNotIn("legal_moves", public)
        self.assertNotIn("terminal_reveal", public)
        self.assertFalse(any(
            isinstance(piece, dict) and "rank" in piece
            for piece in public["board"].values()
        ))
        human_squares = set(human["pieces"])
        ai_squares = set(ai["pieces"])
        self.assertTrue(human_squares.isdisjoint(ai_squares))
        self.assertTrue(all(set(action) <= {"action", "from", "to"} for action in human["legal_actions"]))

    def test_terminal_projection_reveals_every_remaining_rank(self):
        state = self.state()
        state["phase"] = "finished"
        state["active_player_id"] = None
        terminal = self.game.public_state(state, PARTICIPANTS)
        remaining = {
            square: piece
            for square, piece in state["board"].items()
            if piece is not None
        }
        self.assertTrue(terminal["terminal_reveal"])
        self.assertEqual(
            {
                square: piece["rank"]
                for square, piece in terminal["board"].items()
                if piece is not None
            },
            {square: piece["rank"] for square, piece in remaining.items()},
        )

    def test_setup_is_operable_sequential_and_npc_auto_setup_is_authoritative(self):
        state = self.state()
        swap = next(action for action in self.game.legal_actions_for(state, "human-1") if action["action"] == "swap")
        swapped = self.game.apply_action(state, swap, PARTICIPANTS[0])
        self.assertTrue(swapped.retain_turn)
        self.assertEqual(swapped.event_visible_to_player_ids, ["human-1"])
        ready = self.game.apply_action(swapped.state, {"action": "ready"}, PARTICIPANTS[0])
        self.assertEqual(ready.state["active_player_id"], "ai-1")
        npc_legal = self.game.npc_legal_actions(ready.state, PARTICIPANTS[1], PARTICIPANTS)
        self.assertEqual(npc_legal, [{"action": "auto_setup"}])
        chosen = self.game.choose_local_npc_action(ready.state, PARTICIPANTS[1], PARTICIPANTS)
        self.assertIn(chosen, npc_legal)
        started = self.game.apply_action(ready.state, chosen, PARTICIPANTS[1])
        self.assertEqual(started.state["phase"], "play")
        self.assertEqual(started.state["active_player_id"], "human-1")
        self.assertEqual(started.next_player_id, "human-1")

    def test_commander_collision_reveals_only_flag_and_battle(self):
        state = self.state()
        state["phase"] = "play"
        state["active_player_id"] = "human-1"
        state["setup_ready"] = {"human-1": True, "ai-1": True}
        state["board"] = {square: None for square in state["board"]}
        state["board"]["a6"] = {"color": "b", "rank": 0}
        state["board"]["a7"] = {"color": "r", "rank": 1}
        state["board"]["b12"] = {"color": "r", "rank": 11}
        result = self.game.apply_action(
            state, {"action": "move", "from": "a6", "to": "a7"}, PARTICIPANTS[0]
        )
        public = self.game.public_state(result.state, PARTICIPANTS)
        self.assertFalse(public["commanders_alive"]["r"])
        self.assertEqual(public["board"]["b12"]["rank"], 11)
        self.assertEqual(public["last_battle"]["attacker_name"], "炸弹")
        self.assertEqual(public["last_battle"]["defender_name"], "司令")
        revealed = [
            square for square, piece in public["board"].items()
            if isinstance(piece, dict) and "rank" in piece
        ]
        self.assertEqual(revealed, ["b12"])

    def test_flag_capture_is_terminal_from_authoritative_collision(self):
        state = self.state()
        state["phase"] = "play"
        state["active_player_id"] = "human-1"
        state["setup_ready"] = {"human-1": True, "ai-1": True}
        state["board"] = {square: None for square in state["board"]}
        state["board"]["a6"] = {"color": "b", "rank": 9}
        state["board"]["a7"] = {"color": "r", "rank": 11}
        result = self.game.apply_action(
            state, {"action": "move", "from": "a6", "to": "a7"}, PARTICIPANTS[0]
        )
        self.assertEqual(result.state["winner_player_id"], "human-1")
        self.assertEqual(result.state["terminal_reason"], "flag_captured")
        self.assertEqual(result.result, {"winner_player_id": "human-1", "draw": False})
        self.assertEqual(result.public_event["junqi_delta"]["battle"]["defender_name"], "军旗")

    def test_setup_event_projection_defensively_hides_opponents_arrangement(self):
        event = {
            "sender": {"player_id": "human-1"},
            "move": {"action": "swap", "from": "a1", "to": "a2"},
        }
        self.assertIsNone(self.game.project_event(event, PARTICIPANTS[1], PARTICIPANTS))
        self.assertEqual(
            self.game.project_event(event, PARTICIPANTS[0], PARTICIPANTS), event
        )


class JunqiFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-junqi-")
        self.addCleanup(self.temporary.cleanup)
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()

    def test_refresh_persists_setup_and_private_timelines(self):
        room = framework.create_room(
            "junqi", "human_first", "human", "human-1", "ai-1"
        )
        shuffled = framework.play_move(
            room["room_id"], "human", "human-1", {"action": "shuffle"}
        )
        refreshed = framework.get_room(room["room_id"], "human", "human-1")
        self.assertEqual(refreshed["board_state"]["board"], shuffled["board_state"]["board"])
        self.assertEqual(refreshed["current_player_id"], "human-1")
        self.assertEqual(
            engine_validate(refreshed["board_state"]["board"]),
            {"validPlacement": True, "validInventory": True},
        )
        self.assertTrue(framework.list_timeline(room["room_id"], viewer_player_id="human-1"))
        self.assertFalse(framework.list_timeline(room["room_id"], viewer_player_id="ai-1"))

    def test_framework_setup_to_play_and_projection(self):
        room = framework.create_room(
            "junqi", "human_first", "human", "human-2", "ai-2"
        )
        first = framework.project_room_for_viewer(room, "human-2")
        second = framework.project_room_for_viewer(room, "ai-2")
        self.assertEqual(len(first["private_state"]["pieces"]), 25)
        self.assertEqual(len(second["private_state"]["pieces"]), 25)
        self.assertFalse(any(
            isinstance(piece, dict) and "rank" in piece
            for piece in first["board_state"]["board"].values()
        ))
        room = framework.play_move(
            room["room_id"], "human", "human-2", {"action": "ready"}
        )
        self.assertEqual(room["current_player_id"], "ai-2")
        room = framework.play_move(
            room["room_id"], "ai", "ai-2", {"action": "auto_setup"}
        )
        self.assertEqual(room["board_state"]["phase"], "play")
        self.assertEqual(room["current_player_id"], "human-2")

    def test_standard_stake_room_and_resignation_settle_exactly(self):
        room = framework.create_room(
            "junqi", "human_first", "human", "human-stake", "ai-stake", stake=3
        )
        self.assertEqual(room["status"], "pending")
        room = framework.respond_to_invitation(
            room["room_id"], "ai", "ai-stake", "accept"
        )
        self.assertEqual(room["status"], "playing")
        room = framework.resign(room["room_id"], "ai", "ai-stake")
        self.assertEqual(room["winner_player_id"], "human-stake")
        human_delta = next(
            item for item in chips.list_ledger("human", "human-stake")
            if item["transaction_type"] == "duel_win"
        )
        ai_delta = next(
            item for item in chips.list_ledger("ai", "ai-stake")
            if item["transaction_type"] == "duel_loss"
        )
        self.assertEqual(human_delta["amount"], 3)
        self.assertEqual(ai_delta["amount"], -3)
        self.assertEqual(human_delta["amount"] + ai_delta["amount"], 0)

    def test_system_npc_auto_sets_up_then_chooses_an_authoritative_move(self):
        room = framework.create_room(
            "junqi",
            "human_first",
            "human",
            "human-npc",
            ordered_participants=[
                {
                    "player_id": "npc:quiet",
                    "display_name": "安静 NPC",
                    "role": "ai",
                    "participant_kind": "system_npc",
                    "npc_persona_id": "quiet",
                },
                {"player_id": "human-npc", "role": "human"},
            ],
            first_player_id="npc:quiet",
        )
        first = asyncio.run(run_current_npc_turn(room["room_id"]))
        self.assertEqual(first.source, "local")
        self.assertEqual(first.action, {"action": "auto_setup"})
        self.assertEqual(first.room["current_player_id"], "human-npc")
        room = framework.play_move(
            room["room_id"], "human", "human-npc", {"action": "auto_setup"}
        )
        before = get_game("junqi").npc_legal_actions(
            room["board_state"],
            next(item for item in room["participants"] if item["player_id"] == "npc:quiet"),
            room["participants"],
        )
        second = asyncio.run(run_current_npc_turn(room["room_id"]))
        self.assertIn(second.action, before)
        self.assertEqual(second.source, "local")


class JunqiMcpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-junqi-mcp-")
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

    async def test_bootstrap_delta_full_state_and_collision_event_are_private_safe(self):
        created = await self.client.post(
            "/mcp/play",
            json={
                "action": "new",
                "player_id": "ai-mcp",
                "opponent_id": "human-mcp",
                "game_type": "junqi",
                "mode": "ai_first",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        bootstrap = created.json()
        self.assertTrue(bootstrap["bootstrap"])
        room = bootstrap["room"]
        self.assertEqual(room["board_state"]["phase"], "setup")
        self.assertEqual(len(room["private_state"]["pieces"]), 25)
        self.assertEqual(
            [item for item in room["private_state"]["legal_actions"] if item["action"] == "auto_setup"],
            [{"action": "auto_setup"}],
        )
        self.assertFalse(any(
            item["action"] == "swap"
            for item in room["private_state"]["legal_actions"]
        ))
        spec = room["private_state"]["legal_action_spec"]
        self.assertEqual(spec["format"], "junqi_setup_v1")
        self.assertEqual(len(spec["swap"]["own_squares"]), 25)
        self.assertLess(
            len(json.dumps(room["private_state"], ensure_ascii=False)), 2_000
        )
        self.assertFalse(any(
            isinstance(piece, dict) and "rank" in piece
            for piece in room["board_state"]["board"].values()
        ))

        pieces = room["private_state"]["pieces"]
        destinations = spec["swap"]["destinations_by_rank"]

        def allowed(square):
            rank = pieces[square]
            key = {0: "0_bomb", 10: "10_landmine", 11: "11_flag"}.get(
                rank, "other"
            )
            return destinations[key]

        swap = next(
            {"action": "swap", "from": start, "to": end}
            for start in spec["swap"]["own_squares"]
            for end in spec["swap"]["own_squares"]
            if start != end and end in allowed(start) and start in allowed(end)
        )
        swapped = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-mcp",
                "room_id": room["room_id"], "revision": room["revision"],
                "move": swap,
            },
        )
        self.assertEqual(swapped.status_code, 200, swapped.text)
        self.assertTrue(swapped.json()["your_turn"])
        self.assertEqual(
            swapped.json()["private_state"]["legal_action_spec"]["format"],
            "junqi_setup_v1",
        )

        ai_setup = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-mcp", "room_id": room["room_id"],
                "revision": swapped.json()["revision"],
                "move": {"action": "auto_setup"},
            },
        )
        self.assertEqual(ai_setup.status_code, 200, ai_setup.text)
        self.assertNotIn("room", ai_setup.json())
        after_human = framework.play_move(
            room["room_id"], "human", "human-mcp", {"action": "auto_setup"}
        )
        self.assertEqual(after_human["current_player_id"], "ai-mcp")

        delta_response = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "ai-mcp", "room_id": room["room_id"]},
        )
        self.assertEqual(delta_response.status_code, 200, delta_response.text)
        delta = delta_response.json()
        self.assertTrue(delta["your_turn"])
        self.assertTrue(delta["private_state"]["legal_actions"])
        self.assertNotIn("board_state", delta)
        self.assertNotIn("room", delta)

        full_request = {
            "action": "state", "player_id": "ai-mcp",
            "room_id": room["room_id"], "full_state": True,
        }
        full_one = await self.client.post("/mcp/play", json=full_request)
        full_two = await self.client.post("/mcp/play", json=full_request)
        self.assertEqual(full_one.status_code, 200, full_one.text)
        self.assertEqual(full_one.json()["snapshot"], full_two.json()["snapshot"])
        snapshot = full_one.json()["snapshot"]
        self.assertNotIn("public_actions", snapshot["board_state"])
        self.assertEqual(len(snapshot["private_state"]["pieces"]), 25)
        self.assertFalse(any(
            isinstance(piece, dict) and "rank" in piece
            for piece in snapshot["board_state"]["board"].values()
        ))

        legal = delta["private_state"]["legal_actions"][0]
        played = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-mcp", "room_id": room["room_id"],
                "revision": snapshot["revision"], "move": legal,
            },
        )
        self.assertEqual(played.status_code, 200, played.text)
        move_delta = played.json()
        self.assertNotIn("room", move_delta)
        event = next(item for item in move_delta["events"] if "junqi_delta" in item)
        public_move = event["junqi_delta"]
        self.assertEqual(public_move["from"], legal["from"])
        self.assertEqual(public_move["to"], legal["to"])
        if "battle" in public_move:
            self.assertIn(public_move["battle"]["result"], {"capture", "dies", "equal"})
            self.assertIn("attacker_name", public_move["battle"])
            self.assertIn("defender_name", public_move["battle"])


if __name__ == "__main__":
    unittest.main()
