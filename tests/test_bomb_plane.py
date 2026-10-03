import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from app import database, framework, invites, chips, takeover
from app.games import get_game, game_catalog
from app.games.bomb_plane import BombPlane, plane_cells, validate_layout, random_layout, candidates, inferred_attack, SQUARES
from app.npc_controller import run_current_npc_turn, _speech_request, _decision_request
from app.npc_personas import NpcPersona

LAYOUT = [{'head':'C1','direction':'N'}, {'head':'H1','direction':'N'}, {'head':'C6','direction':'N'}]
OVERLAP_LAYOUT = [{'head':'C1','direction':'N'}, {'head':'D1','direction':'N'}, {'head':'C2','direction':'N'}]


def players(kind='bound_machine'):
    return [{'player_id': 'p0', 'role':'human', 'seat_index':0, 'token':'X', 'participant_kind':'human',
             'display_name':'玩家零', 'active':True, 'join_status':'joined'},
            {'player_id': 'p1', 'role':'ai' if kind!='human' else 'human', 'seat_index':1, 'token':'O',
             'participant_kind':kind, 'npc_persona_id':'test-npc' if kind=='system_npc' else None,
             'display_name':'玩家一', 'active':True, 'join_status':'joined'}]


def playing():
    g=BombPlane(random.Random(7)); ps=players(); s=g.initialize(ps)
    for p in ps:
        s=g.apply_action(s,{'action':'set_layout','planes':LAYOUT},p).state
        s=g.apply_action(s,{'action':'ready'},p).state
    return g,s,ps


