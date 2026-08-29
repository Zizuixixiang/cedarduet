"""Shared constants for the Guandan game engine.

Card string format
------------------
Every card is a 2-character string ``<suit><rank>``:

* suits: ``H`` (hearts), ``S`` (spades), ``C`` (clubs), ``D`` (diamonds)
* ranks: ``2``-``9``, ``T`` (10), ``J``, ``Q``, ``K``, ``A``
* jokers: ``SB`` (black/small joker), ``HR`` (red/big joker)

Action format
-------------
An action is a plain list ``[combo_type, key_rank, cards]``, e.g.
``['Pair', '3', ['H3', 'D3']]``.  ``key_rank`` is the rank used for
comparisons: the natural rank for singles/pairs/trips/bombs, the lowest
rank of a sequence (straight, three-pair, two-trips), or ``'R'`` for the
four-joker bomb.  Passing is ``['PASS', 'PASS', 'PASS']``.
"""

# Rank symbols in ascending natural order. 'B' = black joker, 'R' = red joker.
CARD_RANK = ['2', '3', '4', '5', '6', '7', '8', '9', 'T', 'J', 'Q', 'K',
             'A', 'B', 'R']
CARD_RANK_INDEX = {rank: index for index, rank in enumerate(CARD_RANK)}

# Index of 'A' in CARD_RANK; a team wins the episode by passing level A.
LEVEL_A_INDEX = 12

SUITS = ['H', 'S', 'C', 'D']
SUIT_INDEX = {'H': 0, 'S': 1, 'C': 2, 'D': 3}
INDEX_SUIT = {0: 'H', 1: 'S', 2: 'C', 3: 'D'}

SMALL_JOKER = 'SB'
BIG_JOKER = 'HR'

# Combo types that can beat a given combo type (besides higher same-type).
# PASS is always legal when responding.
GREATER_TYPES = {
    'Single': ['PASS', 'Single', 'Bomb', 'StraightFlush'],
    'Pair': ['PASS', 'Pair', 'Bomb', 'StraightFlush'],
    'Trips': ['PASS', 'Trips', 'Bomb', 'StraightFlush'],
    'ThreePair': ['PASS', 'ThreePair', 'Bomb', 'StraightFlush'],
    'ThreeWithTwo': ['PASS', 'ThreeWithTwo', 'Bomb', 'StraightFlush'],
    'TwoTrips': ['PASS', 'TwoTrips', 'Bomb', 'StraightFlush'],
    'Straight': ['PASS', 'Straight', 'Bomb', 'StraightFlush'],
    'StraightFlush': ['PASS', 'Bomb', 'StraightFlush'],
    'Bomb': ['PASS', 'Bomb', 'StraightFlush'],
}

# Combo types that are sequences. Inside a sequence the level card only
# counts at its natural position, so sequences are compared with
# NATURAL_SEQ_VALUE instead of the level-aware table.
SEQUENCE_TYPES = ('Straight', 'ThreePair', 'TwoTrips')

# Natural ordering used for sequences, keyed by the lowest rank of the
# sequence. 'A' counts low (A-2-3-4-5 is the smallest straight; a straight
# ending with A, e.g. T-J-Q-K-A, is keyed by its lowest rank 'T').
NATURAL_SEQ_VALUE = {
    'A': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7,
    '8': 8, '9': 9, 'T': 10, 'J': 11, 'Q': 12, 'K': 13,
}

# Rank row used by the public ``remain_cards`` counters
# (note: this table is A-first, unlike CARD_RANK_INDEX).
REMAIN_RANK_INDEX = {
    'A': 0, '2': 1, '3': 2, '4': 3, '5': 4, '6': 5, '7': 6, '8': 7,
    '9': 8, 'T': 9, 'J': 10, 'Q': 11, 'K': 12, 'B': 13, 'R': 13,
}


def make_card_values(level_rank):
    """Build the level-aware card value table.

    Args:
        level_rank (str): rank character of the current level card,
            e.g. ``'2'`` when playing level 2.

    Returns:
        dict: rank char -> comparison value. Normal ranks keep their
        natural order (2..A = 2..14), the level rank is promoted to 15,
        black joker is 16 and red joker 17.
    """
    values = {'2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8,
              '9': 9, 'T': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14,
              'B': 16, 'R': 17}
    values[level_rank] = 15
    return values


def pass_action():
    """Return a fresh PASS action (a new list, safe to mutate)."""
    return ['PASS', 'PASS', 'PASS']


def is_joker_bomb(action):
    """Whether the action is the four-joker bomb (天王炸), which beats
    every other combo."""
    return action[0] == 'Bomb' and action[1] == 'R'
