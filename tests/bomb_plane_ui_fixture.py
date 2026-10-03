"""Browser-only transport: every action uses real framework + disposable SQLite."""
import json
import os
import sys
from unittest.mock import patch
if not os.environ.get('DUEL_DB_PATH'):
    raise SystemExit('Explicit disposable DUEL_DB_PATH required')
from app import database, framework, invites
from app.games import game_catalog
from tests.test_bomb_plane import players, LAYOUT


def seed(invited=False):
    database.init_db()
    if invited:
        r=invites.create_invite('bomb_plane','human','p0',target_player_count=2,display_name='玩家零')
        invites.join_invite(r['invite_code'],'human','p1',display_name='玩家一')
        with patch.object(invites.secrets,'SystemRandom') as rng:
            rng.return_value.shuffle.side_effect=lambda seats:None
            r=invites.start_invite(r['room_id'],'human','p0')
    else:r=framework.create_room('bomb_plane','human_first','human','p0',ordered_participants=players('human'))
    return r


if __name__=='__main__':
    request=json.load(sys.stdin); viewer=request.get('viewer','p0')
    try:
        action=request['action']
        if action=='seed':r=seed(request.get('invited',False))
        else:
            r=framework.get_room(request['room_id'])
            if action=='move':
                r=framework.play_move(r['room_id'],'human',viewer,request['move'],expected_revision=request['revision'])
            elif action=='opponent':
                pid=r['current_player_id'];state=r['board_state']
                moves=([{'action':'set_layout','planes':LAYOUT},{'action':'ready'}] if state['phase']=='setup' else
                       [{'action':'attack','cell':next(c for c in ['J10','I10','H10','G10','F10','E10','D10'] if c not in {s['cell'] for s in state['shots'][pid]})}])
                for move in moves:r=framework.play_move(r['room_id'],'human',pid,move,expected_revision=r['revision'])
            elif action=='finish':
                while r['status']=='playing':
                    pid=r['current_player_id']; state=r['board_state']
                    if pid=='p0':
                        cell=next(p['head'] for p in state['planes']['p1'] if p['head'] not in {s['cell'] for s in state['shots'][pid]})
                    else:
                        cell=next(c for c in ['J10','I10','H10','G10','F10','E10','D10'] if c not in {s['cell'] for s in state['shots'][pid]})
                    r=framework.play_move(r['room_id'],'human',pid,{'action':'attack','cell':cell},expected_revision=r['revision'])
        result={'ok':True,'room':framework.project_room_for_viewer(r,viewer),'timeline':framework.list_timeline(r['room_id'],100,viewer),'games':game_catalog(),'message':''}
    except framework.DuelError as exc:result={'ok':False,'message':str(exc),'status':exc.status_code}
    print(json.dumps(result,ensure_ascii=False))
