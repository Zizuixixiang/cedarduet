"""Real Duel transaction/API tests; all databases live in TemporaryDirectory."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from app import chips, database, framework, invites, npc_controller, takeover
from app import main as main_module
from app.games import GAMES
from app.games.carcassonne import Carcassonne
from app.local_mcp import forward_play, play_input_schema
from app.local_config import LOCAL_AI_ID, LOCAL_HUMAN_ID
from app.npc_personas import NpcPersona
from tests.test_carcassonne import players, tile, move


class CarcassonneIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='carcassonne-integration-');self.addCleanup(self.tmp.cleanup)
        p=patch.object(database,'DB_PATH',Path(self.tmp.name)/'test.db');p.start();self.addCleanup(p.stop)
        database.init_db();self.game=Carcassonne(random.Random(12))
        p=patch.dict(GAMES,{'carcassonne':self.game});p.start();self.addCleanup(p.stop)
        p=patch.object(main_module,'revision_events',main_module.RevisionEvents());p.start();self.addCleanup(p.stop)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=main_module.app),base_url='http://carcassonne.test');self.addAsyncCleanup(self.client.aclose)

    def ordinary(self,n=2,stake=0):
        room=framework.create_room('carcassonne','human_first','human','p0',opponent_id='p1',ordered_participants=players(n),stake=stake)
        if room.get('confirmation_required'):
            for p in players(n)[1:]:room=framework.respond_to_invitation(room['room_id'],'ai',p['player_id'],'accept')
        return room

    def invited(self,n=2,stake=0,timeout=False):
        room=invites.create_invite('carcassonne','human','p0',target_player_count=n,stake=stake,timeout_takeover=timeout)
        for p in players(n)[1:]:invites.join_invite(room['invite_code'],'ai',p['player_id'])
        with patch.object(invites.secrets,'SystemRandom') as rng:
            rng.return_value.shuffle.side_effect=lambda seats:None
            return invites.start_invite(room['room_id'],'human','p0')

    def actor(self,room):return next(p for p in room['participants'] if p['player_id']==room['current_player_id'])
    def policy(self,room):return self.game.choose_local_npc_action(room['board_state'],self.actor(room),room['participants'])
    def play(self,room,m=None):
        a=self.actor(room)
        return framework.play_move(room['room_id'],a['role'],a['player_id'],m or self.policy(room),expected_revision=room['revision'])

    def persist(self,room,state):
        with database.write_transaction() as conn:conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?',(json.dumps(state),room['room_id']))
        return framework.get_room(room['room_id'])

    async def mcp(self,**body):
        res=await self.client.post('/mcp/play',json=body);self.assertEqual(res.status_code,200,res.text);return res.json()

    def test_ordinary_invite_all_counts_and_round(self):
        for creator in (self.ordinary,self.invited):
            for n in range(2,6):
                room=creator(n);self.assertEqual(room['status'],'playing');ids=[p['player_id'] for p in room['participants']]
                self.assertEqual(len(room['board_state']['scores']),n)
                for pid in ids:self.assertEqual(room['current_player_id'],pid);room=self.play(room)
                self.assertEqual(room['current_player_id'],ids[0]);self.assertEqual(len(room['board_state']['board']),n+1)

    def test_concurrent_duplicate_stale_and_missing_revision(self):
        for creator in (self.ordinary,self.invited):
            room=creator();m=self.policy(room)
            with self.assertRaises(framework.DuelError):framework.play_move(room['room_id'],'human','p0',m)
            def attempt(_):
                try:return self.play(room,m)
                except framework.DuelError:return None
            with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,range(2)))
            self.assertEqual(sum(r is not None for r in results),1)
            now=framework.get_room(room['room_id']);self.assertEqual(now['revision'],room['revision']+1)
            self.assertEqual(len(now['board_state']['board']),2)
            self.assertEqual(len(now['board_state']['deck']),len(room['board_state']['deck'])-1)
            with self.assertRaises(framework.DuelError):self.play(room,m)
            self.assertEqual(framework.get_room(room['room_id'])['board_state'],now['board_state'])

    def test_wrong_seat_role_invalid_region_no_partial_commit(self):
        room=self.ordinary(3);m=self.policy(room)
        for role,pid,bad in [('ai','p1',m),('ai','outsider',m),('ai','p0',m),('human','p0',dict(m,meeple='f99')),('human','p0',dict(m,x=999))]:
            with self.assertRaises(framework.DuelError):framework.play_move(room['room_id'],role,pid,bad,expected_revision=room['revision'])
            now=framework.get_room(room['room_id']);self.assertEqual(now['revision'],room['revision']);self.assertEqual(now['board_state'],room['board_state'])

    def test_cross_process_persistence_same_deck_and_next_move(self):
        room=self.ordinary(4)
        for _ in range(8):room=self.play(room)
        env={**os.environ,'DUEL_DB_PATH':str(database.DB_PATH),'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(Path(__file__).resolve().parents[1])}
        code='import json,sys; from app.framework import get_room; print(json.dumps(get_room(sys.argv[1])["board_state"]))'
        out=subprocess.run([sys.executable,'-c',code,room['room_id']],capture_output=True,text=True,env=env,check=True)
        restored=json.loads(out.stdout);self.assertEqual(restored,room['board_state'])
        self.assertEqual(Carcassonne(random.Random(999)).choose_local_npc_action(restored,self.actor(room),[]),self.policy(room))

    async def test_web_creation_and_bound_mcp_all_counts(self):
        for n in range(2,6):
            machines=[{'id':p['player_id'],'name':p['display_name']} for p in players(n)[1:]]
            headers={'X-Duel-Human-Player':'p0','X-Duel-Bound-Ais':base64.urlsafe_b64encode(json.dumps(machines).encode()).decode()}
            r=await self.client.post('/api/rooms',headers=headers,json={'player_id':'p0','ai_players':[p['id'] for p in machines],'game_type':'carcassonne','target_player_count':n})
            self.assertEqual(r.status_code,200,r.text);room=framework.get_room(r.json()['room']['room_id'])
            self.assertEqual(len(room['participants']),n)
            r=await self.client.post(f"/api/rooms/{room['room_id']}/move",headers=headers,json={'player_id':'p0','revision':room['revision'],'move':self.policy(room)})
            self.assertEqual(r.status_code,200,r.text);self.assertNotIn('"deck":',r.text)
            room=framework.get_room(room['room_id'])
            await self.mcp(action='move',room_id=room['room_id'],player_id='p1',revision=room['revision'],move=self.policy(room))
            full=await self.mcp(action='state',room_id=room['room_id'],player_id='p1',full_state=True)
            self.assertNotIn('"deck":',json.dumps(full));self.assertEqual(len(full['snapshot']['board_state']['board']),3)

    async def test_mcp_invite_web_join_identity_and_moves(self):
        for n in range(2,6):
            created=await self.mcp(action='invite',player_id=f'host{n}',game_type='carcassonne',target_player_count=n)
            code,rid=created['invite_code'],created['room_id']
            r=await self.client.post('/api/invites/join',headers={'X-Duel-Human-Player':f'guest{n}'},json={'invite_code':code,'player_id':'forged'})
            self.assertEqual(r.status_code,200,r.text)
            for i in range(n-2):await self.mcp(action='join',player_id=f'ai-{n}-{i}',invite_code=code)
            with patch.object(invites.secrets,'SystemRandom') as rng:
                rng.return_value.shuffle.side_effect=lambda seats:None
                await self.mcp(action='start',player_id=f'host{n}',room_id=rid)
            room=framework.get_room(rid);self.assertNotIn('forged',[p['player_id'] for p in room['participants']])
            await self.mcp(action='move',player_id=f'host{n}',room_id=rid,revision=room['revision'],move=self.policy(room))
            room=framework.get_room(rid)
            r=await self.client.post(f'/api/rooms/{rid}/move',headers={'X-Duel-Human-Player':f'guest{n}'},json={'player_id':f'guest{n}','revision':room['revision'],'move':self.policy(room)})
            self.assertEqual(r.status_code,200,r.text)

    async def test_local_adapter_identity_schema_and_safe_delta(self):
        transport=httpx.ASGITransport(app=main_module.app)
        status,data=await forward_play({'action':'new','game_type':'carcassonne','mode':'ai_first','player_id':'forged','opponent_id':'forged-other'},transport=transport,base_url='http://carcassonne.test')
        self.assertEqual(status,200,data);room=framework.get_room(data['room']['room_id'])
        self.assertEqual({p['player_id'] for p in room['participants']},{LOCAL_AI_ID,LOCAL_HUMAN_ID})
        status,result=await forward_play({'action':'move','room_id':room['room_id'],'revision':room['revision'],'move':self.policy(room),'player_id':LOCAL_HUMAN_ID},transport=transport,base_url='http://carcassonne.test')
        self.assertEqual(status,200,result);self.assertEqual(result['events'][-1][0], 'local-ai');self.assertNotIn('"deck":',json.dumps(result))
        schema=play_input_schema();self.assertNotIn('player_id',schema['properties']);self.assertIn('revision',schema['properties'])
        self.assertIn('农夫',self.game.rules_text);self.assertIn('placements',self.game.move_format)

    async def test_lifecycle_delta_refreshes_board_without_hidden_deck(self):
        room=self.ordinary(3);room=self.play(room)
        await self.mcp(action='state',room_id=room['room_id'],player_id='p1')
        await self.mcp(action='resign',room_id=room['room_id'],player_id='p2')
        d=await self.mcp(action='state',room_id=room['room_id'],player_id='p1')
        self.assertNotIn('wait',d);self.assertIn('board',d['public_state']);self.assertNotIn('decision_context',d.get('private',{}));self.assertNotIn('"deck":',json.dumps(d))
        q=await self.mcp(action='state',room_id=room['room_id'],player_id='p1',move={'query':'placements','all':True})
        p=q['placements'][0]
        await self.mcp(action='move',room_id=room['room_id'],player_id='p1',revision=d['r'],move=move(*p[:3]))

    async def test_npc_controller_and_timeout_are_local_and_bounded(self):
        room=invites.create_invite('carcassonne','human','p0',target_player_count=3)
        invites.join_invite(room['invite_code'],'ai','p1')
        with patch.object(invites,'select_personas',return_value=[NpcPersona('cc-npc','拼图员','测试')]),patch.object(invites.secrets,'SystemRandom') as rng:
            rng.return_value.shuffle.side_effect=lambda seats:seats.sort(key=lambda p:p['participant_kind']!='system_npc')
            room=invites.start_invite(room['room_id'],'human','p0',fill_with_npcs=True)
        with patch.object(npc_controller,'get_npc_provider',side_effect=AssertionError('must not use model')),patch.object(npc_controller,'_finish_npc_action',return_value=None):
            result=await npc_controller.run_current_npc_turn(room['room_id'])
            self.assertEqual(result.status,'applied');self.assertEqual(result.source,'local')
        r=await self.client.post('/mcp/play',json={'action':'state','room_id':room['room_id'],'player_id':next(p['player_id'] for p in room['participants'] if p['participant_kind']=='system_npc')})
        self.assertEqual(r.status_code,403)
        room=self.invited(timeout=True);old=room['revision']
        def expire():
            with database.write_transaction() as conn:conn.execute('UPDATE room_invites SET turn_started_at=? WHERE room_id=?',((datetime.now(timezone.utc)-timedelta(seconds=181)).isoformat(),room['room_id']))
        expire()
        result=await takeover.run_timeout_turn(room['room_id']);self.assertIsNotNone(result);self.assertEqual(result['revision'],old+1)
        room=framework.get_room(room['room_id']);expire();a=self.actor(room);invites.reclaim(room['room_id'],a['role'],a['player_id'])
        self.assertIsNone(await takeover.run_timeout_turn(room['room_id']))

    async def test_complete_mixed_table_through_real_npc_controller(self):
        room=invites.create_invite('carcassonne','human','p0',target_player_count=5,stake=10)
        invites.join_invite(room['invite_code'],'ai','p1')
        personas=[NpcPersona(f'cc-full-{i}',f'拼图员{i}','测试') for i in range(3)]
        with patch.object(invites,'select_personas',return_value=personas):
            room=invites.start_invite(room['room_id'],'human','p0',fill_with_npcs=True)
        turns=npc_turns=0
        with patch.object(npc_controller,'get_npc_provider',side_effect=AssertionError('must not use model')),patch.object(npc_controller,'_finish_npc_action',return_value=None):
            while room['status']=='playing':
                a=self.actor(room)
                if a['participant_kind']=='system_npc':
                    result=await npc_controller.run_current_npc_turn(room['room_id'])
                    self.assertEqual(result.status,'applied');self.assertEqual(result.source,'local');npc_turns+=1
                else:self.play(room)
                room=framework.get_room(room['room_id']);turns+=1;self.assertLessEqual(turns,71)
        self.assertGreater(npc_turns,35)
        self.assertEqual(len(room['board_state']['board'])+len(room['board_state']['discarded']),72)
        self.assertEqual(sum(room['result']['settlement_deltas'].values()),0)
        npc_ids=[p['player_id'] for p in room['participants'] if p['participant_kind']=='system_npc']
        with database.connect() as conn:
            self.assertFalse(any(conn.execute('SELECT 1 FROM chip_wallets WHERE subject_id=?',(pid,)).fetchone() for pid in npc_ids))

    async def test_web_npc_fill_to_five(self):
        headers={'X-Duel-Human-Player':'p0','X-Duel-Bound-Ais':base64.urlsafe_b64encode(json.dumps([{'id':'p1','name':'小机'}]).encode()).decode()}
        personas=[NpcPersona(f'cc-{i}',f'NPC{i}','测试') for i in range(3)]
        with patch.object(main_module,'select_personas',return_value=personas),patch.object(main_module,'npc_provider_capabilities',side_effect=AssertionError('local only')):
            r=await self.client.post('/api/rooms',headers=headers,json={'player_id':'p0','ai_players':['p1'],'game_type':'carcassonne','target_player_count':5,'fill_with_npcs':True})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(len(r.json()['room']['participants']),5)

    async def test_full_persisted_normal_and_invite_games_mcp_web_rematch(self):
        for creator,n in ((self.ordinary,2),(self.invited,5)):
            room=creator(n);turn=0
            while room['status']=='playing':
                a=self.actor(room);m=self.policy(room)
                if a['role']=='human':
                    r=await self.client.post(f"/api/rooms/{room['room_id']}/move",headers={'X-Duel-Human-Player':a['player_id']},json={'player_id':a['player_id'],'revision':room['revision'],'move':m})
                    self.assertEqual(r.status_code,200,r.text);self.assertNotIn('"deck":',r.text)
                else:
                    d=await self.mcp(action='move',room_id=room['room_id'],player_id=a['player_id'],revision=room['revision'],move=m)
                    self.assertNotIn('"deck":',json.dumps(d))
                room=framework.get_room(room['room_id']);turn+=1;self.assertLessEqual(turn,71)
            self.assertEqual(len(room['board_state']['board'])+len(room['board_state']['discarded']),72)
            self.assertEqual(room['board_state']['supply'],{f'p{i}':7 for i in range(n)})
            if creator==self.ordinary:
                d=await self.mcp(action='rematch',room_id=room['room_id'],player_id='p1')
                fresh=framework.get_room(d['room']['room_id']);self.assertNotEqual(fresh['room_id'],room['room_id']);self.assertEqual(len(fresh['board_state']['board']),1)
            else:
                r=await self.client.post('/mcp/play',json={'action':'rematch','room_id':room['room_id'],'player_id':'p1'});self.assertEqual(r.status_code,409)

    def test_wallet_settlement_exactly_once_unique_and_tie(self):
        for creator,n,tie in ((self.ordinary,2,False),(self.invited,5,False),(self.ordinary,3,True)):
            room=creator(n,stake=10);s=deepcopy(room['board_state'])
            s.update(board=[tile('E')],current_tile='E',deck=[],scores={f'p{i}':0 for i in range(n)},supply={f'p{i}':7 for i in range(n)})
            room=self.persist(room,s)
            # Final completion scores p0=4 or no one, allowing a tied finish.
            room=self.play(room,move(0,-1,2,None if tie else 'c0'))
            d=room['result']['settlement_deltas'];self.assertEqual(sum(d.values()),0)
            self.assertEqual(d['p0'],0 if tie else (n-1)*10)
            with database.connect() as conn:
                ledger=[tuple(r) for r in conn.execute('SELECT id,amount,transaction_type FROM chip_ledger').fetchall()]
            framework.get_room(room['room_id']);framework.get_room(room['room_id'])
            with database.connect() as conn:self.assertEqual([tuple(r) for r in conn.execute('SELECT id,amount,transaction_type FROM chip_ledger').fetchall()],ledger)

if __name__=='__main__':unittest.main()
