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
from app.games.doudizhu import Doudizhu, build_deck
from third_party.onestraw_doudizhu import (
    PATTERN_ENTRY_COUNT,
    can_beat,
    classify_ranks,
    legal_rank_plays,
)


DECK_BY_ID = {card["id"]: card for card in build_deck()}


def cards(*card_ids):
    return [deepcopy(DECK_BY_ID[card_id]) for card_id in card_ids]


def seats():
    return [
        {
            "player_id": "human-1",
            "display_name": "南山",
            "role": "human",
            "participant_kind": "human",
            "seat_index": 0,
            "token": "P1",
        },
        {
            "player_id": "ai-1",
            "display_name": "小机一号",
            "role": "ai",
            "participant_kind": "bound_machine",
            "seat_index": 1,
            "token": "P2",
        },
        {
            "player_id": "ai-2",
            "display_name": "小机二号",
            "role": "ai",
            "participant_kind": "bound_machine",
            "seat_index": 2,
            "token": "P3",
        },
    ]


def only_pattern(ranks, type_code=None):
    patterns = classify_ranks(ranks)
    if type_code is None:
        if len(patterns) != 1:
            raise AssertionError(f"expected one pattern, got {patterns}")
        return patterns[0]
    return next(pattern for pattern in patterns if pattern.type_code == type_code)


class VendoredDoudizhuCoreTests(unittest.TestCase):
    def test_upstream_pattern_universe_and_every_major_family(self):
        self.assertEqual(PATTERN_ENTRY_COUNT, 34152)
        examples = {
            ("3",): "solo",
            ("3", "3"): "pair",
            ("3", "3", "3"): "trio",
            ("3", "3", "3", "4"): "trio_solo",
            ("3", "3", "3", "4", "4"): "trio_pair",
            tuple("34567"): "solo_chain_5",
            tuple("334455"): "pair_chain_3",
            tuple("333444"): "trio_chain_2",
            tuple("33344456"): "trio_chain_solo_2",
            tuple("3334445566"): "trio_chain_pair_2",
            tuple("333345"): "four_two_solo",
            tuple("33334455"): "four_two_pair",
            tuple("3333"): "bomb",
            ("small_joker", "big_joker"): "rocket",
        }
        for ranks, expected in examples.items():
            with self.subTest(ranks=ranks):
                self.assertIn(expected, {value.type_code for value in classify_ranks(ranks)})

    def test_key_boundaries_and_upstream_attachment_semantics(self):
        for invalid in (
            tuple("3456"),
            ("10", "J", "Q", "K", "2"),
            ("J", "Q", "K", "A", "small_joker"),
            tuple("3344"),
            tuple("333444555666777888999"),
        ):
            with self.subTest(invalid=invalid):
                self.assertEqual(classify_ranks(invalid), ())
        self.assertTrue(classify_ranks(tuple("333344")))
        self.assertTrue(classify_ranks(tuple("33334444")))
        ambiguous = classify_ranks(tuple("33332222"))
        self.assertEqual(
            {(value.type_code, value.main_rank) for value in ambiguous},
            {("four_two_pair", "3"), ("four_two_pair", "2")},
        )

    def test_comparison_bombs_rocket_shape_and_length_boundaries(self):
        single_a = only_pattern(("A",))
        single_2 = only_pattern(("2",))
        pair_3 = only_pattern(("3", "3"))
        straight_7 = only_pattern(tuple("34567"))
        straight_8 = only_pattern(tuple("45678"))
        straight_9_long = only_pattern(tuple("3456789"))
        bomb_3 = only_pattern(tuple("3333"))
        bomb_a = only_pattern(("A",) * 4)
        rocket = only_pattern(("small_joker", "big_joker"))
        self.assertTrue(can_beat(single_2, single_a))
        self.assertFalse(can_beat(pair_3, single_a))
        self.assertTrue(can_beat(straight_8, straight_7))
        self.assertFalse(can_beat(straight_9_long, straight_7))
        self.assertTrue(can_beat(bomb_3, single_2))
        self.assertTrue(can_beat(bomb_a, bomb_3))
        self.assertTrue(can_beat(rocket, bomb_a))
        self.assertFalse(can_beat(bomb_a, rocket))

    def test_legal_enumeration_round_trips_and_never_exceeds_hand(self):
        generator = random.Random(20260830)
        deck_ranks = [card["rank"] for card in build_deck()]
        for _sample in range(20):
            hand = generator.sample(deck_ranks, 17)
            hand_counts = {rank: hand.count(rank) for rank in set(hand)}
            for play in legal_rank_plays(hand):
                played_labels = tuple(
                    ("3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A", "2", "small_joker", "big_joker")[rank]
                    for rank in play.ranks
                )
                self.assertIn(play.pattern, classify_ranks(played_labels))
                self.assertTrue(all(
                    played_labels.count(rank) <= count
                    for rank, count in hand_counts.items()
                ))


