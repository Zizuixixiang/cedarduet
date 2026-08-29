import itertools
import json
import random
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database, framework
from app import main as main_module
from app.games import GAMES, game_catalog
from app.games.zhajinhua import (
    MAX_ROUNDS,
    RAISE_TIERS,
    VIRTUAL_BUDGET,
    Zhajinhua,
    build_deck,
)
from third_party.golden_flower_evaluator import compare_hands, evaluate_hand


def card(rank, suit="spades"):
    return {"id": f"{suit}-{rank}", "rank": rank, "suit": suit}


def hand(*cards):
    return list(cards)


def participants(count):
    return [
        {
            "player_id": f"player-{index}",
            "display_name": f"玩家{index}",
            "role": "human" if index == 0 else "ai",
            "participant_kind": "human" if index == 0 else "bound_machine",
            "seat_index": index,
            "active": True,
        }
        for index in range(count)
    ]


def card_faces(value):
    faces = []
    if isinstance(value, dict):
        if set(("rank", "suit")) <= set(value):
            faces.append({"rank": value["rank"], "suit": value["suit"]})
        for child in value.values():
            faces.extend(card_faces(child))
    elif isinstance(value, list):
        for child in value:
            faces.extend(card_faces(child))
    return faces


class ZhajinhuaEvaluatorTests(unittest.TestCase):
    def test_all_six_hand_types_and_fixed_order(self):
        examples = [
            hand(card("A"), card("A", "hearts"), card("A", "clubs")),
            hand(card("Q"), card("K"), card("A")),
            hand(card("A", "hearts"), card("J", "hearts"), card("7", "hearts")),
            hand(card("9"), card("10", "hearts"), card("J", "clubs")),
            hand(card("K"), card("K", "hearts"), card("2", "clubs")),
            hand(card("A"), card("J", "hearts"), card("7", "clubs")),
        ]
        self.assertEqual(
            [evaluate_hand(value).hand_type for value in examples],
            [
                "three_of_a_kind", "straight_flush", "flush",
                "straight", "pair", "high_card",
            ],
        )
        for stronger, weaker in itertools.pairwise(examples):
            self.assertGreater(compare_hands(stronger, weaker), 0)

    def test_a23_is_smallest_straight_and_akq_is_largest(self):
        a23 = hand(card("A"), card("2", "hearts"), card("3", "clubs"))
        s234 = hand(card("2"), card("3", "hearts"), card("4", "clubs"))
        jqk = hand(card("J"), card("Q", "hearts"), card("K", "clubs"))
        akq = hand(card("A"), card("K", "hearts"), card("Q", "clubs"))
        self.assertEqual(evaluate_hand(a23).hand_type, "straight")
        self.assertLess(compare_hands(a23, s234), 0)
        self.assertLess(compare_hands(jqk, akq), 0)

    def test_same_type_compares_every_rank_and_suits_never_break_tie(self):
        self.assertGreater(
            compare_hands(
                hand(card("7"), card("7", "hearts"), card("K", "clubs")),
                hand(card("7", "diamonds"), card("7", "clubs"), card("J")),
            ),
            0,
        )
        left = hand(card("A"), card("K", "hearts"), card("J", "clubs"))
        right = hand(card("A", "diamonds"), card("K", "clubs"), card("J", "hearts"))
        self.assertEqual(compare_hands(left, right), 0)
        self.assertEqual(compare_hands(right, left), 0)

    def test_235_has_no_special_power(self):
        two_three_five = hand(card("2"), card("3", "hearts"), card("5", "clubs"))
        trips = hand(card("2"), card("2", "hearts"), card("2", "clubs"))
        self.assertEqual(evaluate_hand(two_three_five).hand_type, "high_card")
        self.assertLess(compare_hands(two_three_five, trips), 0)

    def test_exhaustive_class_counts_and_permutation_property(self):
        deck = build_deck()
        counts = Counter()
        for selected in itertools.combinations(deck, 3):
            value = evaluate_hand(selected)
            counts[value.hand_type] += 1
            reversed_value = evaluate_hand(reversed(selected))
            self.assertEqual(value, reversed_value)
        self.assertEqual(sum(counts.values()), 22_100)
        self.assertEqual(
            counts,
            {
                "three_of_a_kind": 52,
                "straight_flush": 48,
                "flush": 1_096,
                "straight": 720,
                "pair": 3_744,
                "high_card": 16_440,
            },
        )

    def test_comparison_is_antisymmetric_and_transitive(self):
        generator = random.Random(20260830)
        deck = build_deck()
        samples = [generator.sample(deck, 3) for _index in range(600)]
        ordered = sorted(samples, key=lambda value: evaluate_hand(value).comparison_key)
        for left, right in zip(ordered, ordered[1:]):
            relation = compare_hands(left, right)
            self.assertLessEqual(relation, 0)
            self.assertEqual(relation, -compare_hands(right, left))
        for _index in range(500):
            first, second, third = sorted(
                generator.sample(samples, 3),
                key=lambda value: evaluate_hand(value).comparison_key,
            )
            self.assertLessEqual(compare_hands(first, second), 0)
            self.assertLessEqual(compare_hands(second, third), 0)
            self.assertLessEqual(compare_hands(first, third), 0)


