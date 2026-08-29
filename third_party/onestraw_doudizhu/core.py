"""Rank-only Dou Dizhu rule core adapted from onestraw/doudizhu 0.1.5.

The upstream engine documents 37 fine-grained types and a 34,152-entry
rank-only dictionary.  This module preserves that exact combinatorial model
while replacing its string keys and mutable globals with count vectors and
immutable typed values.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Iterator, Mapping


RANK_LABELS = (
    "3", "4", "5", "6", "7", "8", "9", "10",
    "J", "Q", "K", "A", "2", "small_joker", "big_joker",
)
RANK_INDEX = {rank: index for index, rank in enumerate(RANK_LABELS)}
NORMAL_RANK_COUNT = 13
CHAIN_RANK_COUNT = 12
RANK_CAPACITIES = (4,) * NORMAL_RANK_COUNT + (1, 1)

FAMILY_LABELS = {
    "solo": "单张",
    "pair": "对子",
    "trio": "三张",
    "trio_solo": "三带一",
    "trio_pair": "三带一对",
    "solo_chain": "顺子",
    "pair_chain": "连对",
    "trio_chain": "飞机（不带）",
    "trio_chain_solo": "飞机带单",
    "trio_chain_pair": "飞机带对",
    "four_two_solo": "四带二",
    "four_two_pair": "四带两对",
    "bomb": "炸弹",
    "rocket": "王炸",
}


@dataclass(frozen=True, slots=True)
class CardPattern:
    """One interpretation of a rank multiset.

    A physical combination can have several interpretations.  For example,
    33332222 is a four-with-two-pairs headed by either 3 or 2.  ``type_code``
    plus ``main_value`` therefore travels with every authoritative action.
    """

    type_code: str
    family: str
    main_value: int
    card_count: int
    chain_length: int = 1
    is_bomb: bool = False

    @property
    def label(self) -> str:
        return FAMILY_LABELS[self.family]

    @property
    def main_rank(self) -> str:
        return RANK_LABELS[self.main_value]

    def public(self) -> dict[str, object]:
        return {
            "type": self.type_code,
            "family": self.family,
            "label": self.label,
            "main_rank": self.main_rank,
            "main_value": self.main_value,
            "count": self.card_count,
            "chain_length": self.chain_length,
            "is_bomb": self.is_bomb,
        }


@dataclass(frozen=True, slots=True)
class RankPlay:
    ranks: tuple[int, ...]
    pattern: CardPattern


def _pattern(
    family: str,
    main_value: int,
    card_count: int,
    *,
    chain_length: int = 1,
    is_bomb: bool = False,
) -> CardPattern:
    type_code = (
        f"{family}_{chain_length}"
        if family in {
            "solo_chain", "pair_chain", "trio_chain",
            "trio_chain_solo", "trio_chain_pair",
        }
        else family
    )
    return CardPattern(
        type_code=type_code,
        family=family,
        main_value=main_value,
        card_count=card_count,
        chain_length=chain_length,
        is_bomb=is_bomb,
    )


def _with_counts(*groups: tuple[int, int]) -> tuple[int, ...]:
    counts = [0] * len(RANK_LABELS)
    for rank, count in groups:
        counts[rank] += count
    return tuple(counts)


def _wing_allocations(
    capacities: tuple[int, ...], total_units: int
) -> Iterator[tuple[int, ...]]:
    """Yield stable bounded compositions without materializing a cartesian product."""

    allocation = [0] * len(capacities)

    def visit(index: int, remaining: int) -> Iterator[tuple[int, ...]]:
        if index == len(capacities):
            if remaining == 0:
                yield tuple(allocation)
            return
        for value in range(min(capacities[index], remaining) + 1):
            allocation[index] = value
            yield from visit(index + 1, remaining - value)
        allocation[index] = 0

    yield from visit(0, total_units)


def _add(
    index: dict[tuple[int, ...], list[CardPattern]],
    counts: tuple[int, ...],
    pattern: CardPattern,
) -> None:
    index.setdefault(counts, []).append(pattern)


@lru_cache(maxsize=1)
def _pattern_index() -> Mapping[tuple[int, ...], tuple[CardPattern, ...]]:
    """Build the upstream-compatible 34,152-entry pattern dictionary once."""

    mutable: dict[tuple[int, ...], list[CardPattern]] = {}

    for rank in range(len(RANK_LABELS)):
        _add(mutable, _with_counts((rank, 1)), _pattern("solo", rank, 1))
    for rank in range(NORMAL_RANK_COUNT):
        _add(mutable, _with_counts((rank, 2)), _pattern("pair", rank, 2))
        _add(mutable, _with_counts((rank, 3)), _pattern("trio", rank, 3))
        _add(
            mutable,
            _with_counts((rank, 4)),
            _pattern("bomb", rank, 4, is_bomb=True),
        )
    _add(
        mutable,
        _with_counts((13, 1), (14, 1)),
        _pattern("rocket", 14, 2, is_bomb=True),
    )

    for trio_rank in range(NORMAL_RANK_COUNT):
        for wing_rank in range(len(RANK_LABELS)):
            if wing_rank == trio_rank:
                continue
            _add(
                mutable,
                _with_counts((trio_rank, 3), (wing_rank, 1)),
                _pattern("trio_solo", trio_rank, 4),
            )
        for pair_rank in range(NORMAL_RANK_COUNT):
            if pair_rank == trio_rank:
                continue
            _add(
                mutable,
                _with_counts((trio_rank, 3), (pair_rank, 2)),
                _pattern("trio_pair", trio_rank, 5),
            )

    for length in range(5, CHAIN_RANK_COUNT + 1):
        for start in range(CHAIN_RANK_COUNT - length + 1):
            counts = tuple(
                1 if start <= rank < start + length else 0
                for rank in range(len(RANK_LABELS))
            )
            _add(
                mutable,
                counts,
                _pattern(
                    "solo_chain", start + length - 1, length,
                    chain_length=length,
                ),
            )
    for length in range(3, 11):
        for start in range(CHAIN_RANK_COUNT - length + 1):
            counts = tuple(
                2 if start <= rank < start + length else 0
                for rank in range(len(RANK_LABELS))
            )
            _add(
                mutable,
                counts,
                _pattern(
                    "pair_chain", start + length - 1, length * 2,
                    chain_length=length,
                ),
            )
    for length in range(2, 7):
        for start in range(CHAIN_RANK_COUNT - length + 1):
            counts = tuple(
                3 if start <= rank < start + length else 0
                for rank in range(len(RANK_LABELS))
            )
            _add(
                mutable,
                counts,
                _pattern(
                    "trio_chain", start + length - 1, length * 3,
                    chain_length=length,
                ),
            )

    for wing_unit, family, lengths in (
        (1, "trio_chain_solo", range(2, 6)),
        (2, "trio_chain_pair", range(2, 5)),
    ):
        for length in lengths:
            for start in range(CHAIN_RANK_COUNT - length + 1):
                core = set(range(start, start + length))
                capacities = tuple(
                    0
                    if rank in core or (wing_unit == 2 and rank >= NORMAL_RANK_COUNT)
                    else (RANK_CAPACITIES[rank] // wing_unit)
                    for rank in range(len(RANK_LABELS))
                )
                for wings in _wing_allocations(capacities, length):
                    counts = tuple(
                        (3 if rank in core else 0) + wings[rank] * wing_unit
                        for rank in range(len(RANK_LABELS))
                    )
                    _add(
                        mutable,
                        counts,
                        _pattern(
                            family,
                            start + length - 1,
                            length * (3 + wing_unit),
                            chain_length=length,
                        ),
                    )

    for four_rank in range(NORMAL_RANK_COUNT):
        solo_capacities = tuple(
            0 if rank == four_rank else RANK_CAPACITIES[rank]
            for rank in range(len(RANK_LABELS))
        )
        for wings in _wing_allocations(solo_capacities, 2):
            counts = tuple(
                (4 if rank == four_rank else 0) + wings[rank]
                for rank in range(len(RANK_LABELS))
            )
            _add(
                mutable,
                counts,
                _pattern("four_two_solo", four_rank, 6),
            )

        pair_capacities = tuple(
            0
            if rank == four_rank or rank >= NORMAL_RANK_COUNT
            else RANK_CAPACITIES[rank] // 2
            for rank in range(len(RANK_LABELS))
        )
        for wing_pairs in _wing_allocations(pair_capacities, 2):
            counts = tuple(
                (4 if rank == four_rank else 0) + wing_pairs[rank] * 2
                for rank in range(len(RANK_LABELS))
            )
            _add(
                mutable,
                counts,
                _pattern("four_two_pair", four_rank, 8),
            )

    frozen = {counts: tuple(patterns) for counts, patterns in mutable.items()}
    entry_count = sum(len(patterns) for patterns in frozen.values())
    if entry_count != 34152:
        raise RuntimeError(
            f"onestraw/doudizhu pattern universe drifted: {entry_count} != 34152"
        )
    return frozen


PATTERN_ENTRY_COUNT = sum(len(patterns) for patterns in _pattern_index().values())


def _rank_value(rank: int | str) -> int:
    if isinstance(rank, bool):
        raise ValueError("rank cannot be boolean")
    if isinstance(rank, int):
        if 0 <= rank < len(RANK_LABELS):
            return rank
        raise ValueError(f"rank integer out of range: {rank}")
    try:
        return RANK_INDEX[rank]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"unknown rank: {rank}") from exc


def _counts_for_ranks(ranks: Iterable[int | str]) -> tuple[int, ...]:
    counts = [0] * len(RANK_LABELS)
    for rank in ranks:
        value = _rank_value(rank)
        counts[value] += 1
        if counts[value] > RANK_CAPACITIES[value]:
            raise ValueError(f"too many cards of rank {RANK_LABELS[value]}")
    return tuple(counts)


def classify_ranks(ranks: Iterable[int | str]) -> tuple[CardPattern, ...]:
    """Return every upstream-compatible interpretation of one combination."""

    counts = _counts_for_ranks(ranks)
    return _pattern_index().get(counts, ())


def pattern_from_public(value: Mapping[str, object]) -> CardPattern:
    """Rehydrate a pattern stored in Cedar Duet's public/persistent state."""

    type_code = str(value["type"])
    family = str(value["family"])
    main_value = int(value["main_value"])
    card_count = int(value["count"])
    chain_length = int(value.get("chain_length", 1))
    is_bomb = bool(value.get("is_bomb", False))
    candidate = CardPattern(
        type_code,
        family,
        main_value,
        card_count,
        chain_length,
        is_bomb,
    )
    if candidate.label != value.get("label") or candidate.main_rank != value.get("main_rank"):
        raise ValueError("stored card pattern metadata is inconsistent")
    return candidate


