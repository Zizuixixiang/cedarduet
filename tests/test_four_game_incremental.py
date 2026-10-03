"""Output protocol contract against real temporary SQLite and /mcp/play."""
from copy import deepcopy
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

import httpx
from app import database, framework, invites, main
from app.games import GAMES
from app.games.monopoly import Monopoly
from app.games.rummikub import Rummikub, meld_info
from app.games.bomb_plane import BombPlane
from app.games.carcassonne import Carcassonne


class FourGameIncremental(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='four-game-incremental-')
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(database, 'DB_PATH', Path(self.tmp.name)/'test.db')
        p.start(); self.addCleanup(p.stop)
        database.init_db()
        games = {g.game_type:g for g in (Monopoly(random.Random(37)), Rummikub(random.Random(37)),
                                        BombPlane(random.Random(37)), Carcassonne(random.Random(37)))}
        p = patch.dict(GAMES, games); p.start(); self.addCleanup(p.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://incremental.test')
        self.addAsyncCleanup(self.client.aclose)

    def room(self, game, n, invited=False):
        if invited:
            room = invites.create_invite(game, 'ai', 'p0', target_player_count=n)
            for i in range(1,n): invites.join_invite(room['invite_code'], 'ai', f'p{i}')
            with patch.object(invites.secrets, 'SystemRandom') as rng:
                rng.return_value.shuffle.side_effect = lambda seats: None
                return invites.start_invite(room['room_id'], 'ai', 'p0')
        seats = [dict(player_id=f'p{i}',role='ai',participant_kind='bound_machine',display_name='same name') for i in range(n)]
        return framework.create_room(game, 'ai_first','ai','p0',ordered_participants=seats,require_confirmations=False)

    async def call(self, room, pid='p0', **body):
        res = await self.client.post('/mcp/play',json=dict(room_id=room['room_id'],player_id=pid,**body))
        self.assertEqual(res.status_code,200,res.text)
        return res.json()

    def play(self, room, move=None):
        actor = next(p for p in room['participants'] if p['player_id']==room['current_player_id'])
        game = GAMES[room['game_type']]
        move = move or game.choose_local_npc_action(room['board_state'],actor,room['participants'])
        return framework.play_move(room['room_id'],actor['role'],actor['player_id'],move,expected_revision=room['revision']),move

    def cursor(self, room, pid='p0'):
        with database.connect() as conn:
            return tuple(conn.execute('SELECT last_event_id,mcp_bootstrapped FROM room_event_cursors WHERE room_id=? AND player_id=?',(room['room_id'],pid)).fetchone())

    @staticmethod
    def apply_table(patch_value, melds, kinds):
        size=patch_value['size']
        melds[:]=melds[:size]+[None]*max(0,size-len(melds))
        kinds[:]=kinds[:size]+[None]*max(0,size-len(kinds))
        for i,m,k in patch_value['set']: melds[i]=m; kinds[i]=k

    async def test_all_opponents_order_exactly_once_ordinary_and_invite(self):
        for name,n in [('monopoly',6),('rummikub',4),('carcassonne',5),('bomb_plane',2)]:
            for invited in (False,True):
                with self.subTest(game=name,invited=invited):
                    room=self.room(name,n,invited)
                    if name=='bomb_plane':
                        room,_=self.play(room,{'action':'auto_setup'})
                        room,_=self.play(room,{'action':'auto_setup'})
                    self.assertTrue((await self.call(room,action='state'))['bootstrap'])
                    for _ in range(60):
                        if room['current_player_id']!='p0': break
                        move=GAMES[name].choose_local_npc_action(room['board_state'],room['participants'][0],room['participants'])
                        await self.call(room,action='move',move=move,revision=room['revision'])
                        room=framework.get_room(room['room_id'])
                    expected=[]
                    for _ in range(160):
                        if room['current_player_id']=='p0': break
                        pid=room['current_player_id']; room,move=self.play(room)
                        expected.append((pid,move['action'] if name not in ('bomb_plane','carcassonne') else move.get('cell',room['board_state']['last_action']['tile'] if name=='carcassonne' else None)))
                    self.assertEqual(room['current_player_id'],'p0')
                    reply=await self.call(room,action='state')
                    actual=[tuple(e[:2]) for e in reply['events'] if isinstance(e,list)]
                    self.assertEqual(actual,expected)
                    self.assertEqual({p for p,_ in actual},{f'p{i}' for i in range(1,n)})
                    self.assertNotIn('events',await self.call(room,action='state'))

    async def test_monopoly_bootstrap_base_and_ledger_reconstruction(self):
        room=self.room('monopoly',6); game=GAMES['monopoly']
        boot=await self.call(room,action='state'); ledger=deepcopy(game._public_delta(room['board_state'])['monopoly'])
        for _ in range(120):
            if room['status']!='playing': break
            room,move=self.play(room)
            # Force consuming via chat, as a read by the same observer, even while waiting.
            reply=await self.call(room,action='chat',message='sync')
            for actor,action,*tail in [e for e in reply.get('events',[]) if isinstance(e,list)]:
                ledger['action_seq']+=1
                for k,v in (tail[0] if tail else {}).items():
                    if k in ('p','t'):
                        key,fields=('players',('player_id','cash','position','bankrupt','jailed','jail_turns')) if k=='p' else ('tiles',('id','owner','level','mortgaged'))
                        rows={p[fields[0]]:p for p in ledger[key]}
                        for row in v: rows[row[0]]=dict(zip(fields,row))
                        ledger[key]=list(rows.values())
                    else: ledger['turn_player_id' if k=='next' else k]=v
            actual=game._public_delta(room['board_state'])['monopoly']
            for k in actual:
                if k not in ('note','last_card_events'): self.assertEqual(ledger[k],actual[k],k)
            if room['revision']==1: self.assertNotIn('t',reply['events'][0][2])

    async def test_rummikub_private_delta_rebuilds_hand_and_public_patch(self):
        room=self.room('rummikub',4); game=GAMES['rummikub']
        hands={}; melds=[]; kinds=[]; saw_draw=saw_meld=False
        for p in room['participants']:
            boot=await self.call(room,p['player_id'],action='state')
            hands[p['player_id']]=boot['room']['private_state']['hand']
        for _ in range(150):
            if room['status']!='playing': break
            pid=room['current_player_id']; actor=next(p for p in room['participants'] if p['player_id']==pid)
            state_reply=await self.call(room,pid,action='state')
            self.assertNotIn('private_state',state_reply)
            self.assertNotIn('suggested_move',json.dumps(state_reply))
            move=game.choose_local_npc_action(room['board_state'],actor,room['participants'])
            before=hands[pid][:]
            reply=await self.call(room,pid,action='move',revision=room['revision'],move=move)
            room=framework.get_room(room['room_id'])
            if move['action']=='meld':
                saw_meld=True; used={t for m in move['melds'] for t in m}
                hands[pid]=[t for t in hands[pid] if t not in used]
                self.assertNotIn('-',reply.get('private',{}))
            if move['action']=='draw': saw_draw=True
            delta=reply.get('private',{})
            self.assertNotIn('hand',delta)
            hands[pid]=[t for t in hands[pid] if t not in delta.get('-',[])]+delta.get('+',[])
            self.assertEqual(hands[pid],room['board_state']['hands'][pid])
            for e in reply.get('events',[]):
                if isinstance(e,list) and e[0]==pid and e[1]=='meld': self.apply_table(e[2]['table_patch'],melds,kinds)
            self.assertEqual(melds,room['board_state']['melds'])
            self.assertEqual(kinds,room['board_state']['meld_kinds'])
            if move['action']=='draw':
                other=room['current_player_id']
                if other!=pid:
                    public=await self.call(room,other,action='state')
                    for tile in set(hands[pid])-set(before): self.assertNotIn(tile,json.dumps(public))
        self.assertTrue(saw_draw and saw_meld)

    async def test_bomb_layout_changes_only_and_public_attacks(self):
        room=self.room('bomb_plane',2)
        for pid in ('p0','p1'): await self.call(room,pid,action='state')
        for pid in ('p0','p1'):
            reply=await self.call(room,pid,action='move',revision=room['revision'],move={'action':'auto_setup'})
            room=framework.get_room(room['room_id'])
            self.assertEqual(reply['private']['planes'],room['board_state']['planes'][pid])
        own=await self.call(room,action='state'); self.assertNotIn('private',own)
        await self.call(room,action='move',revision=room['revision'],move={'action':'attack','cell':'A1'})
        room=framework.get_room(room['room_id']); room,_=self.play(room,{'action':'attack','cell':'B1'})
        reply=await self.call(room,action='state')
        self.assertEqual(reply['events'],[['p1','B1',room['board_state']['shots']['p1'][0]['result']]])
        for k in ('room_id','current_actor','your_turn','ok','private_state','status'): self.assertNotIn(k,reply)

    async def test_carcassonne_queries_map_scoring_and_complete_game(self):
        room=self.room('carcassonne',5)
        for p in room['participants']: await self.call(room,p['player_id'],action='state')
        board=deepcopy(room['board_state']['board']); scores=deepcopy(room['board_state']['scores']); supply=deepcopy(room['board_state']['supply'])
        used_regions=0
        for _ in range(71):
            if room['status']!='playing': break
            pid=room['current_player_id']
            normal=await self.call(room,pid,action='state'); self.assertNotIn('placements',json.dumps(normal))
            before=self.cursor(room,pid)
            choices=await self.call(room,pid,action='state',move={'query':'placements','all':True})
            self.assertEqual(before,self.cursor(room,pid))
            x,y,r,regions=choices['placements'][0]
            meeple=regions[0] if regions else None
            candidate=await self.call(room,pid,action='state',move={'query':'placements','x':x,'y':y,'rotation':r,'meeple':meeple})
            self.assertTrue(candidate['valid']); self.assertLessEqual(len(candidate['placements']),8)
            move=dict(action='place',x=x,y=y,rotation=r,meeple=meeple)
            reply=await self.call(room,pid,action='move',revision=normal['r'],move=move)
            room=framework.get_room(room['room_id'])
            e=reply['events'][-1]; actor,tile,x,y,r,m,next_tile,*effects=e
            placed=dict(x=x,y=y,rotation=r,tile=tile)
            if m: placed['meeple']=dict(player_id=actor,region=m); used_regions+=1
            board.append(placed)
            delta=effects[0] if effects else {}
            for scoring in delta.get('scoring',[]):
                for px,py,owner in scoring['returned']:
                    t=next(t for t in board if (t['x'],t['y'])==(px,py)); self.assertEqual(t['meeple']['player_id'],owner); t.pop('meeple')
            scores.update(delta.get('scores',{})); supply.update(delta.get('supply',{}))
            self.assertEqual(board,room['board_state']['board']); self.assertEqual(scores,room['board_state']['scores']); self.assertEqual(supply,room['board_state']['supply'])
        self.assertEqual(room['status'],'finished'); self.assertGreater(used_regions,10)

    async def test_full_resync_without_retaining_bootstrap(self):
        for name,n in [('monopoly',4),('rummikub',4),('bomb_plane',2),('carcassonne',5)]:
            room=self.room(name,n); game=GAMES[name]
            for p in room['participants']:
                await self.call(room,p['player_id'],action='state')  # discard bootstrap
            for step in range(101):
                if room['status']!='playing': break
                if step % 20:
                    room,_=self.play(room)  # fixture generation, not recovery evidence
                    continue
                pid=room['current_player_id']
                full=await self.call(room,pid,action='state',full_state=True)
                snapshot=full['snapshot']; board=snapshot['board_state']
                self.assertEqual(full['r'],room['revision'])
                self.assertTrue(full['full_state']); self.assertNotIn('bootstrap',full)
                self.assertNotIn('events',full)
                self.assertEqual(snapshot['viewer_player_id'],pid)
                self.assertEqual(snapshot['protocol'],2)
                self.assertTrue(snapshot['protocol_guide']); self.assertTrue(snapshot['rules'])
                self.assertTrue(snapshot['action_formats'])
                expected=framework.project_mcp_snapshot_for_viewer(framework._decorate(room),pid)
                expected['board_state'].pop('delta_format',None)
                self.assertEqual(board,expected['board_state'])
                self.assertEqual(snapshot['participants'],expected['participants'])
                keys={'monopoly':('jail_cards',),'rummikub':('hand',),
                      'bomb_plane':('planes',),'carcassonne':()}[name]
                self.assertEqual(snapshot['private_state'],{k:expected['private_state'][k] for k in keys})
                for key in ('_decks','pool','deck','hands','rng_state','placements'):
                    self.assertNotIn(key,board)
                    self.assertNotIn(key,snapshot['private_state'])
                if name=='monopoly':
                    restored=deepcopy(board)
                    game.player(restored,pid)['jail_cards']=[None]*snapshot['private_state']['jail_cards']
                    self.assertEqual(game.legal_actions(restored,pid),game.legal_actions(room['board_state'],pid))
                    self.assertEqual(snapshot['legal_actions'],game.legal_actions(restored,pid))
                self.assertNotIn('events',await self.call(room,pid,action='state'))

    async def test_explicit_full_state_unknown_context_bootstraps_once(self):
        for name,n in [('monopoly',4),('rummikub',4),('bomb_plane',2),('carcassonne',5)]:
            for version in (None,1,999):
                room=self.room(name,n)
                if version is not None:
                    with database.write_transaction() as conn:
                        conn.execute('INSERT INTO mcp_minimal_contexts VALUES(?,?,?)',
                                     (room['room_id'],'p0',json.dumps({'v':version})))
                reply=await self.call(room,action='state',full_state=True)
                self.assertTrue(reply['bootstrap']); self.assertEqual(reply['protocol'],2)
                self.assertIn('protocol_guide',reply); self.assertIn('rules_text',reply['room'])
                self.assertNotIn('events',reply)
                full=await self.call(room,action='state',full_state=True)
                self.assertTrue(full['full_state']); self.assertNotIn('bootstrap',full)
                self.assertNotIn('protocol_guide',full)
                self.assertNotIn('events',await self.call(room,action='state'))

    async def test_full_state_preserves_unread_text_without_replaying_actions(self):
        for name in ('monopoly','rummikub','bomb_plane','carcassonne'):
            for invited in (False,True):
                with self.subTest(game=name,invited=invited):
                    room=self.room(name,2,invited)
                    for pid in ('p0','p1'): await self.call(room,pid,action='state')
                    room,_=self.play(room)
                    framework.post_message(room['room_id'],'ai','p1','pending chat')
                    framework.post_message(room['room_id'],'ai','p1','hidden',visible_to_player_ids={'p1'})
                    # A non-game textual notification uses the same viewer projection.
                    with database.write_transaction() as conn:
                        framework._record_event(conn,room['room_id'],'system','system',room['revision'],
                                                event_type='message',text='explicit notice',visible_to_player_ids={'p0'})
                    room,_=self.play(room)
                    before=deepcopy(framework.get_room(room['room_id'])['board_state'])
                    with database.connect() as conn:
                        messages=[tuple(r) for r in conn.execute('SELECT * FROM room_messages ORDER BY id')]
                    for _ in range(2):
                        full=await self.call(room,action='state',full_state=True)
                        self.assertNotIn('events',full)
                    framework.post_message(room['room_id'],'ai','p1','later chat')
                    reply=await self.call(room,action='state')
                    self.assertEqual(reply['events'],[
                        {'actor':'p1','message':'pending chat'},
                        {'actor':'system','message':'explicit notice'},
                        {'actor':'p1','message':'later chat'}])
                    self.assertNotIn('events',await self.call(room,action='state'))
                    self.assertNotIn('private',reply)
                    with database.connect() as conn:
                        self.assertEqual(messages,[tuple(r) for r in conn.execute('SELECT * FROM room_messages ORDER BY id')][:-1])
                        self.assertEqual(self.cursor(room)[0],conn.execute('SELECT MAX(id) FROM room_messages WHERE room_id=?',(room['room_id'],)).fetchone()[0])
                    self.assertEqual(before,framework.get_room(room['room_id'])['board_state'])
                    # New actions still encode relative to the snapshot baseline.
                    room,_=self.play(room)
                    from app import mcp_minimal
                    reply=mcp_minimal.response(room['room_id'],'p0',consume=True)
                    self.assertEqual(len([e for e in reply.get('events',[]) if isinstance(e,list)]),1)
                    self.assertNotIn('events',mcp_minimal.response(room['room_id'],'p0',consume=True))

    async def test_full_state_retains_action_text_and_new_actions(self):
        for invited in (False,True):
            room=self.room('rummikub',4,invited)
            await self.call(room,action='state')
            room,_=self.play(room,{'action':'draw'})
            room=framework.play_move(room['room_id'],'ai','p1',{'action':'draw'},
                                     message='move annotation',expected_revision=room['revision'])
            await self.call(room,action='state',full_state=True)
            room,_=self.play(room,{'action':'draw'})
            reply=await self.call(room,action='state')
            self.assertEqual(reply['events'],[{'actor':'p1','message':'move annotation'},['p2','draw']])
            self.assertEqual(reply['wait'],'p3')
            self.assertNotIn('private',reply)
            self.assertNotIn('events',await self.call(room,action='state'))

    async def test_full_state_concurrent_text_delivery_and_waiting(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from app import mcp_minimal
        for invited in (False,True):
            for concurrent_full in (False,True):
                room=self.room('rummikub',4,invited)
                await self.call(room,action='state')
                room,_=self.play(room,{'action':'draw'})
                framework.post_message(room['room_id'],'ai','p1','pending chat')
                await self.call(room,action='state',full_state=True)
                # A post-snapshot action must survive alongside the older text.
                room,_=self.play(room,{'action':'draw'})
                barrier=Barrier(2)
                def read(i):
                    barrier.wait(timeout=5)
                    return mcp_minimal.response(room['room_id'],'p0',full=concurrent_full and i==0)
                with ThreadPoolExecutor(2) as pool: replies=list(pool.map(read,range(2)))
                replies.append(await self.call(room,action='state'))
                events=[e for reply in replies for e in reply.get('events',[])]
                self.assertEqual([e for e in events if isinstance(e,dict)],[{'actor':'p1','message':'pending chat'}])
                actions=[e for e in events if isinstance(e,list)]
                if concurrent_full: self.assertIn(actions,([], [['p1','draw']]))
                else: self.assertEqual(actions,[['p1','draw']])
                self.assertTrue(all(reply.get('wait')=='p2' for reply in replies))
                self.assertNotIn('events',await self.call(room,action='state'))

    async def test_full_state_preserves_auction_trade_debt_and_own_jail_cards(self):
        game=GAMES['monopoly']
        for phase in ('auction','trade','debt'):
            room=self.room('monopoly',4)
            for p in room['participants']:
                await self.call(room,p['player_id'],action='state')  # discard bootstrap
            state=deepcopy(room['board_state'])
            state['tiles'][1].update(owner='p0',level=1)
            state['tiles'][3].update(owner='p0',level=1)
            state['tiles'][6].update(owner='p0',mortgaged=True)
            state['players'][0]['jail_cards']=[next(iter(state['_decks']))]
            if phase=='auction':
                state['_auction_queue']=[8]; game._start_auction(state)
            elif phase=='trade':
                state=game.apply_action(state,dict(action='propose_trade',to='p1',give_cash=10,
                    take_cash=0,give_tiles=[],take_tiles=[],action_seq=0),room['participants'][0]).state
            else:
                game._charge(state,'p0',3000,reason='fixture debt'); game._drain_charges(state)
            pid=state['turn_player_id']
            with database.write_transaction() as conn:
                conn.execute('UPDATE rooms SET board_state=?,current_player_id=? WHERE room_id=?',
                             (json.dumps(state),pid,room['room_id']))
            full=await self.call(room,pid,action='state',full_state=True)
            board=full['snapshot']['board_state']; self.assertEqual(board['phase'],state['phase'])
            if phase=='trade':
                self.assertEqual(board['phase'],'roll')
                self.assertEqual(board['turn_player_id'],'p0')
                self.assertIn('later normal turn',full['snapshot']['rules']['trade'])
            self.assertEqual(board[phase],state[phase])
            private=full['snapshot']['private_state']
            self.assertEqual(private,{'jail_cards':len(game.player(state,pid)['jail_cards'])})
            restored=deepcopy(board)
            game.player(restored,pid)['jail_cards']=[None]*private['jail_cards']
            choices=game.legal_actions(restored,pid)
            self.assertEqual(choices,game.legal_actions(state,pid))
            self.assertEqual(full['snapshot']['legal_actions'],choices)
            self.assertEqual(len(board['tiles']),40)
            for choice in choices:
                game.validate_action(restored,choice,{'player_id':pid})

    async def test_full_state_joker_roles(self):
        room=self.room('rummikub',2); await self.call(room,action='state')
        state=deepcopy(room['board_state']); meld=['red-9-1','joker-1','joker-2']
        state['melds']=[meld]; state['meld_kinds']=['run']
        state['pool']=[t for t in state['pool'] if t not in meld]
        state['hands']={p:[t for t in h if t not in meld] for p,h in state['hands'].items()}
        with database.write_transaction() as conn:
            conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?',(json.dumps(state),room['room_id']))
        full=await self.call(room,action='state',full_state=True)
        self.assertEqual(full['snapshot']['board_state']['joker_roles'],meld_info(meld,'run')['joker_roles'])
        self.assertEqual(full['snapshot']['board_state']['meld_kinds'],['run'])
    async def test_full_state_plane_privacy_and_terminal_review(self):
        from app.games.bomb_plane import SQUARES
        for invited in (False,True):
            for ending in ('heads','resign','setup_resign'):
                with self.subTest(invited=invited,ending=ending):
                    room=self.room('bomb_plane',2,invited)
                    for pid in ('p0','p1'): await self.call(room,pid,action='state')
                    room,_=self.play(room,{'action':'auto_setup'})
                    if ending!='setup_resign': room,_=self.play(room,{'action':'auto_setup'})
                    for pid in ('p0','p1'):
                        full=await self.call(room,pid,action='state',full_state=True)
                        self.assertNotIn('revealed_planes',full['snapshot']['board_state'])
                        self.assertEqual(full['snapshot']['private_state'],{'planes':room['board_state']['planes'][pid]})
                    if ending!='heads':
                        await self.call(room,'p1',action='resign')
                    else:
                        heads=[p['head'] for p in room['board_state']['planes']['p1']]
                        for head in heads:
                            room,_=self.play(room,{'action':'attack','cell':head})
                            if room['status']=='finished': break
                            own_heads={p['head'] for p in room['board_state']['planes']['p0']}
                            used={s['cell'] for s in room['board_state']['shots']['p1']}
                            room,_=self.play(room,{'action':'attack','cell':next(c for c in SQUARES if c not in own_heads|used)})
                    room=framework.get_room(room['room_id'])
                    self.assertEqual(room['status'],'finished')
                    for pid in ('p0','p1'):
                        full=await self.call(room,pid,action='state',full_state=True)
                        board=full['snapshot']['board_state']
                        self.assertEqual(full['status'],'finished')
                        self.assertEqual(board['phase'],'finished')
                        self.assertEqual(board['revealed_planes'],room['board_state']['planes'])
                        self.assertEqual(full['snapshot']['private_state'],{'planes':room['board_state']['planes'][pid]})
                        self.assertNotIn('events',full)

    async def test_old_bootstrap_upgrades_once_without_mutating_messages(self):
        for name,n in [('monopoly',4),('rummikub',4),('bomb_plane',2),('carcassonne',5)]:
            room=self.room(name,n)
            framework.claim_mcp_bootstrap(room['room_id'],'p0'); framework.read_new_room_events(room['room_id'],'p0',mcp=True)
            room,_=self.play(room)
            def records():
                with database.connect() as conn: return [tuple(r) for r in conn.execute('SELECT * FROM room_messages WHERE room_id=? ORDER BY id',(room['room_id'],))]
            before=records(); reply=await self.call(room,action='state',wait=True)
            self.assertTrue(reply['bootstrap']); self.assertEqual(reply['protocol'],2)
            self.assertEqual(before,records())
            self.assertNotIn('bootstrap',await self.call(room,action='state'))

    async def test_new_notification_once_without_acknowledging(self):
        from app.notifications import unread_summary, create_notification
        room=self.room('rummikub',2); await self.call(room,action='state')
        before=unread_summary('ai','p0')
        self.assertNotIn('unread',await self.call(room,action='state'))
        with database.write_transaction() as conn:
            create_notification(conn,'ai','p0','game','test',room['room_id'],'new game event',event_key='minimal-new')
        first=await self.call(room,action='state',full_state=True); self.assertIn('unread',first)
        count=unread_summary('ai','p0'); self.assertGreater(count['total'],before['total'])
        self.assertNotIn('unread',await self.call(room,action='state'))
        self.assertEqual(count,unread_summary('ai','p0'))


    async def test_waiting_does_not_consume_and_concurrent_reads_deliver_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from app import mcp_minimal
        room=self.room('rummikub',4)
        await self.call(room,action='state')
        room,_=self.play(room,{'action':'draw'})
        cursor=self.cursor(room)
        waiting=await self.call(room,action='state')
        self.assertEqual(waiting['wait'],'p1'); self.assertEqual(cursor,self.cursor(room))
        for _ in range(3): room,_=self.play(room,{'action':'draw'})
        barrier=Barrier(2)
        def read(_):
            barrier.wait(timeout=5)
            return mcp_minimal.response(room['room_id'],'p0')
        with ThreadPoolExecutor(2) as pool: replies=list(pool.map(read,range(2)))
        events=[e for reply in replies for e in reply.get('events',[])]
        self.assertEqual(events,[[f'p{i}','draw'] for i in range(4)])
        self.assertEqual(sum('private' in reply for reply in replies),1)
        self.assertTrue(all(reply['r']==room['revision'] for reply in replies))

    async def test_wait_heartbeat_keeps_new_notice_visible(self):
        from app.notifications import create_notification
        room=self.room('rummikub',2); await self.call(room,action='state')
        await self.call(room,action='move',revision=room['revision'],move={'action':'draw'})
        with database.write_transaction() as conn:
            create_notification(conn,'ai','p0','game','test',room['room_id'],'new during wait',event_key='wait-notice')
        with patch.object(main,'wait_for_revision',return_value=None):
            reply=await self.call(room,action='state',wait=True,wait_generation=__import__("time").time_ns())
            self.assertIn('unread',reply); self.assertNotEqual(reply.get('status'),'still_waiting')
            again=await self.call(room,action='state',wait=True,wait_generation=__import__("time").time_ns())
            self.assertEqual(again['status'],'still_waiting'); self.assertNotIn('unread',again)

    async def test_schema_upgrade_is_idempotent_and_preserves_context(self):
        room=self.room('rummikub',2); await self.call(room,action='state')
        before=self.cursor(room)
        database.init_db(); database.init_db()
        self.assertEqual(before,self.cursor(room))
        self.assertNotIn('bootstrap',await self.call(room,action='state'))
        with database.connect() as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    async def test_query_validation_and_stale_move_leave_state_unchanged(self):
        room=self.room('carcassonne',2); await self.call(room,action='state')
        cursor=self.cursor(room)
        for query in ({'query':'placements','x':True,'y':0},{'query':'placements','all':'true'},
                      {'query':'placements','x':0,'y':0,'rotation':4}):
            reply=await self.client.post('/mcp/play',json=dict(action='state',room_id=room['room_id'],player_id='p0',move=query))
            self.assertEqual(reply.status_code,400)
        q=await self.call(room,action='state',move={'query':'placements','x':1000,'y':1000})
        self.assertFalse(q['valid']); self.assertTrue(q['placements']); self.assertEqual(cursor,self.cursor(room))
        query=await self.call(room,action='state',move={'query':'placements','all':True})
        x,y,r,_=query['placements'][0]; move=dict(action='place',x=x,y=y,rotation=r,meeple=None)
        await self.call(room,action='move',revision=query['revision'],move=move)
        before=framework.get_room(room['room_id'])
        stale=await self.client.post('/mcp/play',json=dict(action='move',room_id=room['room_id'],player_id='p0',revision=query['revision'],move=move))
        self.assertEqual(stale.status_code,409)
        self.assertEqual(before['board_state'],framework.get_room(room['room_id'])['board_state'])


    async def test_immediate_achievement_payload_survives_atomic_reload(self):
        room=self.room('rummikub',2); await self.call(room,action='state')
        original=main.play_move
        def play(*args,**kwargs):
            result=original(*args,**kwargs)
            result['achievement_unlocks']=[dict(subject_type='ai',subject_id=pid,id='test-award',name='award',reward=5) for pid in ('p0','p1')]
            return result
        with patch.object(main,'play_move',side_effect=play):
            reply=await self.call(room,action='move',revision=room['revision'],move={'action':'draw'})
        self.assertEqual(reply['unlocks'],[dict(id='test-award',name='award',reward=5)])


if __name__=='__main__': unittest.main()
