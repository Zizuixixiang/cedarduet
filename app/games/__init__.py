from .gomoku import Gomoku
from .tictactoe import TicTacToe

GAMES = {
    TicTacToe.game_type: TicTacToe(),
    Gomoku.game_type: Gomoku(),
}


def get_game(game_type: str):
    try:
        return GAMES[game_type]
    except KeyError as exc:
        choices = "、".join(sorted(GAMES))
        raise ValueError(f"不支持的棋种：{game_type}；可选：{choices}") from exc

