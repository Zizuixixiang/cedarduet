"""Guandan player: holds a hand, plays actions, pays and returns tribute.

In this environment the *agent objects themselves* subclass
``GuandanPlayer`` (see ``guandan_rlcard.agents.base_agent``). Besides the
state-keeping methods here, an agent must implement:

* ``step(state) -> action`` - choose a play action,
* ``tribute_act(actions, level_rank) -> action`` - choose AND execute a
  tribute action (call ``execute_tribute``),
* ``back_act(actions, level_rank, tribute_result) -> action`` - choose
  AND execute a tribute-back action (call ``execute_back``).
"""

import functools
import logging

from rlcard.games.base import Card

from guandan_rlcard.constants import CARD_RANK, CARD_RANK_INDEX, SUITS
from guandan_rlcard.game.action_compare import get_gt_actions
from guandan_rlcard.game.card_utils import card_to_str, cards2str, \
    natural_card_cmp

logger = logging.getLogger('guandan')

# Ranks too big to be handed back as tribute (rule: at most a Ten).
_NOT_RETURNABLE_RANKS = ('J', 'Q', 'K', 'A', 'B', 'R')


class GuandanPlayer:
    """Stores a hand and performs play/tribute actions by the rules."""

    def __init__(self, player_id, np_random):
        self.np_random = np_random
        self.player_id = player_id
        self._current_hand = []
        self._current_hand_str = []
        self.teamid = -1
        # Set True once the player's last combo can no longer be beaten
        # (used by the wind-following / 接风 logic).
        self.real_out = False
        # The action this player most recently played.
        self.played_action = None

    @property
    def current_hand(self):
        return self._current_hand

    @property
    def current_hand_str(self):
        return self._current_hand_str

    def set_current_hand(self, cards):
        """Replace the hand (copies the list) and refresh its string form."""
        self._current_hand = list(cards)
        self._current_hand_str = cards2str(self._current_hand)

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def get_state(self, public, others_hands, num_cards_left, actions):
        """Assemble this player's observation dict from the public state."""
        state = {
            'trace': public['trace'].copy(),
            'played_cards': public['played_cards'],
            'self': self.player_id,
            'teamid': self.teamid,
            'rank_list': public['rank_list'],
            'play_team': public['play_team'],
            'greaterPos': public['greaterPos'],
            'pass_num': public['pass_num'],
            'my_pass_num': public['my_pass_num'],
            'greaterAction': public['greaterAction'],
            'current_hand': self.current_hand_str,
            'num_cards_left': num_cards_left,
            'actions': actions,
            'reactions': public['played_actions'],
            'remain_cards': public['remain_cards'],
        }
        for key in ('last_trick_plays', 'last_trick_winner', 'bomb_history',
                    'last_actions', 'rank_card', 'finished_players',
                    'recent_plays'):
            if key in public:
                state[key] = public[key]
        return state

    # ------------------------------------------------------------------
    # Playing
    # ------------------------------------------------------------------

    def available_actions(self, level_rank_index, greater_player=None,
                          judger=None):
        """Legal actions for this player right now.

        When the player holds the table (no greater player, or the
        greater player is themselves) every hand combo is legal and PASS
        is NOT included; otherwise only beating combos plus PASS.
        """
        actions = judger.get_playable_actions(self)
        if greater_player is None or greater_player.player_id == self.player_id:
            return actions
        return get_gt_actions(level_rank_index, greater_player, actions)

    def play(self, action, greater_player=None):
        """Remove the action's cards from the hand.

        Returns:
            GuandanPlayer: the new greater player (this player unless the
            action is PASS).
        """
        if not action or action[0] == 'PASS':
            return greater_player

        for play_card in action[2]:
            for idx, card in enumerate(self._current_hand):
                if play_card == card_to_str(card):
                    self._current_hand.pop(idx)
                    break
            else:
                raise ValueError(
                    f'Player {self.player_id} tried to play {play_card} '
                    'which is not in hand.')

        self.played_action = action
        self.set_current_hand(self._current_hand)
        return self

    # ------------------------------------------------------------------
    # Tribute (进贡 / 还贡)
    # ------------------------------------------------------------------

    def legal_tribute_actions(self, level_rank):
        """Legal tribute actions: the biggest card must be paid, except
        heart level cards (wildcards), which are exempt.

        Args:
            level_rank (str): rank character of the current level.

        Returns:
            list: actions ``['tribute', key, [card_str]]``. For jokers the
            key is the full card string ('HR'/'SB') - a historical quirk
            kept for agent compatibility.
        """
        tribute_actions = []
        level_cards = [suit + level_rank for suit in SUITS if suit != 'H']
        held_level_cards = set(level_cards) & set(self.current_hand_str)

        if self.current_hand[-1].suit in ('BJ', 'RJ'):
            # Hand is sorted; a joker, the biggest card, must be paid.
            joker = self.current_hand_str[-1]
            tribute_actions.append(['tribute', joker, [joker]])
        elif held_level_cards:
            # Non-heart level cards are the biggest remaining cards.
            for card in held_level_cards:
                tribute_actions.append(['tribute', card[-1], [card]])
        else:
            # Pay the biggest natural rank; heart level cards are exempt,
            # so skip the level rank when it tops the hand.
            top_rank = self.current_hand_str[-1][1]
            if top_rank == level_rank:
                top_rank = '2'
                for card in self.current_hand_str:
                    if card[-1] != level_rank and \
                            CARD_RANK_INDEX[card[-1]] > CARD_RANK_INDEX[top_rank]:
                        top_rank = card[-1]
            for card in self.current_hand_str:
                if card[1] == top_rank:
                    tribute_actions.append(['tribute', top_rank, [card]])
        return tribute_actions

    def legal_back_actions(self, level_rank):
        """Legal tribute-back actions: any card up to a Ten that is not a
        level card.

        RULE: the original only excluded the heart level card; level cards
        of other suits could be handed back. A level card counts above an
        Ace, so returning one violates the 'at most a Ten' rule - all
        level cards are now excluded.
        """
        back_actions = []
        for card_str in self.current_hand_str:
            if card_str[-1] in _NOT_RETURNABLE_RANKS:
                continue
            if card_str[-1] == level_rank:
                continue
            back_actions.append(['back', card_str[-1], [card_str]])
        if not back_actions:
            # Extremely rare: no card at Ten or below. Fall back to any
            # card except heart level cards so the phase can proceed.
            for card_str in self.current_hand_str:
                if card_str[0] == 'H' and card_str[-1] == level_rank:
                    continue
                back_actions.append(['back', card_str[-1], [card_str]])
        return back_actions

    def execute_tribute(self, action):
        """Remove and return the tribute card of ``action`` from the hand.

        Upstream callers historically ignored the return value.  Returning the
        actual physical ``Card`` lets an interactive host preserve identity
        across the staged tribute exchange without changing the rule.
        """
        return self._remove_one_card(action, 'tribute')

    def execute_back(self, action):
        """Remove and return the tribute-back card of ``action``."""
        if action[0] != 'back':
            raise ValueError(f'Not a tribute-back action: {action}')
        return self._remove_one_card(action, 'tribute-back')

    def _remove_one_card(self, action, what):
        if len(action) < 3 or not isinstance(action[2], list) or not action[2]:
            raise ValueError(f'Malformed {what} action: {action}')
        card_str = action[2][0]
        new_hand = []
        removed = None
        found = False
        for card in self.current_hand:
            if not found and card_to_str(card) == card_str:
                found = True
                removed = card
                continue
            new_hand.append(card)
        if not found:
            raise ValueError(
                f'Player {self.player_id} has no {card_str} to {what}.')
        self.set_current_hand(new_hand)
        return removed

    def add_card(self, card):
        """Insert a received card, keeping the hand sorted."""
        if not isinstance(card, Card):
            raise ValueError(f'Not a Card object: {card}')
        new_hand = self.current_hand.copy()
        new_hand.append(card)
        new_hand.sort(key=functools.cmp_to_key(natural_card_cmp))
        self.set_current_hand(new_hand)

    def reset(self):
        """Clear per-deal state before a new deal."""
        self._current_hand = []
        self._current_hand_str = []
        self.real_out = False
        self.played_action = None
