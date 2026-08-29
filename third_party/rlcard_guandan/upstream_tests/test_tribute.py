"""Tribute (进贡) and tribute-back (还贡) rules."""

import numpy as np

from guandan_rlcard.baselines.random_agent import RandomAgent
from guandan_rlcard.game.round import GuandanRound

from conftest import make_hand


def player_with(card_strs, player_id=0):
    agent = RandomAgent(player_id, np.random.RandomState(0))
    agent.set_current_hand(make_hand(card_strs))
    return agent


class TestLegalTribute:
    def test_joker_must_be_paid(self):
        player = player_with(['S3', 'H7', 'SA', 'HR'])
        actions = player.legal_tribute_actions('5')
        assert [a[2] for a in actions] == [['HR']]

    def test_level_card_paid_when_no_joker(self):
        player = player_with(['S3', 'S5', 'SA'])
        actions = player.legal_tribute_actions('5')
        assert [a[2] for a in actions] == [['S5']]

    def test_biggest_natural_card_otherwise(self):
        player = player_with(['S3', 'H7', 'SA', 'HA'])
        actions = player.legal_tribute_actions('5')
        assert sorted(a[2][0] for a in actions) == ['HA', 'SA']

    def test_heart_level_card_is_exempt(self):
        # Level 'A': the heart ace is the wildcard and is kept; the
        # biggest non-level rank must be paid instead.
        player = player_with(['S3', 'H7', 'HK', 'HA'])
        actions = player.legal_tribute_actions('A')
        assert [a[2] for a in actions] == [['HK']]


class TestLegalBack:
    def test_only_small_cards_returnable(self):
        player = player_with(['S3', 'HT', 'SJ', 'SA', 'HR'])
        actions = player.legal_back_actions('5')
        assert sorted(a[2][0] for a in actions) == ['HT', 'S3']

    def test_level_cards_of_any_suit_not_returnable(self):
        # RULE: a level card counts above an ace and must not be handed
        # back, whatever its suit.
        player = player_with(['S5', 'H5', 'S3'])
        actions = player.legal_back_actions('5')
        assert [a[2] for a in actions] == [['S3']]

    def test_fallback_when_no_small_card(self):
        player = player_with(['SJ', 'SQ', 'SK'])
        actions = player.legal_back_actions('5')
        assert {a[2][0] for a in actions} == {'SJ', 'SQ', 'SK'}


class TestResist:
    def _round(self):
        return GuandanRound(np.random.RandomState(0), [])

    def test_single_down_resists_with_both_big_jokers(self):
        game_round = self._round()
        players = [player_with(['S3', 'SA'], 0),
                   player_with(['S4', 'SA'], 1),
                   player_with(['S5', 'SA'], 2),
                   player_with(['S6', 'HR', 'HR'], 3)]
        # Previous deal: 0 first, 3 last (single down: 3 pays).
        assert game_round._check_resist([0, 1, 2, 3], players)

    def test_single_down_no_resist_with_one_joker(self):
        game_round = self._round()
        players = [player_with(['S3', 'SA'], 0),
                   player_with(['S4', 'SA'], 1),
                   player_with(['S5', 'SA'], 2),
                   player_with(['S6', 'HR'], 3)]
        assert not game_round._check_resist([0, 1, 2, 3], players)

    def test_double_down_resists_when_winners_hold_no_big_joker(self):
        game_round = self._round()
        players = [player_with(['S3', 'SA'], 0),
                   player_with(['S4', 'HR'], 1),
                   player_with(['S5', 'SA'], 2),
                   player_with(['S6', 'HR'], 3)]
        # Previous deal: 0 and 2 (team 0) first, 1 and 3 双下 and pay.
        assert game_round._check_resist([0, 2, 1, 3], players)

    def test_double_down_no_resist_when_a_winner_holds_one(self):
        game_round = self._round()
        players = [player_with(['S3', 'HR'], 0),
                   player_with(['S4', 'SA'], 1),
                   player_with(['S5', 'SA'], 2),
                   player_with(['S6', 'HR'], 3)]
        assert not game_round._check_resist([0, 2, 1, 3], players)
