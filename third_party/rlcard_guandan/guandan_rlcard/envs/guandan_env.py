"""RLCard-style environment wrapper for Guandan.

This is an *open* environment: states are plain dicts (no fixed-size
encoding), agents are the player objects themselves, and with
``perfect_info`` (default True) every state also exposes all four hands
plus per-hand step estimates. This makes the whole interaction - deals,
tribute phase, every action - visible and easy to debug, at the price of
not being a drop-in for RLCard's built-in RL agents.

Config keys (all optional):
    seed (int): random seed for the game.
    allow_step_back (bool): accepted for compat; step_back is NOT
        supported by this game.
    perfect_info (bool): include other players' hands in states.
"""

import logging

from rlcard.envs import Env

from guandan_rlcard.game.card_utils import cards2str
from guandan_rlcard.game.game import GuandanGame
from guandan_rlcard.game.hand_heuristics import estimate_min_steps
from guandan_rlcard.constants import CARD_RANK, SUITS

logger = logging.getLogger('guandan')

DEFAULT_GUANDAN_CONFIG = {
    'seed': None,
    'allow_step_back': False,
    'perfect_info': True,
}


class GuandanEnv(Env):
    """Guandan environment exposing raw dict states and list actions."""

    def __init__(self, config=None):
        merged = dict(DEFAULT_GUANDAN_CONFIG)
        merged.update(config or {})
        self.name = 'guandan'
        self.game = GuandanGame()
        self.game.perfect_info = merged['perfect_info']
        super().__init__(merged)
        # Oracle shaping value, refreshed at every completed trick:
        # (team0 estimated steps-to-empty) - (team1 estimated steps).
        # Positive means team 0 is behind.
        self.last_oracle = 0
        self.current_oracle = 0

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    def reset(self):
        """Start a new episode; agents must be set via ``set_agents``."""
        if not getattr(self, 'agents', None):
            raise RuntimeError('Call set_agents(...) before reset().')
        state, player_id = self.game.init_game(self.agents)
        self.action_recorder = []
        self.last_oracle = 0
        self.current_oracle = self._calculate_oracle_value()
        self._attach_oracle(state)
        return state, player_id

    def step(self, action, raw_action=False):
        """Apply an action (raw list form; ``raw_action`` kept for API
        compatibility and ignored)."""
        next_state, next_player_id = self.game.step(action)
        self.timestep += 1
        if next_state.get('round_completed', False):
            self.last_oracle = self.current_oracle
            self.current_oracle = self._calculate_oracle_value()
        self._attach_oracle(next_state)
        return next_state, next_player_id

    def run(self, is_training=False):
        """Play one full episode with the registered agents.

        The tribute phase between deals is handled inside the game via
        the agents' ``tribute_act``/``back_act`` methods. When an agent
        has finished its hand it is still asked to act and must return
        ``[]`` (see ``GuandanAgent.step``).

        Returns:
            tuple: (trajectories, payoffs) following the RLCard
            convention. Per-deal wins are available as ``env.game.gwin``
            and the episode winner as ``env.game.winner_team``.
        """
        trajectories = [[] for _ in range(self.num_players)]
        state, player_id = self.reset()

        trajectories[player_id].append(state)
        while not self.is_over():
            agent = self.agents[player_id]
            if not is_training and hasattr(agent, 'eval_step'):
                action = agent.eval_step(state)
            else:
                action = agent.step(state)
            next_state, next_player_id = self.step(action)
            trajectories[player_id].append(action)
            state = next_state
            player_id = next_player_id
            if not self.game.is_over():
                trajectories[player_id].append(state)

        for pid in range(self.num_players):
            final_state = self.get_state(pid)
            self._attach_oracle(final_state)
            trajectories[pid].append(final_state)

        return trajectories, self.get_payoffs()

    def get_payoffs(self):
        """Episode payoffs: +1 per player on the team that passed A."""
        return self.game.get_payoffs()

    def _extract_state(self, state):
        """States are raw dicts by design; no tensor encoding here."""
        return state

    def _attach_oracle(self, state):
        state['last_oracle'] = self.last_oracle
        state['current_oracle'] = self.current_oracle

    def _calculate_oracle_value(self):
        """Difference of the teams' estimated steps-to-empty hands."""
        players = getattr(self.game, 'players', None)
        if not players or len(players) < self.num_players:
            return 0
        steps = [estimate_min_steps(cards2str(p.current_hand),
                                    self.game.cur_rank)
                 for p in players]
        team0 = min(steps[0], steps[2])
        team1 = min(steps[1], steps[3])
        return team0 - team1

    # ------------------------------------------------------------------
    # Information views
    # ------------------------------------------------------------------

    def get_unperfect_information(self):
        """Public information from the current player's point of view."""
        if not hasattr(self.game, 'round'):
            return {}
        current = self.game.round.current_player
        players = self.game.players
        positions = {
            'up': (current + 1) % self.num_players,
            'teammate': (current + 2) % self.num_players,
            'down': (current + 3) % self.num_players,
        }

        info = {
            'current_player': current,
            'current_hand': cards2str(players[current].current_hand),
            'current_rank': self.game.cur_rank,
            'play_team': self.game.round.play_team,
            'trace': self.game.round.trace,
            'remain_cards': getattr(self.game.round, 'remain_cards', {}),
            'last_oracle': self.last_oracle,
            'current_oracle': self.current_oracle,
        }

        played_by_pos = {name: [] for name in positions}
        all_played = []
        for pid, action in self.game.round.trace:
            if action[0] == 'PASS':
                continue
            all_played.extend(action[2])
            for name, pos in positions.items():
                if pid == pos:
                    played_by_pos[name].extend(action[2])
        info['played_cards'] = played_by_pos
        info['all_played_cards'] = all_played
        info['remaining_cards_count'] = {
            name: len(players[pos].current_hand)
            for name, pos in positions.items()}
        info['unseen_cards'] = self._unseen_cards(current, all_played)
        return info

    def _unseen_cards(self, current_player_id, all_played_cards):
        """Full double deck minus played cards minus the own hand."""
        deck = []
        for _ in range(2):
            for suit in SUITS:
                for rank in CARD_RANK[:-2]:
                    deck.append(suit + rank)
            deck.append('SB')
            deck.append('HR')
        for card in all_played_cards:
            if card in deck:
                deck.remove(card)
        own = cards2str(self.game.players[current_player_id].current_hand)
        for card in own:
            if card in deck:
                deck.remove(card)
        return sorted(deck)

    def get_perfect_information(self):
        """Everything in the public view plus all hidden hands."""
        info = self.get_unperfect_information()
        if not info:
            return info
        current = self.game.round.current_player
        players = self.game.players
        positions = {
            'up': (current + 1) % self.num_players,
            'teammate': (current + 2) % self.num_players,
            'down': (current + 3) % self.num_players,
        }
        info['other_hand_cards'] = {
            name: cards2str(players[pos].current_hand)
            for name, pos in positions.items()}
        info['other_legal_actions'] = {
            name: self.game.judger.playable_actions_from_hand(
                players[pos].current_hand, self.game.cur_rank)
            for name, pos in positions.items()}
        steps = {name: estimate_min_steps(
            cards2str(players[pos].current_hand), self.game.cur_rank)
            for name, pos in positions.items()}
        steps['current'] = estimate_min_steps(
            cards2str(players[current].current_hand), self.game.cur_rank)
        info['min_steps_estimation'] = steps
        info['all_players_hands'] = {
            pid: cards2str(players[pid].current_hand)
            for pid in range(self.num_players)}
        return info