class BombPlaneRules(unittest.TestCase):
    def test_shape_rotations_boundaries_collisions(self):
        self.assertEqual(set(plane_cells(LAYOUT[0])),{'C1','A2','B2','C2','D2','E2','C3','B4','C4','D4'})
        expected={'N': {'E5','C6','D6','E6','F6','G6','E7','D8','E8','F8'},
                  'E': {'E5','D3','D4','D5','D6','D7','C5','B4','B5','B6'},
                  'S': {'E5','G4','F4','E4','D4','C4','E3','F2','E2','D2'},
                  'W': {'E5','F7','F6','F5','F4','F3','G5','H6','H5','H4'}}
        for d,cells in expected.items():
            self.assertEqual(set(plane_cells({'head':'E5','direction':d})),cells)
        self.assertEqual(len(candidates()),168)
        for head in ('A1','J10'):
            for d in 'NESW':
                with self.assertRaises(ValueError): plane_cells({'head':head,'direction':d})
        for bad in ('a1','A01','K1','A0','A11',True,None):
            with self.assertRaises(ValueError): plane_cells({'head':bad,'direction':'N'})
        with self.assertRaises(ValueError): validate_layout([LAYOUT[0],LAYOUT[0]])
        validate_layout(LAYOUT)
        for seed in range(100):
            planes=random_layout(random.Random(seed)); validate_layout(planes); self.assertEqual(len(planes),3)

    def test_manual_edit_undo_random_ready_and_lock(self):
        g=BombPlane(random.Random(2)); ps=players(); s=g.initialize(ps)
        with self.assertRaises(ValueError):g.apply_action(s,{'action':'ready'},ps[0])
        s=g.apply_action(s,{'action':'place',**LAYOUT[0]},ps[0]).state
        self.assertEqual(s['active_player_id'],'p0')
        s=g.apply_action(s,{'action':'undo'},ps[0]).state;self.assertEqual(s['planes']['p0'],[])
        s=g.apply_action(s,{'action':'shuffle'},ps[0]).state;self.assertEqual(len(s['planes']['p0']),3)
        s=g.apply_action(s,{'action':'clear'},ps[0]).state;self.assertEqual(s['planes']['p0'],[])
        s=g.apply_action(s,{'action':'set_layout','planes':LAYOUT},ps[0]).state
        s=g.apply_action(s,{'action':'ready'},ps[0]).state
        before=deepcopy(s)
        with self.assertRaises(ValueError):g.apply_action(s,{'action':'shuffle'},ps[0])
        self.assertEqual(s,before)
        s=g.apply_action(s,{'action':'auto_setup'},ps[1]).state
        self.assertEqual(s['phase'],'play');self.assertEqual(s['active_player_id'],'p0')
        for p in ps:
            with self.assertRaises(ValueError):g.apply_action(s,{'action':'set_layout','planes':LAYOUT},p)

    def test_overlapping_bodies_and_head_on_body_are_legal_but_heads_are_unique(self):
        first, second, third = OVERLAP_LAYOUT
        self.assertIn('C2', set(plane_cells(first)) & set(plane_cells(second)))
        validate_layout([first, second])
        self.assertIn(third['head'], plane_cells(first))
        validate_layout(OVERLAP_LAYOUT)
        validate_layout(list(reversed(OVERLAP_LAYOUT)))
        # Different directions at the same head must also be rejected.
        with self.assertRaisesRegex(ValueError, '机头不能重合'):
            validate_layout([{'head':'E5','direction':'N'}, {'head':'E5','direction':'E'}])
        with self.assertRaisesRegex(ValueError, '越界'):
            validate_layout([first, {'head':'A1','direction':'N'}])
        g = BombPlane(); ps = players(); state = g.initialize(ps)
        for plane in OVERLAP_LAYOUT:
            state = g.apply_action(state, {'action':'place', **plane}, ps[1]).state
        self.assertEqual(state['planes']['p1'], OVERLAP_LAYOUT)
        self.assertEqual(state['active_player_id'], 'p0')

    def test_random_layout_accepts_overlap_with_unique_in_bounds_heads(self):
        overlapping = 0
        for seed in range(100):
            planes = random_layout(random.Random(seed))
            self.assertEqual(len(planes), 3)
            self.assertEqual(len({p['head'] for p in planes}), 3)
            cells = [set(plane_cells(p)) for p in planes]
            self.assertTrue(all(len(c) == 10 for c in cells))
            validate_layout(planes)
            overlapping += len(set.union(*cells)) < 30
        self.assertGreater(overlapping, 0)
        # A supplied candidate order can contain both body overlap and duplicate
        # head directions; the generator must skip only the duplicate heads.
        options = [(p['head'], p['direction'], frozenset(plane_cells(p)))
                   for p in OVERLAP_LAYOUT]
        options.insert(1, options[0])
        with patch('app.games.bomb_plane.candidates', return_value=tuple(options)):
            with patch.object(random.Random, 'shuffle', return_value=None):
                self.assertEqual(random_layout(random.Random(0)), OVERLAP_LAYOUT)

    def test_overlap_attack_head_priority_and_three_distinct_heads_win(self):
        for layout in (OVERLAP_LAYOUT, list(reversed(OVERLAP_LAYOUT))):
            g = BombPlane(); ps = players(); state = g.initialize(ps)
            for actor in ps:
                state = g.apply_action(state, {'action':'set_layout', 'planes':layout}, actor).state
                state = g.apply_action(state, {'action':'ready'}, actor).state
            for index, (cell, result) in enumerate([('D2','hit'), ('J10','miss'),
                                                    ('C2','head'), ('C1','head'), ('D1','head')]):
                state = g.apply_action(state, {'action':'attack','cell':cell}, ps[0]).state
                self.assertEqual(state['shots']['p0'][-1], {'cell':cell, 'result':result})
                if index < 4:
                    self.assertEqual(state['phase'], 'play')
                    self.assertNotIn('revealed_planes', g.public_state(state, ps))
                    state = g.apply_action(state, {'action':'attack','cell':f'J{index+1}'}, ps[1]).state
                    before = deepcopy(state)
                    with self.assertRaises(ValueError):
                        g.apply_action(state, {'action':'attack','cell':cell}, ps[0])
                    self.assertEqual(state, before)
            self.assertEqual(state['phase'], 'finished')
            self.assertEqual(g.result_for(state, ps), {'winner_player_id':'p0','draw':False})
            self.assertEqual({s['cell'] for s in state['shots']['p0'] if s['result']=='head'},
                             {'C1', 'D1', 'C2'})

    def test_visible_head_does_not_exclude_a_candidate_body_crossing_it(self):
        plane = OVERLAP_LAYOUT[0]
        option = (plane['head'], plane['direction'], frozenset(plane_cells(plane)))
        with patch('app.games.bomb_plane.candidates', return_value=(option,)):
            self.assertEqual(inferred_attack([{'cell':'C2','result':'head'}], random.Random(0)), 'C1')

    def test_shooting_turns_three_heads_and_downed_body_feedback(self):
        g,s,ps=playing()
        for cell,result in [('J10','miss'),('C1','head'),('C2','hit'),('H1','head'),('C6','head')]:
            s=g.apply_action(s,{'action':'attack','cell':cell},ps[0]).state
            self.assertEqual(s['shots']['p0'][-1],{'cell':cell,'result':result})
            if cell!='C6':
                self.assertEqual(s['active_player_id'],'p1')
                self.assertNotIn('revealed_planes',g.public_state(s,ps))
                spare=next(c for c in SQUARES if c not in {t['cell'] for t in s['shots']['p1']})
                s=g.apply_action(s,{'action':'attack','cell':spare},ps[1]).state
                self.assertEqual(s['active_player_id'],'p0')
        self.assertEqual(g.result_for(s,ps),{'winner_player_id':'p0','draw':False})
        self.assertEqual(s['phase'],'finished')
        self.assertEqual(g.public_state(s,ps)['revealed_planes'],s['planes'])

    def test_invalid_moves_are_pure_and_do_not_touch_rng(self):
        g,s,ps=playing();s['shots']['p0']=[{'cell':'E5','result':'miss'}]
        before=deepcopy(s);seed=g.rng.getstate()
        for move in ({'action':'attack','cell':'E5'},{'action':'attack','cell':'K4'},
                     {'action':'attack','cell':'A1','player_id':'p1'}, {'action':'shuffle'},None,[],'bad'):
            with self.assertRaises(ValueError):g.apply_action(s,move,ps[0])
            self.assertEqual(s,before);self.assertEqual(g.rng.getstate(),seed)
        with self.assertRaises(ValueError):g.apply_action(s,{'action':'attack','cell':'A1'},{'player_id':'stranger'})

    def test_privacy_legal_specs_and_npc_invariance_compatible_layouts(self):
        g,s,ps=playing(); s['shots']['p0']=[{'cell':'J10','result':'miss'}]
        public=g.public_state(s,ps);private=g.private_state(s,ps[0],ps)
        legal=g.npc_legal_actions(s,ps[0],ps)
        baseline=BombPlane(random.Random(44)).choose_local_npc_action(s,ps[0],ps)
        replacements=0
        for seed in range(60):
            new=random_layout(random.Random(seed))
            if any('J10' in plane_cells(p) for p in new):continue
            altered=deepcopy(s);altered['planes']['p1']=new;replacements+=1
            self.assertEqual(g.public_state(altered,ps),public)
            self.assertEqual(g.private_state(altered,ps[0],ps),private)
            self.assertEqual(g.npc_legal_actions(altered,ps[0],ps),legal)
            self.assertEqual(BombPlane(random.Random(44)).choose_local_npc_action(altered,ps[0],ps),baseline)
        self.assertGreater(replacements,10)
        self.assertEqual(g.private_state(s,{'player_id':'outsider'},ps),{})
        self.assertEqual(private['planes'],LAYOUT)
        self.assertNotIn('planes',public)

    def test_complete_simulations_and_json_restore_bounded(self):
        started=time.monotonic();counts=[]
        for seed in range(30):
            g=BombPlane(random.Random(seed));ps=players();s=g.initialize_for_first_player(ps,ps[seed%2]['player_id'])
            for n in range(202):
                actor=next(p for p in ps if p['player_id']==s['active_player_id'])
                move=g.choose_local_npc_action(s,actor,ps);g.validate_action(s,move,actor)
                s=g.apply_action(s,move,actor).state
                s=json.loads(json.dumps(s))
                for planes in s['planes'].values():validate_layout(planes)
                if s['phase']=='finished':break
            else:self.fail('NPC did not finish')
            counts.append(n+1)
        self.assertLess(time.monotonic()-started,12)
        print('30 seeded complete games; moves min/max:',min(counts),max(counts))


