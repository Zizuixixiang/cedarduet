"""Shared helpers for the guandan-rlcard test suite."""

from types import SimpleNamespace

import pytest
from rlcard.games.base import Card

from guandan_rlcard.constants import BIG_JOKER, SMALL_JOKER


def make_hand(card_strs):
    """Build Card objects from card strings ('H3', 'SB', 'HR')."""
    cards = []
    for card_str in card_strs:
        if card_str == SMALL_JOKER:
            cards.append(Card('BJ', ''))
        elif card_str == BIG_JOKER:
            cards.append(Card('RJ', ''))
        else:
            cards.append(Card(card_str[0], card_str[1]))
    return cards


def table_player(action):
    """A minimal stand-in for the greater player holding ``action``."""
    return SimpleNamespace(played_action=action, player_id=0)


@pytest.fixture
def hand_factory():
    return make_hand


@pytest.fixture
def table():
    return table_player
