import json
import random
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database, framework
from app import main as main_module
from app.games import GAMES, game_catalog
from app.games.mahjong import Mahjong, build_wall


def participants():
    return [
        {
            "player_id": f"p{index}",
            "display_name": f"玩家{index}",
            "seat_index": index,
            "role": "human" if index == 0 else "ai",
            "participant_kind": "human" if index == 0 else "bound_machine",
            "token": f"seat-{index}",
            "active": True,
        }
        for index in range(4)
    ]


WAITING_HAND = [
    "W2", "W3", "W4", "T2", "T3", "T4", "B2", "B3", "B4",
    "W6", "W7", "J1", "J1",
]
LOW_FAN_WAITING_HAND = [
    "W1", "W2", "W3", "W4", "W5", "W6", "B7", "B8", "B9",
    "T2", "T3", "J1", "J1",
]
FILLER = ["W4", "W5", "W6", "B1", "B2", "B3", "T6", "T7", "T8", "F1", "J1"]


class MahjongRulesTests(unittest.TestCase):
    def setUp(self):
        self.game = Mahjong(random.Random(20260830))
        self.players = participants()
        self.sequence = 0

    def tiles(self, codes):
        values = []
        for code in codes:
            self.sequence += 1
            values.append({"id": f"fixture-{self.sequence}", "code": code})
        return values

    def state(self):
        state = self.game._empty_state([item["player_id"] for item in self.players])
        state["phase"] = "discard"
        state["flow"]["phase"] = "discard"
        state["turn_player_id"] = "p0"
        state["wall"] = self.tiles(["F4", "F3", "F2", "J3"])
        return state

    @staticmethod
    def action(game, state, player_id, kind):
        return next(
            item for item in game.legal_actions_for(state, player_id)
            if item["kind"] == kind
        )

    @staticmethod
    def submit(action):
        return {"action": "act", "action_id": action["action_id"]}

    @staticmethod
    def zone_count(state):
        return (
            len(state["wall"])
            + sum(len(hand) for hand in state["hands"].values())
            + sum(len(pile) for pile in state["discards"].values())
            + sum(
                len(meld["tiles"])
                for melds in state["melds"].values()
                for meld in melds
            )
            + len(state["robbed_kong_tiles"])
        )

    def test_real_wall_deal_conservation_and_fixed_seat_winds(self):
        state = self.game.initialize(self.players)
        self.assertEqual(len(build_wall()), 136)
        self.assertEqual(self.zone_count(state), 136)
        self.assertEqual(len(state["wall"]), 83)
        self.assertEqual(
            {player_id: len(hand) for player_id, hand in state["hands"].items()},
            {"p0": 14, "p1": 13, "p2": 13, "p3": 13},
        )
        self.assertEqual(state["seat_winds"], {"p0": "东", "p1": "南", "p2": "西", "p3": "北"})
        self.assertEqual(state["dealer_player_id"], "p0")
        self.assertEqual(state["turn_player_id"], "p0")
        advanced = next(
            result
            for action in self.game.legal_actions_for(state, "p0")
            if action["kind"] == "discard"
            for result in [self.game.apply_action(state, self.submit(action), self.players[0])]
            if result.state["phase"] == "discard"
        )
        self.assertEqual(self.zone_count(advanced.state), 136)
        self.assertEqual(len(advanced.state["wall"]), 82)

    def test_cedarduet_stake_policy_covers_all_terminal_win_types_and_draw(self):
        self.assertTrue(self.game.supports_stakes)
        self.assertTrue(self.game.supports_multiplayer_stakes)
        self.assertIn("不是官方麻将竞赛计分", self.game.rules_text)
        self.assertIn("total_fan 不乘算钱包筹码", self.game.rules_text)

        cases = (
            (
                {"winner_player_id": "p0", "win_type": "self_draw", "total_fan": 88},
                {"p0": 15, "p1": -5, "p2": -5, "p3": -5},
            ),
            (
                {
                    "winner_player_id": "p2", "win_type": "discard",
                    "source_player_id": "p0", "total_fan": 8,
                },
                {"p0": -15, "p1": 0, "p2": 15, "p3": 0},
            ),
            (
                {
                    "winner_player_id": "p3", "win_type": "rob_kong",
                    "source_player_id": "p1", "total_fan": 64,
                },
                {"p0": 0, "p1": -15, "p2": 0, "p3": 15},
            ),
            ({"draw": True, "reason": "wall_exhausted"}, {"p0": 0, "p1": 0, "p2": 0, "p3": 0}),
        )
        for result, expected in cases:
            with self.subTest(result=result):
                deltas = self.game.settlement_deltas({}, result, self.players, 5)
                self.assertEqual(deltas, expected)
                self.assertEqual(sum(deltas.values()), 0)

    def test_self_draw_and_discard_win_use_engine_and_enforce_eight_fan(self):
        state = self.state()
        state["hands"]["p0"] = self.tiles([*WAITING_HAND, "W8"])
        state["drawn_tile_id"] = state["hands"]["p0"][-1]["id"]
        state["draw_context"] = {"player_id": "p0", "replacement": False, "wall_last": False}
        hu = self.action(self.game, state, "p0", "hu")
        self.assertGreaterEqual(hu["total_fan"], 8)
        won = self.game.apply_action(state, self.submit(hu), self.players[0])
        self.assertEqual(won.result["win_type"], "self_draw")
        self.assertEqual(won.result["winner_player_id"], "p0")
        self.assertEqual(won.result["total_fan"], sum(item["fan"] for item in won.result["fans"]))

        low = self.state()
        low["hands"]["p0"] = self.tiles([*LOW_FAN_WAITING_HAND, "T4"])
        low["drawn_tile_id"] = low["hands"]["p0"][-1]["id"]
        low["draw_context"] = {"player_id": "p0", "replacement": False, "wall_last": False}
        evaluation = self.game._fan_evaluation(low, "p0", low["hands"]["p0"][-1], source_kind="self_draw")
        self.assertLess(evaluation["total_fan"], 8)
        self.assertFalse(any(item["kind"] == "hu" for item in self.game.legal_actions_for(low, "p0")))

        discard = self.state()
        discard["hands"]["p0"] = self.tiles(["W8", *FILLER, "F2", "F3"])
        discard["hands"]["p1"] = self.tiles(WAITING_HAND)
        tile = discard["hands"]["p0"][0]
        move = next(
            item for item in self.game.legal_actions_for(discard, "p0")
            if item["kind"] == "discard" and item["tile_id"] == tile["id"]
        )
        window = self.game.apply_action(discard, self.submit(move), self.players[0])
        self.assertEqual(window.next_player_id, "p1")
        hu = self.action(self.game, window.state, "p1", "hu")
        won = self.game.apply_action(window.state, self.submit(hu), self.players[1])
        self.assertEqual(won.result["win_type"], "discard")
        self.assertEqual(won.result["source_player_id"], "p0")

    def test_peng_precedes_chi_and_ming_gang_draws_replacement(self):
        state = self.state()
        state["hands"]["p0"] = self.tiles(["W3", *FILLER, "F2", "F3"])
        state["hands"]["p1"] = self.tiles(["W1", "W2", *FILLER])
        state["hands"]["p2"] = self.tiles(["W3", "W3", "W3", *FILLER[:10]])
        discard = next(
            item for item in self.game.legal_actions_for(state, "p0")
            if item["kind"] == "discard" and item["tile_id"] == state["hands"]["p0"][0]["id"]
        )
        opened = self.game.apply_action(state, self.submit(discard), self.players[0])
        queue = opened.state["response_window"]["queue"]
        self.assertEqual(queue[0]["player_id"], "p2")
        self.assertEqual(queue[0]["priority"], "peng_gang")
        self.assertEqual(queue[-1]["player_id"], "p1")
        self.assertEqual(queue[-1]["priority"], "chi")

        peng = self.action(self.game, opened.state, "p2", "peng")
        penged = self.game.apply_action(opened.state, self.submit(peng), self.players[2])
        self.assertEqual(penged.state["melds"]["p2"][0]["kind"], "peng")
        self.assertEqual(penged.next_player_id, "p2")

        passed = self.game.apply_action(
            opened.state,
            self.submit(self.action(self.game, opened.state, "p2", "pass")),
            self.players[2],
        )
        chi = self.action(self.game, passed.state, "p1", "chi")
        eaten = self.game.apply_action(passed.state, self.submit(chi), self.players[1])
        self.assertEqual(eaten.state["melds"]["p1"][0]["kind"], "chi")
        self.assertEqual(eaten.state["melds"]["p1"][0]["source_player_id"], "p0")
        self.assertEqual(eaten.next_player_id, "p1")

        wall_before = len(opened.state["wall"])
        gang = self.action(self.game, opened.state, "p2", "ming_gang")
        claimed = self.game.apply_action(opened.state, self.submit(gang), self.players[2])
        self.assertEqual(claimed.next_player_id, "p2")
        self.assertEqual(len(claimed.state["wall"]), wall_before - 1)
        self.assertEqual(claimed.state["melds"]["p2"][0]["kind"], "ming_gang")
        self.assertEqual(len(claimed.state["melds"]["p2"][0]["tiles"]), 4)
        self.assertIsNotNone(claimed.state["drawn_tile_id"])

    def test_concealed_gang_and_added_gang_robbing_window(self):
        concealed = self.state()
        concealed["hands"]["p0"] = self.tiles(["J2"] * 4 + FILLER[:10])
        action = self.action(self.game, concealed, "p0", "concealed_gang")
        wall_before = len(concealed["wall"])
        konged = self.game.apply_action(concealed, self.submit(action), self.players[0])
        self.assertEqual(konged.state["melds"]["p0"][0]["kind"], "concealed_gang")
        self.assertEqual(len(konged.state["wall"]), wall_before - 1)
        public = self.game.public_state(konged.state, self.players)
        self.assertTrue(all(tile.get("back") for tile in public["melds"]["p0"][0]["tiles"]))
        self.assertNotIn("J2", json.dumps(public, ensure_ascii=False))

        rob = self.state()
        pung_tiles = self.tiles(["W8", "W8", "W8"])
        rob["melds"]["p0"] = [{
            "kind": "peng", "tiles": pung_tiles, "engine_tile": "W8",
            "offer": 1, "source_player_id": "p3",
        }]
        rob["hands"]["p0"] = self.tiles(["W8", *FILLER[:9]])
        rob["hands"]["p1"] = self.tiles(WAITING_HAND)
        add = self.action(self.game, rob, "p0", "added_gang")
        announced = self.game.apply_action(rob, self.submit(add), self.players[0])
        self.assertEqual(announced.state["phase"], "response")
        self.assertEqual(announced.next_player_id, "p1")
        passed = self.game.apply_action(
            announced.state,
            self.submit(self.action(self.game, announced.state, "p1", "pass")),
            self.players[1],
        )
        self.assertEqual(passed.state["melds"]["p0"][0]["kind"], "added_gang")
        self.assertEqual(len(passed.state["melds"]["p0"][0]["tiles"]), 4)
        self.assertEqual(passed.next_player_id, "p0")
        self.assertIsNotNone(passed.state["drawn_tile_id"])
        hu = self.action(self.game, announced.state, "p1", "hu")
        won = self.game.apply_action(announced.state, self.submit(hu), self.players[1])
        self.assertEqual(won.result["win_type"], "rob_kong")
        self.assertEqual(won.state["melds"]["p0"][0]["kind"], "peng")
        self.assertEqual(len(won.state["robbed_kong_tiles"]), 1)

    def test_multiple_hu_responders_are_nearest_first_and_pass_advances(self):
        state = self.state()
        state["hands"]["p0"] = self.tiles(["W8", *FILLER, "F2", "F3"])
        state["hands"]["p1"] = self.tiles(WAITING_HAND)
        state["hands"]["p2"] = self.tiles(WAITING_HAND)
        discard = next(
            item for item in self.game.legal_actions_for(state, "p0")
            if item["kind"] == "discard" and item["tile_id"] == state["hands"]["p0"][0]["id"]
        )
        opened = self.game.apply_action(state, self.submit(discard), self.players[0])
        hu_queue = [
            item["player_id"] for item in opened.state["response_window"]["queue"]
            if item["priority"] == "hu"
        ]
        self.assertEqual(hu_queue[:2], ["p1", "p2"])
        passed = self.game.apply_action(
            opened.state,
            self.submit(self.action(self.game, opened.state, "p1", "pass")),
            self.players[1],
        )
        self.assertEqual(passed.next_player_id, "p2")
        won = self.game.apply_action(
            passed.state,
            self.submit(self.action(self.game, passed.state, "p2", "hu")),
            self.players[2],
        )
        self.assertEqual(won.result["winner_player_ids"], ["p2"])

    def test_private_projection_shanten_events_and_npc_are_authoritative(self):
        state = self.game.initialize(self.players)
        public = self.game.public_state(state, self.players)
        private = self.game.private_state(state, self.players[0], self.players)
        serialized_public = json.dumps(public, ensure_ascii=False)
        for tile in state["hands"]["p0"]:
            self.assertNotIn(tile["id"], serialized_public)
        self.assertEqual(len(private["hand"]), 14)
        self.assertIn(private["shanten_basis"], {"current", "after_best_discard"})
        legal = self.game.npc_legal_actions(state, self.players[0], self.players)
        chosen = self.game.choose_local_npc_action(state, self.players[0], self.players)
        self.assertIn(chosen, legal)
        projected = self.game.project_event(
            {"move": chosen, "event_type": "move"}, self.players[1], self.players
        )
        self.assertEqual(projected["move"], {"action": "act"})
        applied = self.game.apply_action(state, chosen, self.players[0])
        applied = self.game.progress_after_action(state, chosen, self.players[0], self.players, applied)
        self.assertIn("mahjong_delta", applied.public_event)
        self.assertNotIn('"wall":', json.dumps(applied.public_event, ensure_ascii=False))

    def test_authoritative_local_npcs_finish_a_persistent_hand_without_losing_tiles(self):
        game = Mahjong(random.Random(1))
        state = game.initialize(self.players)
        for _step in range(300):
            zones = [
                *state["wall"],
                *(tile for hand in state["hands"].values() for tile in hand),
                *(tile for pile in state["discards"].values() for tile in pile),
                *(
                    tile
                    for melds in state["melds"].values()
                    for meld in melds
                    for tile in meld["tiles"]
                ),
                *state["robbed_kong_tiles"],
            ]
            self.assertEqual(len(zones), 136)
            self.assertEqual(len({tile["id"] for tile in zones}), 136)
            if state["game_result"] is not None:
                break
            player_id = state["turn_player_id"]
            actor = next(item for item in self.players if item["player_id"] == player_id)
            authoritative = game.npc_legal_actions(state, actor, self.players)
            action = game.choose_local_npc_action(state, actor, self.players)
            self.assertIn(action, authoritative)
            state = game.apply_action(state, action, actor).state
        else:
            self.fail("本地 NPC 在 300 个权威动作内未能结束单手")
        self.assertIsNotNone(state["game_result"])


class MahjongFrameworkAndMcpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-mahjong-")
        self.db_patch = patch.object(database, "DB_PATH", Path(self.temporary.name) / "test.db")
        self.db_patch.start()
        database.init_db()
        self.game_patch = patch.dict(GAMES, {"mahjong": Mahjong(random.Random(99))})
        self.game_patch.start()
        self.original_events = main_module.revision_events
        main_module.revision_events = main_module.RevisionEvents()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_module.app),
            base_url="http://duel.test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        main_module.revision_events = self.original_events
        self.game_patch.stop()
        self.db_patch.stop()
        self.temporary.cleanup()

    async def test_registry_mcp_bootstrap_full_state_delta_and_refresh_persistence(self):
        catalog = {item["game_type"]: item for item in game_catalog()}
        self.assertEqual(catalog["mahjong"]["allowed_player_counts"], [4])
        self.assertTrue(catalog["mahjong"]["supports_stakes"])
        self.assertTrue(catalog["mahjong"]["supports_multiplayer_stakes"])
        created = await self.client.post("/mcp/play", json={
            "action": "new", "player_id": "ai-m", "opponent_id": "human-m",
            "participant_ids": ["human-m", "ai-m", "ai-2", "ai-3"],
            "target_player_count": 4, "game_type": "mahjong", "mode": "ai_first",
        })
        self.assertEqual(created.status_code, 200, created.text)
        bootstrap = created.json()
        self.assertTrue(bootstrap["bootstrap"])
        room = bootstrap["room"]
        self.assertEqual(room["current_player_id"], "human-m")
        self.assertEqual(room["board_state"]["wall_remaining"], 83)
        self.assertNotIn("hands", room["board_state"])
        self.assertEqual(len(room["private_state"]["hand"]), 13)
        script = await self.client.get("/static/games/mahjong.js")
        stylesheet = await self.client.get("/static/games/mahjong.css")
        self.assertEqual(script.status_code, 200, script.text)
        self.assertEqual(stylesheet.status_code, 200, stylesheet.text)
        self.assertIn('DuelGameUI.register("mahjong"', script.text)
        self.assertIn("@media (max-width: 320px)", stylesheet.text)

        full_request = {
            "action": "state", "player_id": "ai-m", "room_id": room["room_id"],
            "full_state": True,
        }
        first = await self.client.post("/mcp/play", json=full_request)
        second = await self.client.post("/mcp/play", json=full_request)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["snapshot"], second.json()["snapshot"])
        self.assertNotIn("hands", first.json()["snapshot"]["board_state"])
        self.assertNotIn("action_history", first.json()["snapshot"]["board_state"])

        restored_a = framework.get_room(room["room_id"], "ai", "ai-m")
        restored_b = framework.get_room(room["room_id"], "ai", "ai-m")
        projected_a = framework.project_room_for_viewer(restored_a, "ai-m")
        projected_b = framework.project_room_for_viewer(restored_b, "ai-m")
        self.assertEqual(restored_a["board_state"], restored_b["board_state"])
        self.assertEqual(projected_a["private_state"], projected_b["private_state"])

        staked = framework.create_room(
            "mahjong", "human_first", "human", "stake-human", "stake-ai-1",
            ordered_participants=[
                {"player_id": "stake-human", "role": "human"},
                {"player_id": "stake-ai-1", "role": "ai"},
                {"player_id": "stake-ai-2", "role": "ai"},
                {"player_id": "stake-ai-3", "role": "ai"},
            ],
            first_player_id="stake-human",
            stake=4,
        )
        self.assertEqual(staked["stake"], 4)
        self.assertEqual(staked["status"], "pending")

        responder_bootstrap = await self.client.post("/mcp/play", json={
            "action": "state", "player_id": "ai-2", "room_id": room["room_id"],
        })
        self.assertTrue(responder_bootstrap.json()["bootstrap"])

        human_view = framework.project_room_for_viewer(
            framework.get_room(room["room_id"]), "human-m"
        )
        discard = next(
            item for item in human_view["private_state"]["legal_actions"]
            if item["kind"] == "discard" and item["label"] == "打 8万"
        )
        moved = framework.play_move(
            room["room_id"], "human", "human-m",
            {"action": "act", "action_id": discard["action_id"]},
        )
        self.assertEqual(moved["revision"], 1)
        self.assertEqual(moved["board_state"]["phase"], "response")
        persisted_a = framework.get_room(room["room_id"])["board_state"]["response_window"]
        persisted_b = framework.get_room(room["room_id"])["board_state"]["response_window"]
        self.assertEqual(persisted_a, persisted_b)
        self.assertTrue(persisted_a["queue"])
        delta = await self.client.post("/mcp/play", json={
            "action": "state", "player_id": "ai-2", "room_id": room["room_id"],
        })
        self.assertEqual(delta.status_code, 200, delta.text)
        payload = delta.json()
        payload_json = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("room", payload)
        self.assertTrue(any("mahjong_delta" in item for item in payload.get("events", [])))
        self.assertNotIn("hands", payload_json)
        self.assertNotIn(discard["action_id"], payload_json)
        for hidden_tile in moved["board_state"]["hands"]["human-m"]:
            self.assertNotIn(hidden_tile["id"], payload_json)


if __name__ == "__main__":
    unittest.main()
