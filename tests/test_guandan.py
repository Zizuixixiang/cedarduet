import asyncio
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database, framework
from app import main as main_module
from app.games import game_catalog
from app.games.guandan import Guandan
from third_party.rlcard_guandan.engine import GuandanEngine


def participants():
    return [
        {
            "player_id": "human-1", "display_name": "南山", "role": "human",
            "participant_kind": "human", "seat_index": 0,
        },
        {
            "player_id": "ai-1", "display_name": "小机", "role": "ai",
            "participant_kind": "bound_machine", "seat_index": 1,
        },
        {
            "player_id": "npc:one", "display_name": "青禾", "role": "ai",
            "participant_kind": "system_npc", "npc_persona_id": "one", "seat_index": 2,
        },
        {
            "player_id": "npc:two", "display_name": "石桥", "role": "ai",
            "participant_kind": "system_npc", "npc_persona_id": "two", "seat_index": 3,
        },
    ]


class GuandanPluginTests(unittest.TestCase):
    def setUp(self):
        self.players = participants()
        self.game = Guandan(random.Random(11))
        self.state = self.game.initialize_for_first_player(self.players, "human-1")

    def test_catalog_fixed_four_npc_ready_and_team_stakes(self):
        item = next(entry for entry in game_catalog() if entry["game_type"] == "guandan")
        self.assertEqual(item["display_name"], "掼蛋")
        self.assertEqual(item["category"], "card")
        self.assertEqual(item["allowed_player_counts"], [4])
        self.assertEqual(item["recommended_players"], 4)
        self.assertTrue(item["supports_npcs"])
        self.assertTrue(item["supports_stakes"])
        self.assertTrue(item["supports_multiplayer_stakes"])
        self.assertIn("完整升级赛", self.game.rules_text)
        self.assertIn("获胜队两名玩家各 +stake", self.game.rules_text)
        self.assertIn("不按领先等级", self.game.rules_text)
        self.assertIn("CedarDuet 钱包政策", self.game.rules_text)
        self.assertIn("action_id", self.game.move_format)

    def test_plugin_legal_actions_are_direct_core_projection_and_validate_id_only(self):
        core = GuandanEngine.legal_actions(self.state, "human-1")
        private = self.game.private_state(self.state, self.players[0], self.players)
        self.assertEqual(
            [item["action_id"] for item in private["legal_actions"]],
            [item["action_id"] for item in core],
        )
        self.assertEqual(private["legal_action_count"], len(core))
        self.game.validate_action(
            self.state,
            {"action": "act", "action_id": core[0]["action_id"]},
            self.players[0],
        )
        with self.assertRaisesRegex(ValueError, "只接受"):
            self.game.validate_action(
                self.state,
                {"action": "play", "card_ids": core[0]["card_ids"]},
                self.players[0],
            )

    def test_public_projection_contains_no_other_hands_or_observations(self):
        public = self.game.public_state(self.state, self.players)
        serialized = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("hands", public)
        self.assertNotIn("played_cards", public)
        self.assertNotIn("legal_actions", public)
        self.assertEqual(public["hand_counts"], {
            player["player_id"]: 27 for player in self.players
        })
        for other in self.players[1:]:
            for card in self.state["hands"][other["player_id"]]:
                self.assertNotIn(card["id"], serialized)
        private = self.game.private_state(self.state, self.players[0], self.players)
        self.assertEqual(len(private["hand"]), 27)
        self.assertEqual(
            {item["team"] for item in (
                self.game.participant_summary(public, player, self.players)
                for player in self.players
            )},
            {"甲队", "乙队"},
        )

    def test_play_delta_is_public_and_npc_actions_only_carry_engine_action_id(self):
        before_other_ids = {
            card["id"] for player in self.players[1:]
            for card in self.state["hands"][player["player_id"]]
        }
        legal = GuandanEngine.legal_actions(self.state, "human-1")
        move = {"action": "act", "action_id": legal[0]["action_id"]}
        result = self.game.apply_action(self.state, move, self.players[0])
        result = self.game.progress_after_action(
            {}, move, self.players[0], self.players, result
        )
        delta = result.public_event["guandan_delta"]
        self.assertEqual(delta["kind"], "play")
        self.assertEqual(delta["player_id"], "human-1")
        self.assertEqual(delta["hand_counts"]["human-1"], 26)
        serialized = json.dumps(delta, ensure_ascii=False)
        for card_id in before_other_ids:
            self.assertNotIn(card_id, serialized)

        npc_state = self.game.initialize_for_first_player(self.players, "ai-1")
        npc_actions = self.game.npc_legal_actions(npc_state, self.players[1], self.players)
        self.assertTrue(npc_actions)
        self.assertTrue(all(set(item) == {"action", "action_id"} for item in npc_actions))
        self.assertTrue(all(item["action"] == "act" for item in npc_actions))

    def test_full_match_team_result_has_no_fake_individual_stake_winner(self):
        self.state["match_result"] = {
            "winner_team": "A",
            "winning_player_ids": ["human-1", "npc:one"],
            "placements": ["human-1", "npc:one", "ai-1", "npc:two"],
            "team_levels": {"A": "A", "B": "K"},
            "deal_count": 8,
        }
        self.state["phase"] = "finished"
        result = self.game.result_for(self.state, self.players)
        self.assertEqual(result["winner_team"], "A")
        self.assertNotIn("winner_player_id", result)
        self.assertIn("完整掼蛋升级赛", result["result_text"])
        deltas = self.game.settlement_deltas(self.state, result, self.players, 9)
        self.assertEqual(deltas, {
            "human-1": 9,
            "ai-1": -9,
            "npc:one": 9,
            "npc:two": -9,
        })
        self.assertEqual(sum(deltas.values()), 0)
        self.assertEqual(
            self.game.settlement_deltas(
                self.state, {"draw": True}, self.players, 9
            ),
            {item["player_id"]: 0 for item in self.players},
        )


class GuandanMcpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-guandan-")
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
        ordered = [
            {key: value for key, value in item.items() if key != "seat_index"}
            for item in participants()
        ]
        self.room = framework.create_room(
            "guandan", "human_first", "human", "human-1", "ai-1",
            ordered_participants=ordered, first_player_id="human-1",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        main_module.revision_events = self.original_events
        self.db_patch.stop()
        self.temporary.cleanup()

    async def test_bootstrap_delta_and_repeatable_full_state_are_private_safe(self):
        first = await self.client.post("/mcp/play", json={
            "action": "state", "player_id": "ai-1", "room_id": self.room["room_id"],
        })
        self.assertEqual(first.status_code, 200, first.text)
        bootstrap = first.json()
        self.assertTrue(bootstrap["bootstrap"])
        self.assertEqual(len(bootstrap["room"]["private_state"]["hand"]), 27)
        bootstrap_json = json.dumps(bootstrap, ensure_ascii=False)
        for player_id in ("human-1", "npc:one", "npc:two"):
            for card in self.room["board_state"]["hands"][player_id]:
                self.assertNotIn(card["id"], bootstrap_json)

        human_legal = GuandanEngine.legal_actions(
            self.room["board_state"], "human-1"
        )[0]
        moved = await self.client.post(
            f"/api/rooms/{self.room['room_id']}/move",
            json={
                "player_id": "human-1",
                "move": {"action": "act", "action_id": human_legal["action_id"]},
            },
        )
        self.assertEqual(moved.status_code, 200, moved.text)
        delta_response = await self.client.post("/mcp/play", json={
            "action": "state", "player_id": "ai-1", "room_id": self.room["room_id"],
        })
        delta = delta_response.json()
        self.assertNotIn("room", delta)
        self.assertTrue(delta["your_turn"])
        self.assertEqual(len(delta["private_state"]["hand"]), 27)
        public_deltas = [
            event["guandan_delta"] for event in delta.get("events", [])
            if isinstance(event.get("guandan_delta"), dict)
        ]
        self.assertTrue(public_deltas)
        self.assertEqual(public_deltas[-1]["kind"], "play")
        self.assertEqual(public_deltas[-1]["hand_counts"]["human-1"], 26)

        request = {
            "action": "state", "player_id": "ai-1",
            "room_id": self.room["room_id"], "full_state": True,
        }
        full_one = await self.client.post("/mcp/play", json=request)
        full_two = await self.client.post("/mcp/play", json=request)
        self.assertEqual(full_one.status_code, 200, full_one.text)
        self.assertEqual(full_one.json()["snapshot"], full_two.json()["snapshot"])
        snapshot = full_one.json()["snapshot"]
        self.assertEqual(snapshot["board_state"]["board_kind"], "guandan")
        self.assertEqual(len(snapshot["private_state"]["hand"]), 27)
        snapshot_json = json.dumps(snapshot, ensure_ascii=False)
        latest = framework.get_room(self.room["room_id"], "ai", "ai-1")
        for player_id in ("human-1", "npc:one", "npc:two"):
            for card in latest["board_state"]["hands"][player_id]:
                self.assertNotIn(card["id"], snapshot_json)

    async def test_framework_accepts_positive_stake_room(self):
        ordered = [
            {key: value for key, value in item.items() if key != "seat_index"}
            for item in participants()
        ]
        room = framework.create_room(
            "guandan", "human_first", "human", "human-1", "ai-1",
            ordered_participants=ordered, first_player_id="human-1", stake=3,
        )
        self.assertEqual(room["stake"], 3)
        self.assertEqual(room["status"], "pending")


if __name__ == "__main__":
    unittest.main()
