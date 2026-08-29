"""Heuristic hand analysis shared by the engine and baseline agents.

Nothing in this module affects rule legality. ``combine_handcards`` is a
greedy hand decomposition used by agents to pick tribute-back cards;
``estimate_min_steps`` is a greedy lower-bound-ish estimate of how many
plays a hand needs, used by the environment's oracle shaping value.
"""

from collections import Counter, defaultdict

from guandan_rlcard.constants import (
    BIG_JOKER,
    CARD_RANK,
    CARD_RANK_INDEX,
    SMALL_JOKER,
    SUITS,
)

# A-first natural values used when hunting straights inside a hand.
_STRAIGHT_VALUE = {'A': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7,
                   '8': 8, '9': 9, 'T': 10, 'J': 11, 'Q': 12, 'K': 13}
_VALUE_STRAIGHT = {v: k for k, v in _STRAIGHT_VALUE.items()}


def _group_by_rank(sorted_cards):
    """Group value-sorted card strings into Single/Pair/Trips/Bomb buckets."""
    groups = {'Single': [], 'Pair': [], 'Trips': [], 'Bomb': []}
    bomb_info = {}
    start = 0
    for i in range(1, len(sorted_cards) + 1):
        if i == len(sorted_cards) or sorted_cards[i][-1] != sorted_cards[i - 1][-1]:
            size = i - start
            if size == 1:
                groups['Single'].append(sorted_cards[i - 1])
            elif size == 2:
                groups['Pair'].append(sorted_cards[start:i])
            elif size == 3:
                groups['Trips'].append(sorted_cards[start:i])
            else:
                groups['Bomb'].append(sorted_cards[start:i])
                bomb_info[sorted_cards[start][-1]] = size
            start = i
    return groups, bomb_info


def _best_straight_start(rank_counts):
    """Pick the straight window (5 consecutive ranks) wasting fewest cards.

    Args:
        rank_counts (list): 14-slot count list indexed by _STRAIGHT_VALUE
            (slot 0 unused, slot 1 = 'A', ... slot 13 = 'K').

    Returns:
        int or None: start value of the chosen window ('A'=1 ... 'T'=10),
        or None when no straight is available.
    """
    best_start = None
    best_singles = 10
    best_pairs = 10

    def window_stats(counts):
        singles = sum(1 for c in counts if c == 1)
        pairs_ = sum(1 for c in counts if c == 2)
        trips = sum(1 for c in counts if c == 3)
        return singles, pairs_, trips

    candidates = [(start, rank_counts[start:start + 5])
                  for start in range(1, 10)]
    # T-J-Q-K-A wraps around to slot 1 ('A').
    candidates.append((10, rank_counts[10:14] + [rank_counts[1]]))

    for start, counts in candidates:
        if 0 in counts:
            continue
        singles, pairs_, trips = window_stats(counts)
        # Prefer windows made of lone cards (they are dead weight anyway).
        if singles <= pairs_ or singles < pairs_ + trips:
            continue
        if pairs_ < best_pairs or (pairs_ == best_pairs and best_start is None):
            if pairs_ <= best_pairs:
                best_start, best_singles, best_pairs = start, singles, pairs_
        elif pairs_ == best_pairs and trips <= best_singles:
            best_start, best_pairs = start, pairs_
    return best_start


def combine_handcards(handcards, level_rank, card_values):
    """Greedily decompose a hand into combos for tribute-back heuristics.

    The decomposition extracts at most one straight (or straight flush)
    and groups the rest by rank. Wildcards/jokers are never spent on the
    straight. This is a heuristic, not an exhaustive search.

    Args:
        handcards (list): card strings, e.g. ['H3', 'S3', 'HR', ...].
        level_rank (str): rank character of the current level card.
        card_values (dict): level-aware value table (see make_card_values).

    Returns:
        tuple: (groups, bomb_info) where groups maps combo type ->
        list of card groups and bomb_info maps rank char -> bomb size.
    """
    sorted_cards = sorted(handcards, key=lambda card: card_values[card[1]])
    _, bomb_info = _group_by_rank(sorted_cards)

    # Cards usable for a straight: no jokers, no level cards, no bombs.
    loose_cards = [c for c in sorted_cards
                   if c[-1] != level_rank and c[-1] not in ('B', 'R')]
    for bomb in _group_by_rank(sorted_cards)[0]['Bomb']:
        if bomb[0][-1] != level_rank and bomb[0][-1] not in ('B', 'R'):
            for card in bomb:
                loose_cards.remove(card)

    rank_counts = [0] * 14
    for card in loose_cards:
        rank_counts[_STRAIGHT_VALUE[card[-1]]] += 1

    start = _best_straight_start(rank_counts)
    straight_ranks = []
    if start is not None:
        straight_ranks = [_VALUE_STRAIGHT[v if v <= 13 else v % 13]
                          for v in range(start, start + 5)]

    straight_cards = []
    flush_cards = []
    remaining = list(sorted_cards)
    if straight_ranks:
        # Same-suit check runs over the whole hand (a bomb card may
        # complete a straight flush).
        flush_suit = None
        hand_set = set(sorted_cards)
        for suit in SUITS:
            if all(suit + r in hand_set for r in straight_ranks):
                flush_suit = suit
        if flush_suit is not None:
            flush_cards = [flush_suit + r for r in straight_ranks]
            for card in flush_cards:
                # Remove exactly one copy (the original removed all
                # duplicates by membership test).
                remaining.remove(card)
        else:
            for rank in straight_ranks:
                for card in list(remaining):
                    if card[-1] == rank and card[-1] != level_rank \
                            and card[-1] not in ('B', 'R'):
                        straight_cards.append(card)
                        remaining.remove(card)
                        break
            if len(straight_cards) < 5:
                # Could not actually take one card per rank; put them back.
                remaining.extend(straight_cards)
                remaining.sort(key=lambda card: card_values[card[1]])
                straight_cards = []

    groups, _ = _group_by_rank(remaining)
    groups['Straight'] = []
    groups['StraightFlush'] = []
    if len(straight_cards) == 5:
        if straight_cards[-1][-1] == 'A' and straight_cards[-2][-1] == '5':
            # Present A-2-3-4-5 with the ace first.
            straight_cards = [straight_cards[-1]] + straight_cards[:-1]
        groups['Straight'].append(straight_cards)
    if len(flush_cards) == 5:
        groups['StraightFlush'].append(flush_cards)
    return groups, bomb_info


