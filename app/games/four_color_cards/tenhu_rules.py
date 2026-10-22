"""BGA Chinese help revision 1851: combinations, exact covers and priority.

This module is intentionally independent of nine-pairs and Kinmen Qunhu.
No downloaded rules code or variant-independent 'mahjong' evaluator is used.
"""

from collections import Counter
from functools import lru_cache
from itertools import combinations

from .cards import CODES, CODE_INDEX, COLORS, code, validate_cards

RULES_VERSION = "bga-zh-1851-tenhu-platform1"

# Kind, code multiset, concealed hu, exposed hu. In particular, general kongs
# have the opposite concealed/exposed values to ordinary kongs in THIS version.
PATTERNS = []
for value in CODES:
    rank = int(value[1])
    if rank == 0:
        PATTERNS.append(("general", (value,), 1, 1))
    if rank not in (0, 6):
        PATTERNS.append(("pair", (value,) * 2, 0, 0))
    if rank != 0:
        PATTERNS.append(("pong", (value,) * 3, 3, 1))
    PATTERNS.append(("kong", (value,) * 4, 6 if rank == 0 else 8,
                     8 if rank == 0 else 6))
for color in COLORS:
    PATTERNS.append(("command", tuple(f"{color}{r}" for r in (0, 1, 2)), 2, 2))
    PATTERNS.append(("field", tuple(f"{color}{r}" for r in (3, 4, 5)), 1, 1))
for size, points in ((3, 3), (4, 5)):
    for colors in combinations(COLORS, size):
        PATTERNS.append(("soldiers", tuple(f"{c}6" for c in colors), points, points))
PATTERNS = tuple(PATTERNS)
PATTERN_COUNTS = tuple(Counter(p[1]) for p in PATTERNS)


def meld_info(cards, *, exposed=False):
    validate_cards(cards)
    counts = Counter(map(code, cards))
    for pattern, wanted in zip(PATTERNS, PATTERN_COUNTS):
        if counts == wanted:
            return {"kind": pattern[0], "hu": pattern[3 if exposed else 2],
                    "cards": list(cards), "exposed": exposed}
    raise ValueError("不是 BGA 台湾十胡的合法组合")


def best_partition(cards):
    """Return a maximum concealed-hu exact cover, or None (empty cover is valid).

    Cache lives within the request; it cannot retain private hands globally.
    Every physical card occurs once. A greedy partition is not sufficient:
    pairs, triples, quads and sequences overlap.
    """
    validate_cards(cards)
    counts = Counter(map(code, cards))
    initial = tuple(counts[c] for c in CODES)

    @lru_cache(maxsize=None)
    def solve(remaining):
        first = next((i for i, count in enumerate(remaining) if count), None)
        if first is None:
            return 0, ()
        best = None
        for index, (pattern, wanted) in enumerate(zip(PATTERNS, PATTERN_COUNTS)):
            if CODES[first] not in wanted:
                continue
            if any(remaining[CODE_INDEX[c]] < n for c, n in wanted.items()):
                continue
            rest = list(remaining)
            for c, n in wanted.items():
                rest[CODE_INDEX[c]] -= n
            tail = solve(tuple(rest))
            if tail is not None:
                candidate = pattern[2] + tail[0], (index,) + tail[1]
                if best is None or candidate[0] > best[0]:
                    best = candidate
        return best

    result = solve(initial)
    if result is None:
        return None
    available = {c: sorted(t for t in cards if code(t) == c) for c in counts}
    groups = []
    for index in result[1]:
        group = [available[c].pop(0) for c in PATTERNS[index][1]]
        groups.append(meld_info(group))
    return {"hu": result[0], "groups": groups}


def claims(hand, incoming):
    """Enumerate distinct physical meld choices containing the offered card."""
    validate_cards(hand)
    incoming_code = code(incoming)
    if incoming in hand:
        raise ValueError("待抢牌已经在手中")
    available = {}
    for card in hand:
        available.setdefault(code(card), []).append(card)
    result = []
    for pattern, wanted in zip(PATTERNS, PATTERN_COUNTS):
        if incoming_code not in wanted:
            continue
        needed = wanted.copy()
        needed[incoming_code] -= 1
        if any(len(available.get(c, [])) < n for c, n in needed.items()):
            continue
        # Copies are mechanically interchangeable; one stable representative
        # per code multiset keeps legal actions small and avoids permutations.
        held = sorted(t for c, n in needed.items() for t in sorted(available.get(c, []))[:n])
        group = meld_info(held + [incoming], exposed=True)
        result.append({"held": held, "meld": group})
    return result


def response_windows(order, source, from_deck):
    """Hu > kong/pong > chi; source has first priority only for a draw."""
    if len(set(order)) != len(order) or source not in order or not 2 <= len(order) <= 4:
        raise ValueError("无效响应座次")
    start = order.index(source)
    sequence = [order[(start + d) % len(order)] for d in range(len(order))]
    responders = sequence if from_deck else sequence[1:]
    eaters = sequence[:2] if from_deck else sequence[1:2]
    return [(pid, tier) for tier in ("hu", "pong") for pid in responders] + [
        (pid, "chi") for pid in eaters]


def score_hand(concealed, exposed_groups, flower):
    """Score an already completed hand, before any player-to-player transfer.

    Caller supplies the actual final claimed (exposed) meld separately. Flower
    does not rescue an under-ten hand (Chinese help's explicit tips section).
    """
    validate_cards(concealed)
    all_cards = list(concealed) + [t for group in exposed_groups for t in group]
    validate_cards(all_cards)
    partition = best_partition(concealed)
    if partition is None:
        raise ValueError("仍有无法组成组合的杂牌")
    groups = [meld_info(group, exposed=True) for group in exposed_groups]
    base = partition["hu"] + sum(group["hu"] for group in groups)
    flower_code = code(flower) if flower is not None else None
    # Explicit PLATFORM convention for the help's underspecified empty-wall
    # fallback: an already claimed first discard remains in its meld, but the
    # reference physical card does not match itself. Never duplicate/move it.
    # This is documented separately from the sourced BGA scoring table.
    bonus = sum(t != flower and code(t) == flower_code for t in all_cards) if flower_code else 0
    return {"base_hu": base, "flower_hu": bonus, "qualified": base >= 10,
            "per_opponent": base - 10 + bonus + 1 if base >= 10 else -11,
            "concealed_groups": partition["groups"], "exposed_groups": groups}
