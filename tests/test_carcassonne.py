"""Classic-base rule and graph regressions; no database/network dependencies."""
from collections import Counter
from copy import deepcopy
import json
import random
import unittest

from app.games import game_catalog
from app.games.carcassonne import Carcassonne, FeatureGraph, DIRECTIONS, legal_placements
from app.games.carcassonne_tiles import COUNTS, TILES, oriented, build_deck


def players(n=2):
    return [{"player_id":f"p{i}", "seat_index":i, "role":"human" if i==0 else "ai",
             "display_name":f"玩家{i+1}", "participant_kind":"human" if i==0 else "bound_machine",
             "active":True, "token":f"P{i+1}"} for i in range(n)]


def tile(name,x=0,y=0,rot=0,owner=None,region=None):
    t={"tile":name,"x":x,"y":y,"rotation":rot}
    if owner is not None:t["meeple"]={"player_id":owner,"region":region}
    return t


def fixture(board, current="E", n=2, deck=None):
    g=Carcassonne(random.Random(31));s=g.initialize(players(n))
    s.update(board=deepcopy(board),current_tile=current,deck=deck if deck is not None else ["B"])
    s['supply']={f'p{i}':7-sum(t.get('meeple',{}).get('player_id')==f'p{i}' for t in board) for i in range(n)}
    return g,s


def move(x,y,r=0,m=None):return {"action":"place","x":x,"y":y,"rotation":r,"meeple":m}


