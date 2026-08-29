"""Action comparison: which legal actions beat the current table action.

This module fixes several rule bugs present in the original
implementation (each fix is marked with a ``RULE`` comment):

1. The four-joker bomb beats everything, including straight flushes and
   bombs with 5+ cards (it used to be filtered out by the bomb-length
   check because it only has 4 cards).
2. A straight flush must be strictly greater to beat another straight
   flush (equal lowest rank used to be accepted).
3. Two-trips (钢板) are sequences and are compared by natural rank; the
   level card is not promoted inside a sequence (a level-rank two-trips
   used to wrongly beat KKKAAA).
"""

import copy

from guandan_rlcard.constants import (
    CARD_RANK,
    GREATER_TYPES,
    NATURAL_SEQ_VALUE,
    SEQUENCE_TYPES,
    is_joker_bomb,
    make_card_values,
    pass_action,
)

# A straight flush outranks bombs with fewer than 6 cards.
STRAIGHT_FLUSH_BOMB_LENGTH = 5


def get_gt_actions(level_rank_index, greater_player, available_actions):
    """Filter ``available_actions`` down to those beating the table action.

    Args:
        level_rank_index (int): index of the current level rank in CARD_RANK.
        greater_player (GuandanPlayer): player holding the current biggest
            action (``greater_player.played_action``).
        available_actions (list): legal combos generated from the hand,
            without PASS.

    Returns:
        list: actions that beat the current action; always contains PASS.
    """
    gt_actions = [pass_action()]
    current = greater_player.played_action

    # RULE: nothing beats the four-joker bomb.
    if is_joker_bomb(current):
        return gt_actions

    card_values = make_card_values(CARD_RANK[level_rank_index])
    target_types = copy.deepcopy(GREATER_TYPES[current[0]])

    current_bomb_length = -1
    if current[0] == 'Bomb':
        current_bomb_length = len(current[2])
        if current_bomb_length >= 6:
            # A 6+ card bomb outranks any straight flush.
            target_types.remove('StraightFlush')
    elif current[0] == 'StraightFlush':
        current_bomb_length = STRAIGHT_FLUSH_BOMB_LENGTH

    for action in available_actions:
        if action[0] not in target_types:
            continue
        if action[0] == 'Bomb':
            if _bomb_beats(action, current, current_bomb_length, card_values):
                gt_actions.append(action)
        elif action[0] == 'StraightFlush':
            if _straight_flush_beats(action, current):
                gt_actions.append(action)
        elif _same_type_beats(action, current, card_values):
            gt_actions.append(action)

    return gt_actions


def _bomb_beats(action, current, current_bomb_length, card_values):
    """Whether a bomb action beats the current action."""
    # RULE: the four-joker bomb beats everything; check it before the
    # length comparison (it has only 4 cards).
    if is_joker_bomb(action):
        return True
    length = len(action[2])
    if length < current_bomb_length:
        return False
    if length == current_bomb_length:
        if current[0] == 'StraightFlush':
            # A 5-card bomb cannot beat a straight flush.
            return False
        return card_values[action[1]] > card_values[current[1]]
    return True


def _straight_flush_beats(action, current):
    """Whether a straight flush beats the current action."""
    if current[0] != 'StraightFlush':
        # Beats every normal combo and bombs up to 5 cards
        # (6+ card bombs were excluded via target_types).
        return True
    # RULE: must be strictly greater; an equal straight flush cannot
    # beat the one on the table (original code used '<' instead of '<=').
    return NATURAL_SEQ_VALUE[action[1]] > NATURAL_SEQ_VALUE[current[1]]


def _same_type_beats(action, current, card_values):
    """Whether a same-type normal combo beats the current action."""
    if current[0] in SEQUENCE_TYPES:
        # RULE: sequences (straight, three-pair, two-trips) compare by
        # natural rank; the level card is not promoted inside them.
        # The original code only did this for Straight/ThreePair and let
        # TwoTrips fall through to the level-aware table.
        return NATURAL_SEQ_VALUE[action[1]] > NATURAL_SEQ_VALUE[current[1]]
    return card_values[action[1]] > card_values[current[1]]
