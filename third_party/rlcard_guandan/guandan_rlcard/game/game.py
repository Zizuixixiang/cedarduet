"""Guandan game: ties dealer/judger/player/round together for the env.

Design notes (intentional deviations from stock RLCard, kept from the
original "open" environment):

* ``init_game(players)`` takes the agent objects themselves - agents
  subclass ``GuandanPlayer`` so the full game/agent interaction
  (including the tribute phase) is observable and debuggable.
* States may carry perfect information (every hand) when
  ``perfect_info`` is True; see ``get_state``.
* When the current player has already emptied their hand, the
  environment still asks them to act; the agent must return ``[]``,
  which triggers the wind-follow (接风) bookkeeping in ``step``.
"""

import logging

import numpy as np

from guandan_rlcard.constants import CARD_RANK, LEVEL_A_INDEX
from guandan_rlcard.game.card_utils import advance_level, cards2str
from guandan_rlcard.game.hand_heuristics import estimate_min_steps
from guandan_rlcard.game.judger import GuandanJudger
from guandan_rlcard.game.round import GuandanRound

logger = logging.getLogger('guandan')


class GuandanGame:
    """Game facade exposing RLCard-style APIs for the Guandan env."""

    def __init__(self, allow_step_back=False, interactive_tribute=False):
        # step_back is not supported; the flag exists for Env compat.
        self.allow_step_back = allow_step_back
        self.interactive_tribute = interactive_tribute
        self.np_random = np.random.RandomState()
        self.num_players = 4
        self.game_count = 1
        # Deals won per team across the episode.
        self.gwin = [0, 0]
        # Team that passed level A; -1 while the episode is running.
        self.winner_team = -1
        self.last_deal_summary = None
        # Include other players' hands / step estimates in states.
        self.perfect_info = True

    def init_game(self, players):
        """Start an episode with the four given agent-players.

        Args:
            players (list): four GuandanPlayer (agent) objects.

        Returns:
            tuple: (first state dict, first player id).
        """
        self.team0_rank = 0
        self.team1_rank = 0
        self.cur_rank = 0
        self.game_count = 1
        self.gwin = [0, 0]
        self.winner_team = -1
        self.last_deal_summary = None
        self.players = players

        self.played_cards = [np.zeros((16, 6), dtype=np.int32)
                             for _ in range(self.num_players)]
        self.round = GuandanRound(
            self.np_random, self.played_cards,
            interactive_tribute=self.interactive_tribute)
        self.round.initiate_episode(self.players)
        self.judger = GuandanJudger(self.players, self.cur_rank)

        player_id = self.round.current_player
        self.state = self.get_state(player_id)
        return self.state, player_id

    def step(self, action):
        """Advance the game by one action of the current player.

        Args:
            action (list): a Guandan action, or ``[]``/None when the
                current player has already finished (wind-follow turn).

        Returns:
            tuple: (next state dict, next player id).
        """
        player = self.players[self.round.current_player]
        round_completed = False

        if self.round.tribute_pending:
            tribute_completed = self.round.proceed_tribute(player, action)
            if tribute_completed:
                # The initial new-deal cache was built before cards moved.
                self.judger.reset(self.players, self.cur_rank)
            return self._next_state(self.round.current_player, False)

        # A finished player may be queried for wind-follow turns; whatever
        # the agent returns then must not be played (the protocol asks for
        # [], but agents are not trusted on this).
        if action and not player.current_hand:
            action = []

        if action:
            _, round_completed = self.round.proceed_round(player, action)
            if self.round.game_over:
                # Deal finished: update levels and start the next deal
                # (or end the episode).
                self.rank_update()
                return self._next_state(self.round.current_player,
                                        round_completed)
            if action[0] != 'PASS':
                self.judger.calc_playable_actions(player)

        if self.judger.judge_player_out(player) and not player.real_out:
            if not self.round.out_flag[player.player_id]:
                # Just emptied the hand; wind-follow may happen if the
                # other three players all pass on this final combo.
                self.round.out_flag[player.player_id] = True
            else:
                # The turn came back unbeaten: wind-follow succeeds and
                # the teammate gets a free lead.
                player.real_out = True
                self.round.greater_player = None
                next_id = (player.player_id + 2) % self.num_players
                self.round.current_player = next_id
                return self._next_state(next_id, round_completed)

        next_id = (player.player_id + 1) % self.num_players
        while next_id in self.round.result and self.players[next_id].real_out:
            next_id = (next_id + 1) % self.num_players
        self.round.current_player = next_id
        return self._next_state(next_id, round_completed)

    def _next_state(self, player_id, round_completed):
        state = self.get_state(player_id)
        state['round_completed'] = round_completed
        state['global_turn_count'] = self.round.global_turn_count
        state['turn_winner'] = self.round.turn_winner
        self.state = state
        return state, player_id

    def get_state(self, player_id):
        """Build the state dict observed by ``player_id``."""
        player = self.players[player_id]
        num_cards_left = [len(p.current_hand) for p in self.players]

        if self.is_over():
            actions = []
        elif self.round.tribute_pending:
            actions = list(self.round.available_tribute_actions(player))
        else:
            actions = list(player.available_actions(
                self.cur_rank, self.round.greater_player, self.judger))

        state = player.get_state(self.round.public, None,
                                 num_cards_left, actions)
        state['global_turn_count'] = self.round.global_turn_count
        state['turn_winner'] = self.round.turn_winner
        state['round_completed'] = False

        if self.perfect_info:
            all_hands = {pid: cards2str(p.current_hand)
                         for pid, p in enumerate(self.players)}
            state['all_players_hands'] = all_hands
            state['min_steps_estimation'] = {
                pid: estimate_min_steps(hand, self.cur_rank)
                for pid, hand in all_hands.items()}
            # Positional view of the hidden hands (used by the DanZero+
            # baseline's perfect-information value features).
            state['others_hands'] = {
                'player_down': all_hands[(player_id + 1) % 4],
                'player_opp': all_hands[(player_id + 2) % 4],
                'player_up': all_hands[(player_id + 3) % 4],
            }
        return state

    # ------------------------------------------------------------------
    # Level / episode bookkeeping
    # ------------------------------------------------------------------

    def rank_update(self):
        """Update team levels after a finished deal; roll the next deal."""
        if not self.round.game_over:
            return
        logger.info('Deal finished, finish order: %s', self.round.result)
        self.round.global_turn_count = 0

        finished_result = list(self.round.result)
        ranks_before = [self.team0_rank, self.team1_rank]
        winner_team = self.players[self.round.result[0]].teamid
        self.round.play_team = winner_team
        self.gwin[winner_team] += 1
        gain = self._level_gain(self.round.result)
        if winner_team == 0:
            self.team0_rank = advance_level(self.team0_rank, gain)
        else:
            self.team1_rank = advance_level(self.team1_rank, gain)

        # Notify agents that keep per-deal training state. The game only
        # exposes this single optional hook; experience processing
        # belongs to the agent, not the engine.
        deal_summary = {
            'result': self.round.result,
            'finished_players': self.round.result,
            'info': {
                'finished_players': self.round.result,
                'winners': self.round.result[:2],
                'winner_team': winner_team,
            },
        }
        for player in self.players:
            if hasattr(player, 'on_episode_end'):
                player.on_episode_end(deal_summary, winner_team)

        self.round.rank_list = [self.team0_rank, self.team1_rank]
        self.cur_rank = self.round.rank_list[self.round.play_team]
        self.last_deal_summary = {
            'deal_number': self.game_count,
            'result': finished_result,
            'winner_team': winner_team,
            'level_gain': gain,
            'rank_before': ranks_before,
            'rank_after': [self.team0_rank, self.team1_rank],
        }

        if self.cur_rank > LEVEL_A_INDEX:
            # The winning team passed level A: episode over.
            self.round.episode_over = True
            self.winner_team = winner_team
            for player in self.players:
                player.reset()
        else:
            self.round.team0_rank = self.team0_rank
            self.round.team1_rank = self.team1_rank
            self.round.cur_rank = self.cur_rank
            for player in self.players:
                player.reset()
            self.round.reset()
            self.round.initiate_game(self.players)
            self.judger.reset(self.players, self.cur_rank)
            self.game_count += 1

    @staticmethod
    def _level_gain(result):
        """Level gain from the finish order: 3 for 1st+2nd, 2 for
        1st+3rd, 1 for 1st+4th."""
        first_team = result[0] % 2
        if result[1] % 2 == first_team:
            return 3
        if result[2] % 2 == first_team:
            return 2
        return 1

    def get_payoffs(self):
        """Episode payoffs: +1 for the team that passed A, -1 otherwise."""
        if not self.is_over() or self.winner_team < 0:
            return [0.0, 0.0, 0.0, 0.0]
        return [1.0 if player.teamid == self.winner_team else -1.0
                for player in self.players]

    # ------------------------------------------------------------------
    # RLCard API
    # ------------------------------------------------------------------

    @staticmethod
    def get_num_actions():
        """The raw action space is combinatorial; no fixed encoding."""
        return None

    def get_player_id(self):
        return self.round.current_player

    def get_num_players(self):
        return self.num_players

    def is_over(self):
        return self.round.episode_over
