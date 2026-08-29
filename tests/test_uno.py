import json
import random
import tempfile
import unittest
from collections import Counter
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from app import database, framework
from app.games import GAMES, game_catalog
from app.games.uno import COLORS, Uno, build_uno_deck


def seats(count=2):
    return [
        {
            "player_id": f"player-{index + 1}",
            "display_name": f"玩家 {index + 1}",
            "role": "human" if index == 0 else "ai",
            "participant_kind": "human" if index == 0 else "system_npc",
            "token": f"P{index + 1}",
            "_uno_opener": index == 0,
        }
        for index in range(count)
    ]


DECK_BY_ID = {card["id"]: card for card in build_uno_deck()}


def card(card_id):
    return deepcopy(DECK_BY_ID[card_id])


class CountingRng(random.Random):
    def __init__(self, seed=1):
        super().__init__(seed)
        self.shuffle_calls = 0

    def shuffle(self, values):
        self.shuffle_calls += 1
        super().shuffle(values)


class ExplodingRng:
    def shuffle(self, values):
        del values
        raise AssertionError("persisted state must not reshuffle during projection")


class UnoRulesTests(unittest.TestCase):
    def setUp(self):
        self.game = Uno(random.Random(7))
        self.players = seats(3)

    def scenario(self, hands, top, deck=None, *, direction=1, turn="player-1"):
        state = self.game.initialize(self.players)
        state["cards"] = {
            "deck": [card(value) for value in (deck or [])],
            "discard": [card(top)],
            "hands": {
                player["player_id"]: [card(value) for value in hands.get(player["player_id"], [])]
                for player in self.players
            },
        }
        state["current_color"] = card(top)["color"] or "red"
        state["direction"] = direction
        state["turn_player_id"] = turn
        state["drawn_card"] = None
        state["pending_wild_draw_four"] = None
        state["uno_window"] = None
        state["last_uno"] = None
        state["last_penalty"] = None
        state["last_challenge"] = None
        state["winner_player_id"] = None
        state["flow"] = {"phase": "playing", "round_number": 1, "turn_number": 0}
        return state

    @staticmethod
    def actor(index):
        return seats(3)[index]

    def action_for(self, state, player_id, *, action, card_id=None, **fields):
        actions = self.game._legal_actions_for(state, player_id)
        return next(
            candidate for candidate in actions
            if candidate.get("action") == action
            and (card_id is None or candidate.get("card_id") == card_id)
            and all(candidate.get(key) == value for key, value in fields.items())
        )

    def test_classic_deck_distribution_and_catalog(self):
        deck = build_uno_deck()
        self.assertEqual(len(deck), 108)
        self.assertEqual(len({item["id"] for item in deck}), 108)
        self.assertEqual(Counter(item["kind"] for item in deck), {
            "number": 76,
            "skip": 8,
            "reverse": 8,
            "draw_two": 8,
            "wild": 4,
            "wild_draw_four": 4,
        })
        for color in COLORS:
            colored = [item for item in deck if item["color"] == color]
            values = Counter(
                item.get("value") for item in colored if item["kind"] == "number"
            )
            self.assertEqual(values[0], 1)
            self.assertTrue(all(values[value] == 2 for value in range(1, 10)))
            self.assertEqual(len(colored), 25)
        catalog = {item["game_type"]: item for item in game_catalog()}["uno"]
        self.assertIsInstance(GAMES["uno"], Uno)
        self.assertEqual(catalog["display_name"], "UNO")
        self.assertEqual(catalog["category"], "card")
        self.assertEqual(catalog["allowed_player_counts"], [2, 3, 4, 5, 6])
        self.assertTrue(catalog["supports_npcs"])
        self.assertTrue(catalog["supports_stakes"])
        self.assertTrue(catalog["supports_multiplayer_stakes"])

    def test_deal_for_every_supported_table_and_persisted_randomness(self):
        for count in range(2, 7):
            with self.subTest(count=count):
                rng = CountingRng(count)
                game = Uno(rng)
                state = game.initialize(seats(count))
                self.assertEqual(
                    [len(state["cards"]["hands"][item["player_id"]]) for item in seats(count)],
                    [7] * count,
                )
                self.assertEqual(len(state["cards"]["discard"]), 1)
                self.assertEqual(state["cards"]["discard"][-1]["kind"], "number")
                self.assertEqual(len(state["cards"]["deck"]), 107 - 7 * count)
                calls = rng.shuffle_calls
                game.public_state(state, seats(count))
                game.private_state(state, seats(count)[0], seats(count))
                self.assertEqual(rng.shuffle_calls, calls)
                restored = json.loads(json.dumps(state))
                self.assertEqual(
                    Uno(ExplodingRng()).private_state(restored, seats(count)[0], seats(count))["hand"],
                    state["cards"]["hands"]["player-1"],
                )

    def test_reshuffle_keeps_top_discard_and_persists_new_order(self):
        rng = CountingRng(11)
        game = Uno(rng)
        state = game.initialize(seats(2))
        state["cards"] = {
            "deck": [],
            "discard": [
                card("red-number-1-1"),
                card("blue-number-2-1"),
                card("green-number-3-1"),
                card("yellow-number-4-1"),
            ],
            "hands": {"player-1": [], "player-2": []},
        }
        drawn = game._draw_many(state, "player-1", 1)
        self.assertEqual(rng.shuffle_calls, 2)  # initial deal shuffle + recycle shuffle
        self.assertEqual(state["cards"]["discard"], [card("yellow-number-4-1")])
        self.assertEqual(state["cards"]["hands"]["player-1"], drawn)
        persisted = json.loads(json.dumps(state))
        Uno(ExplodingRng()).public_state(persisted, seats(2))

    def test_same_color_number_symbol_and_mismatched_cards(self):
        state = self.scenario({
            "player-1": [
                "red-number-8-1",
                "blue-number-5-1",
                "green-skip-1",
                "blue-reverse-1",
            ],
        }, "red-number-5-1")
        actions = self.game._legal_actions_for(state, "player-1")
        ids = {item["card_id"] for item in actions if item["action"] == "play"}
        self.assertEqual(ids, {"red-number-8-1", "blue-number-5-1"})

        state = self.scenario({
            "player-1": ["green-skip-1", "blue-reverse-1"],
        }, "red-skip-1")
        ids = {
            item["card_id"] for item in self.game._legal_actions_for(state, "player-1")
            if item["action"] == "play"
        }
        self.assertEqual(ids, {"green-skip-1"})

    def test_skip_reverse_draw_two_and_reverse_direction(self):
        state = self.scenario({
            "player-1": ["red-skip-1", "blue-number-1-1"],
        }, "red-number-5-1")
        result = self.game.apply_action(
            state, {"action": "play", "card_id": "red-skip-1"}, self.actor(0)
        )
        self.assertEqual(result.next_player_id, "player-3")

        state = self.scenario({
            "player-1": ["red-reverse-1", "blue-number-1-1"],
        }, "red-number-5-1")
        result = self.game.apply_action(
            state, {"action": "play", "card_id": "red-reverse-1"}, self.actor(0)
        )
        self.assertEqual(state["direction"], -1)
        self.assertEqual(result.next_player_id, "player-3")

        state = self.scenario({
            "player-1": ["red-draw_two-1", "blue-number-1-1"],
            "player-2": ["green-number-2-1"],
        }, "red-number-5-1", deck=["yellow-number-3-1", "blue-number-4-1"])
        result = self.game.apply_action(
            state, {"action": "play", "card_id": "red-draw_two-1"}, self.actor(0)
        )
        self.assertEqual(len(state["cards"]["hands"]["player-2"]), 3)
        self.assertEqual(result.next_player_id, "player-3")
        self.assertEqual(state["last_penalty"]["draw_count"], 2)
        self.assertNotIn(
            "play", {item["action"] for item in self.game._legal_actions_for(state, "player-2")}
        )

    def test_two_player_reverse_is_skip(self):
        game = Uno(random.Random(2))
        players = seats(2)
        state = game.initialize(players)
        state["cards"] = {
            "deck": [],
            "discard": [card("red-number-5-1")],
            "hands": {
                "player-1": [card("red-reverse-1"), card("blue-number-1-1")],
                "player-2": [card("green-number-2-1")],
            },
        }
        state["current_color"] = "red"
        result = game.apply_action(
            state, {"action": "play", "card_id": "red-reverse-1"}, players[0]
        )
        self.assertEqual(result.next_player_id, "player-1")
        self.assertEqual(state["direction"], 1)

    def test_wild_requires_explicit_color_and_updates_color(self):
        state = self.scenario({
            "player-1": ["wild-1", "blue-number-1-1"],
        }, "red-number-5-1")
        with self.assertRaisesRegex(ValueError, "legal_actions"):
            self.game.validate_action(
                state, {"action": "play", "card_id": "wild-1"}, self.actor(0)
            )
        action = {"action": "play", "card_id": "wild-1", "color": "green", "uno": True}
        self.game.apply_action(state, action, self.actor(0))
        self.assertEqual(state["current_color"], "green")
        self.assertEqual(state["last_uno"]["status"], "declared")

    def test_wdf_illegal_use_challenge_succeeds_server_side(self):
        state = self.scenario({
            "player-1": ["wild-draw-four-1", "red-number-7-1", "blue-number-1-1"],
            "player-2": ["green-number-2-1"],
        }, "red-number-5-1", deck=[
            "yellow-number-1-1", "yellow-number-2-1", "yellow-number-3-1", "yellow-number-4-1",
        ])
        self.game.apply_action(
            state,
            {"action": "play", "card_id": "wild-draw-four-1", "color": "blue"},
            self.actor(0),
        )
        self.assertFalse(state["pending_wild_draw_four"]["was_legal"])
        public = self.game.public_state(state, self.players)
        self.assertNotIn("was_legal", public["penalty_state"]["pending_wild_draw_four"])
        result = self.game.apply_action(
            state, {"action": "challenge_wild_draw_four"}, self.actor(1)
        )
        self.assertTrue(result.retain_turn)
        self.assertEqual(len(state["cards"]["hands"]["player-1"]), 6)
        self.assertTrue(state["last_challenge"]["challenge_succeeded"])

    def test_wdf_legal_challenge_fails_with_six_and_accept_draws_four(self):
        for action, expected in (("challenge_wild_draw_four", 6), ("accept_draw_four", 4)):
            with self.subTest(action=action):
                state = self.scenario({
                    "player-1": ["wild-draw-four-1", "blue-number-1-1"],
                    "player-2": ["green-number-2-1"],
                }, "red-number-5-1", deck=[
                    "yellow-number-1-1", "yellow-number-2-1", "yellow-number-3-1",
                    "yellow-number-4-1", "green-number-5-1", "green-number-6-1",
                ])
                self.game.apply_action(
                    state,
                    {"action": "play", "card_id": "wild-draw-four-1", "color": "blue", "uno": True},
                    self.actor(0),
                )
                result = self.game.apply_action(state, {"action": action}, self.actor(1))
                self.assertFalse(result.retain_turn)
                self.assertEqual(len(state["cards"]["hands"]["player-2"]), expected + 1)
                self.assertEqual(state["last_challenge"]["draw_count"], expected)
                self.assertEqual(result.next_player_id, "player-3")

    def test_uno_declaration_catch_window_and_regular_action_closes_it(self):
        state = self.scenario({
            "player-1": ["red-number-7-1", "blue-number-1-1"],
            "player-2": ["green-number-7-1"],
        }, "red-number-5-1", deck=["yellow-number-3-1", "yellow-number-4-1"])
        self.game.apply_action(
            state, {"action": "play", "card_id": "red-number-7-1"}, self.actor(0)
        )
        self.assertEqual(state["uno_window"]["catcher_player_id"], "player-2")
        self.assertIn(
            {"action": "catch_uno"}, self.game._legal_actions_for(state, "player-2")
        )
        result = self.game.apply_action(state, {"action": "catch_uno"}, self.actor(1))
        self.assertTrue(result.retain_turn)
        self.assertEqual(len(state["cards"]["hands"]["player-1"]), 3)
        self.assertIsNone(state["uno_window"])

        state = self.scenario({
            "player-1": ["red-number-7-1", "blue-number-1-1"],
            "player-2": ["green-number-7-1"],
        }, "red-number-5-1", deck=["yellow-number-3-1"])
        self.game.apply_action(
            state, {"action": "play", "card_id": "red-number-7-1"}, self.actor(0)
        )
        self.game.apply_action(state, {"action": "draw"}, self.actor(1))
        self.assertIsNone(state["uno_window"])
        self.assertEqual(state["last_uno"]["status"], "escaped")

        state = self.scenario({
            "player-1": ["red-number-7-1", "blue-number-1-1"],
        }, "red-number-5-1")
        self.game.apply_action(
            state,
            {"action": "play", "card_id": "red-number-7-1", "uno": True},
            self.actor(0),
        )
        self.assertIsNone(state["uno_window"])
        self.assertEqual(state["last_uno"]["status"], "declared")

    def test_drawn_playable_card_may_be_played_or_passed_only(self):
        state = self.scenario({
            "player-1": ["blue-number-1-1", "green-number-3-1"],
        }, "red-number-5-1", deck=["red-number-7-1"])
        result = self.game.apply_action(state, {"action": "draw"}, self.actor(0))
        self.assertTrue(result.retain_turn)
        actions = self.game._legal_actions_for(state, "player-1")
        play_ids = {item["card_id"] for item in actions if item["action"] == "play"}
        self.assertEqual(play_ids, {"red-number-7-1"})
        self.assertIn({"action": "pass"}, actions)
        result = self.game.apply_action(state, {"action": "pass"}, self.actor(0))
        self.assertEqual(result.next_player_id, "player-2")

    def test_unplayable_draw_ends_turn_without_client_decision(self):
        state = self.scenario({
            "player-1": ["blue-number-1-1"],
        }, "red-number-5-1", deck=["green-number-7-1"])
        result = self.game.apply_action(state, {"action": "draw"}, self.actor(0))
        self.assertFalse(result.retain_turn)
        self.assertEqual(result.next_player_id, "player-2")
        self.assertIsNone(state["drawn_card"])

    def test_last_draw_two_penalty_resolves_before_win(self):
        state = self.scenario({
            "player-1": ["red-draw_two-1"],
            "player-2": ["green-number-2-1"],
        }, "red-number-5-1", deck=["yellow-number-3-1", "blue-number-4-1"])
        result = self.game.apply_action(
            state, {"action": "play", "card_id": "red-draw_two-1"}, self.actor(0)
        )
        self.assertEqual(result.result["winner_player_id"], "player-1")
        self.assertEqual(len(state["cards"]["hands"]["player-2"]), 3)
        self.assertTrue(state["last_penalty"]["resolved"])
        self.assertEqual(state["flow"]["phase"], "finished")

    def test_last_wdf_defers_terminal_until_response(self):
        for response, expected_draw in (("accept_draw_four", 4), ("challenge_wild_draw_four", 6)):
            with self.subTest(response=response):
                state = self.scenario({
                    "player-1": ["wild-draw-four-1"],
                    "player-2": ["green-number-2-1"],
                }, "red-number-5-1", deck=[
                    "yellow-number-1-1", "yellow-number-2-1", "yellow-number-3-1",
                    "yellow-number-4-1", "blue-number-6-1", "blue-number-7-1",
                ])
                played = self.game.apply_action(
                    state,
                    {"action": "play", "card_id": "wild-draw-four-1", "color": "green"},
                    self.actor(0),
                )
                self.assertIsNone(played.result)
                self.assertIsNone(state["winner_player_id"])
                finished = self.game.apply_action(state, {"action": response}, self.actor(1))
                self.assertEqual(finished.result["winner_player_id"], "player-1")
                self.assertEqual(len(state["cards"]["hands"]["player-2"]), expected_draw + 1)

    def test_private_public_projection_and_npc_actions_do_not_leak(self):
        state = self.game.initialize(self.players)
        public = self.game.public_state(state, self.players)
        serialized = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("hands", public)
        self.assertNotIn("discard", public)
        self.assertNotIn("action_history", public)
        for hidden_card in state["cards"]["deck"]:
            self.assertNotIn(hidden_card["id"], serialized)
        private = self.game.private_state(state, self.players[0], self.players)
        self.assertEqual(set(private), {"hand", "legal_actions"})
        self.assertEqual(private["hand"], state["cards"]["hands"]["player-1"])
        for other in self.players[1:]:
            self.assertNotEqual(private["hand"], state["cards"]["hands"][other["player_id"]])
        npc_state = deepcopy(state)
        npc_state["turn_player_id"] = "player-2"
        legal = self.game.npc_legal_actions(npc_state, self.players[1], self.players)
        self.assertTrue(legal)
        for action in legal:
            self.game.validate_action(npc_state, action, self.players[1])

    def test_multiplayer_winner_take_all_settlement(self):
        participants = seats(6)
        deltas = self.game.settlement_deltas(
            {}, {"winner_player_id": "player-4"}, participants, 9
        )
        self.assertEqual(deltas["player-4"], 45)
        self.assertTrue(all(
            delta == -9 for player_id, delta in deltas.items() if player_id != "player-4"
        ))
        self.assertEqual(sum(deltas.values()), 0)


class UnoFrameworkIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-uno-")
        self.addCleanup(self.temporary.cleanup)
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()

    @staticmethod
    def participants(count=4):
        return [
            {
                "player_id": "human-1" if index == 0 else f"ai-{index}",
                "display_name": "人类" if index == 0 else f"小机 {index}",
                "role": "human" if index == 0 else "ai",
                "participant_kind": "human" if index == 0 else "bound_machine",
            }
            for index in range(count)
        ]

    def accept_invitations(self, room):
        for participant in room["participants"]:
            if participant["participant_kind"] == "bound_machine":
                room = framework.respond_to_invitation(
                    room["room_id"], "ai", participant["player_id"], "accept"
                )
        return room

    def test_framework_projection_reload_explicit_opener_and_four_player_stakes(self):
        participants = self.participants()
        room = framework.create_room(
            "uno",
            "human_first",
            "human",
            "human-1",
            opponent_id="ai-1",
            ordered_participants=participants,
            first_player_id="human-1",
            stake=5,
        )
        room = self.accept_invitations(room)
        self.assertEqual(room["board_state"]["turn_player_id"], "human-1")
        first_serialized = json.dumps(room["board_state"], sort_keys=True)
        self.assertEqual(
            json.dumps(framework.get_room(room["room_id"])["board_state"], sort_keys=True),
            first_serialized,
        )
        projected = framework.project_room_for_viewer(room, "human-1")
        self.assertEqual(set(projected["private_state"]), {"hand", "legal_actions"})
        self.assertNotIn("cards", projected["board_state"])
        self.assertNotIn("turn_player_id", projected["board_state"])
        self.assertEqual(
            projected["participants"][0]["game_metadata"]["hand_count"], 7
        )

        state = deepcopy(room["board_state"])
        state.update({
            "turn_player_id": "human-1",
            "current_color": "red",
            "direction": 1,
            "drawn_card": None,
            "pending_wild_draw_four": None,
            "uno_window": None,
            "winner_player_id": None,
            "flow": {"phase": "playing", "round_number": 1, "turn_number": 10},
        })
        state["cards"] = {
            "deck": [],
            "discard": [card("red-number-5-1")],
            "hands": {
                "human-1": [card("red-number-7-1")],
                "ai-1": [card("blue-number-1-1")],
                "ai-2": [card("green-number-2-1")],
                "ai-3": [card("yellow-number-3-1")],
            },
        }
        with database.write_transaction() as conn:
            conn.execute(
                """
                UPDATE rooms
                SET board_state = ?, current_player_id = 'human-1', turn = 'human'
                WHERE room_id = ?
                """,
                (json.dumps(state, ensure_ascii=False), room["room_id"]),
            )
        finished = framework.play_move(
            room["room_id"],
            "human",
            "human-1",
            {"action": "play", "card_id": "red-number-7-1"},
        )
        self.assertEqual(finished["status"], "finished")
        self.assertEqual(finished["winner_player_id"], "human-1")
        self.assertEqual(finished["result"]["settlement_deltas"], {
            "human-1": 15,
            "ai-1": -5,
            "ai-2": -5,
            "ai-3": -5,
        })


if __name__ == "__main__":
    unittest.main()