def _remove_n_of_rank(cards, rank_char, n):
    """Remove up to n cards of the given rank from the list, in place."""
    removed = 0
    for card in list(cards):
        if removed >= n:
            break
        if card[-1] == rank_char:
            cards.remove(card)
            removed += 1


def estimate_min_steps(hand_cards, level_rank_index):
    """Greedy estimate of the number of plays needed to empty a hand.

    Used for the oracle shaping value, not for any rule decision.
    The estimate is approximate: combos are extracted greedily
    (joker bomb, big bombs, straight flush, straights, full houses,
    plates/three-pairs, leftovers) and wildcards grant a small discount.

    Args:
        hand_cards (list): card strings.
        level_rank_index (int): index of the level rank in CARD_RANK.

    Returns:
        int: estimated number of plays.
    """
    if not hand_cards:
        return 0

    wildcard = 'H' + CARD_RANK[level_rank_index]
    wildcard_count = hand_cards.count(wildcard)
    remaining = list(hand_cards)
    steps = 0

    counts = Counter(card[-1] for card in remaining)
    if counts.get('R', 0) >= 2 and counts.get('B', 0) >= 2:
        steps += 1
        for _ in range(2):
            remaining.remove(BIG_JOKER)
            remaining.remove(SMALL_JOKER)

    # Big bombs (6-8 cards), one per rank.
    counts = Counter(card[-1] for card in remaining)
    for rank_char, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        if count >= 6:
            steps += 1
            _remove_n_of_rank(remaining, rank_char, min(count, 8))
    if not remaining:
        return steps

    # One straight flush, if any suit has 5 consecutive ranks.
    by_suit = defaultdict(list)
    for card in remaining:
        by_suit[card[0]].append(card)
    for suit in SUITS:
        suit_ranks = sorted({CARD_RANK_INDEX[c[-1]] for c in by_suit[suit]
                             if c[-1] in CARD_RANK_INDEX and c[-1] not in ('B', 'R')})
        found = False
        for i in range(len(suit_ranks) - 4):
            if suit_ranks[i + 4] - suit_ranks[i] == 4:
                steps += 1
                for j in range(5):
                    rank_char = CARD_RANK[suit_ranks[i + j]]
                    for card in list(remaining):
                        if card[0] == suit and card[-1] == rank_char:
                            remaining.remove(card)
                            break
                found = True
                break
        if found:
            break

    # 5-card and 4-card bombs.
    for size in (5, 4):
        counts = Counter(card[-1] for card in remaining)
        for rank_char, count in sorted(counts.items(),
                                       key=lambda x: (-x[1], x[0])):
            if count >= size:
                steps += 1
                _remove_n_of_rank(remaining, rank_char, size)
        if not remaining:
            return steps

    # One straight across suits.
    counts = Counter(card[-1] for card in remaining)
    rank_indices = sorted(CARD_RANK_INDEX[r] for r in counts
                          if r not in ('B', 'R'))
    for i in range(len(rank_indices) - 4):
        if rank_indices[i + 4] - rank_indices[i] == 4:
            steps += 1
            for j in range(5):
                _remove_n_of_rank(remaining, CARD_RANK[rank_indices[i + j]], 1)
            break

    # Full houses (three with two).
    counts = Counter(card[-1] for card in remaining)
    triples = [r for r, c in counts.items() if c == 3]
    pairs = [r for r, c in counts.items() if c == 2]
    for triple in list(triples):
        if not pairs:
            break
        steps += 1
        pair = pairs.pop(0)
        _remove_n_of_rank(remaining, triple, 3)
        _remove_n_of_rank(remaining, pair, 2)
        triples.remove(triple)

    # One plate (two consecutive triples).
    triples.sort(key=lambda r: CARD_RANK_INDEX.get(r, 100))
    for i in range(len(triples) - 1):
        if CARD_RANK_INDEX[triples[i + 1]] - CARD_RANK_INDEX[triples[i]] == 1:
            steps += 1
            _remove_n_of_rank(remaining, triples[i], 3)
            _remove_n_of_rank(remaining, triples[i + 1], 3)
            break

    # One three-pair (three consecutive pairs).
    pairs.sort(key=lambda r: CARD_RANK_INDEX.get(r, 100))
    for i in range(len(pairs) - 2):
        if (CARD_RANK_INDEX[pairs[i + 1]] - CARD_RANK_INDEX[pairs[i]] == 1
                and CARD_RANK_INDEX[pairs[i + 2]] - CARD_RANK_INDEX[pairs[i + 1]] == 1):
            steps += 1
            for pair in pairs[i:i + 3]:
                _remove_n_of_rank(remaining, pair, 2)
            break

    if not remaining:
        return steps

    # Leftovers: each remaining rank group is one play.
    counts = Counter(card[-1] for card in remaining)
    steps += sum(1 for _ in counts)

    # Wildcards glue groups together; grant a small discount.
    if wildcard_count > 0:
        saved = min(wildcard_count, max(1, steps // 3))
        steps = max(1, steps - saved)
    return steps