def can_beat(candidate: CardPattern, target: CardPattern) -> bool:
    """Compare two selected interpretations using classic Dou Dizhu ordering."""

    if target.family == "rocket":
        return False
    if candidate.family == "rocket":
        return True
    if candidate.family == "bomb":
        return target.family != "bomb" or candidate.main_value > target.main_value
    if target.family == "bomb":
        return False
    return (
        candidate.type_code == target.type_code
        and candidate.main_value > target.main_value
    )


def legal_rank_plays(
    hand_ranks: Iterable[int | str],
    target: CardPattern | None = None,
) -> tuple[RankPlay, ...]:
    """List authoritative rank combinations contained in ``hand_ranks``.

    Suit choice is deliberately outside the third-party core.  The host maps
    each rank multiset to stable physical card IDs before publishing actions.
    """

    hand_counts = _counts_for_ranks(hand_ranks)
    plays: list[RankPlay] = []
    for counts, patterns in _pattern_index().items():
        if any(counts[index] > hand_counts[index] for index in range(len(counts))):
            continue
        ranks = tuple(
            rank
            for rank, count in enumerate(counts)
            for _copy in range(count)
        )
        for pattern in patterns:
            if target is None or can_beat(pattern, target):
                plays.append(RankPlay(ranks, pattern))
    plays.sort(key=lambda play: (
        2 if play.pattern.family == "rocket" else 1 if play.pattern.family == "bomb" else 0,
        play.pattern.card_count,
        play.pattern.main_value,
        play.pattern.type_code,
        play.ranks,
    ))
    return tuple(plays)
