"""Score-only teams exercise persisted regular/invite tables, never production DB."""
from copy import deepcopy
from unittest.mock import patch

from app import framework, invites, liars_dice_setup as setup, npc_controller
from app.npc_personas import NpcPersona
from app.games import get_game
from app.games import liars_dice_party as party
from app.liars_dice_rush import npc_wants_rush
from tests import test_multiplayer_games as support
from tests.test_liars_dice_teams import OPTIONS, expire, seat


class ScoreOnlyTeamsTests(support.MultiplayerGameTestCase):
    def create(self, count=4, *, invite=False, **options):
        people = support.participants(count)
        if invite:
            room = invites.create_invite('liars_dice', 'human', 'human-1',
                target_player_count=count, game_options={**OPTIONS, 'rush_open':False, **options})
            for p in people[1:]:
                room = invites.join_invite(room['invite_code'], p['role'], p['player_id'])
        else:
            room = framework.create_room('liars_dice', 'human_first', 'human', 'human-1',
                opponent_id='ai-1', ordered_participants=people,
                target_player_count=count, game_options={**OPTIONS, 'rush_open':False, **options})
        # Deliberately adjacent teammates. Team-slot order is the reverse of
        # actual seats, so a hidden dependency on team lists is detectable.
        ids = [p['player_id'] for p in room['participants']]
        half = count // 2
        teams = {'red': ids[:half][::-1], 'blue': ids[half:][::-1]}
        for team, members in teams.items():
            for index, pid in enumerate(members, 1):
                room = seat(room, pid, team, index)
        room = expire(room)
        def shuffled_seats(values):
            if values:
                values[:] = values[:half][::-1] + values[half:][::-1]
        with patch('app.invites.secrets.SystemRandom.shuffle', side_effect=shuffled_seats):
            if invite:
                room = invites.start_invite(room['room_id'], 'human', 'human-1')
            else:
                room = setup.start_regular(room['room_id'], 'human', 'human-1')
        room = framework.get_room(room['room_id'])
        self.assertEqual(room['board_state']['teams'], teams)
        # The final physical seats, not pregame joins or team-list order, govern turns.
        seated = [p['player_id'] for p in sorted(room['participants'], key=lambda p: p['seat_index'])]
        self.assertEqual(room['board_state']['participant_order'], seated)
        self.assertEqual(room['board_state']['team_rules_version'], 2)
        self.assertNotIn('team_next_speakers', room['board_state'])
        return room

    def act(self, room, move, pid=None):
        pid = pid or room['current_player_id']
        return framework.play_move(room['room_id'], self.role_for(pid), pid, move,
                                   expected_revision=room['revision'])

    def test_regular_and_invite_every_seat_keeps_a_turn_and_own_cup(self):
        game = get_game('liars_dice')
        for invite in (False, True):
            for count in (4, 6):
                with self.subTest(invite=invite, count=count):
                    room = self.create(count, invite=invite)
                    state = room['board_state']
                    order = state['participant_order']
                    teams = deepcopy(state['teams'])
                    state['dice_by_player'] = {pid: [i+1]*5 for i, pid in enumerate(order)}
                    room = self.replace_state(room, state, order[0])
                    self.assertEqual(game.total_dice(state), count * 7)
                    for p in room['participants']:
                        view = framework.project_room_for_viewer(room, p['player_id'])
                        self.assertEqual(view['private_state']['dice'], state['dice_by_player'][p['player_id']])
                        self.assertEqual(view['private_state']['cup_owner_player_id'], p['player_id'])
                        self.assertNotIn('dice_by_player', view['board_state'])
                        self.assertNotIn('terminal_dice', view['board_state'])
                    # Two circuits, including wraps; no teammate can steal a turn.
                    for index in range(count * 2):
                        pid = order[index % count]
                        self.assertEqual(room['current_player_id'], pid)
                        move = {'action': 'bid', 'quantity': (3*count+1)//2 + index, 'face': 2}
                        with self.assertRaises(framework.DuelError):
                            self.act(room, move, order[(index+1) % count])
                        room = self.act(room, move)
                        self.assertEqual(room['board_state']['teams'], teams)
                    self.assertEqual(room['current_player_id'], order[0])

    def test_opening_and_upper_bounds_use_people_not_teams(self):
        game = get_game('liars_dice')
        for count in (4, 6):
            for literal in (False, True):
                room = self.create(count, literal_dice=literal)
                state = room['board_state']
                maximum = count * (5 if literal else 7)
                self.assertEqual(game.total_dice(state), maximum)
                for face, exact, minimum in ((2, False, count*3//2), (2, True, count), (1, False, count)):
                    self.assertFalse(game._valid_bid(state, minimum-1, face, exact))
                    self.assertTrue(game._valid_bid(state, minimum, face, exact))
                    self.assertTrue(game._valid_bid(state, maximum, face, exact))
                    self.assertFalse(game._valid_bid(state, maximum+1, face, exact))

    def test_rush_expiry_keeps_adjacent_teammates_in_seat_order(self):
        game = get_game('liars_dice')
        for invite in (False, True):
            for count in (4, 6):
                with self.subTest(invite=invite, count=count):
                    room = self.create(count, invite=invite, rush_open=True)
                    order = room['turn_order']
                    room = self.replace_state(room, room['board_state'], order[0])
                    self.assertEqual(game.team_for(room['board_state'], order[0]),
                                     game.team_for(room['board_state'], order[1]))
                    for index, pid in enumerate(order):
                        self.assertEqual(room['current_player_id'], pid)
                        room = self.act(room, {'action':'bid', 'quantity':count*3//2+index, 'face':2})
                        next_pid = order[(index+1) % count]
                        self.assertTrue(framework.participant_can_act(room, next_pid))
                        with self.assertRaises(framework.DuelError):
                            self.act(room, {'action':'bid', 'quantity':count*3//2+index+1, 'face':2})
                        revision = room['revision']
                        with patch('app.liars_dice_rush.time.time', return_value=room['board_state']['rush_until']):
                            room = framework.get_room(room['room_id'])
                            self.assertEqual(room['revision'], revision+1)
                            self.assertEqual(framework.get_room(room['room_id'])['revision'], revision+1)
                        self.assertEqual(room['current_player_id'], next_pid)
                        self.assertEqual(room['turn_order'], order)
                    # The first two seats share a team; normal challenge after expiry is legal.
                    room = self.act(room, {'action':'bid', 'quantity':count*3//2+count, 'face':2})
                    with patch('app.liars_dice_rush.time.time', return_value=room['board_state']['rush_until']):
                        room = framework.get_room(room['room_id'])
                    room = self.act(room, {'action':'challenge'})
                    self.assertEqual(sum(room['board_state']['team_scores'].values()), -1)

    def test_same_team_rush_both_verdicts_and_companion_charge_once(self):
        game = get_game('liars_dice')
        for holds in (False, True):
            room = self.create(rush_open=True)
            state = room['board_state']
            bidder, challenger = state['participant_order'][:2]
            state['dice_by_player'] = {pid:[2,2,3,4,5] for pid in state['participant_order']}
            room = self.replace_state(room, state, bidder)
            room = self.act(room, {'action':'bid', 'quantity':8 if holds else 9, 'face':2})
            room = self.act(room, {'action':'rush_open'}, challenger)
            result = room['board_state']['last_round_result']
            self.assertEqual(result['actual_count'], 8)
            self.assertEqual(len(result['penalties']), 1)
            self.assertEqual(room['board_state']['team_scores'], {'red':-1,'blue':0})
            loser = challenger if holds else bidder
            room = self.act(room, {'action':'acknowledge_round'})
            self.assertEqual(room['current_player_id'], loser)
        for companion in (2, 3):
            room = self.create(rush_open=True, cup_penalty=True, extreme_penalty=True, companion_penalty=True)
            state = room['board_state']; bidder, challenger = state['participant_order'][:2]
            state['dice_by_player'] = {pid:[2]*5 for pid in state['participant_order']}
            room = self.replace_state(room, state, bidder)
            room = self.act(room, {'action':'bid', 'quantity':28, 'face':6})
            with patch.object(game, '_roll', return_value=[companion]):
                room = self.act(room, {'action':'rush_open'}, challenger)
            self.assertEqual(room['board_state']['team_scores'], {'red':-4,'blue':0 if companion == 2 else -4})
            self.assertEqual(len(room['board_state']['last_round_result']['penalties']), 1 if companion == 2 else 2)

    def test_rush_npc_estimate_counts_individual_cups_without_reading_other_dice(self):
        game = get_game('liars_dice')
        room = self.create(rush_open=True)
        state = room['board_state']; bidder, challenger = state['participant_order'][:2]
        state['dice_by_player'] = {pid:[2]*5 for pid in state['participant_order']}
        room = self.replace_state(room, state, bidder)
        room = self.act(room, {'action':'bid', 'quantity':6, 'face':6})
        state = room['board_state']; actor = {'player_id':challenger}
        self.assertFalse(npc_wants_rush(game, state, actor))
        for pid in state['participant_order']:
            if pid != challenger:
                state['dice_by_player'][pid] = [6]*5
        self.assertFalse(npc_wants_rush(game, state, actor))
        # Explicitly persisted legacy table still estimates two shared cups.
        state.pop('team_rules_version')
        state['dice_by_player'][game.cup_owner(state, challenger)] = [2]*5
        self.assertTrue(npc_wants_rush(game, state, actor))

    def test_same_team_challenge_both_verdicts_one_charge_loser_starts(self):
        game = get_game('liars_dice')
        for invite in (False, True):
            for holds in (False, True):
                room = self.create(invite=invite)
                state = room['board_state']
                bidder, challenger, *others = state['participant_order']
                # Exactly six: both teammates and both opponents must count.
                state['dice_by_player'] = {
                    bidder: [2,2,3,4,5], challenger: [2,2,3,4,5],
                    others[0]: [2,3,3,4,5], others[1]: [2,3,3,4,5]}
                room = self.replace_state(room, state, bidder)
                room = self.act(room, {'action':'bid', 'quantity':6 if holds else 7, 'face':2})
                actor = next(p for p in room['participants'] if p['player_id'] == challenger)
                legal = game.npc_legal_actions(room['board_state'], actor, room['participants'])
                self.assertIn({'action':'challenge'}, legal)
                request, mapping = npc_controller._decision_request(room, challenger, legal, temporary=True)
                self.assertEqual(request.private_state['dice'], state['dice_by_player'][challenger])
                self.assertNotIn('dice_by_player', request.public_state)
                self.assertIn('同队可连座及互相质疑', request.game_rules)
                for move in mapping.values():
                    game.validate_action(room['board_state'], move, actor)
                revision = room['revision']
                room = self.act(room, {'action':'challenge'})
                result = room['board_state']['last_round_result']
                loser = challenger if holds else bidder
                self.assertEqual(result['actual_count'], 6)
                self.assertEqual(result['loser_player_id'], loser)
                self.assertEqual(len(result['revealed_dice_by_player']), 4)
                self.assertEqual(len(result['penalties']), 1)
                self.assertEqual(room['board_state']['score_half_units'], {'red':-2, 'blue':0})
                with self.assertRaises(framework.DuelError):
                    framework.play_move(room['room_id'], actor['role'], challenger,
                        {'action':'challenge'}, expected_revision=revision)
                room = self.act(room, {'action':'acknowledge_round'})
                self.assertEqual(room['current_player_id'], loser)
                self.assertEqual(len(room['board_state']['dice_by_player']), 4)
                self.assertEqual(room['board_state']['dice_counts'], {pid:5 for pid in state['participant_order']})
                room = self.act(room, {'action':'end_session'}, 'human-1')
                self.assertEqual(room['result']['winner_team_ids'], ['blue'])
                self.assertEqual(set(room['result']['winner_player_ids']), set(others))
                self.assertEqual(room['result']['team_scores'], {'red':-1, 'blue':0})

    def test_same_team_companion_caps_once_and_other_team_separate(self):
        game = get_game('liars_dice')
        for companion_seat in (2, 3):
            room = self.create(cup_penalty=True, extreme_penalty=True, companion_penalty=True)
            state = room['board_state']
            state['dice_by_player'] = {pid:[2]*5 for pid in state['participant_order']}
            room = self.replace_state(room, state, 'human-1')
            room = self.act(room, {'action':'bid','quantity':10,'face':6})
            with patch.object(game, '_roll', return_value=[companion_seat]):
                room = self.act(room, {'action':'challenge'})
            result = room['board_state']['last_round_result']
            self.assertEqual(room['board_state']['team_scores'], {'red':-4,'blue':0 if companion_seat == 2 else -4})
            self.assertEqual(len(result['penalties']), 1 if companion_seat == 2 else 2)
            self.assertEqual(result['penalties'][0]['raw_multiplier'], 8 if companion_seat == 2 else 4)

    def test_independent_reroll_and_same_team_surrender_retains_all_cups(self):
        game = get_game('liars_dice')
        room = self.create(allow_surrender=True, surrender_no_reroll=True)
        state = room['board_state']
        state['dice_by_player'] = {pid:[1,2,3,4,5] for pid in state['participant_order']}
        bidder, teammate = state['participant_order'][:2]
        before = deepcopy(state['dice_by_player'])
        room = self.replace_state(room, state, bidder)
        with patch.object(game, '_roll', return_value=[2]*5):
            room = self.act(room, {'action':'reroll'})
        self.assertEqual(room['board_state']['dice_by_player'][teammate], before[teammate])
        self.assertTrue(party.can_reroll(room['board_state'], teammate))
        self.assertFalse(party.can_reroll(room['board_state'], bidder))
        room = self.act(room, {'action':'bid','quantity':6,'face':2})
        before = deepcopy(room['board_state']['dice_by_player'])
        room = self.act(room, {'action':'surrender'})
        self.assertEqual(room['board_state']['score_half_units'], {'red':-1,'blue':0})
        self.assertEqual(room['board_state']['last_round_result']['revealed_dice_by_player'], {})
        room = self.act(room, {'action':'acknowledge_round'})
        self.assertEqual(room['board_state']['dice_by_player'], before)
        self.assertEqual(room['current_player_id'], teammate)

    def test_old_saved_versions_preserve_cups_scores_rules_and_turns(self):
        game = get_game('liars_dice')
        for version in (None, 1):
            room = self.create()
            state = room['board_state']
            state.pop('team_rules_version')
            if version is not None:
                state['team_rules_version'] = version
            state['dice_by_player'] = {members[0]: [index+2]*5
                for index, members in enumerate(state['teams'].values())}
            state['team_next_speakers'] = {team: members[0] for team, members in state['teams'].items()}
            state['team_scores'] = {'red':-2, 'blue':-1}
            state['scores'] = {pid: state['team_scores'][team] for team, members in state['teams'].items() for pid in members}
            state['score_half_units'] = {'red':-4, 'blue':-2}
            before = deepcopy(state)
            bidder = state['teams']['red'][0]
            room = self.replace_state(room, state, bidder)
            self.assertEqual(room['board_state'], before)
            self.assertEqual(game.total_dice(state), 14)
            self.assertTrue(game._valid_bid(state, 3, 2))
            view = framework.project_room_for_viewer(room, state['teams']['red'][1])
            self.assertEqual(view['private_state']['dice'], state['dice_by_player'][bidder])
            self.assertEqual(view['board_state']['team_rules_version'], 1)
            self.assertIn('每队共用一盅', game.room_rules(state))
            room = self.act(room, {'action':'bid', 'quantity':3, 'face':6})
            self.assertEqual(room['current_player_id'], state['teams']['blue'][0])
            teammate = next(p for p in room['participants'] if p['player_id'] == state['teams']['red'][1])
            self.assertNotIn({'action':'challenge'}, game.npc_legal_actions(room['board_state'], teammate, room['participants']))
            room = self.act(room, {'action':'challenge'})
            self.assertEqual(room['board_state']['team_scores'], {'red':-3, 'blue':-1})
            room = self.act(room, {'action':'acknowledge_round'})
            self.assertEqual(len(room['board_state']['dice_by_player']), 2)
            self.assertEqual(room['board_state'].get('team_rules_version'), version)

    def test_system_npc_can_challenge_same_team_with_only_own_dice(self):
        game = get_game('liars_dice')
        with patch('app.games.liars_dice.random.SystemRandom.shuffle'):
            room = framework.create_room('liars_dice', 'human_first', 'human', 'human-1',
                opponent_id='ai-1', ordered_participants=support.participants(4, npc_count=2),
                game_options={'mode':'party', 'team_mode':'teams', 'rush_open':False})
        state = room['board_state']
        state['dice_by_player'] = {'human-1':[1]*5, 'ai-1':[2]*5,
                                  'npc:test-1':[3]*5, 'npc:test-2':[4]*5}
        room = self.replace_state(room, state, 'npc:test-1')
        room = self.act(room, {'action':'bid','quantity':6,'face':2})
        self.assertEqual(room['current_player_id'], 'npc:test-2')
        npc = next(p for p in room['participants'] if p['player_id'] == 'npc:test-2')
        legal = game.npc_legal_actions(room['board_state'], npc, room['participants'])
        with patch('app.npc_controller.get_persona', return_value=NpcPersona('test-2', 'NPC 2', 'test')):
            request, mapping = npc_controller._decision_request(room, npc['player_id'], legal)
        self.assertEqual(request.private_state['dice'], [4]*5)
        self.assertNotIn('dice_by_player', request.public_state)
        self.assertIn({'action':'challenge'}, mapping.values())
        room = self.act(room, {'action':'challenge'})
        self.assertEqual(room['board_state']['team_scores'], {'red':0, 'blue':-1})
