"""Zhongxiao interview's exact-cover shape and own-hand listening/NPC helpers.

https://kinmen-story.tw/article/1025 (2021-02-01). Five triples and any
singleton, with at least one eye. Eye: own-color 012/345 or three distinct
soldier colors. Other triples: same rank, three colors; generals may repeat
colors. Four-color soldier groups are deliberately NOT imported from TenHu
or the newspaper's different wording. Turn/scoring conventions are explicit in qunhu.py and rules.py.
"""

from collections import Counter
from functools import lru_cache
from itertools import combinations_with_replacement, combinations
from .cards import CODES, CODE_INDEX, COLORS, code, validate_cards

RULES_VERSION = "kinmen-zhongxiao-interview-20210201"
DEALER_COUNT, OTHER_COUNT, WIN_COUNT = 16, 15, 16
_patterns = []
for color in COLORS:
    for ranks in ((0, 1, 2), (3, 4, 5)):
        _patterns.append((tuple(f"{color}{rank}" for rank in ranks), True))
for rank in range(7):
    colors = (
        combinations_with_replacement(COLORS, 3)
        if rank == 0
        else combinations(COLORS, 3)
    )
    for group in colors:
        _patterns.append((tuple(f"{c}{rank}" for c in group), rank == 6))
PATTERNS = tuple(_patterns)


# Indexed patterns avoid rebuilding Counters inside the exact-cover search.
_COUNTS = tuple(
    tuple((CODE_INDEX[c], n) for c, n in Counter(p).items()) for p, _ in PATTERNS
)
_BY_FIRST = tuple(
    tuple(i for i, w in enumerate(_COUNTS) if any(j == first for j, _ in w))
    for first in range(28)
)


def solver():
    # A request-local cache can be shared by all candidate discards/waits.
    @lru_cache(maxsize=None)
    def solve(left, single_used=False, eye=False):
        first = next((i for i, n in enumerate(left) if n), None)
        if first is None:
            return () if single_used and eye else None
        for index in _BY_FIRST[first]:
            wanted = _COUNTS[index]
            if any(left[i] < n for i, n in wanted):
                continue
            rest = list(left)
            for i, n in wanted:
                rest[i] -= n
            tail = solve(tuple(rest), single_used, eye or PATTERNS[index][1])
            if tail is not None:
                return (index,) + tail
        if not single_used:
            rest = list(left)
            rest[first] -= 1
            tail = solve(tuple(rest), True, eye)
            if tail is not None:
                return (-(first + 1),) + tail
        return None

    return solve


def counts_for(cards):
    counts = Counter(map(code, cards))
    return tuple(counts[c] for c in CODES)


def winning_partition(cards):
    validate_cards(cards)
    if len(cards) != 16:
        return None
    answer = solver()(counts_for(cards))
    if answer is None:
        return None
    pool = {c: sorted(t for t in cards if code(t) == c) for c in CODES}
    result = {"groups": [], "singleton": None}
    for index in answer:
        if index < 0:
            result["singleton"] = pool[CODES[-index - 1]].pop()
        else:
            pattern, eye = PATTERNS[index]
            result["groups"].append(
                {"cards": [pool[c].pop() for c in pattern], "eye": eye}
            )
    return result


def waiting_codes(cards, solve=None):
    if len(cards) != 15:
        return []
    counts = counts_for(cards)
    if not any(
        eye and all(counts[i] >= n for i, n in wanted)
        for (_, eye), wanted in zip(PATTERNS, _COUNTS)
    ):
        return []  # Declaring listen already requires an eye, not a prospective one.
    solve = solve or solver()
    result = []
    for i, c in enumerate(CODES):
        if counts[i] == 4:
            continue
        more = list(counts)
        more[i] += 1
        if solve(tuple(more)) is not None:
            result.append(c)
    return result


def exhibit(cards):
    generals = sorted(t for t in cards if code(t)[1] == "0")
    if generals:
        return generals[:1]
    for color in COLORS:
        field = [
            next((t for t in sorted(cards) if code(t) == f"{color}{r}"), None)
            for r in (3, 4, 5)
        ]
        if all(field):
            return field
    return sorted(t for t in cards if code(t)[1] == "6")[:1]


def quality_evaluator():
    # Own-hand heuristic: maximize disjoint triples/potential pairs. No wall or
    # opponents enter this function; it is not a winning/scoring evaluator.
    patterns = [(w, 100 + 5 * eye) for w, (_, eye) in zip(_COUNTS, PATTERNS)]
    pairs = {
        tuple(sorted(Counter(pair).items()))
        for p, _ in PATTERNS
        for pair in combinations(p, 2)
    }
    patterns += [(tuple((CODE_INDEX[c], n) for c, n in pair), 10) for pair in pairs]
    by_first = [
        [p for p in patterns if any(i == j for j, _ in p[0])] for i in range(28)
    ]

    @lru_cache(maxsize=None)
    def quality(left):
        i = next((i for i, n in enumerate(left) if n), None)
        if i is None:
            return 0
        rest = list(left)
        rest[i] -= 1
        best = quality(tuple(rest))
        for wanted, points in by_first[i]:
            if any(left[j] < n for j, n in wanted):
                continue
            rest = list(left)
            for j, n in wanted:
                rest[j] -= n
            best = max(best, points + quality(tuple(rest)))
        return best

    return lambda cards: quality(counts_for(cards))
