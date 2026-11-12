"""Pure red-dragon wildcard decomposition for the optional 84-tile KWX rule.

Only physical J1 tiles are wild. A discarded J1 remains a natural J1 when
``win_is_wild=False``. Natural red triples/pairs count for dragons, but red
tiles substituting for green/white dragons do not. Any physical red in a hand
disables dragon-seven-pairs and four-return-one; this is a deliberate room
variant, not a claim about every regional rule. No mutable game state is read.
"""
from collections import Counter
from functools import lru_cache
from itertools import product

CODES = tuple(f'{s}{n}' for s, ranks in [('B', 9), ('T', 9), ('J', 3)]
              for n in range(1, ranks + 1))
_INDEX = {code: i for i, code in enumerate(CODES)}
_RED = _INDEX['J1']


def _suit(index):
    return 1 if index < 9 else 2 if index < 18 else 4


def _dragon(index):
    return 1 << (index - 18) if index >= 18 else 0


@lru_cache(maxsize=65536)
def _groups(counts, wild, gap_index):
    """Return (all_triplets, suit_mask, natural_dragons, natural_gap) sets.

Every recursive group consumes the first remaining natural tile. Groups made
only from wild tiles can be moved to the end, so assignments need not be
enumerated over 21**wild possibilities. Equivalent scoring features coalesce.
    """
    if not any(counts):
        if wild == 0:
            return ((True, 0, 0, False),)
        if wild != 3:
            return ()
        # All-wild sequences cannot improve a scoring feature over triples.
        # A triple of actual reds used as reds is a natural red triple.
        return ((True, 1, 0, False), (True, 2, 0, False),
                (True, 4, _dragon(_RED), False))
    if (sum(counts) + wild) % 3:
        return ()
    first = next(i for i, n in enumerate(counts) if n)
    choices = []
    for natural in range(1, min(3, counts[first]) + 1):
        missing = 3 - natural
        if missing <= wild:
            rest = list(counts)
            rest[first] -= natural
            dragon = _dragon(first) if natural == 3 or first == _RED else 0
            choices.append((tuple(rest), wild - missing, True,
                            _suit(first), dragon, False))
    if first < 18:
        suit_start = first // 9 * 9
        for start in range(max(suit_start, first - 2),
                           min(first, suit_start + 6) + 1):
            indices = (start, start + 1, start + 2)
            others = [i for i in indices if i != first]
            for use in product(*[(0, 1) if counts[i] else (0,) for i in others]):
                missing = 2 - sum(use)
                if missing > wild:
                    continue
                rest = list(counts)
                rest[first] -= 1
                for i, take in zip(others, use):
                    rest[i] -= take
                gap = missing == 0 and gap_index == start + 1
                choices.append((tuple(rest), wild - missing, False,
                                _suit(first), 0, gap))
    out = set()
    for rest, left, triple, suit, dragon, gap in choices:
        for all_triples, suits, dragons, has_gap in _groups(rest, left, gap_index):
            out.add((triple and all_triples, suit | suits,
                     dragon | dragons, gap or has_gap))
    return tuple(sorted(out))


def _prepare(codes, melds, meld_count, win_code, win_is_wild):
    codes = tuple(codes)
    if (not isinstance(meld_count, int) or not 0 <= meld_count <= 4
            or len(codes) != 14 - 3 * meld_count
            or any(code not in _INDEX for code in codes)):
        return None
    if win_code is not None and win_code not in codes:
        return None
    physical = Counter(codes)
    for meld in melds:
        tiles = meld.get('tiles', ())
        if len(tiles) not in (3, 4):
            return None
        meld_codes = [t.get('code') for t in tiles]
        if any(code not in _INDEX for code in meld_codes):
            return None
        physical.update(meld_codes)
    if max(physical.values(), default=0) > 4:
        return None
    counts = Counter(codes)
    wild = counts.pop('J1', 0)
    if win_code == 'J1' and not win_is_wild:
        wild -= 1
        counts['J1'] = 1
    return tuple(counts[code] for code in CODES), wild, physical


@lru_cache(maxsize=32768)
def _structures(counts, wild, gap_index):
    out = set()
    for pair in range(len(CODES)):
        for natural in range(min(2, counts[pair]) + 1):
            missing = 2 - natural
            if missing > wild:
                continue
            rest = list(counts)
            rest[pair] -= natural
            pair_dragon = _dragon(pair) if natural == 2 or pair == _RED else 0
            for triples, suits, dragons, gap in _groups(tuple(rest), wild - missing, gap_index):
                out.add((triples, suits | _suit(pair), dragons, pair_dragon, gap))
    return tuple(sorted(out))


