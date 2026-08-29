"""Guandan round: deals, tribute phase, trick flow and public state."""

import logging

from guandan_rlcard.constants import (
    CARD_RANK,
    CARD_RANK_INDEX,
    REMAIN_RANK_INDEX,
    SUIT_INDEX,
)
from guandan_rlcard.game.card_utils import (
    card_from_action,
    cards2str,
    cards_from_tribute_actions,
    str_card_sort_key,
    tribute_card_cmp,
)
from guandan_rlcard.game.dealer import GuandanDealer

logger = logging.getLogger('guandan')


def _fresh_remain_cards():
    """Per-suit rank counters of unseen cards (jokers live in slot 13 of
    the S row for the small joker and the H row for the big joker)."""
    return {
        'S': [2] * 14,
        'H': [2] * 14,
        'C': [2] * 13 + [0],
        'D': [2] * 13 + [0],
    }


class GuandanRound:
    """Drives one episode: deals, tribute phase and trick-by-trick flow."""

    def __init__(self, np_random, played_cards, interactive_tribute=False):
        self.np_random = np_random
        self.played_cards = played_cards
        # Upstream resolves tribute synchronously through agent callbacks.
        # Cedar enables this opt-in mode so each authoritative upstream legal
        # tribute/back action can instead be submitted as a host turn.
        self.interactive_tribute = interactive_tribute
        self.tribute_state = {'status': 'none', 'mode': 'none'}
        self.trace = []
        self.last_trick_plays = []
        self.last_trick_winner = None
        self.greater_player = None
        self.dealer = GuandanDealer(self.np_random)
        self.deck_str = cards2str(self.dealer.deck)
        self.global_turn_count = 0
        self.turn_winner = -1
        self.last_non_pass_player = None

    # ------------------------------------------------------------------
    # Episode / deal setup
    # ------------------------------------------------------------------

    def initiate_episode(self, players):
        """Start an episode (a sequence of deals up to passing level A)."""
        self.players = players
        self.game_over = False        # current deal finished
        self.episode_over = False     # whole episode finished
        self.dealer.determine_teams(players)
        self.first_game = True
        self.team0_rank = 0
        self.team1_rank = 0
        self.play_team = 0
        self.rank_list = [self.team0_rank, self.team1_rank]
        self.cur_rank = self.rank_list[self.play_team]
        # Finish order of the current deal; result[0] is the first out.
        self.result = [-1, -1, -1, -1]
        self.win_count = 0
        self.out_flag = [False, False, False, False]
        self.initiate_game(players)

    def initiate_game(self, players):
        """Start one deal: deal cards and run the tribute phase."""
        self.dealer.deal_cards(players)
        self.pass_num = [0, 0, 0, 0]
        self.my_pass_num = [0, 0, 0, 0]

        logger.info('Playing team %s level %s (level card H%s)',
                    self.play_team, self.cur_rank, CARD_RANK[self.cur_rank])

        tribute_pending = False
        if self.first_game:
            self.first_game = False
            self.current_player = self.np_random.randint(0, 4)
            self.tribute_state = {'status': 'none', 'mode': 'none'}
        elif self.interactive_tribute:
            tribute_pending = self._start_interactive_tribute(players)
        else:
            self._tribute_phase(players)

        # Keep the prior result while an interactive tribute exchange is in
        # progress; it is cleared only after the final return card.
        if not tribute_pending:
            self.result = [-1, -1, -1, -1]
        self.greater_player = None
        self.remain_cards = _fresh_remain_cards()
        self.last_trick_plays = []
        self.last_trick_winner = None
        self._build_public(players)

    # ------------------------------------------------------------------
    # Tribute phase (进贡 / 还贡)
    # ------------------------------------------------------------------

    def _tribute_phase(self, players):
        """Run resist check, tribute and tribute-back; set the leader."""
        if self._check_resist(self.result, players):
            logger.info('Team %s resists the tribute.', (self.play_team + 1) % 2)
            self.current_player = self.result[0]
            return
        level_rank = CARD_RANK[self.cur_rank]
        if self._check_both_down(self.result[-1], self.result[-2]):
            self._double_tribute(players, level_rank)
        else:
            self._single_tribute(players, level_rank)

    # ------------------------------------------------------------------
    # Optional staged tribute API for interactive hosts
    # ------------------------------------------------------------------

    @property
    def tribute_pending(self):
        """Whether the opt-in interactive tribute phase awaits an action."""
        return self.tribute_state.get('status') in (
            'selecting_tribute', 'selecting_return')

    def _start_interactive_tribute(self, players):
        """Prepare tribute using the same upstream rule helpers.

        Returns True when input is required.  Counter-tribute is resolved
        immediately, just like the native synchronous path.
        """
        result = list(self.result)
        first_out = result[0]
        both_down = self._check_both_down(result[-1], result[-2])
        if both_down:
            payers = [result[-1], result[-2]]
            receivers = [result[0], result[1]]
            mode = 'double'
        else:
            payers = [result[-1]]
            receivers = [result[0]]
            mode = 'single'

        if self._check_resist(result, players):
            logger.info('Team %s resists the tribute.',
                        (self.play_team + 1) % 2)
            self.current_player = first_out
            self.tribute_state = {
                'status': 'countered',
                'mode': mode,
                'payer_ids': payers,
                'receiver_ids': receivers,
                'countered': True,
                'tributes': [],
                'returns': [],
                'leader_id': first_out,
            }
            return False

        self.current_player = payers[0]
        self.tribute_state = {
            'status': 'selecting_tribute',
            'mode': mode,
            'payer_ids': payers,
            'receiver_ids': receivers,
            'pending_payer_ids': list(payers),
            'pending_receiver_ids': [],
            'countered': False,
            'tributes': [],
            'returns': [],
            'first_out_id': first_out,
            'leader_id': None,
        }
        return True

    def available_tribute_actions(self, player):
        """Return upstream-authoritative actions for the staged phase."""
        if player.player_id != self.current_player:
            return []
        level_rank = CARD_RANK[self.cur_rank]
        status = self.tribute_state.get('status')
        if status == 'selecting_tribute' and player.player_id in \
                self.tribute_state.get('pending_payer_ids', []):
            return player.legal_tribute_actions(level_rank)
        if status == 'selecting_return' and player.player_id in \
                self.tribute_state.get('pending_receiver_ids', []):
            return player.legal_back_actions(level_rank)
        return []

    def proceed_tribute(self, player, action):
        """Apply one staged tribute/back action through upstream methods.

        Returns True after the exchange is complete and normal play may
        continue.  Validation is exact against the player's upstream legal
        action list.
        """
        legal = self.available_tribute_actions(player)
        if action not in legal:
            raise ValueError('Action is not legal in the tribute phase.')

        status = self.tribute_state['status']
        if status == 'selecting_tribute':
            card = player.execute_tribute(action)
            self.tribute_state['tributes'].append({
                'payer_id': player.player_id,
                'action': list(action),
                'card': card,
            })
            self.tribute_state['pending_payer_ids'].remove(player.player_id)
            if self.tribute_state['pending_payer_ids']:
                self.current_player = \
                    self.tribute_state['pending_payer_ids'][0]
                return False
            self._assign_interactive_tributes(players=self.players)
            return False

        card = player.execute_back(action)
        offering = next(
            item for item in self.tribute_state['tributes']
            if item.get('receiver_id') == player.player_id)
        payer = offering['payer_id']
        self.players[payer].add_card(card)
        self.tribute_state['returns'].append({
            'receiver_id': player.player_id,
            'payer_id': payer,
            'action': list(action),
            'card': card,
        })
        self.tribute_state['pending_receiver_ids'].remove(player.player_id)
        if self.tribute_state['pending_receiver_ids']:
            self.current_player = \
                self.tribute_state['pending_receiver_ids'][0]
            return False
        self._finish_interactive_tribute()
        return True

    def _assign_interactive_tributes(self, players):
        """Assign collected cards with the native single/double rules."""
        offerings = self.tribute_state['tributes']
        receivers = self.tribute_state['receiver_ids']
        if self.tribute_state['mode'] == 'double':
            first, second = offerings
            comparison = tribute_card_cmp(
                first['card'], second['card'], CARD_RANK[self.cur_rank])
            if comparison >= 0:
                transfers = [(first, receivers[0]), (second, receivers[1])]
                leader = (first['payer_id'] if comparison > 0
                          else (receivers[0] + 1) % 4)
            else:
                transfers = [(second, receivers[0]), (first, receivers[1])]
                leader = second['payer_id']
            self.tribute_state['tie'] = comparison == 0
        else:
            transfers = [(offerings[0], receivers[0])]
            leader = offerings[0]['payer_id']
            self.tribute_state['tie'] = False

        for offering, receiver in transfers:
            offering['receiver_id'] = receiver
            players[receiver].add_card(offering['card'])
        self.tribute_state['leader_id'] = leader
        self.tribute_state['pending_receiver_ids'] = list(receivers)
        self.tribute_state['status'] = 'selecting_return'
        self.current_player = receivers[0]

    def _finish_interactive_tribute(self):
        self.tribute_state['status'] = 'complete'
        self.current_player = self.tribute_state['leader_id']
        self.result = [-1, -1, -1, -1]
        self.greater_player = None
        self._build_public(self.players)

    def _double_tribute(self, players, level_rank):
        """Both losers pay tribute (双下): bigger card goes to the winner.

        When the two tribute cards tie, this implementation simplifies
        the official rule: the first-out player takes the last player's
        card (no suit choice) and the player after the first-out leads.
        """
        loser1, loser2 = self.result[-1], self.result[-2]
        winner1, winner2 = self.result[0], self.result[1]

        action1 = players[loser1].tribute_act(
            players[loser1].legal_tribute_actions(level_rank), level_rank)
        action2 = players[loser2].tribute_act(
            players[loser2].legal_tribute_actions(level_rank), level_rank)
        card1, card2 = cards_from_tribute_actions(action1, action2)
        comparison = tribute_card_cmp(card1, card2, level_rank)

        if comparison >= 0:
            transfers = [(loser1, winner1, card1, action1),
                         (loser2, winner2, card2, action2)]
            leader = loser1 if comparison > 0 else (winner1 + 1) % 4
        else:
            transfers = [(loser2, winner1, card2, action2),
                         (loser1, winner2, card1, action1)]
            leader = loser2

        tribute_result = [[payer, receiver, action[2][0]]
                          for payer, receiver, _, action in transfers]
        for payer, receiver, card, tribute_action in transfers:
            players[receiver].add_card(card)
            back_action = players[receiver].back_act(
                players[receiver].legal_back_actions(level_rank),
                level_rank, tribute_result)
            players[payer].add_card(card_from_action(back_action))
            logger.info('Player %s pays tribute %s to player %s, gets back %s',
                        payer, tribute_action[2][0], receiver,
                        back_action[2][0])
        self.current_player = leader

    def _single_tribute(self, players, level_rank):
        """The single last-place player pays tribute to the first out."""
        loser, winner = self.result[-1], self.result[0]
        action = players[loser].tribute_act(
            players[loser].legal_tribute_actions(level_rank), level_rank)
        tribute_result = [[loser, winner, action[2][0]]]

        players[winner].add_card(card_from_action(action))
        back_action = players[winner].back_act(
            players[winner].legal_back_actions(level_rank),
            level_rank, tribute_result)
        players[loser].add_card(card_from_action(back_action))
        logger.info('Player %s pays tribute %s to player %s, gets back %s',
                    loser, action[2][0], winner, back_action[2][0])
        self.current_player = loser

    def _check_resist(self, result, players):
        """Resist (抗贡): the tribute payers jointly hold both big jokers.

        For a double-down this is inferred from the winners' fresh hands:
        if neither winner holds a big joker, both must be with the losing
        pair. For a single-down the last player must hold both.
        """
        if self._check_both_down(result[-2], result[-1]):
            winners_have_none = (
                players[result[0]].current_hand[-1].suit != 'RJ'
                and players[result[1]].current_hand[-1].suit != 'RJ')
            return winners_have_none
        last_hand = players[result[-1]].current_hand
        return sum(1 for card in last_hand[-2:] if card.suit == 'RJ') == 2

    @staticmethod
    def _check_both_down(loser1, loser2):
        """Whether the last two finishers are teammates (双下)."""
        return (loser1 + loser2) % 2 == 0

    # ------------------------------------------------------------------
    # Public state
    # ------------------------------------------------------------------

    def _build_public(self, players):
        self.public = {
            'deck': self.deck_str,
            'play_team': self.play_team,
            'trace': self.trace,
            'rank_list': self.rank_list,
            'played_cards': {n: [] for n in range(len(players))},
            'greaterPos': -1,
            'greaterAction': [],
            'remain_cards': self.remain_cards,
            'pass_num': self.pass_num,
            'my_pass_num': self.my_pass_num,
            'played_actions': [{}, {}, {}, {}],
            'last_trick_plays': self.last_trick_plays,
            'last_trick_winner': self.last_trick_winner,
            'bomb_history': [],
            'last_actions': {i: None for i in range(len(players))},
            'rank_card': 'H' + CARD_RANK[self.cur_rank],
            'finished_players': [],
            'recent_plays': {0: None, 1: None, 2: None, 3: None},
            'jiefeng_status': {
                'out_flags': self.out_flag.copy(),
                'real_out': [players[i].real_out
                             for i in range(len(players))],
            },
        }

    def update_public(self, action):
        """Record an executed action into trace and public counters."""
        self.trace.append([self.current_player, action])
        self.public['rank_list'] = self.rank_list
        self.public['play_team'] = self.play_team
        self.public['pass_num'] = self.pass_num
        self.public['my_pass_num'] = self.my_pass_num

        # Per-player history of (combo type -> key ranks played).
        if action[0] == 'PASS':
            combo_type = (self.greater_player.played_action[0]
                          if self.greater_player else 'None')
            key_rank = 'PASS'
        else:
            combo_type, key_rank = action[0], action[1]
        per_player = self.public['played_actions'][self.current_player]
        per_player.setdefault(combo_type, []).append(key_rank)

        if action[0] != 'PASS':
            for card in action[2]:
                self.remain_cards[card[0]][REMAIN_RANK_INDEX[card[1]]] -= 1
            self.public['remain_cards'] = self.remain_cards

            for card in action[2]:
                if card == 'SB':
                    self.played_cards[self.current_player][13][0] += 1
                elif card == 'HR':
                    self.played_cards[self.current_player][14][0] += 1
                else:
                    self.played_cards[self.current_player][
                        CARD_RANK_INDEX[card[1]]][SUIT_INDEX[card[0]]] += 1
            played = self.public['played_cards'][self.current_player]
            played.extend(action[2])
            self.public['played_cards'][self.current_player] = \
                sorted(played, key=str_card_sort_key)

        self.public['greaterPos'] = (self.greater_player.player_id
                                     if self.greater_player else -1)
        self.public['greaterAction'] = (self.greater_player.played_action
                                        if self.greater_player else [])

        self.public['last_trick_plays'] = self.last_trick_plays
        self.public['last_trick_winner'] = self.last_trick_winner

        bomb_history = [(pid, act) for pid, act in self.trace
                        if act[0] in ('Bomb', 'StraightFlush')]
        self.public['bomb_history'] = bomb_history[-5:]

        last_actions = {pid: None for pid in range(4)}
        recent_plays = {pid: None for pid in range(4)}
        for pid, act in reversed(self.trace):
            if recent_plays[pid] is None:
                recent_plays[pid] = act
            if last_actions[pid] is None and act[0] != 'PASS':
                last_actions[pid] = act
        self.public['last_actions'] = last_actions
        self.public['recent_plays'] = recent_plays

        self.public['rank_card'] = \
            'H' + CARD_RANK[self.rank_list[self.play_team]]
        self.public['finished_players'] = self.result[:self.win_count]

    def get_last_trick_info(self):
        """Plays and winner of the previous trick (heuristic on trace)."""
        if not self.trace:
            return [], None
        trick_start = 0
        for i in range(len(self.trace) - 2, -1, -1):
            curr_player, _ = self.trace[i]
            next_player, _ = self.trace[i + 1]
            # A jump in seat order suggests a new trick started here.
            # Approximate: finished players are skipped in seat order.
            if next_player != (curr_player + 1) % 4:
                trick_start = i + 1
                break
        last_trick_plays = self.trace[trick_start:]
        last_trick_winner = None
        for player_id, action in last_trick_plays:
            if action[0] != 'PASS':
                last_trick_winner = player_id
        return last_trick_plays, last_trick_winner

    # ------------------------------------------------------------------
    # Trick flow
    # ------------------------------------------------------------------

    def proceed_round(self, player, action):
        """Apply one action and update trick bookkeeping.

        Returns:
            tuple: (greater_player, round_completed). ``round_completed``
            is an approximation flagged after three consecutive passes.
        """
        # Beating the combo of a freshly finished player interrupts the
        # wind-follow (接风): their teammate will not get the free lead.
        if self.greater_player \
                and self.out_flag[self.greater_player.player_id] \
                and not self.greater_player.real_out \
                and action[0] != 'PASS':
            self.greater_player.real_out = True

        teammate = (player.player_id + 2) % 4
        if action[0] == 'PASS':
            self.pass_num[player.player_id] += 1
            self.pass_num[teammate] += 1
            self.my_pass_num[player.player_id] += 1
        else:
            self.pass_num[player.player_id] = 0
            self.pass_num[teammate] = 0
            self.my_pass_num[player.player_id] = 0

        # Refresh "previous trick" info when a trick boundary is detected.
        if len(self.trace) >= 3 and action[0] != 'PASS':
            if all(t[1][0] == 'PASS' for t in self.trace[-3:]):
                self.last_trick_plays, self.last_trick_winner = \
                    self.get_last_trick_info()

        self.greater_player = player.play(action, self.greater_player)
        self.update_public(action)

        if action[0] != 'PASS':
            self.last_non_pass_player = player.player_id

        round_completed = False
        consecutive_passes = 0
        last_non_pass_player = None
        for pid, act in reversed(self.trace):
            if act[0] == 'PASS':
                consecutive_passes += 1
            else:
                last_non_pass_player = pid
                break
        if consecutive_passes >= 3 and last_non_pass_player is not None:
            round_completed = True
            self.global_turn_count += 1
            self.turn_winner = last_non_pass_player

        if len(player.current_hand) == 0 and \
                player.player_id not in self.result:
            self.result[self.win_count] = player.player_id
            self.win_count += 1
            if self.win_count == 2 and \
                    (self.result[0] + self.result[1]) % 2 == 0:
                # RULE: a 1st+2nd finish by one team (双上) ends the deal
                # immediately; the original kept playing until a third
                # player finished. Remaining seats fill in seat order.
                remaining = [p.player_id for p in self.players
                             if p.player_id not in self.result[:2]]
                self.result[2], self.result[3] = remaining
                self.game_over = True
            elif self.win_count == 3:
                for p in self.players:
                    if p.player_id not in self.result:
                        self.result[3] = p.player_id
                        break
                self.game_over = True

        return self.greater_player, round_completed

    def reset(self):
        """Reset per-deal state (called between deals of one episode)."""
        self.out_flag = [False, False, False, False]
        self.win_count = 0
        self.game_over = False
        self.trace = []
        self.greater_player = None
        self.deck_str = cards2str(self.dealer.deck)
        self.global_turn_count = 0
        self.turn_winner = -1
        self.last_non_pass_player = None
        self.last_trick_plays = []
        self.last_trick_winner = None
        self.remain_cards = _fresh_remain_cards()
        self.pass_num = [0, 0, 0, 0]
        self.my_pass_num = [0, 0, 0, 0]
        self._build_public(self.players)
