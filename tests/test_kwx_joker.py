import random
import time
import unittest
from functools import lru_cache
from itertools import combinations_with_replacement

from app.games.kwx_joker import CODES, evaluations, waits, winning


def fan_sets(codes, win_code=None, **kwargs):
    return [dict(item['fans']) for item in evaluations(codes.split(), win_code=win_code, **kwargs)]


@lru_cache(maxsize=None)
def plain_groups(codes):
    """Small independent natural-tile oracle; abstract counts may exceed four."""
    if not codes:
        return True
    first = codes[0]
    groups = [(first,) * 3]
    if first[0] in 'BT' and int(first[1]) <= 7:
        groups.append(tuple(first[0] + str(int(first[1]) + n) for n in range(3)))
    for group in groups:
        remaining = list(codes)
        try:
            for code in group:
                remaining.remove(code)
        except ValueError:
            continue
        if plain_groups(tuple(remaining)):
            return True
    return False


def oracle(codes, wild):
    for replacements in combinations_with_replacement(CODES, wild):
        complete = tuple(sorted((*codes, *replacements)))
        if len(complete) == 14 and all(complete.count(code) % 2 == 0 for code in set(complete)):
            return True
        for pair in set(complete):
            if complete.count(pair) >= 2:
                remaining = list(complete)
                remaining.remove(pair)
                remaining.remove(pair)
                if plain_groups(tuple(remaining)):
                    return True
    return False