class CarcassonneRules(unittest.TestCase):
    def test_inventory_independent_counts_and_edge_totals(self):
        # Publisher Big Box 2010 p. B1: A–X. Independent literal expectation.
        expected=[2,4,1,4,5,2,1,3,2,3,3,3,2,3,2,3,1,3,2,1,8,9,4,1]
        self.assertEqual([COUNTS[chr(65+i)] for i in range(24)],expected)
        self.assertEqual(sum(COUNTS.values()),72)
        self.assertEqual(len(build_deck()),71)
        self.assertEqual(Counter(build_deck())['D'],3)
        totals=Counter()
        for name,t in TILES.items():
            totals.update({edge:t['edges'].count(edge)*t['count'] for edge in ('C','R','F')})
        self.assertEqual(totals,{'C':79,'R':94,'F':115})
        self.assertEqual(sum(t['count'] for t in TILES.values() if 'm0' in t['regions']),6)
        self.assertEqual(sum(t['count']*sum(r.get('shields',0) for r in t['regions'].values()) for t in TILES.values()),10)

    def test_all_ports_cover_edges_once_and_field_city_references_valid(self):
        for name,t in TILES.items():
            for rot in range(4):
                d=oriented(name,rot);regions=d['regions'];seen=Counter()
                for rid,r in regions.items():
                    for p in r['ports']:seen[(r['kind'],p)]+=1
                    self.assertTrue(set(r.get('cities',[]))<=set(k for k,v in regions.items() if v['kind']=='city'))
                self.assertTrue(all(count==1 for count in seen.values()),name)
                for side,edge in enumerate(d['edges']):
                    if edge=='C':self.assertEqual(seen['city',side],1);self.assertEqual(seen['field',2*side]+seen['field',2*side+1],0)
                    else:
                        self.assertEqual(seen['field',2*side],1);self.assertEqual(seen['field',2*side+1],1)
                        self.assertEqual(seen['road',side],int(edge=='R'))
                self.assertEqual(oriented(name,rot+4),d|{'rotation':rot+4})

    def test_catalog_and_start_2_3_4_5(self):
        catalog=next(c for c in game_catalog() if c['game_type']=='carcassonne')
        self.assertEqual(catalog['category'],'tabletop');self.assertEqual(catalog['allowed_player_counts'],[2,3,4,5]);self.assertEqual(catalog['recommended_players'],4)
        for n in range(2,6):
            g=Carcassonne(random.Random(n));s=g.initialize(players(n))
            self.assertEqual(s['board'],[tile('D')]);self.assertEqual(len(s['deck']),70)
            self.assertEqual(s['supply'],{f'p{i}':7 for i in range(n)})
            self.assertEqual(Counter([s['current_tile']]+s['deck']+['D']),COUNTS)
            g.prepare_opening_state(s,f'p{n-1}',players(n));self.assertEqual(s['turn_player_id'],f'p{n-1}')
        for n in (0,6):
            with self.assertRaises(ValueError):Carcassonne().initialize(players(n))
        self.assertEqual(Carcassonne().initialize(players(1))['flow']['phase'],'waiting')

    def test_rotation_all_four_sides_negative_coords_and_no_diagonals(self):
        g,s=fixture([tile('D')],current='D')
        for rot in range(4):
            for x,y,r in legal_placements(s['board'],'D'):
                if r!=rot:continue
                g.validate_action(s,move(x,y,r),players()[0])
                # Independent terrain assertion against all occupied neighbors.
                for side,(dx,dy) in enumerate(DIRECTIONS):
                    if (x+dx,y+dy)==(0,0):
                        self.assertEqual(oriented('D',r)['edges'][side],TILES['D']['edges'][(side+2)%4])
        self.assertIn((0,-1,2),legal_placements(s['board'],'D'))
        for m in (move(1,1),move(0,0),move(-2,0),move(0,-1,0),move(10**30,0),move(0,-1,True),move('0',-1),move(0,-1,4)):
            with self.assertRaises(ValueError):g.validate_action(s,m,players()[0])

    def test_all_neighbor_edges_must_match(self):
        board=[tile('D'),tile('E',1,-1,1)]
        self.assertNotIn((1,0,0),legal_placements(board,'U'))
        for x,y,r in legal_placements(board,'E'):
            neighbors={(t['x'],t['y']):t for t in board}
            for side,(dx,dy) in enumerate(DIRECTIONS):
                t=neighbors.get((x+dx,y+dy))
                if t:self.assertEqual(oriented('E',r)['edges'][side],oriented(t['tile'],t['rotation'])['edges'][(side+2)%4])

    def test_separate_cities_and_city_strip_fields(self):
        for name in ('H','I'):
            graph=FeatureGraph([tile(name)])
            self.assertNotEqual(graph.find((0,0,'c0')),graph.find((0,0,'c1')))
        for name in ('F','G'):
            graph=FeatureGraph([tile(name)])
            self.assertEqual(len([c for c in graph.components.values() if c['kind']=='city']),1)
            self.assertNotEqual(graph.find((0,0,'f0')),graph.find((0,0,'f1')))

    def test_road_split_fields_junction_separate_roads_and_monastery_field_wrap(self):
        for name,roads,fields in [('U',1,2),('V',1,2),('W',3,3),('X',4,4),('L',3,3),('A',1,1),('B',0,1),('S',1,2),('T',1,2)]:
            graph=FeatureGraph([tile(name)])
            self.assertEqual(Counter(c['kind'] for c in graph.components.values())['road'],roads,name)
            self.assertEqual(Counter(c['kind'] for c in graph.components.values())['field'],fields,name)
        graph=FeatureGraph([tile('U'),tile('U',0,1)])
        self.assertEqual(graph.find((0,0,'f0')),graph.find((0,1,'f0')))
        self.assertNotEqual(graph.find((0,0,'f0')),graph.find((0,1,'f1')))

    def test_occupation_checks_far_connected_region_and_self(self):
        for owner in ('p0','p1'):
            g,s=fixture([tile('U',0,0,0,owner,'r0'),tile('U',0,1)],'U')
            with self.assertRaisesRegex(ValueError,'整个连通'):g.apply_action(s,move(0,2,0,'r0'),players()[0])
            g.validate_action(s,move(0,2,0,'f0'),players()[0])

    def test_candidate_indirect_external_field_rejoin_occupancy(self):
        # Compare the optimized preview to a full graph on every placement in
        # several actual evolving games, covering indirect local-field joins.
        for seed in range(3):
            g=Carcassonne(random.Random(seed));s=g.initialize(players(3))
            for turn in range(24):
                old=FeatureGraph(s['board'])
                for x,y,r in legal_placements(s['board'],s['current_tile']):
                    graph=FeatureGraph(s['board']+[tile(s['current_tile'],x,y,r)])
                    expected=[rid for rid in TILES[s['current_tile']]['regions'] if not graph.region(x,y,rid)['meeples']]
                    self.assertEqual(old.available(s['current_tile'],x,y,r),expected)
                a={'player_id':s['turn_player_id']};m=g.choose_local_npc_action(s,a,[]);s=g.apply_action(s,m,a).state

    def test_city_completed_immediate_meeple_return_and_no_rescore(self):
        g,s=fixture([tile('E')],'E')
        after=g.apply_action(s,move(0,-1,2,'c0'),players()[0]).state
        self.assertEqual(after['scores']['p0'],4);self.assertEqual(after['supply']['p0'],7)
        self.assertFalse(any(t.get('meeple') for t in after['board']))
        self.assertEqual(g._score(after),[])
        self.assertEqual(s['scores']['p0'],0)

    def test_city_shield_complete_and_unfinished(self):
        board=[tile('M',0,0,0,'p0','c0'),tile('E',0,-1,2),tile('E',1,0,3)]
        g,s=fixture(board);events=g._score(s)
        self.assertEqual(events[0]['points'],8);self.assertEqual(s['scores']['p0'],8)
        g,s=fixture(board[:2]);g._finish(s)
        self.assertEqual(s['scores']['p0'],3)

    def test_multi_segments_one_city_tile_counted_once(self):
        # H's north and south caps rejoin around its west via a city chain.
        board=[tile('H',0,0,0,'p0','c0'),tile('N',0,-1,2),tile('N',-1,-1,1),tile('G',-1,0,1),tile('N',-1,1),tile('N',0,1,3)]
        g,s=fixture(board);graph=FeatureGraph(board)
        self.assertEqual(graph.find((0,0,'c0')),graph.find((0,0,'c1')))
        c=graph.region(0,0,'c0');self.assertEqual(len(c['tiles']),6);self.assertEqual(len(c['nodes']),7);self.assertEqual(c['open'],0)
        g._score(s);self.assertEqual(s['scores']['p0'],12)

    def test_city_majority_and_tie_merge_full_points(self):
        for owners,expected in [(('p0','p1'),{'p0':6,'p1':6}), (('p0','p0'),{'p0':6,'p1':0})]:
            board=[tile('E',-1,0,1,owners[0],'c0'),tile('E',1,0,3,owners[1],'c0')]
            g,s=fixture(board,'G');s=g.apply_action(s,move(0,0),players()[0]).state
            self.assertEqual(s['scores'],expected);self.assertEqual(s['supply'],{'p0':7,'p1':7})
        # Three owners merge with 2:1 majority; shields independent of meeples.
        board=[tile('E',0,-1,2,'p0','c0'),tile('E',1,0,3,'p1','c0'),tile('E',0,1,0,'p0','c0'),tile('E',-1,0,1)]
        g,s=fixture(board,'C');s=g.apply_action(s,move(0,0),players()[0]).state
        self.assertEqual(s['scores'],{'p0':12,'p1':0})

    def test_road_ends_and_loop_score_once_per_tile(self):
        g,s=fixture([tile('A',0,0,0,'p0','r0'),tile('U',0,1),tile('A',0,2,2)])
        g._score(s);self.assertEqual(s['scores']['p0'],3)
        # Four curves form a loop, with no artificial endpoints.
        board=[tile('V',0,0,3,'p0','r0'),tile('V',1,0),tile('V',1,1,1),tile('V',0,1,2)]
        graph=FeatureGraph(board);self.assertEqual(graph.region(0,0,'r0')['open'],0)
        g,s=fixture(board);g._score(s);self.assertEqual(s['scores']['p0'],4)

    def test_monastery_eight_neighbors_and_final_partial(self):
        board=[tile('B',0,0,0,'p0','m0')]+[tile('B',dx,dy) for dx in (-1,0,1) for dy in (-1,0,1) if dx or dy]
        g,s=fixture(board);g._score(s);self.assertEqual(s['scores']['p0'],9);self.assertEqual(s['supply']['p0'],7)
        g,s=fixture(board[:4]);self.assertEqual(g._score(s),[]);g._finish(s);self.assertEqual(s['scores']['p0'],4)

    def test_farm_distinct_completed_cities_and_no_unfinished_city(self):
        # Both E fields surrounding the same two-tile city join via west B tiles.
        board=[tile('E',0,0,0,'p0','f0'),tile('E',0,-1,2),tile('B',-1,0),tile('B',-1,-1),tile('E',-2,0)]
        g,s=fixture(board);self.assertEqual(g._score(s),[]);self.assertEqual(s['supply']['p0'],6)
        graph=FeatureGraph(board);c=graph.region(0,0,'f0');self.assertEqual(len(c['cities']),2)
        g._finish(s);self.assertEqual(s['scores']['p0'],3)
        self.assertEqual(len(s['last_scoring'][0]['cities']),1)

    def test_farm_ties_majority_and_same_city_in_separate_fields(self):
        for extra,expected in [(None,{'p0':3,'p1':3}),('p0',{'p0':3,'p1':0})]:
            board=[tile('E',0,0,0,'p0','f0'),tile('E',0,-1,2,'p1','f0'),tile('B',-1,0,0,extra,'f0'),tile('B',-1,-1)]
            g,s=fixture(board);g._finish(s);self.assertEqual(s['scores'],expected)
        # Two farms on opposite sides of a city strip each supply it.
        board=[tile('G',0,0,0,'p0','f0'),tile('E',-1,0,1),tile('E',1,0,3),tile('B',0,1,0,'p1','f0')]
        g,s=fixture(board);g._finish(s);self.assertEqual(s['scores'],{'p0':3,'p1':3})

    def test_inventory_zero_cannot_use_just_returning_follower(self):
        g,s=fixture([tile('E')],'E');s['supply']['p0']=0
        with self.assertRaisesRegex(ValueError,'没有剩余'):g.apply_action(s,move(0,-1,2,'c0'),players()[0])
        self.assertFalse(any(p[3] for p in g.private_state(s,players()[0],[])['placements']))
        g.validate_action(s,move(0,-1,2,None),players()[0])

    def test_supplement_farmer_outcomes_reported_by_visual_reviewer(self):
        # Main assistant visually reviewed v3 Supplement p1 on 2026-10-02:
        # tied red/blue farms supply 3 finished cities (9 each), black majority
        # supplies 4 (12), yellow corner supplies 2 (6). These are independent
        # scoring fixtures, not a claim to reconstruct the PDF artwork exactly.
        for city_count,owners,expected in [
            (3,['p0','p1'],{'p0':9,'p1':9}),
            (4,['p0','p0','p1'],{'p0':12,'p1':0}),
            (2,['p0'],{'p0':6,'p1':0}),
        ]:
            board=[]
            for i in range(city_count):
                board.extend([tile('E',2*i,0,0,owners[i] if i<len(owners) else None,'f0'),
                              tile('E',2*i,-1,2)])
                if i:board.append(tile('B',2*i-1,0))
            # A further unclosed city touches this same connected field.
            board.extend([tile('B',2*city_count-1,0),tile('E',2*city_count,0)])
            g,s=fixture(board);graph=FeatureGraph(board)
            field=graph.region(0,0,'f0')
            self.assertEqual(len(field['cities']),city_count+1)
            self.assertEqual(sum(not graph.components[c]['open'] for c in field['cities']),city_count)
            g._finish(s);self.assertEqual(s['scores'],expected)
            self.assertEqual(s['supply'],{'p0':7,'p1':7})

    def test_reviewed_city_and_road_field_separations(self):
        # Expected counts from the main assistant's BigBox B1 visual review.
        for name in ('D','J','K'):
            for r in range(4):
                graph=FeatureGraph([tile(name,rot=r)])
                self.assertNotEqual(graph.find((0,0,'f0')),graph.find((0,0,'f1')))
        for name in ('H','I'):
            fields=[r for r in TILES[name]['regions'].values() if r['kind']=='field']
            self.assertEqual(len(fields),1)
            self.assertEqual(set(fields[0]['cities']),{'c0','c1'})
        self.assertEqual(set(TILES['A']['regions']['f0']['ports']),set(range(8)))

    def test_atomic_invalid_move_no_mutation_or_draw(self):
        g,s=fixture([tile('D')]);before=deepcopy(s)
        invalid=[move(0,-1,2,'f9'),move(100,100),move(0,-1,2,[]),{'action':'draw'},dict(move(0,-1,2),tile='C')]
        for m in invalid:
            with self.assertRaises(ValueError):g.apply_action(s,m,players()[0])
            self.assertEqual(s,before)
        with self.assertRaises(ValueError):g.apply_action(s,move(0,-1,2),players()[1])

    def test_unplaceable_discard_then_redraw_no_turn_skip_and_last_discard_finish(self):
        # A closed city surface has no outward city edge; all-C can never fit.
        board=[tile('E'),tile('E',0,-1,2)]
        g,s=fixture(board,deck=['B','C']);g._draw(s)
        self.assertEqual(s['discarded'],['C']);self.assertEqual(s['current_tile'],'B');self.assertEqual(s['turn_player_id'],'p0')
        g,s=fixture(board,deck=['C']);g._draw(s)
        self.assertEqual(s['discarded'],['C']);self.assertIsNone(s['current_tile']);self.assertTrue(s['result'])

    def test_final_last_tile_scoring_once_and_persistent_json(self):
        g,s=fixture([tile('E')],'E',deck=[])
        s=g.apply_action(s,move(0,-1,2,'c0'),players()[0]).state
        before=deepcopy(s);self.assertTrue(s['result']);self.assertEqual(s['scores']['p0'],4)
        self.assertEqual(g._finish(s),before['result']);self.assertEqual(s,before)
        self.assertEqual(json.loads(json.dumps(s)),s)
        with self.assertRaises(ValueError):g.apply_action(s,move(1,0),players()[0])

    def test_public_and_private_never_reveal_deck(self):
        g=Carcassonne(random.Random(2));s=g.initialize(players(3))
        for viewer in players(3):
            pub=g.public_state(s,players(3));priv=g.private_state(s,viewer,players(3))
            for projection in (pub,priv,g.mcp_snapshot_state(pub,viewer,players(3)),g.mcp_bootstrap_state(pub,viewer,players(3))):
                self.assertNotIn('"deck":',json.dumps(projection));self.assertNotIn('rng',json.dumps(projection))
            if viewer['player_id']!='p0':self.assertEqual(priv,{})
        self.assertEqual(set(pub['topology']),{'D'})
        a=players()[0];m=g.choose_local_npc_action(s,a,[]);out=g.apply_action(s,m,a)
        self.assertNotIn('"deck":',json.dumps(out.public_event))

    def test_resignation_removes_only_own_meeples_and_rotates(self):
        g,s=fixture([tile('U',0,0,0,'p0','r0'),tile('U',0,1,0,'p1','f0')],n=3)
        g.apply_resignation(s,'p0',players(3));self.assertEqual(s['turn_player_id'],'p1');self.assertEqual(s['supply']['p0'],7)
        self.assertIn('meeple',s['board'][1]);self.assertFalse(s['result'])
        g.apply_resignation(s,'p1',players(3));self.assertEqual(s['result']['winner_player_id'],'p2')

    def test_npc_and_projections_independent_of_hidden_order(self):
        g=Carcassonne(random.Random(54));s=g.initialize(players(4))
        for _ in range(35):
            a={'player_id':s['turn_player_id']}
            s=g.apply_action(s,g.choose_local_npc_action(s,a,[]),a).state
        a={'player_id':s['turn_player_id']};other=deepcopy(s)
        other['deck'].reverse()
        self.assertNotEqual(s['deck'],other['deck'])
        self.assertEqual(g.public_state(s,[]),g.public_state(other,[]))
        self.assertEqual(g.private_state(s,a,[]),g.private_state(other,a,[]))
        before=deepcopy(s)
        self.assertEqual(g.choose_local_npc_action(s,a,[]),g.choose_local_npc_action(other,a,[]))
        self.assertEqual(s,before)

    def test_stakes_explicit_zero_sum_unique_and_tie(self):
        g=Carcassonne()
        for n in range(2,6):
            d=g.settlement_deltas({}, {'winner_player_id':'p1','draw':False},players(n),10)
            self.assertEqual(d['p1'],(n-1)*10);self.assertEqual(sum(d.values()),0)
            self.assertEqual(g.settlement_deltas({}, {'draw':True},players(n),10),{f'p{i}':0 for i in range(n)})

    def test_complete_npc_games_all_counts_invariants_and_restore(self):
        for n in range(2,6):
            g=Carcassonne(random.Random(70+n));s=g.initialize(players(n));turn=0
            while not s['result']:
                a={'player_id':s['turn_player_id']};m=g.choose_local_npc_action(s,a,[])
                self.assertIsNotNone(m);g.validate_action(s,m,a)
                s=g.apply_action(s,m,a).state;turn+=1
                if turn==30:s=json.loads(json.dumps(s));g=Carcassonne(random.Random(999))
                for p in s['supply']:
                    self.assertEqual(s['supply'][p]+sum(t.get('meeple',{}).get('player_id')==p for t in s['board']),7)
                self.assertEqual(Counter([t['tile'] for t in s['board']]+s['deck']+s['discarded']+([s['current_tile']] if s['current_tile'] else [])),COUNTS)
                self.assertLessEqual(turn,71)
            self.assertEqual(turn+len(s['discarded']),71);self.assertEqual(list(s['supply'].values()),[7]*n)
            self.assertEqual(s['result']['scores'],s['scores'])

if __name__=='__main__':unittest.main()
