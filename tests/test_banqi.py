import json
import random
import tempfile
import unittest
from collections import Counter
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from app import chips, database, framework
from app.games import GAMES, game_catalog
from app.games.banqi import Banqi


PARTICIPANTS = [
    {
        "player_id": "human-1",
        "display_name": "玩家",
        "role": "human",
        "participant_kind": "human",
        "token": "X",
    },
    {
        "player_id": "ai-1",
        "display_name": "小机",
        "role": "ai",
        "participant_kind": "bound_machine",
        "token": "O",
    },
]


def actor(player_id: str) -> dict:
    return deepcopy(PARTICIPANTS[0 if player_id == "human-1" else 1])


def move(from_row: int, from_col: int, to_row: int, to_col: int) -> dict:
    return {
        "action": "move",
        "from_row": from_row,
        "from_col": from_col,
        "to_row": to_row,
        "to_col": to_col,
    }


def flip(row: int, col: int) -> dict:
    return {"action": "flip", "row": row, "col": col}


def piece(value: str, revealed: bool = True) -> dict:
    return {"piece": value, "revealed": revealed}


class BanqiRuleTests(unittest.TestCase):
    def setUp(self):
        self.game = Banqi(rng=random.Random(20260829))

    def empty_state(self, *, assigned: bool = True) -> dict:
        state = self.game.initialize(deepcopy(PARTICIPANTS))
        state["board"] = [[None for _ in range(4)] for _ in range(8)]
        state["current_player_id"] = "human-1"
        state["color_by_player"] = (
            {"human-1": "r", "ai-1": "b"} if assigned else {}
        )
        state["marks_by_player"] = {"human-1": "X", "ai-1": "O"}
        return state

    def test_initial_set_is_complete_randomized_and_entirely_hidden(self):
        state = self.game.initialize(deepcopy(PARTICIPANTS))
        identities = [cell["piece"] for row in state["board"] for cell in row]
        self.assertEqual(Counter(identities), Counter(self.game._full_piece_set()))
        self.assertTrue(all(not cell["revealed"] for row in state["board"] for cell in row))

        public = self.game.public_state(state, PARTICIPANTS)
        self.assertEqual({value for row in public["board"] for value in row}, {"hidden"})
        self.assertEqual(len(public["legal_actions"]), 32)
        self.assertEqual(self.game.private_state(state, PARTICIPANTS[0], PARTICIPANTS), {})
        encoded = json.dumps(public, ensure_ascii=False)
        for identity in set(identities):
            self.assertNotIn(identity, encoded)

    def test_first_flip_assigns_camps_and_only_reveals_that_square(self):
        state = self.game.initialize(deepcopy(PARTICIPANTS))
        first_piece = state["board"][2][1]["piece"]
        first_color = first_piece.split(":", 1)[0]
        result = self.game.apply_action(state, flip(2, 1), actor("human-1"))
        self.assertEqual(result.state["color_by_player"]["human-1"], first_color)
        self.assertEqual(
            result.state["color_by_player"]["ai-1"],
            "b" if first_color == "r" else "r",
        )
        public = self.game.public_state(result.state, PARTICIPANTS)
        self.assertEqual(public["board"][2][1], first_piece)
        self.assertEqual(public["hidden_count"], 31)
        self.assertEqual(
            sum(value == "hidden" for row in public["board"] for value in row),
            31,
        )
        self.assertIn("首翻定色", result.note)

    def test_rank_capture_pawn_king_exception_and_same_rank(self):
        state = self.empty_state()
        state["board"][3][1] = piece("r:p")
        state["board"][3][2] = piece("b:k")
        self.game.validate_action(state, move(3, 1, 3, 2), actor("human-1"))

        state["board"][3][1] = piece("r:k")
        state["board"][3][2] = piece("b:p")
        with self.assertRaisesRegex(ValueError, "合法行动"):
            self.game.validate_action(state, move(3, 1, 3, 2), actor("human-1"))

        state["board"][3][1] = piece("r:n")
        state["board"][3][2] = piece("b:n")
        self.game.validate_action(state, move(3, 1, 3, 2), actor("human-1"))
        state["board"][3][2] = piece("b:r")
        with self.assertRaisesRegex(ValueError, "合法行动"):
            self.game.validate_action(state, move(3, 1, 3, 2), actor("human-1"))

    def test_cannon_needs_one_screen_and_a_revealed_enemy_target(self):
        state = self.empty_state()
        state["board"][0][0] = piece("r:c")
        state["board"][0][1] = piece("r:p", revealed=False)
        state["board"][0][3] = piece("b:k")
        state["board"][7][3] = piece("b:p", revealed=False)
        cannon_capture = move(0, 0, 0, 3)
        self.game.validate_action(state, cannon_capture, actor("human-1"))
        no_screen = deepcopy(state)
        no_screen["board"][0][1] = None
        with self.assertRaisesRegex(ValueError, "合法行动"):
            self.game.validate_action(no_screen, cannon_capture, actor("human-1"))
        two_screens = deepcopy(state)
        two_screens["board"][0][2] = piece("b:p", revealed=False)
        with self.assertRaisesRegex(ValueError, "合法行动"):
            self.game.validate_action(two_screens, cannon_capture, actor("human-1"))
        hidden_target = deepcopy(state)
        hidden_target["board"][0][3]["revealed"] = False
        with self.assertRaisesRegex(ValueError, "合法行动"):
            self.game.validate_action(hidden_target, cannon_capture, actor("human-1"))

        result = self.game.apply_action(state, cannon_capture, actor("human-1"))
        public = self.game.public_state(result.state, PARTICIPANTS)
        self.assertEqual(public["board"][0][3], "r:c")
        self.assertEqual(public["last_action"]["captured"], "b:k")
        self.assertIn("黑将", result.note)

    def test_all_captured_and_immobilized_are_losses(self):
        captured = self.empty_state()
        captured["board"][0][0] = piece("r:r")
        captured["board"][0][1] = piece("b:p")
        result = self.game.apply_action(captured, move(0, 0, 0, 1), actor("human-1"))
        self.assertEqual(result.result, {"winner_player_id": "human-1", "draw": False})
        self.assertEqual(result.state["terminal_reason"], "all_captured")

        stuck = self.empty_state()
        stuck["board"][0][0] = piece("b:p")
        stuck["board"][0][1] = piece("r:a")
        stuck["board"][1][0] = piece("r:a")
        stuck["board"][7][3] = piece("r:r")
        result = self.game.apply_action(stuck, move(7, 3, 6, 3), actor("human-1"))
        self.assertEqual(result.result, {"winner_player_id": "human-1", "draw": False})
        self.assertEqual(result.state["terminal_reason"], "immobilized")
        self.assertIn("困毙", result.note)

    def test_forty_quiet_turns_is_a_draw_but_flip_and_capture_reset_it(self):
        state = self.empty_state()
        state["board"][3][0] = piece("r:r")
        state["board"][5][3] = piece("b:r")
        state["quiet_turns"] = 39
        result = self.game.apply_action(state, move(3, 0, 3, 1), actor("human-1"))
        self.assertEqual(result.result, {"draw": True})
        self.assertEqual(result.state["terminal_reason"], "quiet_turn_limit")

        flipped = self.game.initialize(deepcopy(PARTICIPANTS))
        flipped["quiet_turns"] = 39
        self.assertEqual(
            self.game.apply_action(flipped, flip(0, 0), actor("human-1")).state["quiet_turns"],
            0,
        )

    def test_hidden_identity_permutation_cannot_change_public_or_npc_context(self):
        state = self.empty_state()
        state["board"][2][0] = piece("r:c")
        state["board"][2][1] = piece("b:p", revealed=False)
        state["board"][2][3] = piece("b:k", revealed=False)
        state["board"][6][2] = piece("r:a", revealed=False)
        state["current_player_id"] = "human-1"
        permuted = deepcopy(state)
        permuted["board"][2][1]["piece"], permuted["board"][2][3]["piece"] = (
            permuted["board"][2][3]["piece"], permuted["board"][2][1]["piece"]
        )

        self.assertEqual(
            self.game.public_state(state, PARTICIPANTS),
            self.game.public_state(permuted, PARTICIPANTS),
        )
        self.assertEqual(
            self.game.npc_legal_actions(state, actor("human-1"), PARTICIPANTS),
            self.game.npc_legal_actions(permuted, actor("human-1"), PARTICIPANTS),
        )
        for legal in self.game.npc_legal_actions(state, actor("human-1"), PARTICIPANTS):
            self.game.validate_action(deepcopy(state), legal, actor("human-1"))

    def test_rejects_extra_fields_bad_coordinates_and_unrevealed_normal_capture(self):
        state = self.empty_state()
        state["board"][1][1] = piece("r:r")
        state["board"][1][2] = piece("b:p", revealed=False)
        for payload in (
            {**flip(0, 0), "piece": "r:k"},
            flip(True, 0),
            move(1, 1, 8, 1),
            move(1, 1, 1, 2),
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.game.validate_action(state, payload, actor("human-1"))


class BanqiFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-banqi-")
        self.addCleanup(self.temporary.cleanup)
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()

    def test_catalog_supports_two_player_stakes_and_npc_actions(self):
        item = next(item for item in game_catalog() if item["game_type"] == "banqi")
        self.assertEqual(item["allowed_player_counts"], [2])
        self.assertTrue(item["supports_stakes"])
        self.assertTrue(item["supports_npcs"])

    def test_random_layout_is_persisted_and_refresh_does_not_reshuffle(self):
        game = Banqi(rng=random.Random(88))
        with patch.dict(GAMES, {"banqi": game}):
            room = framework.create_room(
                "banqi", "human_first", "human", "human-save", "ai-save"
            )
            identities = [
                cell["piece"] for row in room["board_state"]["board"] for cell in row
            ]
            restored = framework.get_room(
                room["room_id"], "human", "human-save", "ai-save"
            )
            restored_identities = [
                cell["piece"]
                for row in restored["board_state"]["board"]
                for cell in row
            ]
            self.assertEqual(restored_identities, identities)
            projected = framework.project_room_for_viewer(restored, "human-save")
            self.assertEqual(
                {value for row in projected["board_state"]["board"] for value in row},
                {"hidden"},
            )
            self.assertEqual(projected["private_state"], {})

    def test_flip_event_reveals_only_the_flipped_piece(self):
        game = Banqi(rng=random.Random(99))
        with patch.dict(GAMES, {"banqi": game}):
            room = framework.create_room(
                "banqi", "human_first", "human", "human-event", "ai-event"
            )
            flipped_identity = room["board_state"]["board"][0][0]["piece"]
            room = framework.play_move(
                room["room_id"], "human", "human-event", flip(0, 0)
            )
            projected = framework.project_room_for_viewer(room, "ai-event")
            board = projected["board_state"]["board"]
            self.assertEqual(board[0][0], flipped_identity)
            self.assertEqual(sum(value == "hidden" for row in board for value in row), 31)
            timeline = framework.list_timeline(
                room["room_id"], viewer_player_id="ai-event"
            )
            move_event = next(event for event in timeline if event["event_type"] == "move")
            self.assertIn(Banqi._piece_label(flipped_identity), move_event["move_label"])
            self.assertEqual(move_event["move"], flip(0, 0))
            self.assertNotIn("board", json.dumps(move_event, ensure_ascii=False))

    def test_two_player_stake_settles_on_authoritative_capture(self):
        game = Banqi(rng=random.Random(1))
        prepared = game.initial_state()
        prepared["board"] = [[None for _ in range(4)] for _ in range(8)]
        prepared["board"][0][0] = piece("r:r")
        prepared["board"][0][1] = piece("b:p")
        prepared["color_by_player"] = {"human-chip": "r", "ai-chip": "b"}
        with (
            patch.dict(GAMES, {"banqi": game}),
            patch.object(game, "initial_state", side_effect=lambda: deepcopy(prepared)),
        ):
            room = framework.create_room(
                "banqi", "human_first", "human", "human-chip", "ai-chip", stake=6
            )
            room = framework.respond_to_invitation(
                room["room_id"], "ai", "ai-chip", "accept"
            )
            room = framework.play_move(
                room["room_id"], "human", "human-chip", move(0, 0, 0, 1)
            )
        self.assertEqual(room["status"], "finished")
        self.assertEqual(room["winner_player_id"], "human-chip")
        human_settlements = [
            item for item in chips.list_ledger("human", "human-chip")
            if item["transaction_type"] == "duel_win"
        ]
        ai_settlements = [
            item for item in chips.list_ledger("ai", "ai-chip")
            if item["transaction_type"] == "duel_loss"
        ]
        self.assertEqual([item["amount"] for item in human_settlements], [6])
        self.assertEqual([item["amount"] for item in ai_settlements], [-6])


if __name__ == "__main__":
    unittest.main()
