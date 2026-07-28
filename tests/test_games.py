import unittest

from app.games.gomoku import Gomoku
from app.games.tictactoe import TicTacToe


class GamePluginTests(unittest.TestCase):
    def test_tictactoe_row_win(self):
        game = TicTacToe()
        state = game.initial_state()
        for col in range(3):
            game.validate_move(state, {"row": 1, "col": col}, "X")
            game.apply_move(state, {"row": 1, "col": col}, "X")
        self.assertEqual(game.check_winner(state), "X")

    def test_gomoku_diagonal_five(self):
        game = Gomoku()
        state = game.initial_state()
        for offset in range(5):
            move = {"row": 4 + offset, "col": 3 + offset}
            game.validate_move(state, move, "O")
            game.apply_move(state, move, "O")
        self.assertEqual(game.check_winner(state), "O")

    def test_rejects_occupied_position(self):
        game = TicTacToe()
        state = game.initial_state()
        game.apply_move(state, {"row": 0, "col": 0}, "X")
        with self.assertRaisesRegex(ValueError, "已有棋子"):
            game.validate_move(state, {"row": 0, "col": 0}, "O")


if __name__ == "__main__":
    unittest.main()

