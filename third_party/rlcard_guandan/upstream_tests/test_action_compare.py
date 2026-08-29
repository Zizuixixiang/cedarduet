"""Rules for which actions beat the action on the table."""

from guandan_rlcard.game.action_compare import get_gt_actions

from conftest import table_player

JOKER_BOMB = ['Bomb', 'R', ['HR', 'HR', 'SB', 'SB']]


def beats(level_rank_index, current, candidate):
    result = get_gt_actions(level_rank_index, table_player(current),
                            [candidate])
    return any(action[0] != 'PASS' for action in result)


class TestStraightFlush:
    def test_equal_straight_flush_cannot_beat(self):
        current = ['StraightFlush', '2', ['S2', 'S3', 'S4', 'S5', 'S6']]
        candidate = ['StraightFlush', '2', ['D2', 'D3', 'D4', 'D5', 'D6']]
        assert not beats(2, current, candidate)

    def test_higher_straight_flush_beats(self):
        current = ['StraightFlush', '2', ['S2', 'S3', 'S4', 'S5', 'S6']]
        candidate = ['StraightFlush', '3', ['D3', 'D4', 'D5', 'D6', 'D7']]
        assert beats(2, current, candidate)

    def test_ace_low_straight_flush_is_smallest(self):
        current = ['StraightFlush', 'A', ['SA', 'S2', 'S3', 'S4', 'S5']]
        candidate = ['StraightFlush', '2', ['D2', 'D3', 'D4', 'D5', 'D6']]
        assert beats(2, current, candidate)
        assert not beats(2, candidate,
                         ['StraightFlush', 'A', ['DA', 'D2', 'D3', 'D4', 'D5']])

    def test_five_card_bomb_cannot_beat_straight_flush(self):
        current = ['StraightFlush', '2', ['S2', 'S3', 'S4', 'S5', 'S6']]
        candidate = ['Bomb', 'A', ['SA', 'HA', 'CA', 'DA', 'SA']]
        assert not beats(2, current, candidate)

    def test_six_card_bomb_beats_straight_flush(self):
        current = ['StraightFlush', '2', ['S2', 'S3', 'S4', 'S5', 'S6']]
        candidate = ['Bomb', '4', ['S4', 'H4', 'C4', 'D4', 'S4', 'H4']]
        assert beats(2, current, candidate)

    def test_straight_flush_beats_five_card_bomb(self):
        current = ['Bomb', 'A', ['SA', 'HA', 'CA', 'DA', 'SA']]
        candidate = ['StraightFlush', '2', ['S2', 'S3', 'S4', 'S5', 'S6']]
        assert beats(2, current, candidate)

    def test_straight_flush_cannot_beat_six_card_bomb(self):
        current = ['Bomb', '4', ['S4', 'H4', 'C4', 'D4', 'S4', 'H4']]
        candidate = ['StraightFlush', 'T', ['ST', 'SJ', 'SQ', 'SK', 'SA']]
        assert not beats(2, current, candidate)


class TestJokerBomb:
    def test_beats_six_card_bomb(self):
        current = ['Bomb', '9', ['S9', 'H9', 'C9', 'D9', 'S9', 'H9']]
        assert beats(2, current, JOKER_BOMB)

    def test_beats_straight_flush(self):
        current = ['StraightFlush', 'T', ['ST', 'SJ', 'SQ', 'SK', 'SA']]
        assert beats(2, current, JOKER_BOMB)

    def test_beats_single(self):
        assert beats(2, ['Single', 'R', ['HR']], JOKER_BOMB)

    def test_nothing_beats_it(self):
        candidate = ['Bomb', '2', ['S2'] * 10]
        assert not beats(0, JOKER_BOMB, candidate)


class TestSequencesUseNaturalOrder:
    def test_level_two_trips_does_not_beat_kkkaaa(self):
        # Level is 5 (index 3): 555666 stays a natural 5-start plate.
        current = ['TwoTrips', 'K', ['SK', 'HK', 'CK', 'SA', 'HA', 'CA']]
        candidate = ['TwoTrips', '5', ['S5', 'H5', 'C5', 'S6', 'H6', 'C6']]
        assert not beats(3, current, candidate)

    def test_ace_low_two_trips_is_smallest(self):
        current = ['TwoTrips', 'A', ['SA', 'HA', 'CA', 'S2', 'H2', 'C2']]
        candidate = ['TwoTrips', '2', ['S2', 'H2', 'C2', 'S3', 'H3', 'C3']]
        assert beats(3, current, candidate)

    def test_level_straight_compares_naturally(self):
        # Level 5: a straight keyed '5' (5-9) only counts as 5.
        current = ['Straight', '6', ['S6', 'H7', 'S8', 'H9', 'ST']]
        candidate = ['Straight', '5', ['S5', 'H6', 'S7', 'H8', 'S9']]
        assert not beats(3, current, candidate)

    def test_three_pair_natural_order(self):
        current = ['ThreePair', 'J', ['SJ', 'HJ', 'SQ', 'HQ', 'SK', 'HK']]
        candidate = ['ThreePair', 'Q', ['SQ', 'HQ', 'SK', 'HK', 'SA', 'HA']]
        assert beats(3, current, candidate)


class TestLevelPromotion:
    def test_level_pair_beats_ace_pair(self):
        # Level 5: a pair of 5s outranks a pair of aces.
        current = ['Pair', 'A', ['SA', 'HA']]
        candidate = ['Pair', '5', ['S5', 'D5']]
        assert beats(3, current, candidate)

    def test_joker_pair_beats_level_pair(self):
        current = ['Pair', '5', ['S5', 'D5']]
        candidate = ['Pair', 'B', ['SB', 'SB']]
        assert beats(3, current, candidate)

    def test_equal_pair_cannot_beat(self):
        current = ['Pair', '9', ['S9', 'H9']]
        candidate = ['Pair', '9', ['C9', 'D9']]
        assert not beats(3, current, candidate)


class TestBombs:
    def test_longer_bomb_beats_higher_shorter_bomb(self):
        current = ['Bomb', 'A', ['SA', 'HA', 'CA', 'DA']]
        candidate = ['Bomb', '2', ['S2', 'H2', 'C2', 'D2', 'S2']]
        assert beats(5, current, candidate)

    def test_equal_size_bomb_needs_higher_rank(self):
        current = ['Bomb', '9', ['S9', 'H9', 'C9', 'D9']]
        low = ['Bomb', '8', ['S8', 'H8', 'C8', 'D8']]
        high = ['Bomb', 'T', ['ST', 'HT', 'CT', 'DT']]
        assert not beats(5, current, low)
        assert beats(5, current, high)

    def test_level_bomb_beats_ace_bomb(self):
        # Level 7 (index 5): 7777 beats AAAA.
        current = ['Bomb', 'A', ['SA', 'HA', 'CA', 'DA']]
        candidate = ['Bomb', '7', ['S7', 'H7', 'C7', 'D7']]
        assert beats(5, current, candidate)

    def test_bomb_beats_any_normal_combo(self):
        current = ['ThreeWithTwo', 'A', ['SA', 'HA', 'CA', 'S2', 'H2']]
        candidate = ['Bomb', '2', ['S2', 'H2', 'C2', 'D2']]
        assert beats(5, current, candidate)


def test_pass_is_always_offered():
    current = ['Single', 'R', ['HR']]
    result = get_gt_actions(0, table_player(current), [])
    assert result == [['PASS', 'PASS', 'PASS']]
