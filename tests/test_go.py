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
from app.games import get_game
from app.games.go import Go
from app.games.go_engine import GoEngineError, engine_apply, engine_state


def play_history(moves):
    history = []
    state = None
    applied = None
    for move in moves:
        state, applied, history = engine_apply(history, move)
    return state, applied, history


class GoTenukiRulesTests(unittest.TestCase):
    def setUp(self):
        self.game = Go()
        self.participants = [
            {"player_id": "black-player", "token": "black"},
            {"player_id": "white-player", "token": "white"},
        ]

    def initialized(self):
        state = self.game.initial_state()
        state["players_by_color"] = {
            "black": "black-player",
            "white": "white-player",
        }
        return state

    def apply(self, state, move):
        actor = next(
            item for item in self.participants
            if item["token"] == state["to_play"]
        )
        return self.game.apply_action(state, move, actor)

    def test_fixed_profile_and_empty_authoritative_legal_actions(self):
        state = self.initialized()
        self.assertEqual(state["rule_version"], Go.RULE_VERSION)
        self.assertEqual(state["ko_rule"], "positional-superko")
        self.assertEqual(state["scoring_rule"], "area")
        self.assertEqual(state["komi"], 7.5)
        self.assertFalse(state["suicide_allowed"])
        self.assertEqual(state["to_play"], "black")
        self.assertEqual(len(state["legal_actions"]), 362)
        self.assertIn({"action": "pass"}, state["legal_actions"])
        self.assertTrue(self.game.supports_stakes)
        self.assertFalse(self.game.uses_custom_stake_settlement)
        self.assertIn("终局赢家获得一份房间底注", self.game.rules_text)
        self.assertIn("面积分相同判和", self.game.rules_text)

    def test_single_capture_and_multi_stone_capture_come_from_tenuki(self):
        state, applied, _history = play_history([
            {"action": "play", "row": 1, "col": 0},
            {"action": "play", "row": 0, "col": 0},
            {"action": "play", "row": 5, "col": 5},
            {"action": "play", "row": 10, "col": 10},
            {"action": "play", "row": 0, "col": 1},
        ])
        self.assertEqual(applied["captured"], [{"row": 0, "col": 0}])
        self.assertIsNone(state["board"][0][0])
        self.assertEqual(state["captures"], {"black": 1, "white": 0})

        state, applied, _history = play_history([
            {"action": "play", "row": 1, "col": 0},
            {"action": "play", "row": 0, "col": 0},
            {"action": "play", "row": 1, "col": 1},
            {"action": "play", "row": 0, "col": 1},
            {"action": "play", "row": 0, "col": 2},
        ])
        self.assertEqual(
            applied["captured"],
            [{"row": 0, "col": 0}, {"row": 0, "col": 1}],
        )
        self.assertEqual(state["captures"]["black"], 2)

        plugin_state = self.initialized()
        result = None
        for move in [
            {"action": "play", "row": 1, "col": 0},
            {"action": "play", "row": 0, "col": 0},
            {"action": "play", "row": 5, "col": 5},
            {"action": "play", "row": 10, "col": 10},
            {"action": "play", "row": 0, "col": 1},
        ]:
            result = self.apply(plugin_state, move)
            plugin_state = result.state
        self.assertEqual(
            result.public_event["go_delta"]["captured"],
            [{"row": 0, "col": 0}],
        )

    def test_suicide_and_simple_ko_are_rejected(self):
        suicide_history = [
            {"action": "play", "row": 0, "col": 1},
            {"action": "play", "row": 5, "col": 5},
            {"action": "play", "row": 1, "col": 0},
            {"action": "play", "row": 5, "col": 6},
            {"action": "play", "row": 1, "col": 2},
            {"action": "play", "row": 5, "col": 7},
            {"action": "play", "row": 2, "col": 1},
        ]
        state = engine_state(suicide_history)
        self.assertNotIn(
            {"action": "play", "row": 1, "col": 1},
            state["legal_actions"],
        )
        with self.assertRaisesRegex(GoEngineError, "不合法"):
            engine_apply(
                suicide_history,
                {"action": "play", "row": 1, "col": 1},
            )

        ko_history = [
            {"action": "play", "row": 3, "col": 3},
            {"action": "play", "row": 3, "col": 2},
            {"action": "play", "row": 4, "col": 2},
            {"action": "play", "row": 4, "col": 1},
            {"action": "play", "row": 5, "col": 3},
            {"action": "play", "row": 5, "col": 2},
            {"action": "play", "row": 4, "col": 4},
            {"action": "play", "row": 4, "col": 3},
        ]
        state = engine_state(ko_history)
        self.assertEqual(state["ko_point"], {"row": 4, "col": 2})
        self.assertNotIn(
            {"action": "play", "row": 4, "col": 2},
            state["legal_actions"],
        )

    def test_positional_superko_rejects_non_immediate_cycle(self):
        history = [
            {"action": "play", "row": row, "col": col}
            for row, col in [
                (0, 3), (0, 4), (1, 3), (1, 4), (1, 2), (2, 4),
                (1, 1), (2, 3), (2, 2), (3, 3), (3, 2), (4, 3),
                (3, 1), (4, 2), (3, 0), (4, 1), (0, 8), (4, 0),
                (1, 8), (0, 1), (2, 8), (1, 0), (3, 8), (2, 0),
                (4, 8), (0, 2), (0, 0),
            ]
        ]
        state = engine_state(history)
        self.assertNotIn(
            {"action": "play", "row": 0, "col": 1},
            state["legal_actions"],
        )
        self.assertEqual(state["position_history_count"], len(history) + 1)

    def test_double_pass_requires_matching_bilateral_dead_confirmation(self):
        state = self.initialized()
        state = self.apply(state, {"action": "play", "row": 0, "col": 0}).state
        state = self.apply(state, {"action": "pass"}).state
        state = self.apply(state, {"action": "pass"}).state
        self.assertEqual(state["phase"], "scoring")
        self.assertIn({"action": "confirm_score"}, state["legal_actions"])

        first = self.apply(state, {"action": "confirm_score"})
        self.assertIsNone(first.result)
        self.assertEqual(len(first.state["scoring_confirmations"]), 1)
        changed = self.apply(
            first.state, {"action": "toggle_dead", "row": 0, "col": 0}
        )
        self.assertEqual(changed.state["scoring_confirmations"], {})
        self.assertEqual(changed.public_event["go_delta"]["dead_added"], [
            {"row": 0, "col": 0},
        ])

        confirmed = self.apply(changed.state, {"action": "confirm_score"})
        self.assertIsNone(confirmed.result)
        settled = self.apply(confirmed.state, {"action": "confirm_score"})
        self.assertEqual(settled.state["phase"], "finished")
        self.assertIsNotNone(settled.result)
        self.assertEqual(settled.result["dead_stones"], [{"row": 0, "col": 0}])

    def test_dead_group_does_not_connect_same_color_stones_through_empty_points(self):
        state = self.initialized()
        for action in (
            {"action": "play", "row": 0, "col": 0},
            {"action": "play", "row": 10, "col": 10},
            {"action": "play", "row": 0, "col": 2},
            {"action": "pass"},
            {"action": "pass"},
        ):
            state = self.apply(state, action).state
        changed = self.apply(
            state, {"action": "toggle_dead", "row": 0, "col": 0}
        )
        self.assertEqual(changed.state["dead_stones"], [{"row": 0, "col": 0}])
        self.assertNotIn({"row": 0, "col": 2}, changed.state["dead_stones"])

    def test_area_scoring_adds_seven_point_five_komi(self):
        state = self.initialized()
        state = self.apply(state, {"action": "pass"}).state
        state = self.apply(state, {"action": "pass"}).state
        self.assertEqual(state["score_preview"], {"black": 0, "white": 7.5})
        state = self.apply(state, {"action": "confirm_score"}).state
        settled = self.apply(state, {"action": "confirm_score"})
        self.assertEqual(settled.result["scores"], {"black": 0.0, "white": 7.5})
        self.assertEqual(settled.result["winner_color"], "white")

    def test_public_projection_history_persistence_and_npc_authority(self):
        state = self.initialized()
        state = self.apply(state, {"action": "play", "row": 9, "col": 9}).state
        restored = engine_state(state["engine_history"], state["dead_stones"])
        self.assertEqual(restored["position_identity"], state["position_identity"])
        self.assertEqual(restored["legal_actions"], state["legal_actions"])
        public = self.game.public_state(state, self.participants)
        self.assertNotIn("engine_history", public)
        self.assertNotIn("position_identity", public)
        actor = self.participants[1]
        legal = self.game.npc_legal_actions(state, actor, self.participants)
        chosen = self.game.choose_local_npc_action(state, actor, self.participants)
        self.assertIn(chosen, legal)
        self.assertNotEqual(chosen, {"action": "pass"})


class GoFrameworkPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-go-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temporary.cleanup()

    def test_room_refresh_retains_history_and_generic_resignation(self):
        stake_room = framework.create_room(
            "go", "human_first", "human", "human-stake", "ai-stake",
            stake=4,
        )
        stake_room = framework.respond_to_invitation(
            stake_room["room_id"], "ai", "ai-stake", "accept"
        )
        staked_result = framework.resign(
            stake_room["room_id"], "ai", "ai-stake"
        )
        self.assertEqual(staked_result["winner_player_id"], "human-stake")
        human_delta = next(
            item for item in chips.list_ledger("human", "human-stake")
            if item["transaction_type"] == "duel_win"
        )
        ai_delta = next(
            item for item in chips.list_ledger("ai", "ai-stake")
            if item["transaction_type"] == "duel_loss"
        )
        self.assertEqual((human_delta["amount"], ai_delta["amount"]), (4, -4))
        self.assertEqual(human_delta["amount"] + ai_delta["amount"], 0)
        room = framework.create_room(
            "go", "human_first", "human", "human-go", "ai-go"
        )
        self.assertEqual(room["current_player"]["token"], "black")
        played = framework.play_move(
            room["room_id"], "human", "human-go",
            {"action": "play", "row": 3, "col": 3},
            expected_revision=room["revision"],
        )
        refreshed = framework.get_room(
            room["room_id"], "ai", "ai-go"
        )
        self.assertEqual(
            refreshed["board_state"]["engine_history"],
            played["board_state"]["engine_history"],
        )
        self.assertEqual(refreshed["board_state"]["board"][3][3], "black")
        resigned = framework.resign(room["room_id"], "ai", "ai-go")
        self.assertEqual(resigned["status"], "finished")
        self.assertEqual(resigned["winner_player_id"], "human-go")

        scoring = framework.create_room(
            "go", "human_first", "human", "human-score", "ai-score"
        )
        scoring = framework.play_move(
            scoring["room_id"], "human", "human-score", {"action": "pass"}
        )
        scoring = framework.play_move(
            scoring["room_id"], "ai", "ai-score", {"action": "pass"}
        )
        scoring = framework.play_move(
            scoring["room_id"], "human", "human-score",
            {"action": "confirm_score"},
        )
        refreshed_scoring = framework.get_room(
            scoring["room_id"], "ai", "ai-score"
        )
        self.assertEqual(refreshed_scoring["board_state"]["phase"], "scoring")
        self.assertEqual(
            refreshed_scoring["board_state"]["scoring_confirmations"],
            scoring["board_state"]["scoring_confirmations"],
        )
        self.assertEqual(
            refreshed_scoring["board_state"]["engine_history"],
            [{"action": "pass"}, {"action": "pass"}],
        )


class GoMcpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-go-mcp-")
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

    async def test_bootstrap_delta_and_repeatable_full_state(self):
        started = await self.client.post("/mcp/play", json={
            "action": "new",
            "player_id": "ai-go",
            "opponent_id": "human-go",
            "game_type": "go",
            "mode": "ai_first",
            "stake": 0,
        })
        self.assertEqual(started.status_code, 200, started.text)
        bootstrap = started.json()
        self.assertTrue(bootstrap["bootstrap"])
        board_state = bootstrap["room"]["board_state"]
        self.assertEqual(len(board_state["board"]), 19)
        self.assertNotIn("legal_actions", board_state)
        private = bootstrap["room"]["private_state"]
        self.assertEqual(private["legal_actions"], [{"action": "pass"}])
        spec = private["legal_action_spec"]
        self.assertEqual(spec["format"], "coordinate_rows_v1")
        self.assertEqual(spec["action"], "play")
        self.assertEqual(spec["columns_by_row"], ["0-18"] * 19)
        self.assertLess(len(json.dumps(private, ensure_ascii=False)), 500)
        self.assertNotIn("engine_history", board_state)
        room_id = bootstrap["room"]["room_id"]

        moved = await self.client.post("/mcp/play", json={
            "action": "move",
            "player_id": "ai-go",
            "room_id": room_id,
            "revision": bootstrap["room"]["revision"],
            "move": {"action": "play", "row": 9, "col": 9},
        })
        self.assertEqual(moved.status_code, 200, moved.text)
        delta = moved.json()
        self.assertNotIn("room", delta)
        self.assertNotIn("board_state", delta)
        self.assertNotIn("events", delta)

        request = {
            "action": "state", "player_id": "ai-go",
            "room_id": room_id, "full_state": True,
        }
        first = await self.client.post("/mcp/play", json=request)
        second = await self.client.post("/mcp/play", json=request)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["snapshot"], second.json()["snapshot"])
        snapshot = first.json()["snapshot"]
        self.assertEqual(snapshot["board_state"]["board"][9][9], "black")
        self.assertNotIn("engine_history", snapshot["board_state"])
        self.assertNotIn("legal_actions", snapshot["board_state"])
        self.assertEqual(snapshot["private_state"], {})


if __name__ == "__main__":
    unittest.main()
