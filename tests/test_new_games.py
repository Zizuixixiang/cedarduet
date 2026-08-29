import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.framework import DuelError, create_room, play_move
from app.games import GAMES
from app.games.connect4 import Connect4
from app.games.dots_boxes import DotsBoxes
from app.games.jungle import Jungle
from app.games.othello import Othello


class OthelloTests(unittest.TestCase):
    def test_standard_move_flips_piece(self):
        game = Othello()
        state = game.initial_state()
        game.validate_move(state, {"row": 2, "col": 3}, "X")
        result = game.apply_move(state, {"row": 2, "col": 3}, "X")
        self.assertEqual(result.state["board"][3][3], "X")
        self.assertEqual(result.state["last_move"]["flipped"], 1)

    def test_no_legal_move_is_automatically_skipped(self):
        game = Othello()
        state = game.initial_state()
        state["board"] = [["X" for _ in range(8)] for _ in range(8)]
        state["board"][0][0] = None
        state["board"][0][1] = "O"
        state["board"][0][3] = None
        state["board"][0][4] = "O"
        result = game.apply_move(state, {"row": 0, "col": 0}, "X")
        self.assertTrue(result.retain_turn)
        self.assertIn("自动跳过", result.note)
        self.assertIsNone(game.check_winner(result.state))

    def test_terminal_position_counts_pieces(self):
        game = Othello()
        state = game.initial_state()
        state["board"] = [["X" for _ in range(8)] for _ in range(8)]
        state["board"][0][0] = "O"
        self.assertEqual(game.check_winner(state), "X")
        self.assertEqual(state["scores"], {"X": 63, "O": 1})


class Connect4Tests(unittest.TestCase):
    def test_gravity_and_horizontal_win(self):
        game = Connect4()
        state = game.initial_state()
        for col in range(4):
            game.validate_move(state, {"col": col}, "X")
            game.apply_move(state, {"col": col}, "X")
        self.assertEqual([state["board"][5][col] for col in range(4)], ["X"] * 4)
        self.assertEqual(game.check_winner(state), "X")

    def test_rejects_full_column(self):
        game = Connect4()
        state = game.initial_state()
        for row in range(6):
            state["board"][row][2] = "X"
        with self.assertRaisesRegex(ValueError, "下满"):
            game.validate_move(state, {"col": 2}, "O")

    def test_only_column_is_accepted(self):
        game = Connect4()
        with self.assertRaisesRegex(ValueError, "只接受 col"):
            game.validate_move(game.initial_state(), {"row": 5, "col": 2}, "X")


class DotsBoxesTests(unittest.TestCase):
    def test_completing_box_scores_and_retains_turn(self):
        game = DotsBoxes()
        state = game.initial_state()
        state["horizontal_edges"][0][0] = "O"
        state["horizontal_edges"][1][0] = "X"
        state["vertical_edges"][0][0] = "O"
        move = {"orientation": "v", "row": 0, "col": 1}
        game.validate_move(state, move, "X")
        result = game.apply_move(state, move, "X")
        self.assertTrue(result.retain_turn)
        self.assertEqual(result.state["boxes"][0][0], "X")
        self.assertEqual(result.state["scores"]["X"], 1)

    def test_finished_board_uses_score(self):
        game = DotsBoxes()
        state = game.initial_state()
        state["boxes"] = [["X" for _ in range(4)] for _ in range(4)]
        state["scores"] = {"X": 10, "O": 6}
        self.assertEqual(game.check_winner(state), "X")

    def test_framework_honors_retained_turn(self):
        with tempfile.TemporaryDirectory(prefix="duel-dots-") as directory:
            with patch.object(database, "DB_PATH", Path(directory) / "test.db"):
                database.init_db()
                room = create_room(
                    "dots_boxes", "human_first", "human", "human", "ai"
                )
                moves = [
                    ("human", "human", {"orientation": "h", "row": 0, "col": 0}),
                    ("ai", "ai", {"orientation": "v", "row": 0, "col": 0}),
                    ("human", "human", {"orientation": "h", "row": 1, "col": 0}),
                    ("ai", "ai", {"orientation": "v", "row": 0, "col": 1}),
                ]
                for role, player, move in moves:
                    room = play_move(room["room_id"], role, player, move)
                self.assertEqual(room["turn"], "ai")
                self.assertIn("行动权保留", room["action_note"])