class BombPlaneRooms(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='bomb-plane-')
        self.db=patch.object(database,'DB_PATH',Path(self.tmp.name)/'duel.db');self.db.start();database.init_db()
        self.addCleanup(self.tmp.cleanup);self.addCleanup(self.db.stop)

    def create(self,kind='bound_machine'):
        return framework.create_room('bomb_plane','human_first','human','p0',ordered_participants=players(kind))

    def move(self,r,move):
        actor=next(p for p in r['participants'] if p['player_id']==r['current_player_id'])
        return framework.play_move(r['room_id'],actor['role'],actor['player_id'],move,expected_revision=r['revision'])

    def db_snapshot(self):
        with database.connect() as conn:return '\n'.join(conn.iterdump())

    def test_catalog_waiting_and_each_seat_identity(self):
        item=next(x for x in game_catalog() if x['game_type']=='bomb_plane')
        self.assertEqual(item['allowed_player_counts'],[2]);self.assertEqual(item['category'],'board')
        self.assertTrue(item['supports_stakes']);self.assertTrue(item['uses_local_npc_strategy'])
        r=framework.create_room('bomb_plane','ai_first','ai','waiting-ai')
        self.assertEqual(r['status'],'waiting');framework.project_room_for_viewer(r,'waiting-ai')
        r=framework.join_room(r['room_id'],'human','joined-human')
        self.assertEqual(r['status'],'playing')
        self.assertEqual(len(r['board_state']['participant_order']),2)
        r=self.create()
        for p in r['participants']:
            view=framework.project_room_for_viewer(r,p['player_id'])
            self.assertEqual(view['viewer']['player_id'],p['player_id'])
            self.assertEqual(view['private_state']['planes'],[])
        with self.assertRaises(framework.DuelError):framework.project_room_for_viewer(r,'other')

    def test_private_events_revision_errors_and_concurrent_requests_do_not_write(self):
        r=self.create();r=self.move(r,{'action':'place',**LAYOUT[0]})
        opponent=framework.list_timeline(r['room_id'],100,'p1')
        self.assertNotIn('"head": "C1"',json.dumps(opponent))
        self.assertNotIn('"head": "C1"',json.dumps(framework.project_room_for_viewer(r,'p1')))
        before=self.db_snapshot()
        cases=[('human','p0',{'action':'shuffle'},None),('human','p0',{'action':'shuffle'},r['revision']-1),
               ('human','outsider',{'action':'shuffle'},r['revision']),
               ('human','p0',{'action':'place',**LAYOUT[0]},r['revision']),
               ('human','p0',{'action':'set_layout','planes':LAYOUT,'player_id':'p1'},r['revision'])]
        for role,pid,move,rev in cases:
            with self.assertRaises(framework.DuelError):framework.play_move(r['room_id'],role,pid,move,expected_revision=rev)
            self.assertEqual(self.db_snapshot(),before)
        def attempt(_):
            try:self.move(r,{'action':'shuffle'});return True
            except framework.DuelError:return False
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sorted(pool.map(attempt,range(2))),[False,True])

    def test_attack_duplicate_and_post_start_edit_do_not_write(self):
        r=self.create();r=self.move(r,{'action':'auto_setup'});r=self.move(r,{'action':'auto_setup'})
        r=self.move(r,{'action':'attack','cell':'A1'});r=self.move(r,{'action':'attack','cell':'A1'})
        before=self.db_snapshot()
        for move in ({'action':'attack','cell':'A1'},{'action':'shuffle'},{'action':'ready'}):
            with self.assertRaises(framework.DuelError):self.move(r,move)
            self.assertEqual(self.db_snapshot(),before)

    def test_invites_human_machine_and_npc_seats(self):
        for role in ('human','ai'):
            for friend in ('human','ai'):
                owner=f'{role}-{friend}';r=invites.create_invite('bomb_plane',role,owner,target_player_count=2)
                invites.join_invite(r['invite_code'],friend,owner+'-friend')
                r=invites.start_invite(r['room_id'],role,owner)
                self.assertEqual(len(r['participants']),2)
                r=self.move(r,{'action':'auto_setup'});r=self.move(r,{'action':'auto_setup'})
                for p in r['participants']:
                    v=framework.project_room_for_viewer(r,p['player_id']);self.assertEqual(len(v['private_state']['planes']),3)
                    self.assertNotIn('planes',v['board_state'])
                framework.resign(r['room_id'],role,owner)
        with self.assertRaises(framework.DuelError):invites.create_invite('bomb_plane','human','bad',target_player_count=3)

    def test_npc_contexts_hide_layout_and_complete_controller_game(self):
        r=self.create('system_npc');g=get_game('bomb_plane');turns=0
        with patch('app.npc_controller._finish_npc_action',return_value=None), patch('app.npc_controller.get_persona',return_value=NpcPersona('test-npc','测试','只说公开反馈')):
            for _ in range(202):
                actor=next(p for p in r['participants'] if p['player_id']==r['current_player_id'])
                if actor['participant_kind']=='system_npc':
                    actions=g.npc_legal_actions(r['board_state'],actor,r['participants'])
                    request,_=_decision_request(r,'p1',actions)
                    self.assertNotIn('planes',request.public_state)
                    self.assertEqual(request.private_state.get('planes',[]),r['board_state']['planes']['p1'])
                    turn=asyncio.run(run_current_npc_turn(r['room_id']));self.assertEqual(turn.source,'local');r=turn.room;turns+=1
                    if r['status']!='finished':
                        speech=_speech_request(r,'p1')
                        self.assertEqual(speech.private_state,{})
                        self.assertNotIn('planes',speech.public_state)
                        self.assertNotIn('set_layout',json.dumps(speech.visible_timeline))
                else:r=self.move(r,g.choose_local_npc_action(r['board_state'],actor,r['participants']))
                if r['status']=='finished':break
            else:self.fail('Controller did not finish')
        self.assertGreater(turns,0)
        with database.connect() as conn:self.assertEqual(conn.execute("SELECT COUNT(*) FROM npc_decisions WHERE status='completed'").fetchone()[0],turns)
        for p in r['participants']:
            self.assertEqual(framework.project_room_for_viewer(r,p['player_id'])['board_state']['revealed_planes'],r['board_state']['planes'])

    def test_npc_speech_drops_even_own_manual_layout_events(self):
        r=self.create('system_npc')
        r=self.move(r,{'action':'auto_setup'})
        r=self.move(r,{'action':'set_layout','planes':LAYOUT})
        self.assertIn('set_layout',json.dumps(framework.list_timeline(r['room_id'],100,'p1')))
        with patch('app.npc_controller.get_persona',return_value=NpcPersona('test-npc','测试','公开发言')):
            speech=_speech_request(r,'p1')
        self.assertEqual(speech.private_state,{})
        self.assertNotIn('planes',speech.public_state)
        self.assertNotIn('set_layout',json.dumps(speech.visible_timeline))

    def test_invite_timeout_deploys_and_attacks_as_original_identity(self):
        r=invites.create_invite('bomb_plane','human','owner',target_player_count=2,timeout_takeover_seconds=90)
        invites.join_invite(r['invite_code'],'ai','friend')
        r=invites.start_invite(r['room_id'],'human','owner')
        for step in range(3):
            then=(datetime.now(timezone.utc)-timedelta(seconds=91)).isoformat()
            with database.write_transaction() as conn:
                conn.execute('UPDATE room_invites SET turn_started_at=? WHERE room_id=?',(then,r['room_id']))
            out=asyncio.run(takeover.run_timeout_turn(r['room_id']))
            self.assertIsNotNone(out);self.assertEqual(out['revision'],r['revision']+1)
            self.assertEqual([p['participant_kind'] for p in out['participants']],[p['participant_kind'] for p in r['participants']])
            r=out
        self.assertEqual(r['board_state']['phase'],'play')
        self.assertEqual(sum(map(len,r['board_state']['shots'].values())),1)

    def test_standard_stake_win_and_resignation_exact_settlement(self):
        for ending in ('win','resign'):
            human='human-'+ending;ai='ai-'+ending
            r=framework.create_room('bomb_plane','human_first','human',human,ai,stake=3)
            self.assertEqual(r['status'],'pending')
            r=framework.respond_to_invitation(r['room_id'],'ai',ai,'accept')
            if ending=='resign':
                r=framework.resign(r['room_id'],'ai',ai)
            else:
                for _ in range(2):
                    r=self.move(r,{'action':'set_layout','planes':LAYOUT})
                    r=self.move(r,{'action':'ready'})
                for i,head in enumerate(('C1','H1','C6')):
                    r=self.move(r,{'action':'attack','cell':head})
                    if i<2:r=self.move(r,{'action':'attack','cell':['J10','I10'][i]})
            self.assertEqual(r['winner_player_id'],human)
            for role,pid,expected in [('human',human,3),('ai',ai,-3)]:
                ledger=[e for e in chips.list_ledger(role,pid) if e['transaction_type'] in ('duel_win','duel_loss')]
                self.assertEqual(len(ledger),1);self.assertEqual(ledger[0]['amount'],expected)
            # Merely reading a terminal room must never settle again.
            framework.get_room(r['room_id'])
            self.assertEqual(len([e for e in chips.list_ledger('human',human) if e['transaction_type']=='duel_win']),1)

    def test_cross_process_restore_and_resign_reveal(self):
        r=self.create();r=self.move(r,{'action':'set_layout','planes':LAYOUT})
        env={**os.environ,'DUEL_DB_PATH':str(database.DB_PATH),'PYTHONDONTWRITEBYTECODE':'1'}
        code="import json,sys;from app.framework import get_room,project_room_for_viewer;print(json.dumps(project_room_for_viewer(get_room(sys.argv[1]),'p0')))"
        loaded=json.loads(subprocess.check_output([sys.executable,'-c',code,r['room_id']],env=env))
        self.assertEqual(loaded['private_state']['planes'],LAYOUT);self.assertEqual(loaded['revision'],r['revision'])
        r=self.move(r,{'action':'ready'})
        r=self.move(r,{'action':'auto_setup'})
        r=self.move(r,{'action':'attack','cell':'E5'})
        loaded=json.loads(subprocess.check_output([sys.executable,'-c',code,r['room_id']],env=env))
        self.assertEqual(loaded,framework.project_room_for_viewer(r,'p0'))
        r=framework.resign(r['room_id'],'human','p0')
        self.assertIn('revealed_planes',framework.project_room_for_viewer(r,'p1')['board_state'])
        with self.assertRaises(framework.DuelError):framework.project_room_for_viewer(r,'outsider')


