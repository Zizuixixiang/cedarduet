"""Minimal three-card evaluator adapted from Golden Flower.

The integration intentionally accepts plain ``{"rank", "suit"}`` mappings and
returns a small immutable value. Suits classify flushes but never break ties.
See NOTICE.md and LICENSE in this directory for provenance and licensing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
SUITS = ("spades", "hearts", "diamonds", "clubs")
RANK_VALUE = {rank: value for value, rank in enumerate(RANKS, start=2)}
HAND_TYPE_STRENGTH = {
    "high_card": 0,
    "pair": 1,
    "straight": 2,
    "flush": 3,
    "straight_flush": 4,
    "three_of_a_kind": 5,
}
HAND_TYPE_LABELS = {
    "high_card": "散牌",
    "pair": "对子",
    "straight": "顺子",
    "flush": "金花",
    "straight_flush": "同花顺",
    "three_of_a_kind": "豹子",
}


@dataclass(frozen=True)
class HandValue:
    """Comparable classification with suit-independent tie breakers."""

    hand_type: str
    ranks: tuple[int, ...]

    @property
    def label(self) -> str:
        return HAND_TYPE_LABELS[self.hand_type]

    @property
    def comparison_key(self) -> tuple[int, ...]:
        return (HAND_TYPE_STRENGTH[self.hand_type], *self.ranks)


def _normalized_cards(cards: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
    selected = list(cards)
    if len(selected) != 3:
        raise ValueError("three-card evaluator requires exactly 3 cards")
    normalized: list[tuple[str, str]] = []
    for card in selected:
        if not isinstance(card, Mapping):
            raise ValueError("every card must be a mapping")
        rank = card.get("rank")
        suit = card.get("suit")
        if rank not in RANK_VALUE or suit not in SUITS:
            raise ValueError("card contains an unknown rank or suit")
        normalized.append((str(rank), str(suit)))
    if len(set(normalized)) != 3:
        raise ValueError("a hand cannot contain duplicate physical cards")
    return normalized


def evaluate_hand(cards: Iterable[Mapping[str, Any]]) -> HandValue:
    """Classify the fixed Zha Jin Hua variant used by CedarDuet.

    A-2-3 is a straight whose high value is 3, while Q-K-A is the largest
    straight. The optional 2-3-5-over-trips house rule is deliberately absent.
    """

    selected = _normalized_cards(cards)
    values = [RANK_VALUE[rank] for rank, _suit in selected]
    suits = [suit for _rank, suit in selected]
    counts = Counter(values)
    descending = tuple(sorted(values, reverse=True))
    is_flush = len(set(suits)) == 1

    unique = sorted(set(values))
    if unique == [2, 3, 14]:
        straight_high = 3
    elif len(unique) == 3 and unique[-1] - unique[0] == 2:
        straight_high = unique[-1]
    else:
        straight_high = None

    if len(counts) == 1:
        return HandValue("three_of_a_kind", (values[0],))
    if is_flush and straight_high is not None:
        return HandValue("straight_flush", (straight_high,))
    if is_flush:
        return HandValue("flush", descending)
    if straight_high is not None:
        return HandValue("straight", (straight_high,))
    if 2 in counts.values():
        pair_rank = next(rank for rank, count in counts.items() if count == 2)
        kicker = next(rank for rank, count in counts.items() if count == 1)
        return HandValue("pair", (pair_rank, kicker))
    return HandValue("high_card", descending)


def compare_hands(
    left: HandValue | Iterable[Mapping[str, Any]],
    right: HandValue | Iterable[Mapping[str, Any]],
) -> int:
    """Return 1, 0, or -1; physical suits never break an exact rank tie."""

    left_value = left if isinstance(left, HandValue) else evaluate_hand(left)
    right_value = right if isinstance(right, HandValue) else evaluate_hand(right)
    return (
        (left_value.comparison_key > right_value.comparison_key)
        - (left_value.comparison_key < right_value.comparison_key)
    )
