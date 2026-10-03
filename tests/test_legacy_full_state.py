import json
import random
import unittest
from copy import deepcopy
from unittest.mock import patch

from app import database, framework, full_state, main
from app.games import GAMES
from tests.full_state_support import LEGACY, choose
from tests import test_mcp_compact as compact


class LegacyFullStateTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = compact.McpCompactProtocolTests.asyncSetUp
    asyncTearDown = compact.McpCompactProtocolTests.asyncTearDown

    def room(self, name, count=None):
        game = GAMES[name]
        count = count or max(2, game.min_players)
        seats = [dict(player_id=f'p{i}', role='ai', participant_kind='bound_machine',
                      display_name=f'玩家{i}') for i in range(count)]
        if name == 'xiangqi':
            seats[1].update(role='human', participant_kind='human')
        return framework.create_room(name, 'ai_first', 'ai', 'p0',
            ordered_participants=seats, require_confirmations=False)

    async def call(self, room, pid='p0', **body):
        response = await self.client.post('/mcp/play', json=dict(
            room_id=room['room_id'], player_id=pid, **body))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def replace(self, room, state, pid):
        with database.write_transaction() as conn:
            conn.execute('UPDATE rooms SET board_state=?, current_player_id=? WHERE room_id=?',
                         (json.dumps(state), pid, room['room_id']))
        return framework.get_room(room['room_id'])

    async def test_all_25_safe_information_and_submit_ready_actions(self):
        self.assertEqual(set(LEGACY), full_state.LEGACY_GAMES)
        for name in LEGACY:
            with self.subTest(game=name):
                game = GAMES[name]
                if hasattr(game, '_rng'):
                    rng_patch = patch.object(game, '_rng', random.Random(37))
                    rng_patch.start()
                    self.addCleanup(rng_patch.stop)
                room = self.room(name)
                pid = room['current_player_id']
                old = framework.project_mcp_snapshot_for_viewer(room, pid)
                raw = deepcopy(room)
                new = (await self.call(room, pid, action='state', full_state=True))['snapshot']
                self.assertEqual(room, raw)
                self.assertEqual(old['private_state'], new['private_state'])
                for key, value in old['board_state'].items():
                    self.assertEqual(value, new['board_state'][key], (name, key))
                move = choose(new)
                framework.play_move(room['room_id'], 'ai', pid, move, expected_revision=room['revision'])

    async def test_resync_delivers_all_text_once_and_old_moves_never_replay(self):
        room = self.room('tictactoe')
        await self.call(room, action='state')
        room = framework.play_move(room['room_id'], 'ai', 'p0', {'row': 0, 'col': 0})
        framework.post_message(room['room_id'], 'ai', 'p1', '快照前聊天')
        room = framework.play_move(room['room_id'], 'ai', 'p1', {'row': 1, 'col': 1}, message='走子附言')
        framework.post_message(room['room_id'], 'ai', 'p1', '第二条聊天')
        full = await self.call(room, action='state', full_state=True)
        messages = [e['message'] for e in full['events']]
        self.assertEqual(messages, ['快照前聊天', '走子附言', '第二条聊天'])
        self.assertTrue(all(set(e) == {'name', 'message'} for e in full['events']))
        self.assertNotIn('events', await self.call(room, action='state', full_state=True))
        self.assertNotIn('events', await self.call(room, action='state'))
        framework.post_message(room['room_id'], 'ai', 'p1', '快照后聊天')
        following = await self.call(room, action='state')
        self.assertEqual([e['message'] for e in following['events']], ['快照后聊天'])
        self.assertNotIn('events', await self.call(room, action='state'))

    async def test_bounded_cursor_preserves_concurrent_newer_moves_and_visibility(self):
        room = self.room('tictactoe')
        await self.call(room, action='state')
        old = deepcopy(room)
        room = framework.play_move(room['room_id'], 'ai', 'p0', {'row': 0, 'col': 0})
        room = framework.play_move(room['room_id'], 'ai', 'p1', {'row': 1, 'col': 1})
        self.assertEqual(full_state.read_full_state_text(old, 'p0'), [])
        following = await self.call(room, action='state')
        self.assertIn({'row': 1, 'col': 1}, [e.get('move') for e in following['events']])
        framework.post_message(room['room_id'], 'ai', 'p1', '仅另一席可见')
        with database.write_transaction() as conn:
            conn.execute('UPDATE room_messages SET visible_to_json=? WHERE text=?',
                         ('["p1"]', '仅另一席可见'))
        self.assertNotIn('events', await self.call(room, action='state', full_state=True))

    async def test_default_and_excluded_games_never_enter_full_state_projection(self):
        room = self.room('uno')
        with patch.object(main, 'full_state_response', side_effect=AssertionError('full only')):
            boot = await self.call(room, action='state')
            self.assertTrue(boot['bootstrap'])
            await self.call(room, action='state')
            move = choose(framework.project_mcp_snapshot_for_viewer(room, 'p0'))
            await self.call(room, action='move', move=move, revision=room['revision'])
        for name in ('monopoly', 'rummikub', 'bomb_plane', 'carcassonne'):
            room = self.room(name)
            await self.call(room, action='state')
            expected = main.mcp_minimal.snapshot(room, 'p0', full=True)
            with patch.object(main, 'full_state_response', side_effect=AssertionError('excluded')):
                payload = await self.call(room, action='state', full_state=True)
            self.assertEqual(payload['snapshot'], expected)

    async def test_terminal_resignation_results_and_rule_scoped_privacy(self):
        for name in ('tictactoe', 'doudizhu', 'guandan', 'mahjong', 'junqi', 'uno', 'liars_dice', 'texas_holdem', 'zhajinhua'):
            with self.subTest(game=name):
                room = self.room(name)
                room = framework.resign(room['room_id'], 'ai', 'p0')
                if room['status'] != 'finished':
                    continue
                snapshot = (await self.call(room, 'p1', action='state', full_state=True))['snapshot']
                for key in ('winner', 'winner_player_id', 'result', 'terminal_reason'):
                    self.assertEqual(snapshot[key], room.get(key))
                if name in ('texas_holdem', 'zhajinhua'):
                    self.assertFalse(snapshot['board_state'].get('showdown') or snapshot['board_state'].get('revealed_hands'))
                self.assertEqual(snapshot['private_state'], framework.project_mcp_snapshot_for_viewer(room, 'p1')['private_state'])

    async def test_mahjong_response_window_can_peng_without_other_reads(self):
        from tests.test_mahjong import MahjongRulesTests, FILLER
        fixture = MahjongRulesTests()
        fixture.setUp()
        state = fixture.state()
        state['hands']['p0'] = fixture.tiles(['W3', *FILLER, 'F2', 'F3'])
        state['hands']['p1'] = fixture.tiles(['W1', 'W2', *FILLER])
        state['hands']['p2'] = fixture.tiles(['W3', 'W3', 'W3', *FILLER[:10]])
        action = next(a for a in fixture.game.legal_actions_for(state, 'p0')
                      if a['kind'] == 'discard' and a['tile_id'] == state['hands']['p0'][0]['id'])
        opened = fixture.game.apply_action(state, fixture.submit(action), fixture.players[0])
        room = self.replace(self.room('mahjong'), opened.state, 'p2')
        snapshot = (await self.call(room, 'p2', action='state', full_state=True))['snapshot']
        self.assertTrue(snapshot['board_state']['response_window'])
        self.assertEqual(snapshot['private_state']['own_melds'], [])
        peng = fixture.submit(fixture.action(fixture.game, opened.state, 'p2', 'peng'))
        self.assertIn(peng, snapshot['private_state']['legal_actions'])
        await self.call(room, 'p2', action='move', move=peng, revision=room['revision'])

    async def test_uno_challenge_preserves_pending_and_hides_legality(self):
        from tests.test_uno import UnoRulesTests
        fixture = UnoRulesTests()
        fixture.setUp()
        state = fixture.scenario({
            'player-1': ['wild-draw-four-1', 'red-number-7-1', 'blue-number-1-1'],
            'player-2': ['green-number-2-1'],
        }, 'red-number-5-1', deck=['yellow-number-1-1', 'yellow-number-2-1',
                                  'yellow-number-3-1', 'yellow-number-4-1'])
        # Rename fixture seats to the real room IDs before authoritative play.
        state = json.loads(json.dumps(state).replace('player-1', 'p0').replace('player-2', 'p1').replace('player-3', 'p2'))
        room = self.replace(self.room('uno', 3), state, 'p0')
        room = framework.play_move(room['room_id'], 'ai', 'p0',
                                  {'action': 'play', 'card_id': 'wild-draw-four-1', 'color': 'blue'})
        snapshot = (await self.call(room, 'p1', action='state', full_state=True))['snapshot']
        self.assertTrue(snapshot['board_state']['penalty_state']['pending_wild_draw_four'])
        self.assertNotIn('was_legal', json.dumps(snapshot))
        move = {'action': 'challenge_wild_draw_four'}
        self.assertIn(move, snapshot['private_state']['legal_actions'])
        await self.call(room, 'p1', action='move', move=move, revision=room['revision'])

    async def test_liars_dice_invite_acknowledgement_and_current_dice(self):
        from app import invites
        room = invites.create_invite('liars_dice', 'ai', 'p0', target_player_count=3)
        for pid in ('p1', 'p2'):
            invites.join_invite(room['invite_code'], 'ai', pid)
        with patch.object(invites.secrets, 'SystemRandom') as rng:
            rng.return_value.shuffle.side_effect = lambda seats: None
            room = invites.start_invite(room['room_id'], 'ai', 'p0')
        room = framework.play_move(room['room_id'], 'ai', 'p0',
                                   {'action': 'bid', 'quantity': 15, 'face': 6}, expected_revision=room['revision'])
        room = framework.play_move(room['room_id'], 'ai', 'p1', {'action': 'challenge'}, expected_revision=room['revision'])
        pid = room['current_player_id']
        snapshot = (await self.call(room, pid, action='state', full_state=True))['snapshot']
        self.assertTrue(snapshot['board_state']['pending_next_round'])
        self.assertTrue(snapshot['board_state']['last_round_result']['revealed_dice_by_player'])
        self.assertEqual(snapshot['private_state']['dice'], room['board_state']['dice_by_player'][pid])
        self.assertIn({'action': 'acknowledge_round'}, snapshot['private_state']['legal_actions'])
        await self.call(room, pid, action='move', move={'action': 'acknowledge_round'}, revision=room['revision'])
        updated = framework.get_room(room['room_id'])
        own = full_state.project_full_state(updated, pid)
        self.assertIsNone(own['board_state']['pending_next_round'])
        self.assertNotIn('dice_by_player', own['board_state'])
        self.assertEqual(own['private_state']['dice'], updated['board_state']['dice_by_player'][pid])

    async def test_junqi_restores_old_public_battles_without_survivor_rank(self):
        room = self.room('junqi')
        state = deepcopy(room['board_state'])
        state.update(phase='play', active_player_id='p0', setup_ready={'p0': True, 'p1': True})
        state['board'] = {square: None for square in state['board']}
        state['board'].update({
            'a6': {'color': 'b', 'rank': 4}, 'a7': {'color': 'r', 'rank': 8},
            'b1': {'color': 'b', 'rank': 11}, 'b12': {'color': 'r', 'rank': 11},
            'e7': {'color': 'r', 'rank': 5},
        })
        room = self.replace(room, state, 'p0')
        room = framework.play_move(room['room_id'], 'ai', 'p0',
                                  {'action': 'move', 'from': 'a6', 'to': 'a7'})
        self.assertEqual(room['status'], 'playing')
        # Exercise retention beyond the engine's 40-entry recent-actions cache.
        state = deepcopy(room['board_state'])
        state.update(public_actions=[], last_battle=None, last_action=None)
        room = self.replace(room, state, 'p1')
        snapshot = (await self.call(room, 'p1', action='state', full_state=True))['snapshot']
        self.assertEqual(len(snapshot['board_state']['battles']), 1)
        battle = snapshot['board_state']['battles'][0]['battle']
        self.assertNotIn('attacker_rank', battle)
        self.assertEqual(battle['defender_rank'], 8)
        self.assertNotIn('rank', snapshot['board_state']['board']['a7'])
        self.assertEqual(snapshot['private_state']['pieces']['e7'], 5)
        move = choose(snapshot)
        await self.call(room, 'p1', action='move', move=move, revision=room['revision'])

    async def test_real_terminal_result_is_preserved(self):
        room = self.room('tictactoe')
        for pid, r, c in [('p0', 0, 0), ('p1', 1, 0), ('p0', 0, 1), ('p1', 1, 1), ('p0', 0, 2)]:
            room = framework.play_move(room['room_id'], 'ai', pid, {'row': r, 'col': c})
        snapshot = (await self.call(room, action='state', full_state=True))['snapshot']
        self.assertEqual(snapshot['status'], 'finished')
        self.assertEqual(snapshot['winner_player_id'], 'p0')
        self.assertEqual(snapshot['terminal_reason'], 'game_result')
        self.assertEqual(snapshot['result'], room['result'])
