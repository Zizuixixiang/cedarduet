"""Real SQLite/API/MCP reaction races, deadlines, privacy and one-time seating."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import time
import unittest
from unittest.mock import patch

from app import database, framework as f, invites, main, liars_dice_setup as setup
from app.games import get_game
from app.liars_dice_rush import RushScheduler, npc_wants_rush
from app.npc_scheduler import NpcTurnScheduler
from tests import test_multiplayer_games as support
from tests.test_liars_dice_teams import expire, seat


class RushRulesTests(support.MultiplayerGameTestCase):
    def create(self, count=4, **options):
        return f.create_room('liars_dice', 'human_first', 'human', 'human-1', opponent_id='ai-1',
                             ordered_participants=support.participants(count), game_options={'mode':'party', **options})

    def act(self, room, action, pid=None):
        pid = pid or room['current_player_id']
        return f.play_move(room['room_id'], self.role_for(pid), pid, action, expected_revision=room['revision'])

    def bid(self, room):
        return self.act(room, {'action':'bid', 'quantity':20, 'face':6})

    def test_defaults_legacy_classic_and_disabled(self):
        game = get_game('liars_dice')
        self.assertTrue(game.normalize_options({'mode':'party'})['rush_open'])
        for options in ({'mode':'classic'}, {'mode':'party','rush_open':False}):
            state = game.configure(game.initialize(support.participants(2)), options)
            game.apply_action(state, {'action':'bid','quantity':3,'face':2}, support.participants(2)[0])
            self.assertFalse(game.rush_active(state))
        room = self.create()
        state = room['board_state']; state.pop('rush_open')
        room = self.replace_state(room, state, room['current_player_id'])
        self.assertFalse(room['game_options']['rush_open'])
        room = self.bid(room)
        self.assertFalse(game.rush_active(room['board_state']))

    def test_deadline_reconnect_stale_and_next_bid(self):
        room = self.create()
        with patch('app.games.liars_dice.time.time', return_value=2000000000):
            room = self.bid(room)
            self.assertEqual(room['board_state']['rush_until'], 2000000010)
            revision = room['revision']
            view = f.project_room_for_viewer(f.get_room(room['room_id']), 'ai-2')
            self.assertTrue(view['private_state']['can_rush'])
            self.assertEqual(view['board_state']['server_time'], 2000000000)
            self.assertNotIn('dice_by_player', view['board_state'])
            self.assertNotIn('terminal_dice', view['board_state'])
            for pid in ('human-1',):
                with self.assertRaises(f.DuelError): self.act(room, {'action':'rush_open'}, pid)
            with self.assertRaises(f.DuelError):
                self.act(room, {'action':'bid', 'quantity':21, 'face':6})
        with patch('app.games.liars_dice.time.time', return_value=2000000009.999):
            self.assertTrue(get_game('liars_dice').can_rush(room['board_state'], 'ai-2'))
        with patch('app.games.liars_dice.time.time', return_value=2000000010):
            expired = f.get_room(room['room_id'])
            self.assertEqual(expired['revision'], revision+1)
            self.assertNotIn('rush_until', expired['board_state'])
            with self.assertRaises(f.DuelError): self.act(room, {'action':'rush_open'}, 'ai-2')
            with self.assertRaises(f.DuelError): self.act(expired, {'action':'rush_open'}, 'ai-2')
            normal = self.act(expired, {'action':'bid','quantity':21,'face':6})
            self.assertEqual(normal['board_state']['current_bid']['bidder_player_id'], expired['current_player_id'])
            self.assertEqual(normal['board_state']['participant_order'], room['board_state']['participant_order'])

    def test_mcp_projections_stay_stable_while_web_clock_advances(self):
        game = get_game('liars_dice')
        for options in ({'mode':'classic'}, {'mode':'party', 'rush_open':False},
                        {'mode':'party'}, {'mode':'party', 'team_mode':'teams'}):
            with self.subTest(options=options):
                room = f.create_room('liars_dice', 'human_first', 'human', 'human-1',
                    opponent_id='ai-1', ordered_participants=support.participants(4),
                    game_options=options)
                with patch('app.games.liars_dice.time.time', return_value=2000000000):
                    room = self.act(room, {'action':'bid', 'quantity':6, 'face':2})
                projections = []
                for now in (2000000001, 2000000008):
                    with patch('app.games.liars_dice.time.time', return_value=now):
                        public = game.public_state(room['board_state'], room['participants'])
                        before = deepcopy(public)
                        viewer = room['participants'][1]
                        snapshot = game.mcp_snapshot_state(public, viewer, room['participants'])
                        bootstrap = game.mcp_bootstrap_state(public, viewer, room['participants'])
                        self.assertNotIn('server_time', snapshot)
                        self.assertEqual(snapshot, bootstrap)
                        self.assertEqual(public, before)
                        if options['mode'] == 'party':
                            self.assertEqual(public['server_time'], now)
                            self.assertEqual(snapshot['rush_until'], public['rush_until'])
                        projections.append(snapshot)
                self.assertEqual(*projections)

    def test_concurrent_out_of_turn_winner_scores_once(self):
        room = self.bid(self.create())
        def race(pid):
            try: return self.act(room, {'action':'rush_open'}, pid)
            except f.DuelError: return None
        with ThreadPoolExecutor(3) as pool:
            replies = list(pool.map(race, ['ai-1','ai-2','ai-3']))
        winners = [r for r in replies if r]
        self.assertEqual(len(winners), 1)
        result = winners[0]
        self.assertEqual(result['revision'], room['revision']+1)
        self.assertEqual(sum(result['board_state']['scores'].values()), -1)
        self.assertEqual(len(result['board_state']['round_results']), 1)
        self.assertEqual(result['current_player_id'], 'human-1')
        next_round = self.act(result, {'action':'acknowledge_round'})
        self.assertEqual(next_round['board_state']['flow']['round_number'], 2)
        self.assertEqual(next_round['board_state']['participant_order'], room['board_state']['participant_order'])

    def test_teammate_can_rush_but_bidder_cannot(self):
        room = self.create(team_mode='teams')
        room = self.act(room, {'action':'bid','quantity':28,'face':6})
        game = get_game('liars_dice'); state = room['board_state']
        own_team = game.team_for(state, 'human-1')
        self.assertFalse(f.project_room_for_viewer(room,'human-1')['private_state']['can_rush'])
        with self.assertRaises(f.DuelError): self.act(room, {'action':'rush_open'}, 'human-1')
        teammate = next(pid for pid in state['teams'][own_team] if pid != 'human-1')
        self.assertTrue(f.project_room_for_viewer(room,teammate)['private_state']['can_rush'])
        result = self.act(room, {'action':'rush_open'}, teammate)
        self.assertEqual(sum(result['board_state']['team_scores'].values()), -1)
        self.assertEqual(len(result['board_state']['last_round_result']['penalties']), 1)
        self.assertEqual(len(result['board_state']['last_round_result']['revealed_dice_by_player']), 4)

    def test_start_shuffle_only_once_and_manual_teams_preserved(self):
        options = {'mode':'party','team_mode':'teams','random_teams':False}
        room = self.create(**{k:v for k,v in options.items() if k != 'mode'})
        room = seat(room,'human-1','red',1)
        room = seat(room,'ai-1','red',2)
        room = seat(room,'ai-2','blue',1)
        room = seat(room,'ai-3','blue',2)
        chosen = deepcopy(room['board_state']['team_selection']['seats'])
        room = expire(room)
        # Grouped colors are permitted: no red/blue interleaving step.
        with patch('app.liars_dice_setup.secrets.SystemRandom.shuffle', side_effect=lambda xs: xs.reverse()):
            started = setup.start_regular(room['room_id'], 'human', 'human-1')
        self.assertEqual(started['board_state']['teams'], chosen)
        self.assertEqual(started['turn_order'], ['ai-3','ai-2','ai-1','human-1'])
        self.assertEqual(started['board_state']['participant_order'], started['turn_order'])
        self.assertEqual(started['board_state']['marks_by_player'], {p['player_id']:p['token'] for p in started['participants']})
        for p in started['participants']:
            self.assertEqual(f.project_room_for_viewer(started,p['player_id'])['viewer']['seat'], p['seat_index'])
        with self.assertRaises(f.DuelError): setup.start_regular(room['room_id'],'human','human-1')
        self.assertEqual(f.get_room(room['room_id'])['turn_order'], started['turn_order'])

    def test_pending_does_not_shuffle_until_final_confirmation(self):
        with patch('app.liars_dice_setup.secrets.SystemRandom.shuffle', side_effect=lambda xs: xs.reverse()) as shuffled:
            room = f.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1', stake=5,
                ordered_participants=support.participants(3), game_options={'mode':'party'})
            dice = deepcopy(room['board_state']['dice_by_player'])
            self.assertEqual(shuffled.call_count, 0)
            room = f.respond_to_invitation(room['room_id'],'ai','ai-1','accept')
            self.assertEqual(shuffled.call_count, 0)
            room = f.respond_to_invitation(room['room_id'],'ai','ai-2','accept')
            self.assertEqual(shuffled.call_count, 1)
            self.assertEqual(room['turn_order'], ['ai-2','ai-1','human-1'])
            self.assertEqual(room['board_state']['dice_by_player'], dice)
            room = self.act(room, {'action':'bid','quantity':21,'face':6})
            room = self.act(room, {'action':'rush_open'}, 'ai-1')
            room = self.act(room, {'action':'end_session'}, 'human-1')
            from app.chips import get_wallet
            self.assertEqual(get_wallet('human','human-1')['balance'],195)
            self.assertEqual(sum(room['result']['settlement_deltas'].values()),0)
            self.assertEqual(shuffled.call_count, 1)

    def test_invite_shuffle_not_repeated_and_other_games_unchanged(self):
        room = invites.create_invite('liars_dice','ai','ai-1',target_player_count=3,game_options={'mode':'party'})
        for pid in ('ai-2','ai-3'): invites.join_invite(room['invite_code'],'ai',pid)
        with patch('app.invites.secrets.SystemRandom.shuffle', side_effect=lambda xs: xs.reverse()) as shuffled:
            room = invites.start_invite(room['room_id'],'ai','ai-1')
            self.assertEqual(shuffled.call_count,1)
            for _ in range(2):
                room = self.act(room, {'action':'bid','quantity':21,'face':6})
                room = self.act(room, {'action':'challenge'})
                room = self.act(room, {'action':'acknowledge_round'})
            self.assertEqual(shuffled.call_count,1)
            self.assertEqual(room['turn_order'], ['ai-3','ai-2','ai-1'])
        with patch('app.liars_dice_setup.randomize_seats') as shuffled:
            f.create_room('tictactoe','human_first','human','human-1',opponent_id='ai-1')
            shuffled.assert_not_called()

    def test_expiry_keeps_invite_takeover_cursor_and_reclaim(self):
        with patch('app.invites.npc_provider_capabilities',return_value={'available':True}):
            room=invites.create_invite('liars_dice','ai','ai-1',target_player_count=2,
                game_options={'mode':'party'},timeout_takeover_seconds=90)
        invites.join_invite(room['invite_code'],'ai','ai-2')
        room=invites.start_invite(room['room_id'],'ai','ai-1')
        room=self.act(room,{'action':'bid','quantity':14,'face':6})
        with database.write_transaction() as conn:
            conn.execute("UPDATE room_invites SET reclaim_revision=?, turn_started_at='2000-01-01T00:00:00+00:00' WHERE room_id=?",
                         (room['revision'],room['room_id']))
        with patch('app.liars_dice_rush.time.time',return_value=room['board_state']['rush_until']):
            expired=f.get_room(room['room_id'])
        self.assertEqual(expired['turn_revision'],expired['revision'])
        self.assertEqual(expired['reclaim_revision'],expired['revision'])
        self.assertGreater(expired['turn_started_at'],'2000-01-01T00:00:00+00:00')
        self.assertEqual(expired['current_player_id'],room['current_player_id'])


class RushTransportTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.LiarsDiceMcpTests.asyncSetUp
    asyncTearDown = support.LiarsDiceMcpTests.asyncTearDown

    async def mcp(self, room, pid, action='state', **kwargs):
        return await self.client.post('/mcp/play', json=dict(action=action,player_id=pid,room_id=room['room_id'],**kwargs))

    async def test_full_state_stable_with_live_clock_outside_snapshot(self):
        with patch('app.games.liars_dice.time.time', return_value=2000000000):
            room = f.create_room('liars_dice', 'human_first', 'human', 'human-1',
                opponent_id='ai-1', game_options={'mode':'party'})
            room = f.play_move(room['room_id'], 'human', 'human-1',
                {'action':'bid', 'quantity':3, 'face':2}, expected_revision=room['revision'])
            bootstrap = (await self.mcp(room, 'ai-1')).json()
            self.assertTrue(bootstrap['bootstrap'])
            self.assertNotIn('server_time', bootstrap['room']['board_state'])
            self.assertEqual(bootstrap['server_time'], 2000000000)
            self.assertEqual(bootstrap['room']['board_state']['rush_until'], 2000000010)
        snapshots = []
        for now in (2000000001, 2000000008):
            with patch('app.games.liars_dice.time.time', return_value=now):
                reply = await self.mcp(room, 'ai-1', full_state=True)
                self.assertEqual(reply.status_code, 200, reply.text)
                data = reply.json()
                self.assertEqual(data['server_time'], now)
                self.assertEqual(data['actions'], [{'action':'rush_open'}])
                snapshot = data['snapshot']
                self.assertNotIn('server_time', snapshot['board_state'])
                self.assertEqual(snapshot['board_state']['rush_until'], 2000000010)
                self.assertTrue(snapshot['private_state']['can_rush'])
                snapshots.append(snapshot)
        self.assertEqual(*snapshots)

    async def test_wait_wakes_non_next_machine_and_real_mcp_move(self):
        room = f.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',
            ordered_participants=support.participants(4),game_options={'mode':'party','team_mode':'teams'})
        order = room['turn_order']; actor_index = order.index('human-1')
        other = order[(actor_index+2)%4]
        self.assertNotEqual(other,'human-1')
        # The waiting machine is a teammate outside the ordinary next seat.
        state = room['board_state']
        state['teams'] = {'red':['human-1',other], 'blue':[p for p in order if p not in ('human-1',other)]}
        with database.write_transaction() as conn:
            conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?', (json.dumps(state),room['room_id']))
        await self.mcp(room, other)  # bootstrap before waiting
        waiter = asyncio.create_task(self.mcp(room, other, wait=True))
        await asyncio.sleep(.04)
        response = await self.client.post(f"/api/rooms/{room['room_id']}/move",
            headers={'X-Duel-Human-Player':'human-1'},json={'player_id':'human-1','revision':room['revision'],
            'move':{'action':'bid','quantity':28,'face':6}})
        self.assertEqual(response.status_code,200,response.text)
        ready = await asyncio.wait_for(waiter,1)
        data = ready.json()
        self.assertEqual(data['actions'], [{'action':'rush_open'}])
        self.assertGreater(data['delta']['rush_until'], data['server_time'])
        self.assertNotIn('wait', data)
        self.assertNotIn('dice_by_player',json.dumps(data))
        full = (await self.mcp(room,other,full_state=True)).json()
        self.assertTrue(full['snapshot']['private_state']['can_rush'])
        reply = await self.mcp(room,other,'move',revision=data['r'],move={'action':'rush_open'})
        self.assertEqual(reply.status_code,200,reply.text)
        self.assertIn('round_result',json.dumps(reply.json()))
        again = await self.mcp(room,other,'move',revision=data['r'],move={'action':'rush_open'})
        self.assertEqual(again.status_code,409)

    async def test_npc_window_reacts_out_of_turn_and_pure_npcs_progress(self):
        # Mixed table: NPC has no ordinary turn yet can win the same transaction.
        people = support.participants(4,npc_count=1)
        room = f.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',
            ordered_participants=people,game_options={'mode':'party','team_mode':'teams'})
        state = room['board_state']
        state['teams'] = {'red':['human-1','npc:test-1'], 'blue':['ai-1','ai-2']}
        with database.write_transaction() as conn:
            conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?', (json.dumps(state),room['room_id']))
        room = f.play_move(room['room_id'],'human','human-1',{'action':'bid','quantity':28,'face':6},expected_revision=0)
        notes = []
        async def resume(room): notes.append(room)
        scheduler = RushScheduler(lambda rid: None, resume)
        scheduler.started = True
        try:
            with patch('app.liars_dice_rush.random.SystemRandom.uniform',return_value=.02):
                await scheduler.schedule(room)
                for _ in range(30):
                    if notes: break
                    await asyncio.sleep(.01)
            latest = f.get_room(room['room_id'])
            self.assertEqual(latest['board_state']['last_round_result']['challenger_player_id'],'npc:test-1')
        finally: await scheduler.shutdown()
        # All NPC room: hold ordinary calls for the real deadline, expire and resume.
        people = [dict(player_id=f'npc:p{i}', role='ai',participant_kind='system_npc',npc_persona_id=f'p{i}') for i in range(3)]
        room = f.create_room('liars_dice','ai_first','ai','npc:p0',ordered_participants=people,game_options={'mode':'party'})
        ordinary = NpcTurnScheduler(room_changed=lambda rid: None)
        async def resume_npc(room): await ordinary.schedule(room['room_id'])
        rush = RushScheduler(lambda rid:None,resume_npc)
        ordinary.rush_scheduler = rush
        rush.started = True
        try:
            from app.npc_personas import NpcPersona
            with patch('app.npc_controller.get_persona',side_effect=lambda pid: NpcPersona(pid,pid,'test')), \
                    patch('app.liars_dice_rush.npc_wants_rush', return_value=False):
                # Use a deterministic local provider, retaining the real decision reservation/apply path.
                from app.npc_controller import run_current_npc_turn
                from app.npc_providers import ProviderDecision
                class Provider:
                    name='test'
                    async def decide(self, req):
                        return ProviderDecision(req.legal_actions[0]['action_id'],None)
                async def runner(rid): return await run_current_npc_turn(rid,provider=Provider())
                ordinary._turn_runner=runner
                await ordinary.start()
                await asyncio.sleep(.12)
                latest=f.get_room(room['room_id'])
                self.assertTrue(latest['board_state'].get('rush_until'))
                before=latest['revision']
                # Advance only the authoritative deadline, without changing the NPC cursor.
                with database.write_transaction() as conn:
                    state=latest['board_state']; state['rush_until']=time.time()+.06
                    conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?',(json.dumps(state),room['room_id']))
                await rush.schedule(f.get_room(room['room_id']))
                await asyncio.sleep(.18)
                after=f.get_room(room['room_id'])
                self.assertGreater(after['revision'],before+1)
        finally:
            await rush.shutdown()
            await ordinary.shutdown()

    async def test_seen_window_waits_until_deadline_without_worker(self):
        room = f.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',
            ordered_participants=support.participants(3),game_options={'mode':'party'})
        room = f.play_move(room['room_id'],'human','human-1',{'action':'bid','quantity':21,'face':6},expected_revision=0)
        with database.write_transaction() as conn:
            state = room['board_state']; state['rush_until'] = time.time()+.2
            conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?',(json.dumps(state),room['room_id']))
        initial = (await self.mcp(room,'ai-1')).json()
        self.assertEqual(initial['actions'],[{'action':'rush_open'}])
        waiter = asyncio.create_task(self.mcp(room,'ai-1',wait=True))
        await asyncio.sleep(.03)
        self.assertFalse(waiter.done(), 'already observed window should wait, not spin')
        finished = (await asyncio.wait_for(waiter,1)).json()
        self.assertEqual(finished['r'],initial['r']+1)
        self.assertIsNone(finished['delta']['rush_until'])
        self.assertEqual(finished['actions'],[])

    async def test_worker_restart_recovers_persisted_deadline(self):
        room=f.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',game_options={'mode':'party'})
        room=f.play_move(room['room_id'],'human','human-1',{'action':'bid','quantity':14,'face':6},expected_revision=0)
        with database.write_transaction() as conn:
            state=room['board_state']; state['rush_until']=time.time()+.1
            conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?',(json.dumps(state),room['room_id']))
        resumed=[]
        async def resume(room): resumed.append(room)
        scheduler=RushScheduler(lambda rid:None,resume)
        try:
            await scheduler.start()
            await asyncio.sleep(.16)
            self.assertEqual(resumed[-1]['revision'],room['revision']+1)
            self.assertNotIn('rush_until',resumed[-1]['board_state'])
        finally: await scheduler.shutdown()

    async def test_human_observed_window_long_poll_does_not_spin(self):
        room = f.create_room('liars_dice','ai_first','ai','ai-1',opponent_id='human-1',
            ordered_participants=support.participants(3),game_options={'mode':'party'})
        room = f.play_move(room['room_id'],'ai','ai-1',{'action':'bid','quantity':21,'face':6},expected_revision=0)
        with database.write_transaction() as conn:
            state=room['board_state']; state['rush_until']=time.time()+1
            conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?',(json.dumps(state),room['room_id']))
        path=f"/api/rooms/{room['room_id']}"
        headers={'X-Duel-Human-Player':'human-1'}
        initial=await self.client.get(path,headers=headers)
        self.assertTrue(initial.json()['room']['private_state']['can_rush'])
        waiter=asyncio.create_task(self.client.get(path+f"?wait=true&after_revision={room['revision']}",headers=headers))
        await asyncio.sleep(.03)
        self.assertFalse(waiter.done())
        reply=await asyncio.wait_for(waiter,3)
        self.assertEqual(reply.json()['room']['revision'],room['revision']+1)

    async def test_simultaneous_real_mcp_requests_one_transaction_wins(self):
        room = f.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',
            ordered_participants=support.participants(4),game_options={'mode':'party','team_mode':'teams'})
        room = f.play_move(room['room_id'],'human','human-1',{'action':'bid','quantity':28,'face':6},expected_revision=0)
        import os
        import subprocess
        import sys
        script = """
