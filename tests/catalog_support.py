"""Reviewed production catalog; deliberately independent of the live registry.

New games must be classified here and in the separate privacy/NPC audits.
Use CountEqual for list responses so duplicate IDs cannot hide behind a set.
"""

EXPECTED_GAMES_BY_CATEGORY = {
    'board': {'aeroplane_chess', 'banqi', 'checkers', 'chess', 'chinese_checkers',
              'connect4', 'dots_boxes', 'go', 'gomoku', 'jungle', 'junqi',
              'othello', 'tictactoe', 'xiangqi'},
    'card': {'four_color_tenhu', 'four_color_jiuzhi', 'four_color_qunhu', 'blackjack', 'doudizhu', 'gandengyan', 'guandan', 'mahjong', 'ka_wu_xing',
             'texas_holdem', 'train_cards', 'uno', 'zhajinhua'},
    'dice': {'liars_dice', 'yahtzee'},
    'tabletop': {'bomb_plane', 'monopoly', 'rummikub', 'carcassonne', 'gem_merchant', 'monopoly_plus'},
}

EXPECTED_GAME_TYPES = frozenset().union(*EXPECTED_GAMES_BY_CATEGORY.values())