def _seven_pairs(counts, wild):
    odd = sum(n % 2 for n in counts)
    return sum(counts) + wild == 14 and odd <= wild and (wild - odd) % 2 == 0


def winning(codes, meld_count=0, *, melds=None, win_code=None, win_is_wild=True):
    """Test shape legality; pass actual ``melds`` to enforce total physical counts.

With ``melds`` supplied its length determines meld_count. By default every J1
is wild (the self-draw interpretation used by fixed revealed listening sets).
For an external red winning tile pass win_code='J1', win_is_wild=False.
    """
    if melds is not None:
        meld_count = len(melds)
    prepared = _prepare(codes, melds or (), meld_count, win_code, win_is_wild)
    if prepared is None:
        return False
    counts, wild, _ = prepared
    return bool((meld_count == 0 and _seven_pairs(counts, wild))
                or _structures(counts, wild, -1))


def waits(codes, melds=(), *, win_is_wild=True):
    """Return physically possible winning tile codes, without consulting the wall.

Default includes self-drawn wild J1. ``win_is_wild=False`` returns the shape
set for external tiles; fan minimum / passed-hu restrictions belong to engine.
    """
    codes = tuple(codes)
    if len(codes) != 13 - 3 * len(melds):
        return []
    return [code for code in CODES
            if winning((*codes, code), melds=melds, win_code=code,
                       win_is_wild=win_is_wild)]


def evaluations(codes, melds=(), win_code=None, *, win_is_wild=True):
    """Return candidate ``{'fans': [(name, multiplier)], 'cap': int}`` mappings.

Candidates contain shape fans only. Event fans, minimum discard multiplier,
revealed doubling and choosing the final capped maximum remain engine duties.
Natural 456 is required for 卡五星, with the actual winning B5/T5 in that group.
    """
    codes = tuple(codes)
    prepared = _prepare(codes, melds, len(melds), win_code, win_is_wild)
    if prepared is None:
        return []
    counts, wild, physical = prepared
    gap_index = _INDEX[win_code] if win_code in ('B5', 'T5') else -1
    meld_suits = 0
    meld_dragons = 0
    meld_triplets = True
    for meld in melds:
        values = [t['code'] for t in meld['tiles']]
        for code in values:
            meld_suits |= _suit(_INDEX[code])
        if len(set(values)) == 1:
            meld_dragons |= _dragon(_INDEX[values[0]])
        else:
            meld_triplets = False
    candidates = set()
    if not melds and _seven_pairs(counts, wild):
        suits = {code[0] for code, n in zip(CODES, counts) if n}
        fans = [('清一色', 4)] if len(suits) == 1 and suits <= {'B', 'T'} else []
        quads = 0 if physical['J1'] else sum(n == 4 for n in physical.values())
        fans.append((('七对', '龙七对', '双龙七对')[min(quads, 2)],
                     (4, 8, 16)[min(quads, 2)]))
        candidates.add((tuple(fans), 16 if quads >= 2 else 8))
    for triples, suits, dragons, pair_dragon, gap in _structures(counts, wild, gap_index):
        fans = []
        if suits | meld_suits in (1, 2):
            fans.append(('清一色', 4))
        if triples and meld_triplets:
            fans.append(('碰碰胡', 2))
        dragons |= meld_dragons
        if dragons == 7:
            fans.append(('大三元', 8))
        elif dragons.bit_count() == 2 and pair_dragon and not dragons & pair_dragon:
            fans.append(('小三元', 4))
        if len(melds) == 4:
            fans.append(('手抓一', 4))
        if not physical['J1']:
            quad_fans = []
            for code, count in physical.items():
                if count != 4 or any(m.get('kind', '').endswith('gang')
                                     and m.get('engine_tile') == code for m in melds):
                    continue
                quad_fans.append(('明四归一', 2) if any(m.get('engine_tile') == code for m in melds)
                                 else ('暗四归一', 4))
            if quad_fans:
                fans.append(max(quad_fans, key=lambda item: item[1]))
        if gap:
            fans.append(('卡五星', 2))
        candidates.add((tuple(fans), 8))
    return [dict(fans=list(fans), cap=cap) for fans, cap in sorted(candidates)]