import asyncio, json, sys, httpx
from app import main
print('ready', flush=True)
body = json.loads(sys.stdin.readline())
async def send():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://duel.test') as client:
        reply = await client.post('/mcp/play',json=body)
        print(reply.status_code, flush=True)
asyncio.run(send())
"""
        # Independent workers exercise SQLite serialization, without sharing an
        # asyncio app's in-process notification objects across thread event loops.
        processes = [subprocess.Popen([sys.executable,'-c',script],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
            env={**os.environ,'DUEL_DB_PATH':str(database.DB_PATH)}) for _ in range(3)]
        try:
            for process in processes:
                self.assertEqual(process.stdout.readline().strip(),'ready')
            for i, process in enumerate(processes):
                process.stdin.write(json.dumps({'action':'move','player_id':f'ai-{i+1}',
                    'room_id':room['room_id'],'revision':room['revision'],'move':{'action':'rush_open'}})+'\n')
                process.stdin.flush()
            replies = [process.communicate(timeout=8) for process in processes]
            self.assertEqual(sorted(int(out.strip()) for out, err in replies),[200,409,409],replies)
        finally:
            for process in processes:
                if process.poll() is None: process.kill()
                process.communicate()
        result = f.get_room(room['room_id'])
        self.assertEqual(sum(result['board_state']['team_scores'].values()),-1)
        self.assertEqual(len(result['board_state']['round_results']),1)