class ZhajinhuaCoreTests(unittest.TestCase):
    def setUp(self):
        self.game = Zhajinhua(rng=random.Random(73))

    def new_state(self, count=3):
        roster = participants(count)
        return roster, self.game.initialize_for_first_player(roster, "player-0")

    def apply(self, state, roster, move):
        actor_id = state["turn_player_id"]
        actor = next(item for item in roster if item["player_id"] == actor_id)
        result = self.game.apply_action(state, move, actor)
        return self.game.progress_after_action(state, move, actor, roster, result)

    def test_catalog_supports_two_through_six_without_wallet_stakes(self):
        item = {entry["game_type"]: entry for entry in game_catalog()}["zhajinhua"]
        self.assertEqual(item["display_name"], "炸金花")
        self.assertEqual(item["allowed_player_counts"], [2, 3, 4, 5, 6])
        self.assertTrue(item["supports_npcs"])
        self.assertFalse(item["supports_stakes"])
        for count in (2, 3, 6):
            with self.subTest(count=count):
                roster, state = self.new_state(count)
                dealt = [
                    card["id"]
                    for cards in state["cards"]["hands"].values()
                    for card in cards
                ]
                self.assertEqual(len(dealt), count * 3)
                self.assertEqual(len(dealt), len(set(dealt)))
                self.assertEqual(state["pot"], count)
                self.assertEqual(len(roster), count)

    def test_peek_reveals_only_to_self_and_retains_turn(self):
        roster, state = self.new_state()
        before = self.game.public_state(state, roster)
        self.assertNotIn("cards", before)
        self.assertFalse(card_faces(before))
        for viewer in roster:
            private = self.game.private_state(state, viewer, roster)
            self.assertEqual(private["hand"], [{"hidden": True}] * 3)
            self.assertFalse(private["hand_revealed"])

        applied = self.apply(state, roster, {"action": "peek"})
        self.assertTrue(applied.retain_turn)
        self.assertEqual(state["turn_player_id"], "player-0")
        self.assertEqual(len(card_faces(self.game.private_state(state, roster[0], roster))), 3)
        self.assertFalse(card_faces(self.game.private_state(state, roster[1], roster)))
        public = self.game.public_state(state, roster)
        self.assertFalse(card_faces(public))
        self.assertTrue(public["players"]["player-0"]["seen"])

    def test_authoritative_call_raise_fold_and_compare_legality(self):
        roster, state = self.new_state()
        legal = self.game.legal_actions_for(state, "player-0")
        self.assertIn({"action": "call", "cost": 1}, legal)
        self.assertEqual(
            [action["unit"] for action in legal if action["action"] == "raise"],
            [2, 4, 8],
        )
        with self.assertRaisesRegex(ValueError, "authoritative"):
            self.game.validate_action(
                state,
                {"action": "raise", "unit": 3, "cost": 3},
                roster[0],
            )
        with self.assertRaisesRegex(ValueError, "authoritative"):
            self.game.validate_action(
                state, {"action": "call", "cost": 2}, roster[0]
            )
        self.apply(state, roster, {"action": "peek"})
        legal = self.game.legal_actions_for(state, "player-0")
        self.assertIn({"action": "call", "cost": 2}, legal)
        self.assertNotIn("compare", {action["action"] for action in legal})
        self.apply(state, roster, {"action": "raise", "unit": 2, "cost": 4})
        self.assertEqual(state["blind_unit"], 2)
        self.assertEqual(state["pot"], 7)
        self.assertEqual(state["turn_player_id"], "player-1")

    def test_multiplayer_rotation_round_and_fold_terminal(self):
        roster, state = self.new_state(3)
        for expected in ("player-0", "player-1", "player-2"):
            self.assertEqual(state["turn_player_id"], expected)
            self.apply(state, roster, {"action": "call", "cost": 1})
        self.assertEqual(state["flow"]["round_number"], 2)
        self.assertEqual(state["turn_player_id"], "player-0")
        first_fold = self.apply(state, roster, {"action": "fold"})
        self.assertEqual(first_fold.participant_activity, {"player-0": "eliminated"})
        self.assertEqual(state["turn_player_id"], "player-1")
        last_fold = self.apply(state, roster, {"action": "fold"})
        self.assertEqual(last_fold.result["winner_player_id"], "player-2")
        self.assertEqual(state["flow"]["phase"], "finished")
        self.assertEqual(state["pot"], 6)

    def test_compare_eliminates_loser_without_revealing_cards(self):
        roster, state = self.new_state(3)
        state["flow"]["round_number"] = 2
        state["player_state_by_player"]["player-0"]["seen"] = True
        state["cards"]["hands"]["player-0"] = hand(
            card("A"), card("A", "hearts"), card("A", "clubs")
        )
        state["cards"]["hands"]["player-1"] = hand(
            card("K"), card("K", "hearts"), card("K", "clubs")
        )
        action = next(
            action for action in self.game.legal_actions_for(state, "player-0")
            if action.get("target_player_id") == "player-1"
        )
        applied = self.apply(state, roster, action)
        self.assertEqual(applied.participant_activity, {"player-1": "eliminated"})
        self.assertEqual(state["player_state_by_player"]["player-1"]["status"], "compare_lost")
        self.assertEqual(state["turn_player_id"], "player-2")
        self.assertFalse(state["last_compare"]["cards_revealed"])
        self.assertFalse(card_faces(self.game.public_state(state, roster)))
        self.assertFalse(card_faces(applied.public_event))

    def test_exact_compare_tie_eliminates_initiator(self):
        roster, state = self.new_state(2)
        state["flow"]["round_number"] = 2
        state["player_state_by_player"]["player-0"]["seen"] = True
        state["cards"]["hands"]["player-0"] = hand(
            card("A"), card("K", "hearts"), card("J", "clubs")
        )
        state["cards"]["hands"]["player-1"] = hand(
            card("A", "diamonds"), card("K", "clubs"), card("J", "hearts")
        )
        action = next(
            action for action in self.game.legal_actions_for(state, "player-0")
            if action["action"] == "compare"
        )
        applied = self.apply(state, roster, action)
        self.assertTrue(state["last_compare"]["tied"])
        self.assertEqual(state["last_compare"]["loser_player_id"], "player-0")
        self.assertEqual(applied.result["winner_player_id"], "player-1")
        self.assertFalse(state["revealed_hands"])

    def test_virtual_pot_conservation_and_per_player_cap(self):
        roster, state = self.new_state(2)
        state["player_state_by_player"]["player-0"]["contribution"] = 63
        state["pot"] = 64
        self.game._assert_virtual_conservation(state)
        legal = self.game.legal_actions_for(state, "player-0")
        self.assertIn({"action": "call", "cost": 1}, legal)
        self.assertFalse(any(action["action"] == "raise" for action in legal))
        self.apply(state, roster, {"action": "call", "cost": 1})
        self.assertEqual(state["player_state_by_player"]["player-0"]["contribution"], VIRTUAL_BUDGET)
        self.assertEqual(
            state["pot"],
            sum(player["contribution"] for player in state["player_state_by_player"].values()),
        )
        state["turn_player_id"] = "player-0"
        legal = self.game.legal_actions_for(state, "player-0")
        self.assertEqual(
            {action["action"] for action in legal},
            {"peek", "fold"},
        )

    def test_round_cap_forces_public_showdown_and_exact_top_tie_draws(self):
        roster, state = self.new_state(3)
        state["flow"]["round_number"] = MAX_ROUNDS
        state["acted_player_ids_this_round"] = ["player-1", "player-2"]
        state["cards"]["hands"]["player-0"] = hand(
            card("A"), card("K", "hearts"), card("J", "clubs")
        )
        state["cards"]["hands"]["player-1"] = hand(
            card("A", "diamonds"), card("K", "clubs"), card("J", "hearts")
        )
        state["cards"]["hands"]["player-2"] = hand(
            card("9"), card("7", "hearts"), card("4", "clubs")
        )
        applied = self.apply(state, roster, {"action": "call", "cost": 1})
        self.assertTrue(applied.result["draw"])
        self.assertEqual(applied.result["tied_player_ids"], ["player-0", "player-1"])
        self.assertEqual(set(state["revealed_hands"]), {item["player_id"] for item in roster})
        self.assertEqual(len(card_faces(self.game.public_state(state, roster))), 9)
        self.assertEqual(len(card_faces(applied.public_event)), 9)

    def test_npc_actions_are_exactly_authoritative_and_validate(self):
        roster, state = self.new_state(6)
        actor = roster[0]
        authoritative = self.game.legal_actions_for(state, actor["player_id"])
        npc_actions = self.game.npc_legal_actions(state, actor, roster)
        self.assertEqual(npc_actions, authoritative)
        for action in npc_actions:
            self.game.validate_action(state, action, actor)
        self.assertNotIn("rank", json.dumps(self.game.npc_public_actions(state, actor, roster)))


class ZhajinhuaFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-zhajinhua-framework-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temporary.cleanup()

    def test_three_player_framework_rotation_and_activity(self):
        roster = participants(3)
        room = framework.create_room(
            "zhajinhua", "human_first", "human", "player-0", "player-1",
            ordered_participants=roster,
        )
        room = framework.play_move(
            room["room_id"], "human", "player-0", {"action": "peek"}
        )
        self.assertEqual(room["current_player_id"], "player-0")
        view = framework.project_room_for_viewer(room, "player-0")
        call = next(
            action for action in view["private_state"]["legal_actions"]
            if action["action"] == "call"
        )
        room = framework.play_move(room["room_id"], "human", "player-0", call)
        self.assertEqual(room["current_player_id"], "player-1")
        room = framework.play_move(
            room["room_id"], "ai", "player-1", {"action": "fold"}
        )
        self.assertEqual(room["current_player_id"], "player-2")
        eliminated = next(
            item for item in room["participants"] if item["player_id"] == "player-1"
        )
        self.assertFalse(eliminated["active"])
        self.assertEqual(eliminated["activity_state"], "eliminated")

    def test_real_stake_is_rejected(self):
        with self.assertRaisesRegex(framework.DuelError, "尚未定义筹码结算"):
            framework.create_room(
                "zhajinhua", "human_first", "human", "player-0", "player-1",
                stake=1,
            )


class ZhajinhuaMcpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-zhajinhua-mcp-")
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

    async def test_bootstrap_delta_and_full_state_never_leak_opponent_hands(self):
        response = await self.client.post(
            "/mcp/play",
            json={
                "action": "new",
                "player_id": "ai-mcp",
                "opponent_id": "human-mcp",
                "participant_ids": ["human-mcp", "ai-mcp", "ai-other"],
                "game_type": "zhajinhua",
                "mode": "ai_first",
                "target_player_count": 3,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        bootstrap = response.json()
        self.assertTrue(bootstrap["bootstrap"])
        room = bootstrap["room"]
        room_id = room["room_id"]
        self.assertEqual(room["current_actor"]["player_id"], "ai-mcp")
        self.assertFalse(card_faces(room["board_state"]))
        self.assertFalse(card_faces(room["private_state"]))
        self.assertEqual(room["private_state"]["hand"], [{"hidden": True}] * 3)

        other_bootstrap = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "ai-other", "room_id": room_id},
        )
        self.assertEqual(other_bootstrap.status_code, 200, other_bootstrap.text)
        self.assertTrue(other_bootstrap.json()["bootstrap"])
        self.assertFalse(card_faces(other_bootstrap.json()["room"]))

        peek = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-mcp", "room_id": room_id,
                "move": {"action": "peek"}, "revision": room["revision"],
            },
        )
        self.assertEqual(peek.status_code, 200, peek.text)
        peek_payload = peek.json()
        self.assertTrue(peek_payload["your_turn"])
        self.assertEqual(len(card_faces(peek_payload["private_state"])), 3)
        self.assertTrue(all(not card_faces(event) for event in peek_payload["events"]))
        public_delta = next(
            event["zhajinhua_delta"]
            for event in peek_payload["events"]
            if "zhajinhua_delta" in event
        )
        self.assertEqual(public_delta["pot"], 3)
        self.assertTrue(public_delta["players"]["ai-mcp"]["seen"])

        called = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-mcp", "room_id": room_id,
                "move": next(
                    action for action in peek_payload["private_state"]["legal_actions"]
                    if action["action"] == "call"
                ),
                "revision": peek_payload["revision"],
            },
        )
        self.assertEqual(called.status_code, 200, called.text)
        self.assertEqual(called.json()["current_actor"]["player_id"], "ai-other")
        self.assertTrue(all(not card_faces(event) for event in called.json()["events"]))

        other_delta = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "ai-other", "room_id": room_id},
        )
        self.assertEqual(other_delta.status_code, 200, other_delta.text)
        self.assertFalse(card_faces(other_delta.json()))
        self.assertTrue(any(
            "zhajinhua_delta" in event for event in other_delta.json().get("events", [])
        ))

        own_snapshot = await self.client.post(
            "/mcp/play",
            json={
                "action": "state", "player_id": "ai-mcp", "room_id": room_id,
                "full_state": True,
            },
        )
        self.assertEqual(own_snapshot.status_code, 200, own_snapshot.text)
        own = own_snapshot.json()["snapshot"]
        self.assertEqual(len(card_faces(own["private_state"])), 3)
        self.assertFalse(card_faces(own["board_state"]))
        self.assertNotIn("action_history", own["board_state"])

        other_snapshot = await self.client.post(
            "/mcp/play",
            json={
                "action": "state", "player_id": "ai-other", "room_id": room_id,
                "full_state": True,
            },
        )
        self.assertEqual(other_snapshot.status_code, 200, other_snapshot.text)
        other = other_snapshot.json()["snapshot"]
        self.assertFalse(card_faces(other))
        self.assertEqual(other["private_state"]["hand"], [{"hidden": True}] * 3)


if __name__ == "__main__":
    unittest.main()
