from .aeroplane_chess import AeroplaneChess
from .banqi import Banqi
from .blackjack import Blackjack
from .connect4 import Connect4
from .checkers import Checkers
from .chess import Chess
from .chinese_checkers import ChineseCheckers
from .dots_boxes import DotsBoxes
from .doudizhu import Doudizhu
from .gomoku import Gomoku
from .go import Go
from .gandengyan import Gandengyan
from .guandan import Guandan
from .jungle import Jungle
from .junqi import Junqi
from .liars_dice import LiarsDice
from .mahjong import Mahjong
from .othello import Othello
from .tictactoe import TicTacToe
from .texas_holdem import TexasHoldem
from .train_cards import TrainCards
from .uno import Uno
from .xiangqi import Xiangqi
from .yahtzee import Yahtzee
from .zhajinhua import Zhajinhua

GAME_CATEGORIES = frozenset({"board", "card", "dice"})

# Short, centralized lobby/invitation copy. ``X`` is replaced with the room's
# concrete integer stake; the browser uses the same metadata for a live preview.
STAKE_PRESENTATIONS = {
    "texas_holdem": ("买入 🪙X/人", "最大亏 X"),
    "zhajinhua": ("计价 🪙X/单位", "最多 64X"),
    "gandengyan": ("底注 🪙X", "按剩牌×倍率，最高16倍"),
    "doudizhu": ("底注 🪙X", "叫分/炸弹会翻倍"),
    "mahjong": ("底注 🪙X", "点炮最多 3X"),
    "blackjack": ("下注 🪙X/人", "胜+X/负-X/和0"),
}
DEFAULT_STAKE_PRESENTATION = ("🪙X/人", "")

GAMES = {
    AeroplaneChess.game_type: AeroplaneChess(),
    Banqi.game_type: Banqi(),
    Blackjack.game_type: Blackjack(),
    TicTacToe.game_type: TicTacToe(),
    TexasHoldem.game_type: TexasHoldem(),
    TrainCards.game_type: TrainCards(),
    Gomoku.game_type: Gomoku(),
    Go.game_type: Go(),
    Gandengyan.game_type: Gandengyan(),
    Guandan.game_type: Guandan(),
    Othello.game_type: Othello(),
    Connect4.game_type: Connect4(),
    Checkers.game_type: Checkers(),
    Chess.game_type: Chess(),
    ChineseCheckers.game_type: ChineseCheckers(),
    DotsBoxes.game_type: DotsBoxes(),
    Doudizhu.game_type: Doudizhu(),
    LiarsDice.game_type: LiarsDice(),
    Mahjong.game_type: Mahjong(),
    Yahtzee.game_type: Yahtzee(),
    Uno.game_type: Uno(),
    Jungle.game_type: Jungle(),
    Junqi.game_type: Junqi(),
    Xiangqi.game_type: Xiangqi(),
    Zhajinhua.game_type: Zhajinhua(),
}


def get_game(game_type: str):
    try:
        return GAMES[game_type]
    except KeyError as exc:
        choices = "、".join(sorted(GAMES))
        raise ValueError(f"不支持的棋种：{game_type}；可选：{choices}") from exc


def stake_presentation(game_type: str, stake: int | None = None) -> dict[str, str]:
    """Return short stake label/hint metadata, optionally resolved for a room."""
    label, hint = STAKE_PRESENTATIONS.get(
        game_type, DEFAULT_STAKE_PRESENTATION
    )
    if stake is None:
        return {"stake_label": label, "stake_hint": hint}
    if stake <= 0:
        return {"stake_label": "娱乐局", "stake_hint": ""}
    value = str(stake)
    return {
        "stake_label": label.replace("X", value),
        "stake_hint": hint.replace("X", value),
    }


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
            "uses_local_npc_strategy": plugin.uses_local_npc_strategy,
            "supports_stakes": plugin.supports_stakes,
            "supports_multiplayer_stakes": plugin.supports_multiplayer_stakes,
            "uses_custom_stake_settlement": plugin.uses_custom_stake_settlement,
            **stake_presentation(plugin.game_type),
        })
    return catalog
