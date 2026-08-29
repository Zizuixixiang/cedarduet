"""Combo generation from a hand (GuandanJudger.playable_actions_from_hand)."""

from guandan_rlcard.game.judger import GuandanJudger

from conftest import make_hand

LEVEL_T = 8  # level rank 'T' - irrelevant to most cases below


def actions_of(card_strs, level_rank_index=LEVEL_T, combo_type=None):
    actions = GuandanJudger.playable_actions_from_hand(
        make_hand(card_strs), level_rank_index)
    if combo_type is None:
        return actions
    return [a for a in actions if a[0] == combo_type]


class TestThreePair:
    def test_aa2233_generated_even_with_king_pair(self):
        hand = ['SA', 'HA', 'S2', 'H2', 'S3', 'H3', 'SK', 'HK']
        keys = {a[1] for a in actions_of(hand, combo_type='ThreePair')}
        assert 'A' in keys

    def test_qqkkaa_generated(self):
        hand = ['SQ', 'HQ', 'SK', 'HK', 'SA', 'HA']
        keys = {a[1] for a in actions_of(hand, combo_type='ThreePair')}
        assert 'Q' in keys

    def test_kkaa22_not_generated(self):
        hand = ['SK', 'HK', 'SA', 'HA', 'S2', 'H2']
        keys = {a[1] for a in actions_of(hand, combo_type='ThreePair')}
        assert 'K' not in keys


class TestTwoTrips:
    def test_aaa222_generated(self):
        hand = ['SA', 'HA', 'CA', 'S2', 'H2', 'C2']
        keys = {a[1] for a in actions_of(hand, combo_type='TwoTrips')}
        assert 'A' in keys

    def test_kkkaaa_generated(self):
        hand = ['SK', 'HK', 'CK', 'SA', 'HA', 'CA']
        keys = {a[1] for a in actions_of(hand, combo_type='TwoTrips')}
        assert 'K' in keys


class TestThreeWithTwo:
    def test_joker_pair_attachment(self):
        hand = ['S9', 'H9', 'C9', 'SB', 'SB']
        combos = actions_of(hand, combo_type='ThreeWithTwo')
        assert any(sorted(a[2]) == ['C9', 'H9', 'S9', 'SB', 'SB']
                   for a in combos)

    def test_same_rank_pair_not_attached(self):
        # 9999 + 9 pair impossible anyway; trips + pair of same rank
        # must not appear (it would be a bomb).
        hand = ['S9', 'H9', 'C9', 'D9', 'S9']
        combos = actions_of(hand, combo_type='ThreeWithTwo')
        assert all(a[1] != '9' or not all(c[-1] == '9' for c in a[2])
                   for a in combos)


class TestPairs:
    def test_no_duplicate_pair_actions(self):
        hand = ['S3', 'H3', 'S3']
        pairs = [tuple(sorted(a[2]))
                 for a in actions_of(hand, combo_type='Pair')]
        assert len(pairs) == len(set(pairs))

    def test_wildcard_pair(self):
        # Level 5 (index 3): H5 is the wildcard and pairs with any rank.
        hand = ['S9', 'H5']
        pairs = actions_of(hand, level_rank_index=3, combo_type='Pair')
        assert any(sorted(a[2]) == ['H5', 'S9'] and a[1] == '9'
                   for a in pairs)


class TestBombs:
    def test_joker_bomb_generated(self):
        hand = ['HR', 'HR', 'SB', 'SB']
        bombs = actions_of(hand, combo_type='Bomb')
        assert any(a[1] == 'R' for a in bombs)

    def test_ten_card_bomb_with_wildcards(self):
        # Level 5: eight nines + both H5 wildcards = 10-card bomb.
        hand = ['S9', 'H9', 'C9', 'D9', 'S9', 'H9', 'C9', 'D9', 'H5', 'H5']
        bombs = actions_of(hand, level_rank_index=3, combo_type='Bomb')
        assert any(len(a[2]) == 10 for a in bombs)

    def test_wildcard_completes_bomb(self):
        hand = ['S9', 'H9', 'C9', 'H5']
        bombs = actions_of(hand, level_rank_index=3, combo_type='Bomb')
        assert any(a[1] == '9' and len(a[2]) == 4 for a in bombs)


class TestStraights:
    def test_plain_straight(self):
        hand = ['S3', 'H4', 'S5', 'H6', 'S7']
        straights = actions_of(hand, combo_type='Straight')
        assert any(a[1] == '3' for a in straights)

    def test_ace_low_straight(self):
        hand = ['SA', 'H2', 'S3', 'H4', 'S5']
        straights = actions_of(hand, combo_type='Straight')
        assert any(a[1] == 'A' for a in straights)

    def test_same_suit_run_is_straight_flush(self):
        hand = ['S3', 'S4', 'S5', 'S6', 'S7']
        assert not actions_of(hand, combo_type='Straight')
        flushes = actions_of(hand, combo_type='StraightFlush')
        assert any(a[1] == '3' for a in flushes)

    def test_wildcard_fills_straight(self):
        # Level 5: H5 substitutes the missing 6.
        hand = ['S3', 'H4', 'C5', 'H5', 'S7']
        straights = actions_of(hand, level_rank_index=3,
                               combo_type='Straight')
        assert any(a[1] == '3' and 'H5' in a[2] and 'S7' in a[2]
                   for a in straights)

    def test_no_straight_across_ace(self):
        # Q-K-A-2-3 is illegal.
        hand = ['SQ', 'HK', 'SA', 'H2', 'S3']
        straights = actions_of(hand, combo_type='Straight')
        assert straights == []


def test_single_includes_jokers():
    hand = ['S9', 'SB', 'HR']
    singles = actions_of(hand, combo_type='Single')
    keys = {a[1] for a in singles}
    assert {'9', 'B', 'R'} <= keys
