"""Guandan dealer: shuffles two 54-card decks and deals 27 cards each."""

import functools

from rlcard.utils import init_54_deck

from guandan_rlcard.game.card_utils import natural_card_cmp

HAND_SIZE = 27
NUM_PLAYERS = 4


class GuandanDealer:
    """Shuffles and deals cards and assigns the two teams."""

    def __init__(self, np_random):
        self.np_random = np_random
        # Guandan uses two full decks (108 cards including 4 jokers).
        self.deck = init_54_deck()
        self.deck.extend(init_54_deck())
        self.teams_assigned = False

    def shuffle(self):
        """Shuffle the deck in place."""
        self.np_random.shuffle(self.deck)

    def deal_cards(self, players):
        """Shuffle and deal 27 sorted cards to each of the 4 players.

        Args:
            players (list): four GuandanPlayer objects.

        Returns:
            list: the four hands (lists of Card objects).
        """
        if len(players) != NUM_PLAYERS:
            raise ValueError('Guandan requires exactly 4 players, got '
                             f'{len(players)}.')
        self.shuffle()

        all_hands = []
        for index, player in enumerate(players):
            if player is None:
                raise ValueError(f'Player slot {index} is empty.')
            start = index * HAND_SIZE
            hand = self.deck[start:start + HAND_SIZE]
            hand.sort(key=functools.cmp_to_key(natural_card_cmp))
            all_hands.append(hand)
            player.set_current_hand(hand)
        return all_hands

    def determine_teams(self, players):
        """Assign seats 0/2 to team 0 and seats 1/3 to team 1 (once)."""
        if self.teams_assigned:
            return
        self.teams_assigned = True
        if len(players) != NUM_PLAYERS:
            raise ValueError('Guandan requires exactly 4 players, got '
                             f'{len(players)}.')
        players[0].teamid = 0
        players[1].teamid = 1
        players[2].teamid = 0
        players[3].teamid = 1