class RedJokerTests(unittest.TestCase):
    def test_ordinary_shape_joker_and_external_red(self):
        hand = 'B1 B2 B3 B4 B5 B6 T1 T2 T3 T7 T8 J2 J2 J1'.split()
        self.assertTrue(winning(hand))
        self.assertFalse(winning(hand, win_code='J1', win_is_wild=False))
        natural_red_pair = 'B1 B2 B3 B4 B5 B6 T1 T2 T3 T7 T8 T9 J1 J1'.split()
        self.assertTrue(winning(natural_red_pair, win_code='J1', win_is_wild=False))

    def test_physical_limit_and_melds(self):
        self.assertFalse(winning('B1 B1 B1 B1 B1 B2 B3 B4 B5 B6 B7 B8 B9 J1'.split()))
        self.assertFalse(winning(['J1'] * 5 + ['B1'] * 3 + ['T2'] * 3 + ['T3'] * 3))
        self.assertFalse(winning(['W1'] * 2))
        melds = [dict(kind='peng', engine_tile='B1', tiles=[dict(code='B1')] * 3)]
        self.assertTrue(winning('B1 J1 B2 B3 B4 T1 T2 T3 J2 J2 J2'.split(), melds=melds))
        self.assertFalse(winning('B1 B1 B2 B3 B4 T1 T2 T3 J2 J2 J2'.split(), melds=melds))
        self.assertNotIn('B1', waits('B1 B2 B3 B4 T1 T2 T3 J2 J2 J2'.split(), melds))

    def test_seven_pairs_and_quad_exclusions(self):
        fans = fan_sets('B1 B1 B1 B1 B3 B3 B7 B7 T4 T4 T6 T6 J2 J1')
        self.assertTrue(any('七对' in f for f in fans))
        self.assertFalse(any('龙七对' in f or '暗四归一' in f or '明四归一' in f for f in fans))
        no_red = fan_sets('B1 B1 B1 B1 B3 B3 B7 B7 T4 T4 T6 T6 J2 J2')
        self.assertTrue(any('龙七对' in f for f in no_red))
        self.assertTrue(winning('B1 B2 B2 B3 B3 B4 B4 B5 B5 B6 J1 J1 J1 J1'.split()))

    def test_clear_suit_requires_effective_decomposition(self):
        fans = fan_sets('B1 B2 B3 B4 B5 B6 B7 B8 B9 B2 B2 B8 B8 J1')
        self.assertTrue(any('清一色' in f for f in fans))
        external = fan_sets('B1 B2 B3 B4 B5 B6 B7 B8 B9 B2 B2 J1 J1 J1',
                            win_code='J1', win_is_wild=False)
        self.assertTrue(external)
        self.assertFalse(any('清一色' in f for f in external))

    def test_natural_gap_and_wildcard_gap_counterexamples(self):
        natural = fan_sets('B1 B2 B3 T1 T2 T3 T7 T8 T9 B4 B5 B6 J2 J1', 'B5')
        self.assertTrue(any('卡五星' in f for f in natural))
        missing_four = fan_sets('B1 B2 B3 T1 T2 T3 T7 T8 T9 J1 B5 B6 J2 J2', 'B5')
        self.assertTrue(missing_four)
        self.assertFalse(any('卡五星' in f for f in missing_four))
        missing_six = fan_sets('B1 B2 B3 T1 T2 T3 T7 T8 T9 B4 B5 J1 J2 J2', 'B5')
        self.assertFalse(any('卡五星' in f for f in missing_six))
        different_win = fan_sets('B1 B2 B3 T1 T2 T3 T7 T8 T9 B4 B5 B6 J2 J1', 'J1')
        self.assertFalse(any('卡五星' in f for f in different_win))

    def test_dragons_require_actual_tiles_in_same_decomposition(self):
        big = fan_sets('J1 J1 J1 J2 J2 J2 J3 J3 J3 B1 B2 B3 T4 T4')
        self.assertTrue(any('大三元' in f for f in big))
        small = fan_sets('J1 J1 J2 J2 J2 J3 J3 J3 B1 B2 B3 T4 T5 T6')
        self.assertTrue(any('小三元' in f for f in small))
        fake_white = fan_sets('J1 J1 J1 J1 J2 J2 J2 J3 J3 B1 B2 B3 T4 T4')
        self.assertTrue(fake_white)
        self.assertFalse(any('大三元' in f for f in fake_white))
        # The same reds cannot supply a natural pair AND fill the missing white.
        reused = fan_sets('J1 J1 J2 J2 J2 J3 J3 B1 B2 B3 T4 T5 T6 T7')
        self.assertFalse(any('小三元' in f for f in reused))

    def test_waits_distinguish_self_draw_and_discard_red(self):
        hand = 'B1 B2 B3 B4 B5 B6 T1 T2 T3 T7 T8 J2 J2'.split()
        self.assertIn('J1', waits(hand))
        self.assertNotIn('J1', waits(hand, win_is_wild=False))
        self.assertEqual(set(waits(hand)) - {'J1'}, {'T6', 'T9'})
        for code in waits(hand):
            self.assertTrue(winning([*hand, code], win_code=code))

    def test_four_jokers_do_not_enumerate_every_substitution(self):
        started = time.monotonic()
        for hand in (
            'B1 B2 B3 B4 B5 B6 T1 T2 J2 J3 J1 J1 J1 J1',
            'B1 B1 B2 B2 B3 B3 B4 B4 B5 B5 J1 J1 J1 J1',
            'B1 B4 B7 T1 T4 T7 J2 J2 J3 J3 J1 J1 J1 J1',
        ):
            codes = hand.split()
            evaluations(codes, win_code='J1')
            waits(codes[:-1])
        self.assertLess(time.monotonic() - started, 5.0)

    def test_cached_solver_matches_independent_small_wild_oracle(self):
        rng = random.Random(731)
        pool = [code for code in CODES if code != 'J1']
        # Short concealed shapes permit exhaustive replacement comparisons,
        # including two jokers and abstract five-of-a-kind substitutions.
        for size, meld_count in ((2, 4), (5, 3), (8, 2)):
            for wild in (0, 1, 2):
                for _ in range(30):
                    natural = [rng.choice(pool) for _ in range(size - wild)]
                    if any(natural.count(code) > 4 for code in natural):
                        continue
                    self.assertEqual(winning(natural + ['J1'] * wild, meld_count),
                                     oracle(natural, wild), (natural, wild))


if __name__ == '__main__':
    unittest.main()
