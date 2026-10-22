"""Taiwanese five-pair exact-cover goal, independent of turn/scoring in jiuzhi.py.

Source: 怪e紅傑克, 2010-08-14. Ten pairs / twenty cards is not this variant.
"""
from collections import defaultdict
from .cards import code, validate_cards

RULES_VERSION = 'taiwan-jiuzhi-five-pairs-20100814'
DEALER_COUNT, OTHER_COUNT, WIN_COUNT = 10, 9, 10


def winning_pairs(cards):
    """Five same-color/same-rank pairs, each physical ID exactly once."""
    validate_cards(cards)
    if len(cards) != WIN_COUNT:
        return None
    by_code = defaultdict(list)
    for card in sorted(cards):
        by_code[code(card)].append(card)
    if any(len(group) % 2 for group in by_code.values()):
        return None
    return [group[i:i+2] for group in by_code.values() for i in range(0, len(group), 2)]
