import asyncio
from collections import Counter
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
import time
import unittest
from unittest.mock import patch

from app import database, framework, invites, takeover
from app.games import game_catalog, get_game
from app.games.rummikub import Rummikub, TILES, build_tiles, meld_info
from app.npc_controller import run_current_npc_turn
from app.npc_personas import NpcPersona


def tile(color, number, copy=1):
    return f'{color}-{number}-{copy}'


def run(color, start, end, copy=1):
    return [tile(color, n, copy) for n in range(start, end + 1)]


def group(number, colors=('red', 'blue', 'black'), copy=1):
    return [tile(c, number, copy) for c in colors]


def players(count=2):
    return [{'player_id': f'p{i}', 'role': 'human' if i == 0 else 'ai',
             'seat_index': i, 'token': f'P{i + 1}', 'participant_kind': 'human' if i == 0 else 'bound_machine',
             'display_name': f'玩家{i}', 'active': True, 'join_status': 'joined'} for i in range(count)]


def fixture(hand, table=None, opened=True, other=None, count=2, empty_pool=False):
    g = Rummikub(random.Random(9))
    s = g.initialize(players(count))
    s['hands'] = {f'p{i}': list(other if i == 1 and other is not None else [tile('black', 13, 2)])
                  for i in range(count)}
    # Fixtures with >2 seats should supply their own hands when testing identity.
    s['hands']['p0'] = list(hand)
    s['melds'] = deepcopy(table or [])
    s['opened'] = {p: opened for p in s['hands']}
    used = set(t for m in s['melds'] for t in m) | set(t for h in s['hands'].values() for t in h)
    s['pool'] = [] if empty_pool else [t for t in TILES if t not in used]
    return g, s


def meld(melds):
    return {'action': 'meld', 'melds': melds}


