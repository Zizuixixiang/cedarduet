import json
import random
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from app import database, framework
from app.games import GAMES, game_catalog
from app.games.gandengyan import (
    MAX_MULTIPLIER,
    Gandengyan,
    build_deck,
    can_beat,
    classify_cards,
)


DECK_BY_ID = {card["id"]: card for card in build_deck()}


def cards(*card_ids):
    return [deepcopy(DECK_BY_ID[card_id]) for card_id in card_ids]


def seats(count):
    return [
        {
            "player_id": "human-1" if index == 0 else f"ai-{index}",
            "display_name": "人类" if index == 0 else f"小机 {index}",
            "role": "human" if index == 0 else "ai",
            "participant_kind": "human" if index == 0 else "bound_machine",
            "seat_index": index,
            "token": f"P{index + 1}",
        }
        for index in range(count)
    ]


def ordinary(pattern_type, rank, count):
    return {
        "type": pattern_type,
        "count": count,
        "rank": rank,
        "rank_value": ("3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A", "2").index(rank),
        "is_bomb": False,
    }


class GandengyanRuleTests(unittest.TestCase):
    def setUp(self):
        self.game = Gandengyan(random.Random(20260829))

    def custom_state(self, hands, deck=()):
        table = seats(len(hands))
        state = self.game.initialize(table)
        player_ids = [seat["player_id"] for seat in table]
        state["cards"] = {
            "deck": cards(*deck),
            "discard": [],
            "hands": {
                player_id: cards(*hands[index])
                for index, player_id in enumerate(player_ids)
            },
        }
        state["turn_player_id"] = player_ids[0]
        state["trick"] = self.game._new_trick(1, player_ids[0])
        state["flow"].update({"phase": "leading", "round_number": 1, "turn_number": 0})
        return table, state

    def test_catalog_metadata_and_fixed_version_rules(self):
        item = {entry["game_type"]: entry for entry in game_catalog()}["gandengyan"]
        self.assertEqual(item["display_name"], "干瞪眼")
        self.assertEqual(item["category"], "card")
        self.assertEqual(item["allowed_player_counts"], [2, 3, 4])
        self.assertTrue(item["supports_npcs"])
        self.assertTrue(item["supports_stakes"])
        self.assertTrue(item["supports_multiplayer_stakes"])
        rules = GAMES["gandengyan"].rules_text
        for phrase in (
            "本项目固定的四川常见 54 张版本",
            "3<4<5<6<7<8<9<10<J<Q<K<A<2",
            "恰好高一级",
            "大小王不能单出",
            "不是万能赖子",
            "不采用广告牌或癞子变体",
            "最高 16 倍",
            "不采用春天、天胡",
        ):
            self.assertIn(phrase, rules)

    def test_54_card_deck_is_unique_and_dealing_supports_two_to_four(self):
        deck = build_deck()
        self.assertEqual(len(deck), 54)
        self.assertEqual(len({card["id"] for card in deck}), 54)
        self.assertEqual(sum(card["suit"] != "joker" for card in deck), 52)
        self.assertEqual(sum(card["suit"] == "joker" for card in deck), 2)
        for count, expected_deck in ((2, 43), (3, 38), (4, 33)):
            with self.subTest(count=count):
                game = Gandengyan(random.Random(100 + count))
                state = game.initialize(seats(count))
                hand_counts = [
                    len(state["cards"]["hands"][seat["player_id"]])
                    for seat in seats(count)
                ]
                self.assertEqual(hand_counts, [6] + [5] * (count - 1))
                self.assertEqual(len(state["cards"]["deck"]), expected_deck)
                self.assertEqual(state["turn_player_id"], "human-1")

    def test_seeded_shuffle_and_projecting_state_never_rerolls(self):
        first = Gandengyan(random.Random(88)).initialize(seats(4))
        second = Gandengyan(random.Random(88)).initialize(seats(4))
        self.assertEqual(first["cards"], second["cards"])
        persisted = json.dumps(first, ensure_ascii=False, sort_keys=True)
        game = Gandengyan(random.Random(999))
        game.public_state(first, seats(4))
        game.private_state(first, seats(4)[0], seats(4))
        self.assertEqual(json.dumps(first, ensure_ascii=False, sort_keys=True), persisted)

    def test_explicit_nonfirst_seat_dealer_gets_six_cards_and_opening_turn(self):
        table = seats(4)
        state = self.game.initialize_for_first_player(table, "ai-2")
        counts = {
            player_id: len(hand)
            for player_id, hand in state["cards"]["hands"].items()
        }
        self.assertEqual(counts, {
            "human-1": 5,
            "ai-1": 5,
            "ai-2": 6,
            "ai-3": 5,
        })
        self.assertEqual(state["turn_player_id"], "ai-2")
        self.assertEqual(state["trick"]["leader_player_id"], "ai-2")

    def test_recognizes_all_patterns_and_rejects_invalid_or_two_sequences(self):
        expectations = {
            ("S3",): "single",
            ("S3", "H3"): "pair",
            ("S3", "H3", "C3"): "three_bomb",
            ("S3", "H3", "C3", "D3"): "four_bomb",
            ("JOKER-S", "JOKER-B"): "joker_bomb",
            ("S3", "H4", "C5"): "straight",
            ("S3", "H3", "S4", "H4"): "consecutive_pairs",
        }
        for card_ids, expected in expectations.items():
            with self.subTest(card_ids=card_ids):
                self.assertEqual(classify_cards(cards(*card_ids))["type"], expected)
        for card_ids in (
            ("JOKER-S",),
            ("JOKER-S", "JOKER-B", "S3"),
            ("S3", "H4", "C6"),
            ("S3", "H4", "C2"),
            ("S3", "H3", "S4", "H4", "S2", "H2"),
        ):
            with self.subTest(invalid=card_ids):
                self.assertIsNone(classify_cards(cards(*card_ids)))

    def test_ordinary_follow_is_exactly_one_step_and_two_is_special(self):
        three = ordinary("single", "3", 1)
        four = ordinary("single", "4", 1)
        five = ordinary("single", "5", 1)
        two = ordinary("single", "2", 1)
        self.assertTrue(can_beat(four, three))
        self.assertFalse(can_beat(five, three))
        self.assertTrue(can_beat(two, three))
        self.assertTrue(can_beat(two, ordinary("single", "K", 1)))
        self.assertFalse(can_beat(two, two))
        self.assertTrue(
            can_beat(ordinary("pair", "2", 2), ordinary("pair", "6", 2))
        )
        self.assertFalse(
            can_beat(ordinary("pair", "7", 2), ordinary("single", "6", 1))
        )

        straight_345 = classify_cards(cards("S3", "H4", "C5"))
        straight_456 = classify_cards(cards("S4", "H5", "C6"))
        straight_567 = classify_cards(cards("S5", "H6", "C7"))
        pairs_3344 = classify_cards(cards("S3", "H3", "S4", "H4"))
        pairs_4455 = classify_cards(cards("S4", "H4", "S5", "H5"))
        self.assertTrue(can_beat(straight_456, straight_345))
        self.assertFalse(can_beat(straight_567, straight_345))
        self.assertTrue(can_beat(pairs_4455, pairs_3344))

    def test_bomb_comparison_uses_strength_then_rank_without_exact_step(self):
        triple3 = classify_cards(cards("S3", "H3", "C3"))
        triple9 = classify_cards(cards("S9", "H9", "C9"))
        quad3 = classify_cards(cards("S3", "H3", "C3", "D3"))
        quad_a = classify_cards(cards("SA", "HA", "CA", "DA"))
        joker = classify_cards(cards("JOKER-S", "JOKER-B"))
        self.assertTrue(can_beat(triple9, ordinary("single", "A", 1)))
        self.assertTrue(can_beat(triple9, triple3))
        self.assertTrue(can_beat(quad3, triple9))
        self.assertTrue(can_beat(quad_a, quad3))
        self.assertTrue(can_beat(joker, quad_a))
        self.assertFalse(can_beat(triple9, quad3))

    def test_legal_actions_are_physical_server_combinations_and_jokers_never_single(self):
        table, state = self.custom_state([
            ("S3", "H3", "C3", "D3", "JOKER-S", "JOKER-B", "S7"),
            ("S4",),
        ])
        legal = self.game.legal_actions_for(state, "human-1")
        self.assertIn(
            {
                "action": "play",
                "card_ids": ["JOKER-S", "JOKER-B"],
                "pattern_type": "joker_bomb",
                "pattern_label": "王炸",
            },
            legal,
        )
        self.assertFalse(any(
            action.get("card_ids") in (["JOKER-S"], ["JOKER-B"])
            for action in legal
        ))
        self.assertEqual(sum(action.get("pattern_type") == "three_bomb" for action in legal), 4)
        for action in legal:
            self.game.validate_action(deepcopy(state), action, table[0])

    def test_follow_actions_include_exact_step_two_and_bombs_but_not_skipped_rank(self):
        table, state = self.custom_state([
            ("S4", "H5", "C2", "S8", "H8", "C8", "S9"),
            ("S3", "S6"),
        ])
        state["trick"]["last_play"] = {
            "player_id": "ai-1",
            "cards": cards("S3"),
            "pattern": classify_cards(cards("S3")),
        }
        state["flow"]["phase"] = "following"
        legal = self.game.legal_actions_for(state, "human-1")
        single_ids = {
            tuple(action["card_ids"])
            for action in legal
            if action.get("pattern_type") == "single"
        }
        self.assertEqual(single_ids, {("S4",), ("C2",)})
        self.assertTrue(any(action.get("pattern_type") == "three_bomb" for action in legal))
        self.assertIn({"action": "pass"}, legal)

    def test_pass_resets_after_a_successful_follow(self):
        table, state = self.custom_state([
            ("S3", "S9"),
            ("S8", "H8"),
            ("S4", "S10"),
            ("S7", "H7"),
        ])
        self.game.apply_action(state, {"action": "play", "card_ids": ["S3"]}, table[0])
        self.game.apply_action(state, {"action": "pass"}, table[1])
        self.assertEqual(state["trick"]["pass_player_ids"], ["ai-1"])
        self.game.apply_action(state, {"action": "play", "card_ids": ["S4"]}, table[2])
        self.assertEqual(state["trick"]["pass_player_ids"], [])
        self.assertEqual(state["trick"]["last_play"]["player_id"], "ai-2")

    def test_all_other_pass_ends_trick_and_draws_from_winner_in_seat_order(self):
        table, state = self.custom_state(
            [
                ("S3", "S9"),
                ("S6", "S8"),
                ("S7", "S10"),
            ],
            deck=("C6", "D6", "H6"),
        )
        self.game.apply_action(state, {"action": "play", "card_ids": ["S3"]}, table[0])
        self.game.apply_action(state, {"action": "pass"}, table[1])
        result = self.game.apply_action(state, {"action": "pass"}, table[2])
        self.assertEqual(result.next_player_id, "human-1")
        self.assertEqual(state["flow"], {"phase": "leading", "round_number": 2, "turn_number": 0})
        self.assertIsNone(state["trick"]["last_play"])
        self.assertEqual(state["turn_player_id"], "human-1")
        self.assertIn("H6", [card["id"] for card in state["cards"]["hands"]["human-1"]])
        self.assertIn("D6", [card["id"] for card in state["cards"]["hands"]["ai-1"]])
        self.assertIn("C6", [card["id"] for card in state["cards"]["hands"]["ai-2"]])
        self.assertEqual(state["cards"]["deck"], [])

    def test_emptying_deck_stops_later_seats_from_drawing(self):
        table, state = self.custom_state(
            [("S3", "S9"), ("S6",), ("S7",)], deck=("H6",)
        )
        before = {player_id: len(hand) for player_id, hand in state["cards"]["hands"].items()}
        self.game.apply_action(state, {"action": "play", "card_ids": ["S3"]}, table[0])
        self.game.apply_action(state, {"action": "pass"}, table[1])
        self.game.apply_action(state, {"action": "pass"}, table[2])
        after = {player_id: len(hand) for player_id, hand in state["cards"]["hands"].items()}
        self.assertEqual(after["human-1"], before["human-1"])
        self.assertEqual(after["ai-1"], before["ai-1"])
        self.assertEqual(after["ai-2"], before["ai-2"])
        self.assertEqual(state["last_action"]["draw_counts"], {
            "human-1": 1, "ai-1": 0, "ai-2": 0,
        })

    def test_player_wins_immediately_after_play_record_is_consistent(self):
        table, state = self.custom_state([("S3",), ("S4", "S5")])
        result = self.game.apply_action(
            state, {"action": "play", "card_ids": ["S3"]}, table[0]
        )
        self.assertEqual(result.result["winner_player_id"], "human-1")
        self.assertEqual(state["flow"]["phase"], "finished")
        self.assertEqual(state["winner_player_id"], "human-1")
        self.assertEqual(state["cards"]["hands"]["human-1"], [])
        self.assertEqual(state["cards"]["discard"][-1]["id"], "S3")
        self.assertEqual(state["trick"]["last_play"]["cards"][0]["id"], "S3")
        self.assertEqual(state["action_history"][-1]["action"], "play")

    def test_every_bomb_doubles_multiplier_up_to_cap(self):
        table, state = self.custom_state([
            ("S3", "H3", "C3", "S5", "H5", "C5", "D5", "JOKER-S", "JOKER-B", "S9"),
            ("S4", "H4", "C4", "S6", "H6", "C6", "D6", "S10"),
        ])
        plays = (
            (table[0], ["S3", "H3", "C3"]),
            (table[1], ["S4", "H4", "C4"]),
            (table[0], ["S5", "H5", "C5", "D5"]),
            (table[1], ["S6", "H6", "C6", "D6"]),
            (table[0], ["JOKER-S", "JOKER-B"]),
        )
        for actor, card_ids in plays:
            self.game.apply_action(state, {"action": "play", "card_ids": card_ids}, actor)
        self.assertEqual(state["bomb_count"], 5)
        self.assertEqual(state["multiplier"], MAX_MULTIPLIER)

    def test_settlement_is_hand_count_scaled_and_zero_sum(self):
        table, state = self.custom_state([
            (), ("S3", "H4"), ("S5", "H6", "C7", "D8", "S9")
        ])
        state["winner_player_id"] = "human-1"
        state["multiplier"] = 4
        deltas = self.game.settlement_deltas(
            state,
            {"winner_player_id": "human-1", "draw": False},
            table,
            3,
        )
        self.assertEqual(deltas, {"human-1": 84, "ai-1": -24, "ai-2": -60})
        self.assertEqual(sum(deltas.values()), 0)

    def test_public_private_projection_does_not_leak_hands_or_deck_order(self):
        table, state = self.custom_state(
            [("S3", "H3"), ("S4", "H4"), ("S5", "H5")],
            deck=("S6", "H6", "C6"),
        )
        public = self.game.public_state(state, table)
        public_json = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("hands", public)
        self.assertNotIn("deck", public)
        for card_id in ("S3", "H3", "S4", "H4", "S5", "H5", "S6", "H6", "C6"):
            self.assertNotIn(f'"{card_id}"', public_json)
        self.assertEqual(public["hand_counts"], {"human-1": 2, "ai-1": 2, "ai-2": 2})
        private = self.game.private_state(state, table[1], table)
        self.assertEqual({card["id"] for card in private["hand"]}, {"S4", "H4"})
        private_json = json.dumps(private, ensure_ascii=False)
        self.assertNotIn('"S3"', private_json)
        self.assertNotIn('"S5"', private_json)
        self.assertNotIn('"S6"', private_json)
        self.assertEqual(
            private["legal_actions"],
            self.game.npc_legal_actions(state, table[1], table),
        )

    def test_npc_receives_only_authoritative_legal_actions(self):
        table, state = self.custom_state([("S3", "H3"), ("S4", "H4", "S8")])
        state["trick"]["last_play"] = {
            "player_id": "human-1",
            "cards": cards("S3", "H3"),
            "pattern": classify_cards(cards("S3", "H3")),
        }
        state["turn_player_id"] = "ai-1"
        state["flow"]["phase"] = "following"
        legal = self.game.npc_legal_actions(state, table[1], table)
        self.assertIn({
            "action": "play",
            "card_ids": ["S4", "H4"],
            "pattern_type": "pair",
            "pattern_label": "对子",
        }, legal)
        self.assertIn({"action": "pass"}, legal)
        for action in legal:
            self.game.validate_action(deepcopy(state), action, table[1])
        history_json = json.dumps(
            self.game.npc_public_actions(state, table[1], table), ensure_ascii=False
        )
        self.assertNotIn('"S8"', history_json)


class GandengyanFrameworkSettlementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-gandengyan-")
        self.addCleanup(self.temporary.cleanup)
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()
        self.game = Gandengyan(random.Random(7))
        self.game_patch = patch.dict(GAMES, {"gandengyan": self.game})
        self.game_patch.start()
        self.addCleanup(self.game_patch.stop)

    @staticmethod
    def room_participants(count):
        return [
            {
                "player_id": "human-1" if index == 0 else f"ai-{index}",
                "display_name": "人类" if index == 0 else f"小机 {index}",
                "role": "human" if index == 0 else "ai",
                "participant_kind": "human" if index == 0 else "bound_machine",
            }
            for index in range(count)
        ]

    @staticmethod
    def replace_state(room, state, current_player_id="human-1"):
        with database.write_transaction() as conn:
            conn.execute(
                "UPDATE rooms SET board_state = ?, current_player_id = ?, turn = ? WHERE room_id = ?",
                (
                    json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                    current_player_id,
                    "human" if current_player_id.startswith("human") else "ai",
                    room["room_id"],
                ),
            )
        return framework.get_room(room["room_id"])

    def make_terminal_ready_room(self, count, stake, loser_hands, multiplier):
        room = framework.create_room(
            "gandengyan",
            "human_first",
            "human",
            "human-1",
            opponent_id="ai-1",
            ordered_participants=self.room_participants(count),
            stake=stake,
        )
        for participant in room["participants"]:
            if participant["participant_kind"] == "bound_machine":
                room = framework.respond_to_invitation(
                    room["room_id"], "ai", participant["player_id"], "accept"
                )
        state = deepcopy(room["board_state"])
        state["cards"] = {
            "deck": [],
            "discard": [],
            "hands": {"human-1": cards("S3")},
        }
        for index, count_in_hand in enumerate(loser_hands, start=1):
            available = ("S4", "H5", "C6", "D7", "S8", "H9")
            state["cards"]["hands"][f"ai-{index}"] = cards(*available[:count_in_hand])
        state["turn_player_id"] = "human-1"
        state["trick"] = self.game._new_trick(1, "human-1")
        state["flow"].update({"phase": "leading", "round_number": 1, "turn_number": 0})
        state["multiplier"] = multiplier
        return self.replace_state(room, state)

    def test_framework_explicit_ai_opener_is_dealer_and_state_turn(self):
        room = framework.create_room(
            "gandengyan",
            "ai_first",
            "human",
            "human-1",
            opponent_id="ai-1",
            ordered_participants=self.room_participants(3),
            first_player_id="ai-2",
        )
        self.assertEqual(room["current_player_id"], "ai-2")
        self.assertEqual(room["board_state"]["turn_player_id"], "ai-2")
        self.assertEqual(room["board_state"]["trick"]["leader_player_id"], "ai-2")
        self.assertEqual(
            {
                player_id: len(hand)
                for player_id, hand in room["board_state"]["cards"]["hands"].items()
            },
            {"human-1": 5, "ai-1": 5, "ai-2": 6},
        )

    def test_waiting_join_and_leave_redeal_for_the_mode_selected_opener(self):
        room = framework.create_room(
            "gandengyan",
            "ai_first",
            "human",
            "human-1",
            ordered_participants=self.room_participants(1),
        )
        self.assertEqual(room["status"], "waiting")
        self.assertEqual(room["current_player_id"], "human-1")
        self.assertEqual(
            {player_id: len(hand) for player_id, hand in room["board_state"]["cards"]["hands"].items()},
            {"human-1": 6},
        )

        room = framework.join_room(room["room_id"], "ai", "ai-1")
        self.assertEqual(room["status"], "playing")
        self.assertEqual(room["current_player_id"], "ai-1")
        self.assertEqual(room["board_state"]["turn_player_id"], "ai-1")
        self.assertEqual(room["board_state"]["trick"]["leader_player_id"], "ai-1")
        self.assertEqual(
            {player_id: len(hand) for player_id, hand in room["board_state"]["cards"]["hands"].items()},
            {"human-1": 5, "ai-1": 6},
        )

        with database.write_transaction() as conn:
            conn.execute(
                "UPDATE rooms SET status = 'waiting' WHERE room_id = ?",
                (room["room_id"],),
            )
        room = framework.leave_room(room["room_id"], "ai", "ai-1")
        self.assertEqual(room["status"], "waiting")
        self.assertEqual(room["current_player_id"], "human-1")
        self.assertEqual(room["board_state"]["turn_player_id"], "human-1")
        self.assertEqual(
            {player_id: len(hand) for player_id, hand in room["board_state"]["cards"]["hands"].items()},
            {"human-1": 6},
        )

    def test_two_player_room_uses_custom_scaled_settlement(self):
        room = self.make_terminal_ready_room(2, stake=3, loser_hands=(3,), multiplier=2)
        room = framework.play_move(
            room["room_id"], "human", "human-1", {"action": "play", "card_ids": ["S3"]}
        )
        self.assertEqual(room["result"]["settlement_deltas"], {
            "human-1": 18, "ai-1": -18,
        })
        self.assertTrue(room["result"]["settlement_zero_sum"])

    def test_four_player_room_settlement_covers_every_seat_and_is_zero_sum(self):
        room = self.make_terminal_ready_room(
            4, stake=2, loser_hands=(1, 2, 3), multiplier=4
        )
        room = framework.play_move(
            room["room_id"], "human", "human-1", {"action": "play", "card_ids": ["S3"]}
        )
        self.assertEqual(room["result"]["settlement_deltas"], {
            "human-1": 48,
            "ai-1": -8,
            "ai-2": -16,
            "ai-3": -24,
        })
        self.assertEqual(sum(room["result"]["settlement_deltas"].values()), 0)


if __name__ == "__main__":
    unittest.main()
