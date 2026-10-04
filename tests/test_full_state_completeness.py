"""Recovery without bootstrap memory; real /mcp/play and disposable SQLite.

Engine constants are used only as assertion oracles. Recovery inputs are the
returned snapshot and, for legal placements, the existing repeatable query.
"""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from app import database, framework
from app.games import GAMES
from app.games.carcassonne import FeatureGraph
from app.games.rummikub import TILES, meld_info
from tests import test_four_game_incremental as fixtures


class FullStateCompleteness(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.FourGameIncremental.asyncSetUp
    room = fixtures.FourGameIncremental.room
    call = fixtures.FourGameIncremental.call
    play = fixtures.FourGameIncremental.play
    cursor = fixtures.FourGameIncremental.cursor

    async def forget(self, room):
        for p in room['participants']:
            await self.call(room, p['player_id'], action='state')
        # No bootstrap response is returned or saved by this helper.

    async def recover(self, room, pid=None):
        pid = pid or room.get('current_player_id') or 'p0'
        with database.connect() as conn:
            context = conn.execute('SELECT context FROM mcp_minimal_contexts WHERE room_id=? AND player_id=?',
                                   (room['room_id'], pid)).fetchone()[0]
            self.assertEqual(json.loads(context)['v'], 2)
        reply = await self.call(room, pid, action='state', full_state=True)
        again = await self.call(room, pid, action='state', full_state=True)
        self.assertEqual(reply['snapshot'], again['snapshot'])
        self.assertNotIn('bootstrap', reply)
        self.assertNotIn('events', reply)
        s = reply['snapshot']
        self.assertEqual((reply['r'], s['revision']), (room['revision'], room['revision']))
        self.assertEqual(s['viewer_player_id'], pid)
        self.assertEqual(s['status'], room['status'])
        self.assertEqual(s['game'], room['game_type'])
        self.assertEqual({p['player_id'] for p in s['participants']},
                         {p['player_id'] for p in room['participants']})
        for p in s['participants']:
            self.assertTrue({'player_id','name','seat','token','status','handle'} <= p.keys())
        self.assertEqual(s['protocol'], 2)
        for text in ('revision:r', 'action_seq', 'omitted', 'null clears', 'Unread chat', 'replayed'):
            self.assertIn(text, s['protocol_guide'])
        for key in ('hands','pool','deck','_decks','rng_state'):
            self.assertNotIn(key, s['board_state'])
        return s

    def persist(self, room, state):
        pid = state.get('turn_player_id', state.get('active_player_id'))
        with database.write_transaction() as conn:
            conn.execute('UPDATE rooms SET board_state=?,current_player_id=? WHERE room_id=?',
                         (json.dumps(state), pid, room['room_id']))
        return framework.get_room(room['room_id'])

    async def test_monopoly_mid_late_full_ledger_rules_cards_and_action_parameters(self):
        room = self.room('monopoly', 4)
        await self.forget(room)
        game = GAMES['monopoly']
        for step in range(301):
            if step in (120, 300):
                s = await self.recover(room)
                b, raw = s['board_state'], room['board_state']
                self.assertEqual([t['id'] for t in b['tiles']], list(range(40)))
                for actual, expected in zip(b['tiles'], raw['tiles']):
                    for key in ('id','name','kind','group','price','rents','build_cost',
                                'mortgage_value','redemption_cost','owner','level','mortgaged'):
                        self.assertEqual(actual[key], expected[key], (step, actual['id'], key))
                for key in ('phase','dice','action_seq','current_player_id','turn_player_id',
                            'turn_number','doubles','extra_roll','trades_this_turn',
                            'auction','trade','debt','last_card_events'):
                    self.assertEqual(b[key], raw[key], key)
                self.assertEqual(s['rules']['money'], dict(initial_cash=1500,start_salary=200,
                    income_tax=200,luxury_tax=100,bail=50,bank_houses=32,bank_hotels=12))
                for field, words in {'rent':['25/50/100/200','4*dice','10*sum'],
                                     'assets':['half','ceil','No build/redeem in debt'],
                                     'cards':['chance','chest','last_card_events','order hidden'],
                                     'trade':['3 proposals','trades','one per recipient','recipient responds','Mortgages'],
                                     'jail':['Third failed','no extra roll']}.items():
                    for word in words:self.assertIn(word,s['rules'][field])
                self.assertEqual(set(s['action_formats']['propose_trade']),
                                 {'to','give_cash','take_cash','give_tiles','take_tiles'})
                restored = deepcopy(b)
                pid = s['viewer_player_id']
                game.player(restored,pid)['jail_cards'] = [None]*s['private_state']['jail_cards']
                self.assertEqual(game.legal_actions(restored,pid),game.legal_actions(raw,pid))
                self.assertEqual(s['legal_actions'],game.legal_actions(raw,pid))
                self.assertEqual(s['trade_options']['partners'],game.trade_options(raw,pid)['partners'])
            if step < 300:room,_ = self.play(room)
        # Exercise a public card effect without exposing any future deck entry.
        raw=deepcopy(room['board_state']); raw['last_card_events']=[]
        game._event(raw,game.player(raw,raw['current_player_id']),'chance')
        game._drain_charges(raw)
        room=self.persist(room,raw);s=await self.recover(room)
        self.assertEqual(s['board_state']['last_card_events'],raw['last_card_events'])
        self.assertTrue(s['board_state']['last_card_events'])
        self.assertNotIn('_decks',json.dumps(s))

    async def test_rummikub_visible_ids_table_jokers_and_empty_pool(self):
        room=self.room('rummikub',4);await self.forget(room)
        for step in range(41):
            if step in (20,40):
                s=await self.recover(room);self.assert_rummikub(s,room['board_state'])
            if step<40:room,_=self.play(room)
        state=deepcopy(room['board_state'])
        # A conserved 106-tile late-game fixture with two explicit joker roles.
        meld=['red-9-1','joker-1','joker-2']
        all_ids=[t for h in state['hands'].values() for t in h]+state['pool']+[t for m in state['melds'] for t in m]
        remaining=[t for t in all_ids if t not in meld]
        self.assertEqual(len(set(all_ids)),106)
        state.update(melds=[meld],meld_kinds=['run'],pool=[],turn_player_id='p0',
                     hands={'p0':remaining[:3],'p1':remaining[3:36],'p2':remaining[36:69],'p3':remaining[69:]},
                     blocked_player_ids=['p1','p2','p3'])
        state['opened']['p0']=True;state['flow']['turn_number']=90
        room=self.persist(room,state);s=await self.recover(room)
        self.assert_rummikub(s,state)
        self.assertEqual(s['board_state']['pool_count'],0)
        self.assertEqual(s['board_state']['blocked_player_ids'],['p1','p2','p3'])
        await self.call(room,'p0',action='move',revision=room['revision'],move={'action':'pass'})
        room=framework.get_room(room['room_id']);s=await self.recover(room,'p0')
        self.assertEqual(s['status'],'finished');self.assertEqual(s['board_state']['result'],room['board_state']['result'])
        self.assert_rummikub(s,room['board_state'])

    def assert_rummikub(self,s,raw):
        b=s['board_state'];pid=s['viewer_player_id']
        self.assertEqual(b['melds'],raw['melds']);self.assertEqual(b['meld_kinds'],raw['meld_kinds'])
        self.assertEqual(s['private_state'],{'hand':raw['hands'][pid]})
        self.assertEqual(s['rules']['opening_threshold'],30)
        for text in ('copy=1|2','N=1..13','joker-1|joker-2','physical tiles'):
            self.assertIn(text,b['tile_encoding'])
        for field,words in {'group':['3..4','distinct colors'],'run':['ascending','never wraps'],
                            'opening':['>=30','old melds remain unchanged'],
                            'joker':['jointly','old non-joker companion','original hand tile'],
                            'turn':['empty pool','clears blocked']}.items():
            for word in words:self.assertIn(word,s['rules'][field])
        self.assertEqual(s['action_formats']['no_args'],['draw','pass'])
        self.assertIn('FULL final table',s['action_formats']['meld']['melds'])
        # Reconstruct physical tiles from the delivered encoding/inventory, not TILES.
        inv=s['rules']['inventory'];low,high=inv['numbers']
        decoded={f'{c}-{n}-{copy}':{'id':f'{c}-{n}-{copy}','color':c,'number':n}
                 for c in inv['colors'] for n in range(low,high+1) for copy in range(1,inv['copies']+1)}
        decoded.update({f'joker-{n}':{'id':f'joker-{n}','color':'joker','number':0}
                        for n in range(1,inv['jokers']+1)})
        self.assertEqual(len(decoded),inv['tiles'])
        for tid in s['private_state']['hand']+[t for m in b['melds'] for t in m]:
            self.assertEqual(decoded[tid],TILES[tid])
        with patch('app.games.rummikub.TILES',decoded):
            roles={j:r for m,k in zip(b['melds'],b['meld_kinds']) for j,r in meld_info(m,k)['joker_roles'].items()}
        self.assertEqual(roles,b['joker_roles'])
        visible=json.dumps(s)
        for owner,hand in raw['hands'].items():
            if owner!=pid:
                for tid in hand:self.assertNotIn(json.dumps(tid),visible)
        for tid in raw['pool']:self.assertNotIn(json.dumps(tid),visible)

    async def test_plane_geometry_all_directions_shots_setup_and_terminal(self):
        from app.games.bomb_plane import plane_cells
        room=self.room('bomb_plane',2);await self.forget(room)
        room,_=self.play(room,{'action':'place','head':'C1','direction':'N'})
        s=await self.recover(room,'p0')
        self.assertEqual(s['private_state']['planes'],[{'head':'C1','direction':'N'}])
        room,_=self.play(room,{'action':'auto_setup'});room,_=self.play(room,{'action':'auto_setup'})
        for step in range(81):
            if step in (38,80) or room['status']=='finished':
                for pid in ('p0','p1'):
                    s=await self.recover(room,pid);b=s['board_state'];rules=s['rules']
                    self.assertEqual((b['rows'],b['cols']),(10,10))
                    self.assertEqual(s['private_state']['planes'],room['board_state']['planes'][pid])
                    directions=s['action_formats']['place']['direction']
                    self.assertEqual(directions,['N','E','S','W'])
                    self.assertEqual(len(rules['north_offsets']),10)
                    # Decode all valid candidate shapes from payload dimensions/offsets.
                    for direction in directions:
                        for x in range(b['cols']):
                            for y in range(b['rows']):
                                points=[]
                                for dx,dy in rules['north_offsets']:
                                    for _ in range(directions.index(direction)):dx,dy=-dy,dx
                                    points.append((x+dx,y+dy))
                                if all(0<=px<b['cols'] and 0<=py<b['rows'] for px,py in points):
                                    head=f'{chr(65+x)}{y+1}'
                                    self.assertEqual([f'{chr(65+px)}{py+1}' for px,py in points],
                                                     list(plane_cells({'head':head,'direction':direction})))
                    for word in ('Bodies may overlap','heads must be distinct','head may overlap another body'):
                        self.assertIn(word,rules['layout'])
                    for word in ('ANY head','else hit','else miss','downed bodies still return hit'):
                        self.assertIn(word,rules['shots'])
                    for actor,shots in room['board_state']['shots'].items():
                        self.assertEqual(b['shots'][actor],{r:[v['cell'] for v in shots if v['result']==r]
                                                         for r in ('miss','hit','head')})
                    if room['status']=='playing':self.assertNotIn('revealed_planes',b)
                    else:self.assertEqual(b['revealed_planes'],room['board_state']['planes'])
                if room['status']=='finished':break
            if step<80:
                pid=room['current_player_id'];used={x['cell'] for x in room['board_state']['shots'][pid]}
                cell=next(f'{c}{r}' for r in range(1,11) for c in 'ABCDEFGHIJ' if f'{c}{r}' not in used)
                room,_=self.play(room,{'action':'attack','cell':cell})
        if room['status']=='playing':
            framework.resign(room['room_id'],'ai','p1');room=framework.get_room(room['room_id'])
            s=await self.recover(room,'p0');self.assertEqual(s['board_state']['revealed_planes'],room['board_state']['planes'])

    async def test_carcassonne_reconstructs_all_regions_and_repeatable_query(self):
        from app.games.carcassonne_tiles import oriented
        room=self.room('carcassonne',5);await self.forget(room)
        for step in range(73):
            if step in (30,60) or room['status']=='finished':
                pid=room['current_player_id'] or 'p0';s=await self.recover(room,pid);b=s['board_state']
                for key in ('board','scores','supply','flow','turn_player_id','current_tile','discarded','result'):
                    self.assertEqual(b[key],room['board_state'][key])
                self.assertEqual(b['deck_count'],len(room['board_state']['deck']))
                self.assertEqual(set(b['topology']),set('ABCDEFGHIJKLMNOPQRSTUVWX'))
                self.assertIn('Opposite field half-ports connect in reverse order',b['coordinates'])
                self.assertIn('C=city,R=road,F=field',s['rules']['topology'])
                self.assertIn('return ALL',s['rules']['completion'])
                self.assertIn('distinct',s['rules']['end'])
                self.assertEqual(s['rules']['scoring'],dict(completed_road_per_tile=1,
                    completed_city_per_tile_or_shield=2,unfinished_road_per_tile=1,
                    unfinished_city_per_tile_or_shield=1,monastery_max=9,field_per_adjacent_completed_city=3))
                self.assertEqual(set(s['action_formats']['place']),{'x','y','rotation','meeple'})
                # The decoder's sole tile definitions come from full_state, never engine TILES.
                def decoded(name,rotation=0):
                    data=deepcopy(b['topology'][name]);data['rotation']=rotation
                    data['edges']=[data['edges'][(i-rotation)%4] for i in range(4)]
                    for region in data['regions'].values():
                        field=region['kind']=='field'
                        region['ports']=sorted((p+rotation*(2 if field else 1))%(8 if field else 4) for p in region['ports'])
                    return data
                for name in b['topology']:
                    for rotation in range(4):self.assertEqual(decoded(name,rotation),oriented(name,rotation))
                oracle=FeatureGraph(room['board_state']['board'])
                with patch('app.games.carcassonne.oriented',decoded):
                    recovered=FeatureGraph(b['board'])
                self.assertEqual(recovered.components,oracle.components)
                for tile in b['board']:
                    if 'meeple' in tile:self.assertIn(tile['meeple']['region'],decoded(tile['tile'])['regions'])
                if room['status']=='finished':break
                cursor=self.cursor(room,pid)
                query=dict(action='state',move={'query':'placements','all':True})
                a=await self.call(room,pid,**query);c=await self.call(room,pid,**query)
                self.assertEqual(a,c);self.assertEqual(cursor,self.cursor(room,pid))
                self.assertEqual(set(a),{'revision','placements'});self.assertTrue(a['placements'])
                for x,y,r,regions in a['placements']:
                    self.assertTrue(set(regions)<=decoded(b['current_tile'],r)['regions'].keys())
                other=next(p['player_id'] for p in room['participants'] if p['player_id']!=pid)
                off=await self.recover(room,other)
                self.assertEqual(off['board_state']['topology'],b['topology'])
                denied=await self.client.post('/mcp/play',json=dict(room_id=room['room_id'],player_id=other,**query))
                self.assertEqual(denied.status_code,400)
            room,_=self.play(room)
