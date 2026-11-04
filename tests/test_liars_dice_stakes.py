"""Party stake regressions; every test uses a disposable SQLite database."""
import base64
import json
import unittest
import subprocess
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import chips, database, framework, invites, liars_dice_setup
from app.games.liars_dice import LiarsDice
from app.npc_personas import NpcPersona
from tests import test_multiplayer_games as support
from tests.test_liars_dice_teams import OPTIONS, expire, seat


class PartyStakeTests(support.MultiplayerGameTestCase):
    def setUp(self):
        super().setUp()
        # Ledger expectations use named seats; random-seat behavior has its own suite.
        shuffle = patch('app.liars_dice_setup.secrets.SystemRandom.shuffle', lambda rng, values: None)
        shuffle.start()
        self.addCleanup(shuffle.stop)

    def create(self, count=3, stake=5, npc_count=0, **options):
        room = framework.create_room('liars_dice', 'human_first', 'human', 'human-1',
            opponent_id='ai-1', ordered_participants=support.participants(count, npc_count=npc_count),
            stake=stake, game_options={'mode': 'party', 'rush_open': False, **options})
        if stake:
            self.assertEqual(room['status'], 'pending' if count - npc_count > 1 else 'playing')
            room = self.accept_bound_players(room)
        return room

    def act(self, room, move, pid=None):
        pid = pid or room['current_player_id']
        return framework.play_move(room['room_id'], self.role_for(pid), pid, move,
                                   expected_revision=room['revision'])

    def finish_scores(self, room, units, completed=1):
        state = room['board_state']
        state['score_half_units'] = dict(zip(state.get('teams') or state['participant_order'], units))
        state['records']['human-1']['losses'] = completed
        room = self.replace_state(room, state, room['current_player_id'])
        return self.act(room, {'action': 'end_session'}, 'human-1')

    def assert_payout(self, room, expected):
        self.assertEqual(room['result']['settlement_deltas'], expected)
        self.assertEqual(sum(expected.values()), 0)
        self.assertGreaterEqual(min(expected.values()), -room['stake'])
        for p in room['participants']:
            delta = expected[p['player_id']]
            if p['participant_kind'] == 'system_npc':
                self.assertEqual(delta, 0)
            else:
                self.assertEqual(chips.get_wallet(p['role'], p['player_id'])['balance'], 200 + delta)
        self.assertEqual(framework.get_room(room['room_id'])['result'], room['result'])
        with database.write_transaction() as conn:
            self.assertFalse(chips.settle_duel_room(conn, room))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM chip_wallets WHERE subject_id LIKE 'npc:%'").fetchone()[0], 0)
        with self.assertRaises(framework.DuelError):
            self.act(room, {'action': 'end_session'}, 'human-1')

    def test_real_round_then_end_pays_once_zero_stake_never_pays(self):
        for stake in (0, 5):
            room = self.create(2, stake=stake, allow_surrender=True)
            room = self.act(room, {'action': 'bid', 'quantity': 3, 'face': 2})
            room = self.act(room, {'action': 'surrender'})
            with closing(database.connect()) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM chip_settlement_batches').fetchone()[0], 0)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM chip_ledger').fetchone()[0], 0)
            room = self.act(room, {'action': 'end_session'}, 'human-1')
            self.assertEqual(room['result']['scores'], {'human-1': 0, 'ai-1': -.5})
            if stake:
                self.assert_payout(room, {'human-1': 5, 'ai-1': -5})
            else:
                self.assertNotIn('settlement_deltas', room['result'])

    def test_split_winners_stable_seat_remainder(self):
        room = self.finish_scores(self.create(), [0, 0, -2])
        self.assertEqual([r['rank'] for r in room['result']['rankings']], [1, 1, 3])
        self.assert_payout(room, {'human-1': 3, 'ai-1': 2, 'ai-2': -5})

    def test_refunds_all_tied_no_rounds_and_only_npc_winner(self):
        cases = [(3, 0, [0, 0, 0], 1), (3, 0, [0, 0, 0], 0),
                 (3, 1, [-2, -4, 0], 1), (3, 1, [0, 0, -2], 1)]
        for count, npcs, units, rounds in cases:
            room = self.finish_scores(self.create(count, npc_count=npcs), units, rounds)
            self.assertIn('筹码退回', room['result']['result_text'])
            self.assert_payout(room, {p['player_id']: 0 for p in room['participants']})

    def test_team_winners_share_only_real_losers_pool(self):
        room = self.create(6, npc_count=2, team_mode='teams')
        state = room['board_state']
        # Isolate mixed real/NPC team payout from random team assignment.
        state['teams'] = {'red': ['human-1', 'ai-1', 'npc:test-1'],
                          'blue': ['ai-2', 'ai-3', 'npc:test-2']}
        room = self.replace_state(room, state, room['current_player_id'])
        room = self.finish_scores(room, [0, -2])
        self.assertEqual(len(room['result']['rankings']), 2)
        self.assert_payout(room, {'human-1': 5, 'ai-1': 5, 'ai-2': -5, 'ai-3': -5,
                                  'npc:test-1': 0, 'npc:test-2': 0})

    def test_teammate_rush_charges_team_once_and_chips_only_at_table_end(self):
        room = self.create(6, npc_count=2, team_mode='teams', rush_open=True)
        state = room['board_state']
        state['teams'] = {'red':['human-1','ai-1','npc:test-1'],
                          'blue':['ai-2','ai-3','npc:test-2']}
        state['dice_by_player'] = {pid:[2]*5 for pid in state['participant_order']}
        room = self.replace_state(room, state, 'human-1')
        room = self.act(room, {'action':'bid', 'quantity':42, 'face':6})
        room = self.act(room, {'action':'rush_open'}, 'ai-1')
        self.assertEqual(room['board_state']['team_scores'], {'red':-1,'blue':0})
        with closing(database.connect()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM chip_settlement_batches').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM chip_ledger').fetchone()[0], 0)
        room = self.act(room, {'action':'end_session'}, 'human-1')
        self.assert_payout(room, {'human-1':-5,'ai-1':-5,'ai-2':5,'ai-3':5,
                                  'npc:test-1':0,'npc:test-2':0})

    def test_manual_confirm_then_wait_reenter_start_with_npcs(self):
        room = framework.create_room('liars_dice', 'human_first', 'human', 'human-1',
            opponent_id='ai-1', ordered_participants=support.participants(3),
            stake=5, game_options=OPTIONS, target_player_count=6)
        self.assertEqual(room['status'], 'pending')
        room = liars_dice_setup.remember_profiles(room, {'ai-1': {'name': '小机', 'avatar': {'type': 'emoji', 'value': '🌟'}}})
        self.assertIn(room['room_id'], [r['room_id'] for r in framework.list_human_rooms('human-1')])
        with self.assertRaises(framework.DuelError):
            liars_dice_setup.start_regular(room['room_id'], 'human', 'human-1', True)
        room = self.accept_bound_players(room)
        self.assertEqual(room['status'], 'waiting')
        self.assertFalse(room['team_selection']['can_start'])
        self.assertEqual(room['board_state']['participant_avatars']['ai-1']['value'], '🌟')
        self.assertEqual(framework.list_human_rooms('human-1')[0]['room_id'], room['room_id'])
        room = seat(framework.get_room(room['room_id']), 'ai-1', 'red', 1)
        with self.assertRaises(framework.DuelError):
            liars_dice_setup.start_regular(room['room_id'], 'human', 'human-1', True)
        room = expire(room)
        with patch('app.invites.npc_provider_capabilities', return_value={'available': True}), patch(
                'app.npc_personas.select_personas', side_effect=lambda n: [NpcPersona(f'test-{i}', f'NPC{i}', 'test') for i in range(n)]):
            room = liars_dice_setup.start_regular(room['room_id'], 'human', 'human-1', True)
        self.assertEqual(room['status'], 'playing')
        self.assertEqual(room['board_state']['teams']['red'][0], 'ai-1')
        room = self.act(room, {'action': 'end_session'}, 'human-1')
        self.assert_payout(room, {p['player_id']: 0 for p in room['participants']})

    def test_invite_selected_machine_confirms_external_joins_then_start(self):
        room = invites.create_invite('liars_dice', 'human', 'human-1', target_player_count=4,
            stake=5, game_options=OPTIONS, ai_players=['ai-1'],
            trusted_bound_ais=[{'id': 'ai-1', 'name': '机器'}])
        self.assertEqual(room['status'], 'pending')
        self.assertIsNone(room['invite_code'])
        with self.assertRaises(framework.DuelError):
            invites.start_invite(room['room_id'], 'human', 'human-1', fill_with_npcs=True)
        room = framework.respond_to_invitation(room['room_id'], 'ai', 'ai-1', 'accept')
        self.assertEqual(room['status'], 'waiting')
        self.assertEqual(invites.preview_invite(room['invite_code'])['stake'], 5)
        room = invites.join_invite(room['invite_code'], 'ai', 'ai-2')
        room = seat(room, 'ai-1', 'blue', 2)
        room = expire(room)
        with patch('app.invites.npc_provider_capabilities', return_value={'available': True}), patch(
                'app.invites.select_personas', return_value=[NpcPersona('test-1', 'NPC', 'test')]):
            room = invites.start_invite(room['room_id'], 'human', 'human-1', fill_with_npcs=True)
        self.assertEqual(room['status'], 'playing')
        self.assertEqual(room['board_state']['teams']['blue'][1], 'ai-1')
        room = self.finish_scores(room, [0, -2])
        self.assertEqual(sum(room['result']['settlement_deltas'].values()), 0)
        self.assertEqual(room['result']['settlement_deltas']['npc:test-1'], 0)

    def test_full_manual_start_budget_recheck_and_invite_external_requirement(self):
        room = framework.create_room('liars_dice', 'human_first', 'human', 'human-1',
            ordered_participants=support.participants(4), game_options=OPTIONS, stake=5)
        room = expire(self.accept_bound_players(room))
        chips.get_wallet('human', 'human-1')
        with database.write_transaction() as conn:
            conn.execute("UPDATE chip_wallets SET balance=0 WHERE subject_type='human'")
        with self.assertRaisesRegex(framework.DuelError, '筹码不足'):
            liars_dice_setup.start_regular(room['room_id'], 'human', 'human-1')
        with database.write_transaction() as conn:
            conn.execute("UPDATE chip_wallets SET balance=200 WHERE subject_type='human'")
        self.assertEqual(liars_dice_setup.start_regular(room['room_id'], 'human', 'human-1')['status'], 'playing')
        room = invites.create_invite('liars_dice', 'human', 'human-1', target_player_count=4,
                                    stake=5, game_options=OPTIONS)
        room = expire(room)
        with self.assertRaisesRegex(framework.DuelError, '外部受邀'):
            invites.start_invite(room['room_id'], 'human', 'human-1', fill_with_npcs=True)

    def test_bid_only_refund_and_reveal_end(self):
        room = self.create(2)
        room = self.act(room, {'action': 'bid', 'quantity': 3, 'face': 2})
        room = self.act(room, {'action': 'end_session'}, 'human-1')
        self.assertEqual(room['result']['outcome'], 'no_contest')
        self.assert_payout(room, {'human-1': 0, 'ai-1': 0})
        room = self.create(2)
        room = self.act(room, {'action': 'bid', 'quantity': 10, 'face': 6})
        room = self.act(room, {'action': 'challenge'})
        room = self.act(room, {'action': 'end_session'}, 'human-1')
        winner = room['result']['winner_player_ids'][0]
        self.assert_payout(room, {p['player_id']: 5 if p['player_id'] == winner else -5 for p in room['participants']})

    def test_team_tie_refund_and_ordinary_join_preserves_selection(self):
        room = self.create(4, team_mode='teams')
        room = self.finish_scores(room, [-2, -2])
        self.assert_payout(room, {p['player_id']: 0 for p in room['participants']})
        room = framework.create_room('liars_dice', 'human_first', 'human', 'human-1',
            ordered_participants=support.participants(2), game_options=OPTIONS, target_player_count=4, stake=5)
        room = self.accept_bound_players(room)
        room = seat(room, 'human-1', 'red', 1)
        with self.assertRaisesRegex(framework.DuelError, '跨账号加入请使用邀请码'):
            framework.join_room(room['room_id'], 'ai', 'ai-2')
        room = framework.get_room(room['room_id'])
        self.assertEqual(room['status'], 'waiting')
        self.assertEqual(room['team_selection']['seats']['red'][0], 'human-1')
        self.assertNotIn('dice_by_player', room['board_state'])

    def test_rejected_and_expired_paid_invites_cancel_without_debit(self):
        for reject in (False, True):
            room = invites.create_invite('liars_dice', 'human', 'human-1', target_player_count=4,
                stake=5, game_options=OPTIONS, ai_players=['ai-1'],
                trusted_bound_ais=[{'id': 'ai-1', 'name': '小机'}])
            if reject:
                framework.respond_to_invitation(room['room_id'], 'ai', 'ai-1', 'reject')
            else:
                with database.write_transaction() as conn:
                    conn.execute("UPDATE rooms SET confirmation_expires_at='2000-01-01T00:00:00+00:00' WHERE room_id=?", (room['room_id'],))
            with self.assertRaises(framework.DuelError) as missing:
                framework.get_room(room['room_id'])
            self.assertEqual(missing.exception.status_code, 404)
            with closing(database.connect()) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM chip_ledger').fetchone()[0], 0)

    def test_classic_payout_contract_unchanged(self):
        game = LiarsDice()
        people = [{**p, 'seat_index': i} for i, p in enumerate(support.participants(4))]
        room = {'participants': people, 'stake': 5}
        result = framework._attach_multiplayer_settlement(game, room, {'mode': 'classic'}, {'winner_player_id': 'ai-1'})
        self.assertEqual(result['settlement_deltas'], {'human-1': -5, 'ai-1': 15, 'ai-2': -5, 'ai-3': -5})

    def test_stake_inputs_and_end_confirmation_dom(self):
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('app/static/app.js','utf8');
const names=['resolvedStakeMetadata','renderSelectedStakeHint','updateInviteStakeAvailability','resign'];
const nodes={};const $=id=>nodes[id]||(nodes[id]={value:'7',dataset:{},disabled:false,replaceChildren(){},append(){},appendChild(){}});
let mode='party',prompt='';
const context={$,identity:{games:[{game_type:'liars_dice',supports_stakes:true,supports_multiplayer_stakes:true}]},selectedGameType:()=> 'liars_dice',selectedGameOptions:()=>({mode}),document:{createElement:()=>({}),createTextNode:s=>s},room:{game_type:'liars_dice',board_state:{mode:'party'},stake:7},window:{confirm:s=>{prompt=s;return false;}}};
vm.createContext(context);
for(const name of names){const start=source.indexOf(`function ${name}(`);const end=source.indexOf('\n}',start)+2;vm.runInContext((name==='resign'?'async ':'')+source.slice(start,end),context);}
$('inviteCount').value='6';
for(mode of ['party','classic','party']){context.renderSelectedStakeHint();context.updateInviteStakeAvailability();for(const id of ['stake','inviteStake']){assert.equal($(id).value,'7');assert.equal($(id).disabled,false);}}
context.resign();assert.ok(prompt.includes('将按当前分数结算平台筹码'));
'''
        result = subprocess.run(['node', '-e', script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


# Reuse only the isolated ASGI fixture, not unrelated inherited tests.
class PartyStakeTransportTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.LiarsDiceMcpTests.asyncSetUp
    asyncTearDown = support.LiarsDiceMcpTests.asyncTearDown

    def headers(self):
        return {'X-Duel-Human-Player': 'human-1', 'X-Duel-Bound-Ais': base64.b64encode(
            json.dumps([{'id': 'ai-1', 'name': '小机'}]).encode()).decode()}

    async def test_paid_manual_web_create_confirm_list_reenter_start_both_room_kinds(self):
        for invite in (False, True):
            response = await self.client.post('/api/invites' if invite else '/api/rooms', headers=self.headers(), json={
                'player_id': 'human-1', 'game_type': 'liars_dice', 'ai_players': ['ai-1'], 'stake': 5,
                'target_player_count': 4, 'game_options': OPTIONS})
            self.assertEqual(response.status_code, 200, response.text)
            room = response.json()['room']
            rid = room['room_id']
            self.assertEqual(room['status'], 'pending')
            # Pending can outlive the original 20s window; it must stay read-only.
            expire(framework.get_room(rid))
            for _ in range(2):
                reopened = await self.client.get(f'/api/rooms/{rid}', headers=self.headers())
                pending = reopened.json()['room']
                self.assertEqual(pending['status'], 'pending')
                self.assertFalse(pending['room_ready'])
                self.assertFalse(pending['team_selection']['can_start'])
                self.assertEqual(pending['team_selection'], pending['board_state']['team_selection'])
                self.assertEqual(pending['private_state'], {})
                for secret in ('dice_by_player', 'first_player_id', 'deferred_npcs'):
                    self.assertNotIn(secret, reopened.text)
            denied = await self.client.post(f'/api/rooms/{rid}/move', headers=self.headers(), json={
                'player_id': 'human-1', 'revision': pending['revision'],
                'move': {'action': 'choose_team_seat', 'team': 'red', 'seat': 1}})
            self.assertEqual(denied.status_code, 409)
            early = await self.client.post(f'/api/rooms/{rid}/start', headers=self.headers(), json={'fill_with_npcs': True})
            self.assertEqual(early.status_code, 409)
            accepted = await self.client.post('/mcp/play', json={'action': 'accept', 'player_id': 'ai-1', 'room_id': rid})
            self.assertEqual(accepted.status_code, 200, accepted.text)
            room = framework.get_room(rid)
            self.assertEqual(room['status'], 'waiting')
            self.assertFalse(room['team_selection']['can_start'])
            seated = await self.client.post(f'/api/rooms/{rid}/move', headers=self.headers(), json={
                'player_id': 'human-1', 'revision': room['revision'],
                'move': {'action': 'choose_team_seat', 'team': 'red', 'seat': 1}})
            self.assertEqual(seated.status_code, 200, seated.text)
            self.assertEqual(seated.json()['room']['team_selection']['seats']['red'][0], 'human-1')
            if invite:
                joined = await self.client.post('/api/invites/join', headers={'X-Duel-Human-Player': 'human-2'},
                                                json={'invite_code': room['invite_code']})
                self.assertEqual(joined.status_code, 200, joined.text)
            who = await self.client.get('/api/whoami', headers=self.headers())
            self.assertEqual(who.json()['rooms'][0]['room_id'], rid)
            reopened = await self.client.get(f'/api/rooms/{rid}', headers=self.headers())
            self.assertEqual(reopened.status_code, 200, reopened.text)
            self.assertIn('team_selection', reopened.json()['room'])
            early = await self.client.post(f'/api/rooms/{rid}/start', headers=self.headers(), json={'fill_with_npcs': True})
            self.assertEqual(early.status_code, 409)
            expire(framework.get_room(rid))
            with patch('app.invites.npc_provider_capabilities', return_value={'available': True}), patch(
                    'app.invites.select_personas', side_effect=lambda n: [NpcPersona(f'test-{i}', f'NPC{i}', 'test') for i in range(n)]), patch(
                    'app.npc_personas.select_personas', side_effect=lambda n: [NpcPersona(f'test-{i}', f'NPC{i}', 'test') for i in range(n)]), patch(
                    'app.main._schedule_current_system_npc', new=AsyncMock()):
                started = await self.client.post(f'/api/rooms/{rid}/start', headers=self.headers(), json={'fill_with_npcs': True})
            self.assertEqual(started.status_code, 200, started.text)
            self.assertEqual(started.json()['room']['status'], 'playing')
            self.assertNotIn('team_selection', started.json()['room'])
            self.assertNotIn('dice_by_player', started.json()['room']['board_state'])
            self.assertEqual(len(started.json()['room']['private_state']['dice']), 5)

    async def test_paid_end_mcp_increment_private_full_result_and_duplicate(self):
        room = framework.create_room('liars_dice', 'human_first', 'human', 'human-1',
            opponent_id='ai-1', stake=5, game_options={'mode': 'party', 'rush_open': False, 'allow_surrender': True, 'surrender_no_reroll': True})
        rid = room['room_id']
        room = framework.respond_to_invitation(rid, 'ai', 'ai-1', 'accept')
        await self.client.post('/mcp/play', json={'action': 'state', 'player_id': 'ai-1', 'room_id': rid})
        room = framework.play_move(rid, 'human', 'human-1', {'action': 'bid', 'quantity': 3, 'face': 2}, expected_revision=room['revision'])
        room = framework.play_move(rid, 'ai', 'ai-1', {'action': 'surrender'}, expected_revision=room['revision'])
        await self.client.post('/mcp/play', json={'action': 'state', 'player_id': 'ai-1', 'room_id': rid})
        response = await self.client.post('/mcp/play', json={'action': 'move', 'player_id': 'ai-1', 'room_id': rid,
            'revision': room['revision'], 'move': {'action': 'end_session'}})
        self.assertEqual(response.status_code, 200, response.text)
        for key in ('rankings', 'dice_by_player', 'terminal_dice', 'round_results'):
            self.assertNotIn(key, response.text)
        ended = framework.get_room(rid)
        self.assertEqual(ended['result']['settlement_deltas'], {'human-1': 5, 'ai-1': -5})
        full = await self.client.post('/mcp/play', json={'action': 'state', 'player_id': 'ai-1', 'room_id': rid, 'full_state': True})
        self.assertEqual(full.status_code, 200, full.text)
        self.assertEqual(full.json()['snapshot']['result'], ended['result'])
        self.assertEqual(full.json()['snapshot']['private_state']['dice'], ended['board_state']['dice_by_player']['ai-1'])
        self.assertNotIn('dice_by_player', full.json()['snapshot']['board_state'])
        self.assertEqual(full.json()['snapshot']['board_state']['last_round_result']['revealed_dice_by_player'], {})
        repeat = await self.client.post('/mcp/play', json={'action': 'move', 'player_id': 'ai-1', 'room_id': rid,
            'revision': ended['revision'], 'move': {'action': 'end_session'}})
        self.assertEqual(repeat.status_code, 409)
        self.assertEqual(chips.get_wallet('human', 'human-1')['balance'], 205)
        wallet_ui = await self.client.get('/api/whoami', headers=self.headers())
        self.assertEqual(wallet_ui.json()['wallet']['balance'], 205)
