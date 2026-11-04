"""Final party standings use the existing ledger; all persistence is temporary."""
import json
import random
from copy import deepcopy
from unittest.mock import patch

from app import database, framework, full_state
from app.games import GAMES
from app.games.liars_dice import LiarsDice
from tests import test_multiplayer_games as support


class PartyResultsTests(support.MultiplayerGameTestCase):
    def setUp(self):
        super().setUp()
        self.game = LiarsDice(random.Random(87))
        self.game_patch = patch.dict(GAMES, {'liars_dice': self.game})
        self.game_patch.start()
        self.addCleanup(self.game_patch.stop)

    def create(self, count=3, **options):
        return framework.create_room('liars_dice', 'human_first', 'human', 'human-1',
            opponent_id='ai-1', ordered_participants=support.participants(count),
            game_options={'mode': 'party', 'rush_open': False, **options})

    def act(self, room, action, pid=None):
        pid = pid or room['current_player_id']
        return framework.play_move(room['room_id'], self.role_for(pid), pid, action,
                                   expected_revision=room['revision'])

    def test_zero_rounds_and_bid_only_are_no_contest(self):
        for bid in (False, True):
            with self.subTest(bid=bid):
                room = self.create()
                if bid:
                    room = self.act(room, {'action': 'bid', 'quantity': 5, 'face': 2})
                before = deepcopy(room['board_state']['scores'])
                room = self.act(room, {'action': 'end_session'}, 'ai-2')
                result = room['result']
                self.assertEqual((result['outcome'], result['completed_rounds']), ('no_contest', 0))
                self.assertEqual(result['winner_player_ids'], [])
                self.assertTrue(all(row['rank'] is None for row in result['rankings']))
                self.assertEqual(result['scores'], before)
                self.assertIn('无胜负', result['result_text'])
                self.assertNotIn('和棋', result['result_text'])
                for person in room['participants']:
                    pid = person['player_id']
                    view = full_state.project_full_state(room, pid)
                    self.assertNotIn('terminal_dice', view['board_state'])
                    self.assertNotIn('dice_by_player', view['board_state'])
                    self.assertEqual(view['private_state']['dice'], room['board_state']['dice_by_player'][pid])
                with self.assertRaises(framework.DuelError):
                    self.act(room, {'action': 'end_session'}, 'ai-2')

    def test_reveal_then_unfinished_round_preserves_ledger_and_persisted_ranking(self):
        room = self.create()
        room = self.act(room, {'action': 'bid', 'quantity': 15, 'face': 6})
        room = self.act(room, {'action': 'challenge'})
        room = self.act(room, {'action': 'acknowledge_round'})
        room = self.act(room, {'action': 'bid', 'quantity': 15, 'face': 6})
        ledger = deepcopy(room['board_state']['score_half_units'])
        room = self.act(room, {'action': 'end_session'}, 'human-1')
        result = room['result']
        self.assertEqual(result['completed_rounds'], 1)
        self.assertEqual([r['rank'] for r in result['rankings']], [1, 2, 3])
        self.assertEqual([r['score'] for r in result['rankings']], [1, 0, -1])
        self.assertEqual(len(result['winner_player_ids']), 1)
        self.assertEqual(room['board_state']['score_half_units'], ledger)
        self.assertIsNone(room['winner_player_id'])
        reloaded = framework.get_room(room['room_id'])
        self.assertEqual(reloaded['result'], result)
        self.assertEqual(full_state.project_full_state(reloaded, 'ai-1')['result'], result)
        listed = next(r for r in framework.list_human_rooms('human-1') if r['room_id'] == room['room_id'])
        self.assertTrue(listed['party_dice'])
        self.assertNotIn('rankings', listed)

    def test_end_during_reveal_or_surrender_counts_only_completed_round(self):
        for action in ('challenge', 'surrender'):
            for keep in (False, True):
                with self.subTest(action=action, keep=keep):
                    room = self.create(2, allow_surrender=True, surrender_no_reroll=keep)
                    room = self.act(room, {'action': 'bid', 'quantity': 10, 'face': 6})
                    room = self.act(room, {'action': action})
                    ledger = deepcopy(room['board_state']['score_half_units'])
                    room = self.act(room, {'action': 'end_session'}, 'human-1')
                    result = room['result']
                    self.assertEqual(result['completed_rounds'], 1)
                    self.assertEqual([r['rank'] for r in result['rankings']], [1, 2])
                    self.assertEqual([r['score'] for r in result['rankings']], [0 if action == 'surrender' else 1, -.5 if action == 'surrender' else -1])
                    self.assertEqual(room['board_state']['score_half_units'], ledger)
                    view = full_state.project_full_state(room, 'ai-1')
                    self.assertNotIn('terminal_dice', view['board_state'])
                    if action == 'surrender' and keep:
                        self.assertEqual(view['board_state']['last_round_result']['revealed_dice_by_player'], {})

    def test_competition_ties_and_authoritative_half_unit_scores(self):
        # Seed valid accumulated ledgers to isolate ordering from random cup rolls.
        for units, ranks in (([-1, -1, -4, -4], [1, 1, 3, 3]),
                             ([-2, -2, -2, -2], [1, 1, 1, 1]),
                             ([-8, -2, -3, -3], [1, 2, 2, 4])):
            room = self.create(4)
            state = room['board_state']
            state['score_half_units'] = dict(zip(state['participant_order'], units))
            state['records']['human-1']['losses'] = 4
            room = self.replace_state(room, state, room['current_player_id'])
            room = self.act(room, {'action': 'end_session'}, 'human-1')
            result = room['result']
            self.assertEqual([r['rank'] for r in result['rankings']], ranks)
            self.assertEqual([r['score'] for r in result['rankings']], sorted([u / 2 for u in units], reverse=True))
            self.assertEqual(len(result['winner_player_ids']), ranks.count(1))
            self.assertEqual(result['completed_rounds'], 4)

    def test_teams_rank_once_and_members_share_winning_id_list(self):
        for count in (4, 6):
            for tied in (False, True):
                with self.subTest(count=count, tied=tied):
                    room = self.create(count, team_mode='teams', allow_surrender=True)
                    room = self.act(room, {'action': 'bid', 'quantity': 10, 'face': 6})
                    room = self.act(room, {'action': 'surrender'})
                    state = room['board_state']
                    if tied:
                        state['score_half_units'] = {'red': -1, 'blue': -1}
                        room = self.replace_state(room, state, room['current_player_id'])
                    ledger = deepcopy(state['score_half_units'])
                    room = self.act(room, {'action': 'end_session'}, 'human-1')
                    result = room['result']
                    self.assertEqual(len(result['rankings']), 2)
                    self.assertEqual(result['team_scores'], {k: v / 2 for k, v in ledger.items()})
                    for row in result['rankings']:
                        self.assertEqual(row['player_ids'], state['teams'][row['team_id']])
                        self.assertEqual(len(row['player_ids']), count // 2)
                    self.assertEqual(len(result['winner_team_ids']), 2 if tied else 1)
                    self.assertEqual(len(result['winner_player_ids']), count if tied else count // 2)
                    self.assertEqual(room['board_state']['score_half_units'], ledger)
                    for members in state['teams'].values():
                        for pid in members:
                            view = full_state.project_full_state(room, pid)
                            self.assertEqual(view['private_state']['dice'], state['dice_by_player'][pid])
                            self.assertEqual(view['board_state']['team_scores'], result['team_scores'])
                            self.assertNotIn('terminal_dice', view['board_state'])

    def test_legacy_finished_room_projects_results_without_rewriting_storage(self):
        room = self.create(2)
        room = self.act(room, {'action': 'bid', 'quantity': 10, 'face': 6})
        room = self.act(room, {'action': 'challenge'})
        room = self.act(room, {'action': 'end_session'}, 'human-1')
        expected = room['result']
        old = {k: v for k, v in expected.items() if k in ('draw', 'mode', 'reason', 'scores', 'records')}
        state = room['board_state']
        state.pop('score_half_units')
        state.pop('round_results')
        state['session_result'] = old
        with database.write_transaction() as conn:
            conn.execute('UPDATE rooms SET board_state=?, result_json=? WHERE room_id=?',
                         (json.dumps(state), json.dumps(old), room['room_id']))
        projected = framework.get_room(room['room_id'])
        self.assertEqual(projected['result'], expected)
        with database.write_transaction() as conn:
            self.assertEqual(json.loads(conn.execute('SELECT result_json FROM rooms WHERE room_id=?',
                             (room['room_id'],)).fetchone()[0]), old)

    def test_npc_and_nonparticipant_cannot_end(self):
        room = self.create()
        with self.assertRaises(ValueError):
            self.game.validate_action(room['board_state'], {'action': 'end_session'},
                {**room['participants'][0], 'participant_kind': 'system_npc'})
        with self.assertRaises(framework.DuelError):
            self.act(room, {'action': 'end_session'}, 'ai-outsider')
        with self.assertRaises(framework.DuelError):
            framework.play_move(room['room_id'], 'human', 'human-1',
                {'action': 'end_session'}, expected_revision=room['revision'], takeover=True)
        self.assertEqual(framework.get_room(room['room_id'])['status'], 'playing')


class PartyResultMcpTests(support.LiarsDiceMcpTests):
    async def test_terminal_delta_is_compact_full_state_has_rankings_and_private_cup(self):
        room = framework.create_room('liars_dice', 'human_first', 'human', 'human-1',
            opponent_id='ai-1', game_options={'mode': 'party'}, require_confirmations=False)

        async def call(**body):
            response = await self.client.post('/mcp/play', json={
                'player_id': 'ai-1', 'room_id': room['room_id'], **body})
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        await call(action='state')
        room = framework.play_move(room['room_id'], 'human', 'human-1',
            {'action': 'bid', 'quantity': 3, 'face': 2}, expected_revision=room['revision'])
        await call(action='state')
        reply = await call(action='move', revision=room['revision'], move={'action': 'end_session'})
        self.assertEqual(reply['result']['outcome'], 'no_contest')
        for key in ('rankings', 'records', 'round_results', 'scores', 'terminal_dice', 'dice_by_player'):
            self.assertNotIn(key, json.dumps(reply))
        room = framework.get_room(room['room_id'])
        self.assertEqual(await call(action='state'), {'r': room['revision']})
        full = await call(action='state', full_state=True)
        self.assertEqual(full['snapshot']['result'], room['result'])
        self.assertEqual(full['snapshot']['private_state']['dice'], room['board_state']['dice_by_player']['ai-1'])
        self.assertNotIn('terminal_dice', full['snapshot']['board_state'])
        self.assertNotIn('dice_by_player', full['snapshot']['board_state'])
        self.assertEqual(await call(action='state'), {'r': room['revision']})
        # A first-ever read after termination must still keep opponents' cups private.
        with database.write_transaction() as conn:
            conn.execute('DELETE FROM mcp_minimal_contexts WHERE room_id=?', (room['room_id'],))
        boot = await call(action='state')
        self.assertTrue(boot['bootstrap'])
        self.assertNotIn('rankings', json.dumps(boot))
        self.assertNotIn('terminal_dice', json.dumps(boot))