class DoudizhuGameTests(unittest.TestCase):
    def setUp(self):
        self.game = Doudizhu(random.Random(20260830))
        self.table = seats()

    def playing_state(self, hands, *, landlord="human-1", turn="human-1", score=1):
        state = self.game.initialize_for_first_player(self.table, "human-1")
        state["cards"] = {
            "deck": [],
            "discard": [],
            "hands": {
                item["player_id"]: cards(*hands[index])
                for index, item in enumerate(self.table)
            },
        }
        state["bottom_cards"] = cards("S2", "H2", "C2")
        state["bottom_revealed"] = True
        state["landlord_player_id"] = landlord
        state["roles_by_player"] = {
            item["player_id"]: "landlord" if item["player_id"] == landlord else "farmer"
            for item in self.table
        }
        state["base_score"] = score
        state["multiplier"] = score
        state["turn_player_id"] = turn
        state["trick"] = self.game._new_trick(1, turn)
        state["flow"].update({"phase": "playing", "round_number": 1, "turn_number": 0})
        return state

    def action(self, state, player_id, action_type, **matches):
        return next(
            action for action in self.game.legal_actions_for(state, player_id)
            if action["action"] == action_type
            and all(action.get(key) == value for key, value in matches.items())
        )

    def apply(self, state, player_id, action_type, **matches):
        actor = next(item for item in self.table if item["player_id"] == player_id)
        action = self.action(state, player_id, action_type, **matches)
        result = self.game.apply_action(state, action, actor)
        return action, self.game.progress_after_action(
            state, action, actor, self.table, result
        )

    def test_catalog_deck_fixed_seats_rules_and_stakes(self):
        item = {value["game_type"]: value for value in game_catalog()}["doudizhu"]
        self.assertEqual(item["display_name"], "斗地主")
        self.assertEqual(item["allowed_player_counts"], [3])
        self.assertEqual(item["recommended_players"], 3)
        self.assertTrue(item["supports_npcs"])
        self.assertTrue(item["supports_stakes"])
        self.assertTrue(item["supports_multiplayer_stakes"])
        deck = build_deck()
        self.assertEqual(len(deck), 54)
        self.assertEqual(len({card["id"] for card in deck}), 54)
        state = self.game.initialize_for_first_player(self.table, "ai-1")
        self.assertEqual(
            {player_id: len(hand) for player_id, hand in state["cards"]["hands"].items()},
            {"human-1": 17, "ai-1": 17, "ai-2": 17},
        )
        self.assertEqual(len(state["bottom_cards"]), 3)
        self.assertEqual(state["turn_player_id"], "ai-1")
        for phrase in (
            "0 分（不叫）", "严格高于", "全部不叫", "最后一位不叫者",
            "定地主后底牌向全桌公开", "34,152", "王炸最高",
            "不设倍数上限", "不设春天、反春天", "房间底注×终局倍数",
            "地主获得两份结算单位", "地主扣两份", "三人合计始终为 0",
        ):
            self.assertIn(phrase, GAMES["doudizhu"].rules_text)

    def test_bid_flow_bottom_merge_roles_and_illegal_equal_bid(self):
        state = self.game.initialize_for_first_player(self.table, "human-1")
        original_bottom = deepcopy(state["bottom_cards"])
        public_before = self.game.public_state(state, self.table)
        self.assertFalse(public_before["bottom_revealed"])
        self.assertEqual(public_before["bottom_cards"], [])
        self.apply(state, "human-1", "bid", score=1)
        self.assertEqual(
            {action["score"] for action in self.game.legal_actions_for(state, "ai-1")},
            {0, 2, 3},
        )
        with self.assertRaisesRegex(ValueError, "合法动作"):
            self.game.validate_action(
                state, {"action": "bid", "action_id": "bid:1", "score": 1}, self.table[1]
            )
        self.apply(state, "ai-1", "bid", score=0)
        _action, result = self.apply(state, "ai-2", "bid", score=2)
        self.assertEqual(state["flow"]["phase"], "playing")
        self.assertEqual(state["landlord_player_id"], "ai-2")
        self.assertEqual(state["roles_by_player"], {
            "human-1": "farmer", "ai-1": "farmer", "ai-2": "landlord",
        })
        self.assertEqual(len(state["cards"]["hands"]["ai-2"]), 20)
        self.assertEqual(state["cards"]["hands"]["ai-2"][-3:], original_bottom)
        self.assertEqual(state["turn_player_id"], "ai-2")
        self.assertEqual(state["multiplier"], 2)
        public = self.game.public_state(state, self.table)
        self.assertEqual(public["bottom_cards"], original_bottom)
        delta = result.public_event["doudizhu_delta"]
        self.assertEqual(delta["kind"], "landlord_decided")
        self.assertEqual(
            delta["bottom_card_ids"], [card["id"] for card in original_bottom]
        )

    def test_three_points_ends_bidding_immediately(self):
        state = self.game.initialize_for_first_player(self.table, "human-1")
        self.apply(state, "human-1", "bid", score=3)
        self.assertEqual(state["landlord_player_id"], "human-1")
        self.assertEqual(state["turn_player_id"], "human-1")
        self.assertEqual(state["multiplier"], 3)
        self.assertEqual(len(state["bidding"]["actions"]), 1)

    def test_all_pass_redeals_and_last_no_bid_retains_opening_turn(self):
        state = self.game.initialize_for_first_player(self.table, "human-1")
        first_hand = deepcopy(state["cards"]["hands"]["ai-2"])
        self.apply(state, "human-1", "bid", score=0)
        self.apply(state, "ai-1", "bid", score=0)
        _action, result = self.apply(state, "ai-2", "bid", score=0)
        self.assertTrue(result.retain_turn)
        self.assertEqual(state["turn_player_id"], "ai-2")
        self.assertEqual(state["deal_number"], 2)
        self.assertEqual(state["bidding"]["round"], 2)
        self.assertEqual(state["bidding"]["opener_player_id"], "ai-2")
        self.assertEqual([len(hand) for hand in state["cards"]["hands"].values()], [17, 17, 17])
        self.assertNotEqual(state["cards"]["hands"]["ai-2"], first_hand)
        self.assertIsNone(result.public_event)

    def test_authoritative_physical_actions_and_ambiguous_interpretations(self):
        state = self.playing_state([
            ("S3", "H3", "C3", "D3", "S2", "H2", "C2", "D2", "S9"),
            ("S4",),
            ("S5",),
        ])
        legal = self.game.legal_actions_for(state, "human-1")
        ambiguous = [
            action for action in legal
            if set(action.get("card_ids", [])) == {"S3", "H3", "C3", "D3", "S2", "H2", "C2", "D2"}
        ]
        self.assertEqual(len(ambiguous), 2)
        self.assertEqual({action["main_rank"] for action in ambiguous}, {"3", "2"})
        self.assertEqual(len({action["action_id"] for action in ambiguous}), 2)
        for action in legal:
            self.game.validate_action(deepcopy(state), action, self.table[0])
        forged = deepcopy(ambiguous[0])
        forged["pattern_type"] = "rocket"
        with self.assertRaisesRegex(ValueError, "不一致"):
            self.game.validate_action(state, forged, self.table[0])

    def test_rank_equivalent_single_uses_selected_physical_card_and_keeps_actions_compact(self):
        state = self.playing_state([
            ("S10", "H10", "D10"),
            ("S4",),
            ("S5",),
        ])
        state["trick"].update({
            "leader_player_id": "ai-1",
            "last_play": {
                "sequence": 1,
                "player_id": "ai-1",
                "cards": cards("S9"),
                "pattern": only_pattern(("9",)).public(),
            },
        })
        singles = [
            action for action in self.game.legal_actions_for(state, "human-1")
            if action.get("pattern_type") == "solo"
            and action.get("main_rank") == "10"
        ]
        self.assertEqual(len(singles), 1)
        self.assertEqual(singles[0]["card_ids"], ["S10"])

        move = {
            "action": "play",
            "action_id": singles[0]["action_id"],
            "card_ids": ["D10"],
        }
        result = self.game.apply_action(state, move, self.table[0])
        progressed = self.game.progress_after_action(
            state, move, self.table[0], self.table, result
        )
        self.assertEqual(
            {card["id"] for card in state["cards"]["hands"]["human-1"]},
            {"S10", "H10"},
        )
        self.assertEqual([card["id"] for card in state["last_action"]["cards"]], ["D10"])
        self.assertEqual(
            progressed.public_event,
            {
                "doudizhu_delta": {
                    "kind": "play",
                    "pattern_type": "solo",
                    "pattern_label": "单张",
                }
            },
        )

    def test_rank_equivalent_pair_accepts_any_two_and_rejects_forged_replacements(self):
        def pair_state():
            value = self.playing_state([
                ("S10", "H10", "D10", "SJ"),
                ("S4",),
                ("S5",),
            ])
            value["trick"].update({
                "leader_player_id": "ai-1",
                "last_play": {
                    "sequence": 1,
                    "player_id": "ai-1",
                    "cards": cards("S9", "H9"),
                    "pattern": only_pattern(("9", "9")).public(),
                },
            })
            return value

        state = pair_state()
        pairs = [
            action for action in self.game.legal_actions_for(state, "human-1")
            if action.get("pattern_type") == "pair"
            and action.get("main_rank") == "10"
        ]
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["card_ids"], ["S10", "H10"])
        move = {
            "action": "play",
            "action_id": pairs[0]["action_id"],
            "card_ids": ["H10", "D10"],
        }
        result = self.game.apply_action(state, move, self.table[0])
        self.game.progress_after_action(state, move, self.table[0], self.table, result)
        self.assertEqual(
            {card["id"] for card in state["cards"]["hands"]["human-1"]},
            {"S10", "SJ"},
        )
        self.assertEqual(
            {card["id"] for card in state["last_action"]["cards"]},
            {"H10", "D10"},
        )

        canonical_state = pair_state()
        canonical = self.action(
            canonical_state, "human-1", "play", pattern_type="pair", main_rank="10"
        )
        for rejected, message in (
            (["H10", "SJ"], "点数组合"),
            (["H10", "H10"], "不能重复"),
            (["H10", "C10"], "不全在"),
        ):
            with self.subTest(card_ids=rejected):
                with self.assertRaisesRegex(ValueError, message):
                    self.game.validate_action(
                        canonical_state,
                        {
                            "action": "play",
                            "action_id": canonical["action_id"],
                            "card_ids": rejected,
                        },
                        self.table[0],
                    )
        with self.assertRaisesRegex(ValueError, "未发布的字段"):
            self.game.validate_action(
                canonical_state,
                {
                    "action": "play",
                    "action_id": canonical["action_id"],
                    "card_ids": ["H10", "D10"],
                    "selected_card_ids": ["H10", "D10"],
                },
                self.table[0],
            )

    def test_action_id_only_keeps_canonical_cards_and_complex_rank_multisets_resolve(self):
        canonical_state = self.playing_state([
            ("S10", "H10", "D10"),
            ("S4",),
            ("S5",),
        ])
        canonical_state["trick"].update({
            "leader_player_id": "ai-1",
            "last_play": {
                "sequence": 1,
                "player_id": "ai-1",
                "cards": cards("S9"),
                "pattern": only_pattern(("9",)).public(),
            },
        })
        canonical = self.action(
            canonical_state, "human-1", "play", pattern_type="solo", main_rank="10"
        )
        id_only = {"action": "play", "action_id": canonical["action_id"]}
        result = self.game.apply_action(canonical_state, id_only, self.table[0])
        self.game.progress_after_action(
            canonical_state, id_only, self.table[0], self.table, result
        )
        self.assertEqual(
            {card["id"] for card in canonical_state["cards"]["hands"]["human-1"]},
            {"H10", "D10"},
        )

        straight_state = self.playing_state([
            ("S3", "H3", "S4", "H4", "S5", "H5", "S6", "H6", "S7", "H7"),
            ("S8",),
            ("S9",),
        ])
        straight = self.action(
            straight_state, "human-1", "play", pattern_type="solo_chain_5"
        )
        resolved_straight = self.game._resolve_action(
            straight_state,
            "human-1",
            {
                "action": "play",
                "action_id": straight["action_id"],
                "card_ids": ["H3", "H4", "H5", "H6", "H7"],
            },
        )
        self.assertEqual(
            resolved_straight["card_ids"], ["H3", "H4", "H5", "H6", "H7"]
        )
        self.assertEqual(resolved_straight["pattern_type"], "solo_chain_5")

        trio_state = self.playing_state([
            ("S6", "H6", "C6", "D6", "S9", "H9"),
            ("S4",),
            ("S5",),
        ])
        trio = self.action(
            trio_state,
            "human-1",
            "play",
            pattern_type="trio_solo",
            main_rank="6",
        )
        resolved_trio = self.game._resolve_action(
            trio_state,
            "human-1",
            {
                "action": "play",
                "action_id": trio["action_id"],
                "card_ids": ["H6", "C6", "D6", "H9"],
            },
        )
        self.assertEqual(
            resolved_trio["card_ids"], ["H6", "C6", "D6", "H9"]
        )
        self.assertEqual((resolved_trio["pattern_type"], resolved_trio["main_rank"]), ("trio_solo", "6"))

    def test_cannot_beat_pass_rotation_and_two_passes_restore_lead(self):
        state = self.playing_state([
            ("S3", "S9"),
            ("S4", "S8"),
            ("S5", "S7"),
        ])
        self.apply(state, "human-1", "play", card_ids=["S9"])
        self.assertEqual(
            {action["action"] for action in self.game.legal_actions_for(state, "ai-1")},
            {"pass"},
        )
        self.apply(state, "ai-1", "pass")
        _action, result = self.apply(state, "ai-2", "pass")
        self.assertEqual(state["turn_player_id"], "human-1")
        self.assertIsNone(state["trick"]["last_play"])
        self.assertEqual(state["trick"]["pass_player_ids"], [])
        self.assertIsNone(result.public_event)
        self.assertNotIn("pass", {action["action"] for action in self.game.legal_actions_for(state, "human-1")})

    def test_successful_follow_clears_pass_state(self):
        state = self.playing_state([
            ("S3", "S9"),
            ("S4", "S8"),
            ("S5", "S7"),
        ])
        self.apply(state, "human-1", "play", card_ids=["S3"])
        self.apply(state, "ai-1", "pass")
        self.apply(state, "ai-2", "play", card_ids=["S5"])
        self.assertEqual(state["trick"]["pass_player_ids"], [])
        self.assertEqual(state["trick"]["last_play"]["player_id"], "ai-2")

    def test_bomb_and_rocket_double_informational_multiplier(self):
        state = self.playing_state([
            ("S3", "H3", "C3", "D3", "JOKER-S", "JOKER-B", "S9"),
            ("S4", "S8"),
            ("S5", "S7"),
        ], score=2)
        self.apply(state, "human-1", "play", pattern_type="bomb")
        self.assertEqual((state["multiplier"], state["bomb_count"]), (4, 1))
        self.apply(state, "ai-1", "pass")
        self.apply(state, "ai-2", "pass")
        _action, result = self.apply(state, "human-1", "play", pattern_type="rocket")
        self.assertEqual((state["multiplier"], state["bomb_count"]), (8, 2))
        self.assertIsNone(result.public_event)

    def test_farmer_finish_returns_team_winners_and_chip_settlement(self):
        state = self.playing_state([
            ("S9", "H9"),
            ("S3",),
            ("S5", "S7"),
        ], landlord="human-1", turn="ai-1", score=3)
        _action, result = self.apply(state, "ai-1", "play", card_ids=["S3"])
        self.assertEqual(state["flow"]["phase"], "finished")
        self.assertEqual(state["winner_player_id"], "ai-1")
        self.assertEqual(state["winning_side"], "farmers")
        self.assertEqual(state["winning_player_ids"], ["ai-1", "ai-2"])
        self.assertEqual(result.result["winning_player_ids"], ["ai-1", "ai-2"])
        self.assertEqual(
            self.game.settlement_deltas(state, result.result, self.table, 5),
            {"human-1": -30, "ai-1": 15, "ai-2": 15},
        )
        self.assertIsNone(result.public_event)

    def test_stake_multiplier_examples_cover_landlord_and_farmer_wins(self):
        state = {
            "landlord_player_id": "human-1",
            "multiplier": 6,
        }
        landlord = self.game.settlement_deltas(
            state,
            {"winning_side": "landlord", "draw": False},
            self.table,
            5,
        )
        self.assertEqual(
            landlord, {"human-1": 60, "ai-1": -30, "ai-2": -30}
        )
        self.assertEqual(sum(landlord.values()), 0)

        state["multiplier"] = 12
        farmers = self.game.settlement_deltas(
            state,
            {"winning_side": "farmers", "draw": False},
            self.table,
            2,
        )
        self.assertEqual(
            farmers, {"human-1": -48, "ai-1": 24, "ai-2": 24}
        )
        self.assertEqual(sum(farmers.values()), 0)
        self.assertEqual(
            self.game.settlement_deltas(
                {}, {"draw": True, "reason": "insufficient_players"}, self.table, 9
            ),
            {"human-1": 0, "ai-1": 0, "ai-2": 0},
        )

    def test_resignation_forfeit_covers_bidding_and_decided_sides(self):
        bidding = self.game.result_for_resignation(
            {"landlord_player_id": None}, "human-1", self.table
        )
        self.assertEqual(
            self.game.settlement_deltas({}, bidding, self.table, 5),
            {"human-1": -10, "ai-1": 5, "ai-2": 5},
        )
        playing = self.game.result_for_resignation(
            {"landlord_player_id": "human-1", "multiplier": 6},
            "ai-1",
            self.table,
        )
        self.assertEqual(playing["winning_side"], "landlord")
        self.assertEqual(
            self.game.settlement_deltas(
                {"landlord_player_id": "human-1", "multiplier": 6},
                playing,
                self.table,
                5,
            ),
            {"human-1": 60, "ai-1": -30, "ai-2": -30},
        )

    def test_public_private_npc_and_participant_relationships_are_safe(self):
        state = self.playing_state([
            ("S3", "H3", "C8"),
            ("S4", "H4", "C9"),
            ("S5", "H5", "C10"),
        ])
        public = self.game.public_state(state, self.table)
        encoded = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("hands", public)
        for card_id in ("S3", "H3", "C8", "S4", "H4", "C9", "S5", "H5", "C10"):
            self.assertNotIn(f'"{card_id}"', encoded)
        private = self.game.private_state(state, self.table[0], self.table)
        self.assertEqual({card["id"] for card in private["hand"]}, {"S3", "H3", "C8"})
        self.assertEqual(
            private["legal_actions"],
            self.game.npc_legal_actions(state, self.table[0], self.table),
        )
        farmer_summary = self.game.participant_summary(public, self.table[1], self.table)
        self.assertEqual(farmer_summary["identity"], "农民")
        self.assertEqual(farmer_summary["partner_player_id"], "ai-2")
        self.assertNotIn("S4", json.dumps(self.game.npc_public_actions(state, self.table[1], self.table)))

    def test_terminal_projection_reveals_all_remaining_hands_high_to_low_only(self):
        state = self.playing_state([
            ("S3",),
            ("S3", "H2", "JOKER-B"),
            ("SK", "HA"),
        ])
        playing = self.game.public_state(state, self.table)
        self.assertNotIn("terminal_hands", playing)
        self.assertNotIn("S3", json.dumps(playing, ensure_ascii=False))

        _action, _result = self.apply(state, "human-1", "play", card_ids=["S3"])
        terminal = self.game.public_state(state, self.table)
        self.assertEqual(terminal["terminal_hands"]["human-1"], [])
        self.assertEqual(
            [card["rank"] for card in terminal["terminal_hands"]["ai-1"]],
            ["big_joker", "2", "3"],
        )
        self.assertEqual(
            [card["rank"] for card in terminal["terminal_hands"]["ai-2"]],
            ["A", "K"],
        )
        self.assertNotIn("deck", terminal)

        resigned_review = self.game.terminal_public_state(
            self.playing_state([("S4",), ("H5",), ("C6",)]), self.table
        )
        self.assertEqual(set(resigned_review["terminal_hands"]), {
            "human-1", "ai-1", "ai-2",
        })


class DoudizhuFrameworkAndMcpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-doudizhu-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()
        self.original_events = main_module.revision_events
        main_module.revision_events = main_module.RevisionEvents()
        self.game = Doudizhu(random.Random(77))
        self.game_patch = patch.dict(GAMES, {"doudizhu": self.game})
        self.game_patch.start()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_module.app),
            base_url="http://duel.test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.game_patch.stop()
        main_module.revision_events = self.original_events
        self.db_patch.stop()
        self.temporary.cleanup()

    @staticmethod
    def mixed_participants():
        return [
            {
                "player_id": "human-1", "role": "human",
                "participant_kind": "human", "display_name": "南山",
            },
            {
                "player_id": "ai-1", "role": "ai",
                "participant_kind": "bound_machine", "display_name": "小机",
            },
            {
                "player_id": "npc:quiet", "role": "ai",
                "participant_kind": "system_npc", "npc_persona_id": "quiet",
                "display_name": "安静 NPC",
            },
        ]

    def test_framework_accepts_mixed_fixed_three_and_positive_stakes(self):
        room = framework.create_room(
            "doudizhu", "human_first", "human", "human-1",
            opponent_id="ai-1", ordered_participants=self.mixed_participants(),
            first_player_id="human-1",
        )
        self.assertEqual(room["status"], "playing")
        self.assertEqual(
            [item["participant_kind"] for item in room["participants"]],
            ["human", "bound_machine", "system_npc"],
        )
        self.assertEqual(room["stake"], 0)
        staked = framework.create_room(
            "doudizhu", "human_first", "human", "human-2",
            ordered_participants=[
                {"player_id": "human-2", "role": "human"},
                {"player_id": "ai-2", "role": "ai"},
                {"player_id": "ai-3", "role": "ai"},
            ],
            stake=1,
        )
        self.assertEqual(staked["stake"], 1)
        self.assertEqual(staked["status"], "pending")

    async def test_mcp_bootstrap_delta_full_state_and_private_hands(self):
        participants = seats()
        for item in participants:
            item.pop("seat_index")
            item.pop("token")
        room = framework.create_room(
            "doudizhu", "ai_first", "human", "human-1",
            opponent_id="ai-1", ordered_participants=participants,
            first_player_id="ai-1",
        )
        room_id = room["room_id"]
        bootstrap = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "ai-1", "room_id": room_id},
        )
        self.assertEqual(bootstrap.status_code, 200, bootstrap.text)
        payload = bootstrap.json()
        self.assertTrue(payload["bootstrap"])
        projected = payload["room"]
        self.assertEqual(len(projected["private_state"]["hand"]), 17)
        self.assertEqual(projected["board_state"]["bottom_cards"], [])
        raw_hands = room["board_state"]["cards"]["hands"]
        encoded = json.dumps(payload, ensure_ascii=False)
        for other_id in ("human-1", "ai-2"):
            for card in raw_hands[other_id]:
                self.assertNotIn(f'"{card["id"]}"', encoded)

        bid = next(
            action for action in projected["private_state"]["legal_actions"]
            if action.get("score") == 1
        )
        moved = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-1", "room_id": room_id,
                "move": bid,
            },
        )
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertNotIn("events", moved.json())
        self.assertNotIn("private_state", moved.json())

        latest = framework.get_room(room_id)
        ai2_action = next(
            action for action in self.game.private_state(
                latest["board_state"], latest["participants"][2], latest["participants"]
            )["legal_actions"]
            if action.get("score") == 0
        )
        framework.play_move(room_id, "ai", "ai-2", ai2_action)
        latest = framework.get_room(room_id)
        human_action = next(
            action for action in self.game.private_state(
                latest["board_state"], latest["participants"][0], latest["participants"]
            )["legal_actions"]
            if action.get("score") == 0
        )
        framework.play_move(room_id, "human", "human-1", human_action)

        full = await self.client.post(
            "/mcp/play",
            json={
                "action": "state", "player_id": "ai-1", "room_id": room_id,
                "full_state": True,
            },
        )
        self.assertEqual(full.status_code, 200, full.text)
        snapshot = full.json()["snapshot"]
        self.assertEqual(len(snapshot["private_state"]["hand"]), 20)
        self.assertEqual(len(snapshot["board_state"]["bottom_cards"]), 3)
        self.assertEqual(snapshot["board_state"]["landlord_player_id"], "ai-1")
        latest = framework.get_room(room_id)
        snapshot_json = json.dumps(snapshot, ensure_ascii=False)
        for other_id in ("human-1", "ai-2"):
            for card in latest["board_state"]["cards"]["hands"][other_id]:
                self.assertNotIn(f'"{card["id"]}"', snapshot_json)

        play = next(
            action for action in snapshot["private_state"]["legal_actions"]
            if action["action"] == "play"
        )
        played = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-1", "room_id": room_id,
                "move": {"action": "play", "action_id": play["action_id"]},
            },
        )
        self.assertEqual(played.status_code, 200, played.text)
        referee_deltas = [
            event["doudizhu_delta"]
            for event in played.json().get("events", [])
            if "doudizhu_delta" in event
        ]
        self.assertEqual(
            [item["kind"] for item in referee_deltas],
            ["landlord_decided", "play"],
        )
        self.assertNotIn("cards", referee_deltas[0])
        play_delta = referee_deltas[-1]
        self.assertEqual(play_delta["kind"], "play")
        self.assertEqual(set(play_delta["card_ids"]), set(play["card_ids"]))
        public_bottom_ids = {
            card["id"] for card in snapshot["board_state"]["bottom_cards"]
        }
        unplayed_private = (
            {card["id"] for card in snapshot["private_state"]["hand"]}
            - set(play["card_ids"])
            - public_bottom_ids
        )
        encoded_response = json.dumps(played.json(), ensure_ascii=False)
        self.assertTrue(
            all(card_id not in encoded_response for card_id in unplayed_private)
        )

    async def test_mcp_all_pass_redeal_returns_only_actors_new_private_hand(self):
        participants = seats()
        participants = [participants[0], participants[2], participants[1]]
        for item in participants:
            item.pop("seat_index")
            item.pop("token")
        room = framework.create_room(
            "doudizhu", "human_first", "human", "human-1",
            opponent_id="ai-1", ordered_participants=participants,
            first_player_id="human-1",
        )
        room_id = room["room_id"]
        for player_id, role in (("human-1", "human"), ("ai-2", "ai")):
            latest = framework.get_room(room_id)
            actor = next(
                item for item in latest["participants"]
                if item["player_id"] == player_id
            )
            action = next(
                value for value in self.game.private_state(
                    latest["board_state"], actor, latest["participants"]
                )["legal_actions"]
                if value.get("score") == 0
            )
            framework.play_move(room_id, role, player_id, action)

        bootstrap = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "ai-1", "room_id": room_id},
        )
        self.assertEqual(bootstrap.status_code, 200, bootstrap.text)
        before_ids = {
            card["id"] for card in bootstrap.json()["room"]["private_state"]["hand"]
        }
        no_bid = next(
            action for action in bootstrap.json()["room"]["private_state"]["legal_actions"]
            if action.get("score") == 0
        )
        redealt = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-1", "room_id": room_id,
                "move": no_bid,
            },
        )
        self.assertEqual(redealt.status_code, 200, redealt.text)
        payload = redealt.json()
        self.assertEqual(len(payload["private_state"]["hand"]), 17)
        after_ids = {card["id"] for card in payload["private_state"]["hand"]}
        self.assertNotEqual(before_ids, after_ids)
        self.assertNotIn("events", payload)
        latest = framework.get_room(room_id)
        for other_id in ("human-1", "ai-2"):
            for card in latest["board_state"]["cards"]["hands"][other_id]:
                self.assertNotIn(
                    f'"{card["id"]}"',
                    json.dumps(payload["private_state"], ensure_ascii=False),
                )


if __name__ == "__main__":
    unittest.main()