class JungleTests(unittest.TestCase):
    def setUp(self):
        self.game = Jungle()

    def empty_state(self):
        state = self.game.initial_state()
        state["board"] = [[None for _ in range(7)] for _ in range(9)]
        return state

    def test_rat_enters_water_and_elephant_cannot_capture_rat(self):
        state = self.empty_state()
        state["board"][3][0] = "X:R"
        self.game.validate_move(
            state,
            {"from_row": 3, "from_col": 0, "to_row": 3, "to_col": 1},
            "X",
        )
        state["board"][3][0] = None
        state["board"][2][0] = "X:E"
        state["board"][2][1] = "O:R"
        with self.assertRaisesRegex(ValueError, "不能吃"):
            self.game.validate_move(
                state,
                {"from_row": 2, "from_col": 0, "to_row": 2, "to_col": 1},
                "X",
            )

    def test_water_rat_cannot_capture_land_elephant(self):
        state = self.empty_state()
        state["board"][3][1] = "X:R"
        state["board"][3][0] = "O:E"
        with self.assertRaisesRegex(ValueError, "不能吃"):
            self.game.validate_move(
                state,
                {"from_row": 3, "from_col": 1, "to_row": 3, "to_col": 0},
                "X",
            )

    def test_lion_tiger_jump_and_rat_block(self):
        state = self.empty_state()
        state["board"][6][1] = "X:T"
        jump = {"from_row": 6, "from_col": 1, "to_row": 2, "to_col": 1}
        self.game.validate_move(state, jump, "X")
        state["board"][4][1] = "O:R"
        with self.assertRaisesRegex(ValueError, "阻挡"):
            self.game.validate_move(state, jump, "X")

    def test_enemy_in_own_trap_can_be_captured_by_any_beast(self):
        state = self.empty_state()
        state["board"][7][2] = "X:R"
        state["board"][7][3] = "O:E"
        self.game.validate_move(
            state,
            {"from_row": 7, "from_col": 2, "to_row": 7, "to_col": 3},
            "X",
        )

    def test_den_rules_and_no_pieces_win(self):
        state = self.empty_state()
        state["board"][8][2] = "X:C"
        with self.assertRaisesRegex(ValueError, "己方兽穴"):
            self.game.validate_move(
                state,
                {"from_row": 8, "from_col": 2, "to_row": 8, "to_col": 3},
                "X",
            )
        state["board"][8][2] = None
        state["board"][1][3] = "X:C"
        move = {"from_row": 1, "from_col": 3, "to_row": 0, "to_col": 3}
        result = self.game.apply_move(state, move, "X")
        self.assertEqual(self.game.check_winner(result.state), "X")
        self.assertIn("兽穴", result.note)

    def test_opponent_with_no_pieces_loses(self):
        state = self.empty_state()
        state["board"][4][0] = "X:C"
        result = self.game.apply_move(
            state,
            {"from_row": 4, "from_col": 0, "to_row": 5, "to_col": 0},
            "X",
        )
        self.assertEqual(self.game.check_winner(result.state), "X")
        self.assertIn("无棋子", result.note)


class RegistryErrorTests(unittest.TestCase):
    def test_every_plugin_has_shared_rules_and_move_format(self):
        self.assertEqual(len(GAMES), 8)
        for plugin in GAMES.values():
            self.assertTrue(plugin.rules_text)
            self.assertTrue(plugin.move_format)
            self.assertEqual(plugin.min_players, 2)
            self.assertLessEqual(plugin.max_players, 6)
            self.assertIn(2, plugin.resolved_allowed_player_counts())

    def test_unknown_game_lists_every_available_type(self):
        with self.assertRaises(DuelError) as caught:
            create_room("chess", "human_first", "human", "human")
        for game_type in (
            "tictactoe", "gomoku", "othello",
            "connect4", "dots_boxes", "liars_dice", "jungle",
            "xiangqi",
        ):
            self.assertIn(game_type, caught.exception.message)


if __name__ == "__main__":
    unittest.main()