class RummikubRuleTests(unittest.TestCase):
    def assert_invalid(self, g, state, move, message=''):
        before = deepcopy(state)
        with self.assertRaisesRegex(ValueError, message):
            g.apply_action(state, move, {'player_id': 'p0'})
        self.assertEqual(state, before, 'invalid drafts must be pure')

    def test_catalog_deal_and_entity_conservation_2_3_4(self):
        catalog = next(g for g in game_catalog() if g['game_type'] == 'rummikub')
        self.assertEqual(catalog['display_name'], '拉密')
        self.assertEqual(catalog['category'], 'tabletop')
        self.assertEqual(catalog['allowed_player_counts'], [2, 3, 4])
        self.assertEqual(catalog['recommended_players'], 4)
        self.assertFalse(catalog['supports_stakes'])
        self.assertEqual(len(build_tiles()), 106)
        self.assertEqual(len(TILES), 106)
        counts = Counter((t['color'], t['number']) for t in build_tiles())
        self.assertEqual(set(counts.values()), {2})
        for count in (2, 3, 4):
            s = Rummikub(random.Random(count)).initialize(players(count))
            self.assertEqual([len(h) for h in s['hands'].values()], [14] * count)
            self.assertEqual(len(s['pool']), 106 - count * 14)
            self.assertEqual(set(s['pool'] + sum(s['hands'].values(), [])), set(TILES))
        with self.assertRaises(ValueError):
            Rummikub().initialize(players(5))

    def test_group_run_boundaries_and_order(self):
        valid = [group(7), group(7, ('red','blue','black','orange')), run('red',1,13),
                 ['joker-1', 'red-2-1', 'red-3-1'], ['red-11-1','joker-1','joker-2']]
        for ids in valid:
            self.assertGreater(meld_info(ids)['points'], 0)
        invalid = [group(7)[:2], ['red-7-1','red-7-2','blue-7-1'],
                   ['red-12-1','red-13-1','red-1-1'], ['red-1-1','blue-2-1','red-3-1'],
                   ['red-3-1','red-2-1','red-1-1'], ['joker-1','red-1-1','red-2-1'],
                   ['red-12-1','red-13-1','joker-1'], ['red-1-1']*3]
        for ids in invalid:
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                meld_info(ids)

    def test_opening_29_rejected_30_accepted_and_joker_value(self):
        for table, good in [([run('red',7,9), ['blue-1-1','blue-2-1','joker-1']], True),
                            ([run('red',7,9), ['joker-1','blue-2-1','blue-3-1']], True),
                            ([run('red',6,8), run('blue',1,3)], False),
                            ([['red-9-1','joker-1','red-11-1']], True),
                            ([run('red',4,6), group(4), ['joker-1','blue-1-1','blue-2-1']], False)]:
            # Last is also an illegal run, guarding a zero-valued end joker.
            g,s=fixture(sum(table, []), opened=False)
            if good:
                out=g.apply_action(s,meld(table),players()[0]).state
                self.assertTrue(out['opened']['p0'])
            else:
                self.assert_invalid(g,s,meld(table))
        # Exact 29 boundary: four-run 2+3+4+5=14, plus three fives=15.
        table=[run('red',2,5),group(5,('blue','black','orange'))]
        g,s=fixture(sum(table,[]),opened=False)
        self.assert_invalid(g,s,meld(table),'29')
        g,s=fixture(group(10),opened=False)
        self.assertTrue(g.apply_action(s,meld([group(10)]),players()[0]).state['opened']['p0'])

    def test_two_jokers_have_explicit_group_or_run_interpretation(self):
        ids=['red-9-1','joker-1','joker-2']
        self.assertEqual(meld_info(ids,'group')['points'],27)
        self.assertEqual(meld_info(ids,'run')['points'],30)
        self.assertEqual(meld_info(ids)['kind'],'run')
        g,s=fixture(ids+['blue-1-1'],opened=False)
        self.assert_invalid(g,s,{'action':'meld','melds':[ids],'kinds':['group']},'27')
        out=g.apply_action(s,{'action':'meld','melds':[ids],'kinds':['run']},players()[0]).state
        self.assertEqual(out['meld_kinds'],['run'])
        for kinds in ([],['bad'],[None],{'0':'run'}):
            self.assert_invalid(g,s,{'action':'meld','melds':[ids],'kinds':kinds},'kinds')
        # A persisted lower-valued group keeps its identity; an unrelated rack
        # meld cannot silently change both old jokers into a run.
        g,s=fixture(group(10),[ids]); s['meld_kinds']=['group']
        self.assert_invalid(g,s,{'action':'meld','melds':[ids,group(10)],'kinds':['run','group']},'万能牌')
        g.validate_action(s,{'action':'meld','melds':[ids,group(10)],'kinds':['group','group']},players()[0])

    def test_opening_cannot_touch_table_even_with_30_other_points(self):
        old=[['joker-1','red-2-1','red-3-1']]
        own=group(10)+['red-4-1']
        g,s=fixture(own,old,opened=False)
        self.assert_invalid(g,s,meld([['red-2-1','red-3-1','joker-1'],group(10)]),'首次')
        self.assert_invalid(g,s,meld([old[0]+['red-4-1'],group(10)]),'首次')
        self.assertTrue(g.apply_action(s,meld(old+[group(10)]),players()[0]).state['opened']['p0'])

    def test_atomic_split_combine_and_physical_twins(self):
        old=[run('red',4,8)]
        g,s=fixture(['red-6-2','blue-13-1'],old)
        out=g.apply_action(s,meld([run('red',4,6),['red-6-2','red-7-1','red-8-1']]),players()[0]).state
        self.assertEqual(out['hands']['p0'],['blue-13-1'])
        self.assertIn('red-6-1',sum(out['melds'],[])); self.assertIn('red-6-2',sum(out['melds'],[]))
        # Pull fourth group member into a new run (official manipulation example).
        old=[group(4,('red','blue','black','orange'))]
        g,s=fixture(['blue-3-1','blue-5-1','blue-6-1'],old)
        out=g.apply_action(s,meld([group(4,('red','black','orange')),run('blue',3,6)]),players()[0]).state
        self.assertTrue(out['result'])
        # Merge two runs into a single run using a rack bridge.
        g,s=fixture(['red-4-1'],[run('red',1,3),run('red',5,7)])
        self.assertTrue(g.apply_action(s,meld([run('red',1,7)]),players()[0]).result)

    def test_combined_and_multiple_splits(self):
        table=[run('orange',1,4),group(1,('red','blue','black','orange'),2)]
        g,s=fixture(['black-1-1'],table)
        proposal=[run('orange',2,4),group(1,('blue','black','orange'),2),
                  ['black-1-1','orange-1-1','red-1-2']]
        g.validate_action(s,meld(proposal),players()[0])
        # Official p3 multiple split: turn three runs into 5/6/7 groups and 8/9/10.
        table=[run('red',5,7),run('blue',5,7),run('black',5,9)]
        g,s=fixture(['orange-5-1','black-10-1'],table)
        proposal=[group(5,('red','blue','black','orange')),group(6),group(7),run('black',8,10)]
        g.validate_action(s,meld(proposal),players()[0])

    def test_joker_official_example_one_either_missing_color_or_both(self):
        old=[['red-3-1','blue-3-1','joker-1']]
        for replacement in (['black-3-1'],['orange-3-1'],['black-3-1','orange-3-1']):
            with self.subTest(replacement=replacement):
                g,s=fixture(replacement+['red-10-1','red-11-1'],old)
                proposal=[old[0][:2]+replacement,['red-10-1','red-11-1','joker-1']]
                g.validate_action(s,meld(proposal),players()[0])

    def test_joker_official_example_two_split_no_matching_four(self):
        old=[['red-2-1','red-3-1','joker-1','red-5-1','red-6-1']]
        g,s=fixture(['red-1-1','red-7-1','blue-10-1','blue-11-1'],old)
        g.validate_action(s,meld([run('red',1,3),run('red',5,7),['blue-10-1','blue-11-1','joker-1']]),players()[0])

    def test_joker_official_example_three_end_replacement(self):
        g,s=fixture(['blue-5-1','red-10-1','red-11-1'],[['joker-1','blue-6-1','blue-7-1']])
        g.validate_action(s,meld([run('blue',5,7),['red-10-1','red-11-1','joker-1']]),players()[0])

    def test_joker_official_example_four_distribute_companions(self):
        old=[['black-1-1','black-2-1','joker-1'],group(1,('red','blue','orange')),group(2,('red','blue','orange'))]
        g,s=fixture(['red-10-1','red-11-1'],old)
        proposal=[group(1,('red','blue','orange','black')),group(2,('red','blue','orange','black')),
                  ['red-10-1','red-11-1','joker-1']]
        g.validate_action(s,meld(proposal),players()[0])

    def test_joker_replacement_can_come_from_table(self):
        old=[['red-3-1','blue-3-1','joker-1'],['black-3-1','black-4-1','black-5-1','black-6-1']]
        g,s=fixture(['red-10-1','red-11-1'],old)
        g.validate_action(s,meld([group(3),run('black',4,6),['red-10-1','red-11-1','joker-1']]),players()[0])

    def test_released_joker_requires_rack_tile_in_its_new_meld(self):
        old=[['red-3-1','blue-3-1','joker-1'],run('black',7,10)]
        g,s=fixture(['black-3-1','blue-9-1','orange-9-1'],old)
        # Rearranging joker to a table-only new run is invalid even with hand played elsewhere.
        proposal=[group(3),['joker-1','black-7-1','black-8-1'],['black-9-1','blue-9-1','orange-9-1']]
        self.assert_invalid(g,s,meld(proposal),'桌面牌') # loses black10 as well
        old=[['red-3-1','blue-3-1','joker-1'],run('black',7,11)]
        g,s=fixture(['black-3-1'],old)
        self.assert_invalid(g,s,meld([group(3),['joker-1','black-7-1','black-8-1'],run('black',9,11)]),'万能牌')
        self.assert_invalid(g,s,meld([group(3),run('black',7,11)]),'桌面牌')

    def test_two_table_jokers_keep_joint_color_constraints_after_split(self):
        # In the old four-group the jokers are black/orange in some order.
        # Both cannot silently become orange when the two black fives below
        # are pulled from table runs into two separate joker groups.
        table=[['red-5-1','blue-5-1','joker-1','joker-2'],run('black',5,8),run('black',5,8,2)]
        proposal=[['red-5-1','black-5-1','joker-1'],['blue-5-1','black-5-2','joker-2'],
                  run('black',6,8),run('black',6,8,2),group(10)]
        g,s=fixture(group(10),table)
        self.assert_invalid(g,s,meld(proposal),'万能牌')
        # If one replacement is a rack tile, that joker is allowed to be
        # retrieved/reused there and the other can retain orange.
        table=[['red-5-1','blue-5-1','joker-1','joker-2'],run('black',5,8)]
        g,s=fixture(['black-5-2'],table)
        g.validate_action(s,meld([['red-5-1','black-5-1','joker-1'],
                                  ['blue-5-1','black-5-2','joker-2'],run('black',6,8)]),players()[0])

    def test_joker_may_stay_in_place_while_run_splits_or_group_shrinks(self):
        old=[['red-1-1','red-2-1','joker-1','red-4-1','red-5-1','red-6-1']]
        g,s=fixture(['red-7-1'],old)
        g.validate_action(s,meld([old[0][:3],run('red',4,7)]),players()[0])
        old=[['red-3-1','blue-3-1','black-3-1','joker-1']]
        g,s=fixture(['black-4-1','black-5-1'],old)
        g.validate_action(s,meld([['red-3-1','blue-3-1','joker-1'],run('black',3,5)]),players()[0])

    def test_loss_duplicate_opponent_tile_no_hand_and_malformed_rejected(self):
        old=[run('red',1,3)]
        g,s=fixture(['red-4-1'],old,other=['red-5-1'])
        invalid=[meld([run('red',2,4)]),meld([run('red',1,4),run('red',1,3)]),
                 meld([run('red',1,5)]),meld(old),{'action':'draw','tile_id':'red-5-1'},
                 {'action':'meld','melds':None},meld([None]),meld([['bad']*3]),meld([[1,2,3]]),
                 {'action':'meld','melds':old,'player_id':'p1'}]
        for action in invalid:
            with self.subTest(action=action): self.assert_invalid(g,s,action)

    def test_draw_ends_turn_and_empty_pool_does_not_end_game(self):
        g,s=fixture(['red-1-1','red-2-1'],opened=False)
        s['pool']=['red-3-1']
        out=g.apply_action(s,{'action':'draw'},players()[0]).state
        self.assertEqual(out['hands']['p0'],['red-1-1','red-2-1','red-3-1'])
        self.assertIsNone(out['result']); self.assertEqual(out['turn_player_id'],'p1')
        self.assert_invalid(g,out,meld([run('red',1,3)]),'回合')
        with self.assertRaisesRegex(ValueError,'已空'):
            g.validate_action(out,{'action':'draw'},players()[1])

    def test_exhaustion_pass_cycle_reset_and_scoring(self):
        g,s=fixture(['red-4-1'],[run('red',1,3)],other=['joker-1','blue-6-1'],empty_pool=True)
        s=g.apply_action(s,{'action':'pass'},players()[0]).state
        self.assertIsNone(s['result'])
        s=g.apply_action(s,{'action':'pass'},players()[1]).state
        self.assertEqual(s['result']['reason'],'blocked')
        self.assertEqual(s['result']['tile_scores'],{'p0':32,'p1':-32})
        g,s=fixture(['red-4-1','orange-13-1'],[run('red',1,3)],other=['red-5-1'],empty_pool=True)
        s=g.apply_action(s,{'action':'pass'},players()[0]).state
        # p1 can extend only after the missing 4 is played; instead p1 passes then
        # test reset directly with p0 owning the current opportunity.
        s['turn_player_id']='p0'
        s=g.apply_action(s,meld([run('red',1,4)]),players()[0]).state
        self.assertEqual(s['blocked_player_ids'],[])
        self.assertIsNone(s['result'])

    def test_tied_stalemate_fractional_scores_and_normal_finish(self):
        g,s=fixture(['red-1-1'],other=['blue-1-1'],count=3,empty_pool=True)
        s['hands']['p2']=['black-4-1']
        for p in players(3): s=g.apply_action(s,{'action':'pass'},p).state
        self.assertEqual(s['result']['winning_player_ids'],['p0','p1'])
        self.assertTrue(s['result']['draw'])
        self.assertEqual(s['result']['tile_scores'],{'p0':1.5,'p1':1.5,'p2':-3})
        g,s=fixture(group(10),opened=False,other=['joker-1','blue-6-1'])
        out=g.apply_action(s,meld([group(10)]),players()[0]).state
        self.assertEqual(out['result']['tile_scores'],{'p0':36,'p1':-36})

    def test_private_state_no_opponent_or_pool_even_terminal(self):
        g,s=fixture(group(10),opened=False,other=['joker-1','blue-6-1'])
        for terminal in (False,True):
            if terminal: s=g.apply_action(s,meld([group(10)]),players()[0]).state
            public=g.public_state(s,players())
            private=g.private_state(s,players()[0],players())
            text=json.dumps([public,private])
            for hidden in ['joker-1','blue-6-1',s['pool'][0]]: self.assertNotIn(hidden,text)
            self.assertNotIn('hands',public); self.assertNotIn('pool',public)

    def test_resignation_frozen_hand_no_recycle_and_terminal(self):
        g=Rummikub(random.Random(1)); s=g.initialize(players(4)); before=deepcopy(s)
        s['blocked_player_ids']=['p0']
        g.apply_resignation(s,'p0',players(4))
        self.assertEqual(s['pool'],before['pool']); self.assertEqual(s['hands']['p0'],before['hands']['p0'])
        self.assertEqual(s['blocked_player_ids'],[]); self.assertEqual(s['turn_player_id'],'p1')
        self.assertIsNone(s['result'])
        g.apply_resignation(s,'p2',players(4)); g.apply_resignation(s,'p3',players(4))
        self.assertEqual(s['result']['winner_player_id'],'p1')
        self.assertEqual(s['result']['tile_scores'],{})

    def test_npc_complete_games_legality_conservation_and_bounded_time(self):
        start=time.monotonic(); reasons=Counter()
        for count in (2,3,4):
            for seed in range(12):
                g=Rummikub(random.Random(seed)); ps=players(count); s=g.initialize(ps)
                for turn in range(500):
                    actor=next(p for p in ps if p['player_id']==s['turn_player_id'])
                    action=g.choose_local_npc_action(s,actor,ps)
                    g.validate_action(s,action,actor)
                    s=g.apply_action(s,action,actor).state
                    zones=s['pool']+sum(s['hands'].values(),[])+sum(s['melds'],[])
                    self.assertEqual(Counter(zones),Counter(TILES.keys()))
                    if s['result']:
                        reasons[s['result']['reason']]+=1
                        break
                else: self.fail(f'NPC did not finish count={count}, seed={seed}')
        self.assertEqual(sum(reasons.values()),36)
        self.assertLess(time.monotonic()-start,45,'bounded local policy should not block requests')

    def test_npc_decision_independent_of_hidden_information(self):
        g=Rummikub(random.Random(14)); s=g.initialize(players(4))
        action=g.choose_local_npc_action(s,players(4)[0],players(4))
        for pid in ('p1','p2','p3'): s['hands'][pid].reverse()
        s['pool'].reverse()
        self.assertEqual(action,g.choose_local_npc_action(s,players(4)[0],players(4)))


class RummikubRoomTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='rummikub-',dir=os.environ.get('RUMMIKUB_TEST_TMP'))
        self.db=patch.object(database,'DB_PATH',Path(self.tmp.name)/'duel.db')
        self.db.start(); database.init_db()
        self.addCleanup(self.tmp.cleanup); self.addCleanup(self.db.stop)

    def create(self, count=2):
        return framework.create_room('rummikub','human_first','human','p0',ordered_participants=players(count))

    def play(self,room,move,actor=None,revision=None):
        actor=actor or next(p for p in room['participants'] if p['player_id']==room['current_player_id'])
        return framework.play_move(room['room_id'],actor['role'],actor['player_id'],move,
                                   expected_revision=room['revision'] if revision is None else revision)

    def test_required_stale_duplicate_revision_and_wrong_identity(self):
        room=self.create(); move={'action':'draw'}
        with self.assertRaisesRegex(framework.DuelError,'revision'):
            framework.play_move(room['room_id'],'human','p0',move)
        for role,pid in [('ai','p1'),('ai','p0'),('human','intruder')]:
            with self.assertRaises(framework.DuelError):
                framework.play_move(room['room_id'],role,pid,move,expected_revision=room['revision'])
        new=self.play(room,move)
        self.assertEqual(new['revision'],room['revision']+1)
        with self.assertRaisesRegex(framework.DuelError,'revision'): self.play(room,move)
        self.assertEqual(framework.get_room(room['room_id'])['revision'],new['revision'])

    def test_concurrent_duplicate_only_one_commits(self):
        room=self.create()
        def send():
            try: self.play(room,{'action':'draw'}); return True
            except framework.DuelError: return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sum(executor.map(lambda _:send(),range(2))),1)
        self.assertEqual(len(framework.get_room(room['room_id'])['board_state']['hands']['p0']),15)

    def test_invalid_submission_preserves_persisted_state_events_revision(self):
        room=self.create()
        with database.connect() as conn:
            before=conn.execute('SELECT * FROM rooms').fetchall()
            events=conn.execute('SELECT COUNT(*) FROM room_messages').fetchone()[0]
        with self.assertRaises(framework.DuelError): self.play(room,meld([['joker-1']*3]))
        with database.connect() as conn:
            self.assertEqual([tuple(r) for r in before],[tuple(r) for r in conn.execute('SELECT * FROM rooms')])
            self.assertEqual(events,conn.execute('SELECT COUNT(*) FROM room_messages').fetchone()[0])

    def test_persistence_new_process_and_safe_all_viewers(self):
        room=self.play(self.create(4),{'action':'draw'})
        script="""import json, sys
from app import framework
room=framework.get_room(sys.argv[1]); print(json.dumps(room['board_state'],sort_keys=True))
"""
        env={**os.environ,'DUEL_DB_PATH':str(database.DB_PATH)}
        result=subprocess.run([sys.executable,'-c',script,room['room_id']],env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),room['board_state'])
        for p in room['participants']:
            view=framework.project_room_for_viewer(room,p['player_id'])
            self.assertEqual([t['id'] for t in view['private_state']['hand']],room['board_state']['hands'][p['player_id']])
            visible=json.dumps(view)
            for other,hand in room['board_state']['hands'].items():
                if other != p['player_id']:
                    for t in hand: self.assertNotIn('"'+t+'"',visible)

    def test_invite_wait_start_2_3_4_and_npc_fill(self):
        for count in (2,3,4):
            room=invites.create_invite('rummikub','ai',f'owner{count}',target_player_count=count)
            self.assertEqual(room['board_state'],{})
            for n in range(count-1): invites.join_invite(room['invite_code'],'human',f'guest{count}-{n}')
            room=invites.start_invite(room['room_id'],'ai',f'owner{count}')
            self.assertEqual(room['status'],'playing')
            self.assertEqual([len(h) for h in room['board_state']['hands'].values()],[14]*count)
            self.play(room,{'action':'draw'})
        room=invites.create_invite('rummikub','human','owner-npc',target_player_count=4)
        invites.join_invite(room['invite_code'],'ai','external-npc')
        with patch.object(invites,'select_personas',return_value=[NpcPersona('one','一','测试'),NpcPersona('two','二','测试')]):
            room=invites.start_invite(room['room_id'],'human','owner-npc',fill_with_npcs=True)
        self.assertEqual(sum(p['participant_kind']=='system_npc' for p in room['participants']),2)

    def test_takeover_reuses_original_identity_and_revision(self):
        room=invites.create_invite('rummikub','human','owner',target_player_count=2,timeout_takeover=True)
        invites.join_invite(room['invite_code'],'human','friend')
        room=invites.start_invite(room['room_id'],'human','owner')
        then=(datetime.now(timezone.utc)-timedelta(seconds=91)).isoformat()
        with database.write_transaction() as conn:
            conn.execute('UPDATE room_invites SET turn_started_at=? WHERE room_id=?',(then,room['room_id']))
        # Invite startup establishes presence; disable any provider calls by policy.
        before=framework.get_room(room['room_id'])
        out=asyncio.run(takeover.run_timeout_turn(room['room_id']))
        self.assertIsNotNone(out)
        self.assertEqual(out['revision'],before['revision']+1)
        self.assertEqual([p['participant_kind'] for p in out['participants']],['human','human'])

    def test_leave_continues_three_to_two_then_finishes_without_scores(self):
        room=self.create(3)
        room=framework.leave_room(room['room_id'],'ai','p1')
        room=framework.get_room(room['room_id'])
        self.assertEqual(room['status'],'playing')
        self.assertEqual(room['board_state']['active_player_ids'],['p0','p2'])
        framework.resign(room['room_id'],'ai','p2')
        room=framework.get_room(room['room_id'])
        self.assertEqual(room['status'],'finished')
        self.assertEqual(room['result']['winner_player_id'],'p0')
        self.assertEqual(room['result']['tile_scores'],{})

    def test_real_npc_controller_finishes_mixed_room_without_model_decisions(self):
        ps=players(4)
        for p in ps[2:]:
            p.update(participant_kind='system_npc',npc_persona_id='test-npc')
        room=framework.create_room('rummikub','human_first','human','p0',ordered_participants=ps)
        g=get_game('rummikub'); npc_turns=0
        # Speech is a separate tested platform capability; this asserts the real
        # persisted NPC decision/reservation/move path never needs a model.
        with patch('app.npc_controller._finish_npc_action',return_value=None):
            for _ in range(500):
                actor=next(p for p in room['participants'] if p['player_id']==room['current_player_id'])
                if actor['participant_kind']=='system_npc':
                    turn=asyncio.run(run_current_npc_turn(room['room_id']))
                    self.assertEqual(turn.source,'local')
                    room=turn.room; npc_turns+=1
                else:
                    move=g.choose_local_npc_action(room['board_state'],actor,room['participants'])
                    room=self.play(room,move)
                if room['status']=='finished': break
            else: self.fail('Mixed NPC room did not finish')
        self.assertGreater(npc_turns,0)
        with database.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM npc_decisions WHERE status='completed'").fetchone()[0],npc_turns)
        self.assertEqual(room['stake'],0)

    def test_waiting_single_seat_has_no_deal_or_action(self):
        room=framework.create_room('rummikub','ai_first','ai','p0')
        self.assertEqual(room['status'],'waiting')
        view=framework.project_room_for_viewer(room,'p0')
        self.assertEqual(view['private_state']['hand'],[])
        self.assertEqual(view['private_state']['legal_actions'],[])
        self.assertIsNone(view['private_state']['suggested_move'])

    def test_only_npcs_remaining_finishes_and_never_awards_tile_scores(self):
        ps=players(3)
        for p in ps[1:]: p.update(participant_kind='system_npc',npc_persona_id='test-npc')
        room=framework.create_room('rummikub','human_first','human','p0',ordered_participants=ps)
        room=framework.resign(room['room_id'],'human','p0')
        self.assertEqual(room['status'],'finished')
        self.assertEqual(room['result']['reason'],'only_system_npcs_remaining')
        self.assertEqual(room['result']['tile_scores'],{})

    def test_nonzero_stake_rejected_wallet_not_used(self):
        with self.assertRaises(framework.DuelError):
            framework.create_room('rummikub','human_first','human','h','a',stake=1)
        room=self.create()
        self.assertEqual(room['stake'],0)


class RummikubTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx
        from app import main
        self.tmp=tempfile.TemporaryDirectory(prefix='rummikub-http-',dir=os.environ.get('RUMMIKUB_TEST_TMP'))
        self.db=patch.object(database,'DB_PATH',Path(self.tmp.name)/'http.db')
        self.db.start(); database.init_db()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose(); self.db.stop(); self.tmp.cleanup()

    async def test_mcp_structured_invite_state_and_move(self):
        async def request(**body):
            response=await self.client.post('/mcp/play',json=body)
            self.assertEqual(response.status_code,200,response.text)
            return response.json()
        created=await request(action='invite',player_id='ai1',game_type='rummikub',target_player_count=2)
        await request(action='join',player_id='ai2',invite_code=created['invite_code'])
        await request(action='start',player_id='ai1',room_id=created['room_id'])
        raw=framework.get_room(created['room_id']); actor=raw['current_player_id']
        view=await request(action='state',player_id=actor,room_id=created['room_id'],full_state=True)
        # Full state and bootstrap provide a structured, viewer-safe snapshot.
        text=json.dumps(view)
        if view.get('full_state'):
            self.assertIn('FULL final table',view['snapshot']['action_formats']['meld']['melds'])
            self.assertEqual(view['snapshot']['rules']['opening_threshold'],30)
        else:
            self.assertIn('legal_action_spec',text); self.assertIn('opening_threshold',text)
        for pid,hand in raw['board_state']['hands'].items():
            if pid!=actor:
                for tid in hand: self.assertNotIn('"'+tid+'"',text)
        moved=await request(action='move',player_id=actor,room_id=created['room_id'],
                            revision=raw['revision'],move={'action':'draw'})
        self.assertEqual(moved['r'],raw['revision']+1)
        stale=await self.client.post('/mcp/play',json={'action':'move','player_id':actor,'room_id':created['room_id'],
                      'revision':raw['revision'],'move':{'action':'draw'}})
        self.assertEqual(stale.status_code,409)

    async def test_internal_web_route_checks_proxy_supplied_participant(self):
        room=framework.create_room('rummikub','human_first','human','p0',ordered_participants=players())
        response=await self.client.post('/api/rooms/'+room['room_id']+'/move',
            headers={'X-Duel-Human-Player':'outsider'},
            json={'move':{'action':'draw'},'revision':room['revision'],'player_id':'outsider'})
        self.assertEqual(response.status_code,403)
        response=await self.client.post('/api/rooms/'+room['room_id']+'/move',
            headers={'X-Duel-Human-Player':'p0'},json={'move':{'action':'draw'},'revision':room['revision'],'player_id':'p0'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['room']['viewer']['player_id'],'p0')
        self.assertEqual(len(response.json()['room']['private_state']['hand']),15)


if __name__=='__main__': unittest.main()
