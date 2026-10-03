"""Isolated fixtures/transport for DOM and optional browser acceptance checks.

DUEL_DB_PATH must explicitly name a disposable database. Never use production.
"""
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

if not os.environ.get('DUEL_DB_PATH'):
    raise SystemExit('DUEL_DB_PATH must point to a disposable test database')

from app import database, framework, invites
from app.games.rummikub import TILES
from tests.test_rummikub import players, run


def seed(name, count=2, invited=False):
    database.init_db()
    table=[]; opened=False; empty=False
    hand=run('red',10,12)+['red-1-1','blue-2-1','black-4-1','orange-7-1','blue-8-1',
                         'black-9-1','orange-10-1','red-13-2','blue-12-2','black-1-2','orange-2-2']
    if name=='ambiguous':
        hand=['red-9-1','joker-1','joker-2','blue-1-1']
    elif name=='long':
        table=[run(c,n,n+2,copy) for c in ('red','blue','black','orange') for copy in (1,2) for n in (1,4,7)][:20]
        used=set(sum(table,[])); hand=[t for t in TILES if t not in used][:28]; opened=True
    elif name=='split':
        table=[run('red',1,6)]
        hand=['red-7-1','blue-10-1','blue-11-1','blue-12-1','orange-2-2']; opened=True
    elif name=='joker':
        table=[['red-3-1','blue-3-1','joker-1'],run('black',7,11)]
        hand=['black-3-1','blue-10-1','blue-11-1','red-13-2']; opened=True
    elif name=='empty':
        table=[run('red',1,3)]; hand=['red-4-1','orange-13-1']; opened=True; empty=True
    if invited:
        room=invites.create_invite('rummikub','human','p0',target_player_count=count,display_name='玩家0')
        for i in range(1,count):
            invites.join_invite(room['invite_code'],'human',f'p{i}',display_name=f'玩家{i}')
        with patch.object(invites.secrets,'SystemRandom') as secure:
            secure.return_value.shuffle.side_effect=lambda seats:None
            room=invites.start_invite(room['room_id'],'human','p0')
    else:
        room=framework.create_room('rummikub','human_first','human','p0',ordered_participants=players(count))
    state=room['board_state']; used=set(hand+sum(table,[])); rest=[t for t in TILES if t not in used]
    # Keep a real draw available in the long-table fixture, including four seats.
    each=len(rest)//(count-1) if empty else min(14,(len(rest)-1)//(count-1))
    hands={'p0':hand,**{f'p{i}':rest[(i-1)*each:i*each] for i in range(1,count)}}
    pool=rest[(count-1)*each:]
    if empty:
        hands[f'p{count-1}'].extend(pool); pool=[]
    state.update(hands=hands,pool=pool,melds=table,
                 opened={f'p{i}':opened if i==0 else True for i in range(count)},turn_player_id='p0')
    assert len(set(sum(hands.values(),[])+pool+sum(table,[])))==106
    with database.write_transaction() as conn:
        conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?',(json.dumps(state),room['room_id']))
    return framework.get_room(room['room_id'])


if __name__=='__main__':
    request=json.load(sys.stdin)
    try:
        if request['action']=='seed': room=seed(request.get('fixture','opening'),request.get('count',2),request.get('invited',False))
        elif request['action']=='move':
            room=framework.play_move(request['room_id'],'human','p0',request['move'],expected_revision=request['revision'])
        elif request['action']=='state': room=framework.get_room(request['room_id'])
        elif request['action']=='opponent':
            room=framework.get_room(request['room_id'])
            actor=next(p for p in room['participants'] if p['player_id']==room['current_player_id'])
            room=framework.play_move(room['room_id'],actor['role'],actor['player_id'],{'action':'draw' if room['board_state']['pool'] else 'pass'},expected_revision=room['revision'])
        else: raise ValueError('Unknown test fixture action')
        result={'ok':True,'room':framework.project_room_for_viewer(room,request.get('viewer','p0')),'timeline':[], 'message':''}
    except framework.DuelError as exc: result={'ok':False,'message':str(exc),'status':exc.status_code}
    print(json.dumps(result,ensure_ascii=False))
