"""Card conversion, sorting and single-card comparison helpers."""

from rlcard.games.base import Card

from guandan_rlcard.constants import (
    CARD_RANK,
    CARD_RANK_INDEX,
    LEVEL_A_INDEX,
    BIG_JOKER,
    SMALL_JOKER,
)


def card_to_str(card):
    """Convert a Card object to its 2-char string form ('H3', 'SB', 'HR')."""
    if card.rank == '':
        # Jokers carry their identity in the suit field ('RJ'/'BJ').
        return BIG_JOKER if card.suit == 'RJ' else SMALL_JOKER
    return card.suit + card.rank


def cards2str(cards):
    """Convert a list of Card objects to a list of card strings."""
    return [card_to_str(card) for card in cards]


def str_card_sort_key(card_str):
    """Sort key for card strings: natural rank first, then suit."""
    return (CARD_RANK.index(card_str[1]), card_str[0])


def natural_card_cmp(card_1, card_2):
    """Compare two Card objects by natural rank only (level card NOT
    promoted). Jokers rank above all normal cards. Returns -1/0/1.

    Used to keep hands sorted; gameplay comparisons go through
    :func:`tribute_card_cmp` or the action-level logic instead.
    """
    key = []
    for card in [card_1, card_2]:
        if card.rank == '':
            key.append(14 if card.suit == 'RJ' else 13)
        else:
            key.append(CARD_RANK.index(card.rank))
    if key[0] > key[1]:
        return 1
    if key[0] < key[1]:
        return -1
    return 0


def tribute_card_cmp(card1, card2, level_rank):
    """Compare two Card objects with level card and jokers promoted.

    Used to decide which tribute card is bigger. Order:
    red joker > black joker > level card > A > K > ... > 2.

    Args:
        card1, card2 (Card): cards to compare.
        level_rank (str): rank character of the current level card.

    Returns:
        int: 1 if card1 > card2, -1 if card1 < card2, 0 if equal rank.
    """
    special_order = {'RJ': 6, 'BJ': 4, level_rank: 2}
    base_order = '23456789TJQKA'

    def value(card):
        if card.suit in special_order:      # jokers ('RJ'/'BJ' suits)
            return special_order[card.suit]
        if card.rank in special_order:      # level card
            return special_order[card.rank]
        # Normal cards normalised below any special card.
        return base_order.index(card.rank) / len(base_order)

    value1, value2 = value(card1), value(card2)
    if value1 > value2:
        return 1
    if value1 < value2:
        return -1
    return 0


def count_wildcards(card_strs, level_rank_index):
    """Count wildcards (heart level cards, 逢人配) in a list of card strings."""
    return card_strs.count('H' + CARD_RANK[level_rank_index])


def card_from_str(card_str):
    """Build a Card object from a card string ('H3', 'SB', 'HR')."""
    if card_str == BIG_JOKER:
        return Card('RJ', '')
    if card_str == SMALL_JOKER:
        return Card('BJ', '')
    return Card(card_str[0], card_str[1])


def card_from_action(action):
    """Build a Card object from the first card of an action."""
    return card_from_str(action[2][0])


def cards_from_tribute_actions(action1, action2):
    """Build the two Card objects carried by two tribute actions."""
    return card_from_action(action1), card_from_action(action2)


def advance_level(rank_index, gain):
    """Advance a team's level by ``gain`` with the level-A gate applied.

    Rules implemented:
    * A team cannot jump past A: any advance crossing A stops at A.
    * At level A, a 1st+4th finish (gain 1) does NOT pass A - the team
      stays at A and must try again.
    * At level A, a 1st+2nd (gain 3) or 1st+3rd (gain 2) finish passes A
      and wins the episode; the returned index goes beyond
      ``LEVEL_A_INDEX`` to signal that.

    Args:
        rank_index (int): current level index into CARD_RANK (0 = level 2).
        gain (int): 1, 2 or 3 depending on how teammates finished.

    Returns:
        int: new level index. A value > LEVEL_A_INDEX means the team
        passed A and the episode is over.
    """
    if rank_index < LEVEL_A_INDEX and rank_index + gain > LEVEL_A_INDEX:
        return LEVEL_A_INDEX
    if rank_index == LEVEL_A_INDEX and gain == 1:
        return LEVEL_A_INDEX
    return rank_index + gain
