import json
import tempfile
import unittest
from collections import Counter
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from app import database, framework
from app.games import GAMES, game_catalog
from app.games.blackjack import (
    DEALER_ID,
    SHOE_DECKS,
    Blackjack,
    build_shoe,
    hand_value,
)


class StackedRng:
    """Keep a valid four-deck shoe while scripting cards popped from its top."""

    def __init__(self, draw_ranks):
        self.draw_ranks = list(draw_ranks)
        self.shuffle_calls = 0

    def shuffle(self, cards):
        self.shuffle_calls += 1
        selected = []
        remaining = list(cards)
        for rank in self.draw_ranks:
            index = next(
                index for index, card in enumerate(remaining)
                if card["rank"] == rank
            )
            selected.append(remaining.pop(index))
        cards[:] = remaining + list(reversed(selected))


def participants(count):
    return [
        {
            "player_id": "human-1",
            "display_name": "人类",
            "role": "human",
            "participant_kind": "human",
        },
        *(
            {
                "player_id": f"ai-{index}",
                "display_name": f"小机 {index}",
                "role": "ai",
                "participant_kind": "bound_machine",
            }
            for index in range(1, count)
        ),
    ]


def deal_script(first_cards, dealer_first, second_cards, dealer_second, *hits):
    return [*first_cards, dealer_first, *second_cards, dealer_second, *hits]


class BlackjackValueAndShoeTests(unittest.TestCase):
    def test_four_deck_shoe_has_standard_quantity_and_distribution(self):
        shoe = build_shoe()
        self.assertEqual(len(shoe), 52 * SHOE_DECKS)
        self.assertEqual(len({card["card_id"] for card in shoe}), len(shoe))
        self.assertEqual(set(Counter(card["rank"] for card in shoe).values()), {16})
        self.assertEqual(set(Counter(card["suit"] for card in shoe).values()), {52})
        self.assertEqual(
            set(Counter((card["rank"], card["suit"]) for card in shoe).values()),
            {4},
        )

    def test_aces_choose_best_non_busting_value_and_mark_soft_or_hard(self):
        self.assertEqual(hand_value([{"rank": "A"}, {"rank": "6"}])["total"], 17)
        self.assertTrue(hand_value([{"rank": "A"}, {"rank": "6"}])["soft"])
        value = hand_value([{"rank": "A"}, {"rank": "9"}, {"rank": "A"}])
        self.assertEqual(value["total"], 21)
        self.assertTrue(value["soft"])
        hard = hand_value([{"rank": "A"}, {"rank": "9"}, {"rank": "A"}, {"rank": "5"}])
        self.assertEqual(hard["total"], 16)
        self.assertFalse(hard["soft"])

    def test_only_two_cards_can_be_natural_blackjack(self):
        natural = hand_value([{"rank": "A"}, {"rank": "K"}])
        ordinary = hand_value([{"rank": "7"}, {"rank": "7"}, {"rank": "7"}])
        self.assertTrue(natural["blackjack"])
        self.assertFalse(ordinary["blackjack"])
        self.assertEqual(ordinary["total"], 21)

    def test_next_round_reshuffles_only_below_authoritative_initial_deal_minimum(self):
        rng = StackedRng([])
        game = Blackjack(rng)
        state = game.initialize(participants(3))
        zones = state["cards"]
        all_cards = [
            *zones["deck"],
            *zones["discard"],
            *(card for hand in zones["hands"].values() for card in hand),
        ]
        required = game.cards_required_to_deal(3)
        zones["deck"] = all_cards[:required - 1]
        zones["discard"] = all_cards[required - 1:]
        for hand in zones["hands"].values():
            hand.clear()
        self.assertTrue(game.prepare_round_shoe(state, state["participant_order"]))
        self.assertEqual(len(zones["deck"]), 208)
        self.assertEqual(zones["discard"], [])
        self.assertTrue(all(not hand for hand in zones["hands"].values()))
        self.assertEqual(state["shoe_shuffle_count"], 2)
        self.assertEqual(rng.shuffle_calls, 2)

        zones["discard"].append(zones["deck"].pop())
        shuffle_count = state["shoe_shuffle_count"]
        self.assertFalse(game.prepare_round_shoe(state, state["participant_order"]))
        self.assertEqual(state["shoe_shuffle_count"], shuffle_count)


class BlackjackFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-blackjack-")
        self.addCleanup(self.temporary.cleanup)
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()

    def create(self, draw_ranks, count=2, mode="human_first"):
        rng = StackedRng(draw_ranks)
        game = Blackjack(rng)
        game_patch = patch.dict(GAMES, {"blackjack": game})
        game_patch.start()
        self.addCleanup(game_patch.stop)
        room = framework.create_room(
            "blackjack",
            mode,
            "human",
            "human-1",
            opponent_id="ai-1",
            ordered_participants=participants(count),
        )
        return room, game, rng

    @staticmethod
    def role(player_id):
        return "human" if player_id == "human-1" else "ai"

    def move(self, room, action):
        player_id = room["current_player_id"]
        return framework.play_move(
            room["room_id"], self.role(player_id), player_id, {"action": action}
        )

    def test_catalog_is_card_two_to_six_npc_and_entertainment_only(self):
        item = {entry["game_type"]: entry for entry in game_catalog()}["blackjack"]
        self.assertEqual(item["display_name"], "21点")
        self.assertEqual(item["category"], "card")
        self.assertEqual(item["allowed_player_counts"], [2, 3, 4, 5, 6])
        self.assertTrue(item["supports_npcs"])
        self.assertFalse(item["supports_stakes"])
        self.assertFalse(item["supports_multiplayer_stakes"])
        with self.assertRaisesRegex(framework.DuelError, "尚未定义筹码"):
            framework.create_room(
                "blackjack", "human_first", "human", "human-1", "ai-1", stake=1
            )

    def test_random_shoe_and_draws_persist_across_reload_without_reshuffle(self):
        script = deal_script(["5", "6"], "9", ["7", "8"], "8")
        room, _game, rng = self.create(script)
        raw_hand = deepcopy(room["board_state"]["cards"]["hands"]["human-1"])
        raw_deck = deepcopy(room["board_state"]["cards"]["deck"])
        self.assertEqual(rng.shuffle_calls, 1)
        reloaded = framework.get_room(room["room_id"])
        self.assertEqual(reloaded["board_state"]["cards"]["hands"]["human-1"], raw_hand)
        self.assertEqual(reloaded["board_state"]["cards"]["deck"], raw_deck)
        self.assertEqual(rng.shuffle_calls, 1)

    def test_waiting_join_and_leave_keep_natural_skip_and_state_turn_aligned(self):
        room, _game, rng = self.create(["A", "5", "6", "K", "K", "9", "7", "8"])
        self.assertEqual(room["current_player_id"], "ai-1")
        self.assertEqual(room["board_state"]["turn_player_id"], "ai-1")

        with database.write_transaction() as conn:
            conn.execute(
                "UPDATE rooms SET status = 'waiting' WHERE room_id = ?",
                (room["room_id"],),
            )
        room = framework.join_room(room["room_id"], "ai", "ai-2")
        self.assertEqual(room["status"], "playing")
        self.assertEqual(room["current_player_id"], "ai-1")
        self.assertEqual(room["board_state"]["turn_player_id"], "ai-1")
        self.assertEqual(
            room["board_state"]["player_status_by_player"]["human-1"],
            "blackjack",
        )

        with database.write_transaction() as conn:
            conn.execute(
                "UPDATE rooms SET status = 'waiting' WHERE room_id = ?",
                (room["room_id"],),
            )
        room = framework.leave_room(room["room_id"], "ai", "ai-2")
        self.assertEqual(room["status"], "waiting")
        self.assertEqual(room["current_player_id"], "ai-1")
        self.assertEqual(room["board_state"]["turn_player_id"], "ai-1")
        self.assertEqual(rng.shuffle_calls, 3)

    def test_dealer_hole_card_is_a_uniform_placeholder_in_every_view(self):
        script = deal_script(["A", "9"], "10", ["K", "7"], "6")
        room, _game, _rng = self.create(script)
        raw_hole = room["board_state"]["cards"]["hands"][DEALER_ID][1]
        for viewer_id in ("human-1", "ai-1"):
            projected = framework.project_room_for_viewer(room, viewer_id)
            state = projected["board_state"]
            self.assertEqual(state["dealer"]["hand"][1], {"hidden": True})
            self.assertTrue(state["dealer"]["hole_hidden"])
            self.assertNotIn("cards", state)
            self.assertNotIn("deck_count", state)
            self.assertNotIn("card_id", json.dumps(state, ensure_ascii=False))
            self.assertEqual(
                projected["private_state"]["hand"],
                state["players"][viewer_id]["hand"],
            )
            self.assertNotIn(raw_hole["card_id"], json.dumps(projected, ensure_ascii=False))
        natural = framework.project_room_for_viewer(room, "human-1")
        current = framework.project_room_for_viewer(room, "ai-1")
        self.assertEqual(room["current_player_id"], "ai-1")
        self.assertEqual(natural["private_state"]["legal_actions"], [])
        self.assertEqual(
            current["private_state"]["legal_actions"],
            [{"action": "hit"}, {"action": "stand"}],
        )

    def test_hit_retains_turn_then_bust_ends_hand_and_advances(self):
        script = deal_script(["10", "9"], "10", ["6", "8"], "7", "K")
        room, _game, _rng = self.create(script)
        room = self.move(room, "hit")
        self.assertEqual(room["board_state"]["player_status_by_player"]["human-1"], "bust")
        self.assertEqual(room["current_player_id"], "ai-1")
        public = framework.project_room_for_viewer(room, "ai-1")["board_state"]
        self.assertEqual(public["players"]["human-1"]["value"]["total"], 26)
        self.assertEqual(public["players"]["human-1"]["status"], "bust")

    def test_two_through_six_players_rotate_by_seat_once(self):
        for count in range(2, 7):
            with self.subTest(count=count):
                script = deal_script(
                    ["5"] * count,
                    "10",
                    ["6"] * count,
                    "7",
                )
                room, _game, _rng = self.create(script, count=count)
                observed = []
                for _ in range(count):
                    observed.append(room["current_player_id"])
                    room = self.move(room, "stand")
                self.assertEqual(observed, room["turn_order"])
                self.assertEqual(room["status"], "finished")
                self.assertIsNone(room["current_player_id"])

    def test_ai_first_wraps_to_earlier_seat_in_the_same_round(self):
        script = deal_script(["9", "8", "7"], "10", ["7", "8", "9"], "7")
        room, _game, _rng = self.create(script, count=3, mode="ai_first")
        observed = []
        while room["status"] == "playing":
            observed.append(room["current_player_id"])
            room = self.move(room, "stand")
        self.assertEqual(observed, ["ai-1", "ai-2", "human-1"])

    def test_dealer_stands_on_soft_seventeen_without_drawing(self):
        script = deal_script(["10", "9"], "A", ["8", "9"], "6", "K")
        room, _game, _rng = self.create(script)
        room = self.move(room, "stand")
        room = self.move(room, "stand")
        dealer_hand = room["board_state"]["cards"]["hands"][DEALER_ID]
        self.assertEqual(len(dealer_hand), 2)
        dealer_value = hand_value(dealer_hand)
        self.assertEqual(dealer_value["total"], 17)
        self.assertTrue(dealer_value["soft"])

    def test_dealer_bust_makes_every_non_bust_player_win(self):
        script = deal_script(["10", "10"], "6", ["8", "9"], "9", "K")
        room, _game, _rng = self.create(script)
        room = self.move(room, "stand")
        room = self.move(room, "stand")
        self.assertEqual(room["status"], "finished")
        self.assertTrue(room["result"]["dealer"]["bust"])
        self.assertEqual(
            {item["outcome"] for item in room["result"]["outcomes"]}, {"win"}
        )

    def test_natural_blackjack_beats_non_natural_21_while_ordinary_21_pushes(self):
        script = deal_script(["A", "7"], "10", ["K", "7"], "6", "7", "5")
        room, _game, _rng = self.create(script)
        self.assertEqual(room["current_player_id"], "ai-1")
        room = self.move(room, "hit")
        room = self.move(room, "stand")
        outcomes = room["result"]["outcomes_by_player"]
        self.assertEqual(room["result"]["dealer"]["total"], 21)
        self.assertFalse(room["result"]["dealer"]["natural_blackjack"])
        self.assertEqual(outcomes["human-1"]["outcome"], "win")
        self.assertTrue(outcomes["human-1"]["natural_blackjack"])
        self.assertEqual(outcomes["ai-1"]["outcome"], "push")

    def test_both_naturals_push_and_dealer_natural_beats_ordinary_21(self):
        script = deal_script(["A", "7"], "A", ["K", "7"], "K", "7")
        room, _game, _rng = self.create(script)
        room = self.move(room, "hit")
        room = self.move(room, "stand")
        outcomes = room["result"]["outcomes_by_player"]
        self.assertEqual(outcomes["human-1"]["outcome"], "push")
        self.assertEqual(outcomes["ai-1"]["outcome"], "loss")

    def test_terminal_result_and_public_state_keep_every_independent_outcome(self):
        script = deal_script(["10", "9", "10"], "10", ["8", "8", "6"], "7", "K")
        room, _game, _rng = self.create(script, count=3)
        room = self.move(room, "stand")
        room = self.move(room, "stand")
        room = self.move(room, "hit")
        self.assertEqual(room["status"], "finished")
        self.assertEqual(room["winner"], "draw")
        self.assertTrue(room["result"]["draw"])
        self.assertEqual(room["result"]["terminal_result"], "blackjack_dealer_comparison")
        self.assertEqual(set(room["result"]["outcomes_by_player"]), set(room["turn_order"]))
        projected = framework.project_room_for_viewer(room, "human-1")
        self.assertEqual(
            projected["board_state"]["game_result"]["outcomes_by_player"],
            room["result"]["outcomes_by_player"],
        )
        self.assertIn("胜", projected["board_state"]["result_text"])
        result_events = [
            item for item in framework.list_timeline(room["room_id"], viewer_player_id="human-1")
            if item["event_type"] == "result"
        ]
        self.assertTrue(any("21点结算" in item["text"] for item in result_events))

    def test_npc_receives_only_authoritative_legal_hit_stand_choices(self):
        script = deal_script(["5", "10"], "10", ["6", "8"], "7")
        room, game, _rng = self.create(script, mode="ai_first")
        actor = deepcopy(next(
            item for item in room["participants"] if item["player_id"] == "ai-1"
        ))
        actor["participant_kind"] = "system_npc"
        state = deepcopy(room["board_state"])
        actions = game.npc_legal_actions(state, actor, room["participants"])
        self.assertEqual(actions[0], {"action": "stand"})
        self.assertEqual({item["action"] for item in actions}, {"hit", "stand"})
        for action in actions:
            game.validate_action(deepcopy(state), action, actor)
        self.assertNotIn("deck", json.dumps(actions))

    def test_move_events_never_contain_drawn_or_hole_card_identity(self):
        script = deal_script(["5", "9"], "10", ["6", "8"], "6", "2")
        room, _game, _rng = self.create(script)
        hole_id = room["board_state"]["cards"]["hands"][DEALER_ID][1]["card_id"]
        room = self.move(room, "hit")
        events = framework.list_timeline(
            room["room_id"], viewer_player_id="ai-1", public_only=True
        )
        encoded = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("card_id", encoded)
        self.assertNotIn(hole_id, encoded)
        self.assertEqual(events[-1]["move"], {"action": "hit"})


if __name__ == "__main__":
    unittest.main()