class BombPlaneTransport(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx
        from app import main
        self.tmp=tempfile.TemporaryDirectory(prefix='bomb-plane-http-')
        self.db=patch.object(database,'DB_PATH',Path(self.tmp.name)/'http.db');self.db.start();database.init_db()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test')
    async def asyncTearDown(self):
        await self.client.aclose();self.db.stop();self.tmp.cleanup()
    async def test_overlap_layout_via_ordinary_and_invite_mcp_keeps_setup_independent(self):
        for invited in (False, True):
            if invited:
                room = invites.create_invite('bomb_plane','ai','m0',target_player_count=2)
                invites.join_invite(room['invite_code'],'ai','m1')
                room = invites.start_invite(room['room_id'],'ai','m0')
            else:
                room = framework.create_room('bomb_plane','human_first','human','p0',ordered_participants=players())
            cursor = room['current_player_id']
            pid = next(p['player_id'] for p in room['participants'] if p['player_id'] != cursor)
            bootstrap = await self.client.post('/mcp/play',json={'action':'state',
                'room_id':room['room_id'],'player_id':pid,'wait':False})
            self.assertEqual(bootstrap.status_code,200,bootstrap.text)
            self.assertIn('飞机可互相重叠，但三个机头不能重合',bootstrap.text)
            response = await self.client.post('/mcp/play',json={'action':'state',
                'room_id':room['room_id'],'player_id':pid,'full_state':True})
            self.assertEqual(response.status_code,200,response.text)
            self.assertIn('overlap allowed, unique heads',bootstrap.text)
            self.assertEqual(response.json()['snapshot']['private_state'],{'planes':[]})
            async def move(payload, revision):
                return await self.client.post('/mcp/play',json={'action':'move','room_id':room['room_id'],
                    'player_id':pid,'revision':revision,'move':payload})
            response = await move({'action':'set_layout','planes':OVERLAP_LAYOUT},room['revision'])
            self.assertEqual(response.status_code,200,response.text)
            latest = framework.get_room(room['room_id'])
            self.assertEqual(latest['current_player_id'],cursor)
            self.assertEqual(latest['board_state']['planes'][pid],OVERLAP_LAYOUT)
            self.assertEqual(framework.project_room_for_viewer(latest,cursor)['private_state']['planes'],[])
            duplicate = await move({'action':'set_layout','planes':[OVERLAP_LAYOUT[0]]*3},latest['revision'])
            self.assertEqual(duplicate.status_code,400,duplicate.text)
            self.assertEqual(framework.get_room(room['room_id'])['revision'],latest['revision'])
            response = await move({'action':'ready'},latest['revision'])
            self.assertEqual(response.status_code,200,response.text)
            latest = framework.get_room(room['room_id'])
            self.assertEqual(latest['board_state']['phase'],'setup')
            actor = next(p for p in latest['participants'] if p['player_id']==cursor)
            latest = framework.play_move(latest['room_id'],actor['role'],cursor,{'action':'auto_setup'},expected_revision=latest['revision'])
            self.assertEqual(latest['board_state']['phase'],'play')
            self.assertEqual(latest['current_player_id'],cursor)

    async def test_mcp_invite_guide_and_actions(self):
        async def call(**body):
            r=await self.client.post('/mcp/play',json=body);self.assertEqual(r.status_code,200,r.text);return r.json()
        r=await call(action='invite',player_id='m0',game_type='bomb_plane',target_player_count=2)
        await call(action='join',player_id='m1',invite_code=r['invite_code'])
        await call(action='start',player_id='m0',room_id=r['room_id'])
        raw=framework.get_room(r['room_id']);pid=raw['current_player_id']
        v=await call(action='state',room_id=r['room_id'],player_id=pid,full_state=True)
        if v.get('full_state'):
            self.assertEqual(v['snapshot']['action_formats']['place']['direction'],['N','E','S','W'])
            self.assertIn('set_layout',v['snapshot']['action_formats'])
        else:
            self.assertIn('legal_action_spec',json.dumps(v))
        out=await call(action='move',room_id=r['room_id'],player_id=pid,revision=raw['revision'],move={'action':'set_layout','planes':LAYOUT})
        self.assertEqual(out['r'],raw['revision']+1)
        other=next(p['player_id'] for p in raw['participants'] if p['player_id']!=pid)
        v=await call(action='state',room_id=r['room_id'],player_id=other,full_state=True)
        self.assertNotIn('"head": "C1"',json.dumps(v))
        stale=await self.client.post('/mcp/play',json=dict(action='move',room_id=r['room_id'],player_id=pid,revision=raw['revision'],move={'action':'shuffle'}))
        self.assertEqual(stale.status_code,409)
    async def test_web_internal_proxy_identity_rejects_outsider(self):
        r=framework.create_room('bomb_plane','human_first','human','p0',ordered_participants=players())
        url='/api/rooms/'+r['room_id']+'/move'
        bad=await self.client.post(url,headers={'X-Duel-Human-Player':'outsider'},json={'player_id':'outsider','revision':0,'move':{'action':'shuffle'}})
        self.assertEqual(bad.status_code,403)
        good=await self.client.post(url,headers={'X-Duel-Human-Player':'p0'},json={'player_id':'p0','revision':0,'move':{'action':'place',**LAYOUT[0]}})
        self.assertEqual(good.status_code,200,good.text)
        self.assertEqual(good.json()['room']['private_state']['planes'],[LAYOUT[0]])

if __name__=='__main__':unittest.main()
