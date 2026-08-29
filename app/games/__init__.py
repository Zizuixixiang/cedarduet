from .connect4 import Connect4
from .dots_boxes import DotsBoxes
from .gomoku import Gomoku
from .jungle import Jungle
from .liars_dice import LiarsDice
from .othello import Othello
from .tictactoe import TicTacToe
from .xiangqi import Xiangqi

GAME_CATEGORIES = frozenset({"board", "card", "dice"})

GAMES = {
    TicTacToe.game_type: TicTacToe(),
    Gomoku.game_type: Gomoku(),
    Othello.game_type: Othello(),
    Connect4.game_type: Connect4(),
    DotsBoxes.game_type: DotsBoxes(),
    LiarsDice.game_type: LiarsDice(),
    Jungle.game_type: Jungle(),
    Xiangqi.game_type: Xiangqi(),
}


def get_game(game_type: str):
    try:
        return GAMES[game_type]
    except KeyError as exc:
        choices = "、".join(sorted(GAMES))
        raise ValueError(f"不支持的棋种：{game_type}；可选：{choices}") from exc


def game_catalog() -> list[dict]:
    catalog = []
    for plugin in GAMES.values():
        counts = plugin.resolved_allowed_player_counts()
        if plugin.category not in GAME_CATEGORIES:
            raise ValueError(
                f"{plugin.game_type} 的 category 必须是 board/card/dice"
            )
        catalog.append({
            "game_type": plugin.game_type,
            "display_name": plugin.display_name,
            "category": plugin.category,
            "min_players": counts[0],
            "max_players": counts[-1],
            "allowed_player_counts": list(counts),
            "recommended_players": plugin.resolved_recommended_players(),
            "supports_npcs": plugin.supports_npcs,
            "supports_stakes": plugin.supports_stakes,
            "supports_multiplayer_stakes": plugin.supports_multiplayer_stakes,
        })
    return catalog
