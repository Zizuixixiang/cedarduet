"""Subprocess fixture transport for the real browser, strictly disposable DB."""
import json
import os
from pathlib import Path
import random
import sys
from unittest.mock import patch

if not os.environ.get('DUEL_DB_PATH'):
    raise SystemExit('DUEL_DB_PATH must explicitly name a temporary test DB')
from app import database, framework, invites
from app.games import GAMES, game_catalog
from app.games.carcassonne import Carcassonne
from tests.test_carcassonne import players


def seed(name, count, invited):
    database.init_db()
    game=Carcassonne(random.Random(81+count))
    with patch.dict(GAMES,{'carcassonne':game}):
        if invited:
            room=invites.create_invite('carcassonne','human','p0',target_player_count=count,display_name='南杉')
            for i in range(1,count):invites.join_invite(room['invite_code'],'ai',f'p{i}',display_name=f'小机{i}')
            with patch.object(invites.secrets,'SystemRandom') as rng:
                rng.return_value.shuffle.side_effect=lambda seats:None
                room=invites.start_invite(room['room_id'],'human','p0')
        else:
            room=framework.create_room('carcassonne','human_first','human','p0',opponent_id='p1',ordered_participants=players(count),require_confirmations=False)
        s=room['board_state']
        if name=='late':
            for _ in range(60):
                a={'player_id':s['turn_player_id']};m=game.choose_local_npc_action(s,a,[]);s=game.apply_action(s,m,a).state
        elif name=='scoring':
            s['deck'].append(s['current_tile']);s['deck'].remove('E');s['current_tile']='E'
        elif name=='terminal':
            while not s['result']:
                a={'player_id':s['turn_player_id']};m=game.choose_local_npc_action(s,a,[]);s=game.apply_action(s,m,a).state
        with database.write_transaction() as conn:
            conn.execute('UPDATE rooms SET board_state=?,current_player_id=?,turn=? WHERE room_id=?',
                         (json.dumps(s),s['turn_player_id'],'human' if s['turn_player_id']=='p0' else 'ai',room['room_id']))
            if s['result']:
                conn.execute("UPDATE rooms SET status='finished',result_json=? WHERE room_id=?",(json.dumps(s['result']),room['room_id']))
        return framework.get_room(room['room_id'])

if __name__=='__main__':
    r=json.load(sys.stdin)
    try:
        if r['action']=='seed':room=seed(r.get('fixture','opening'),r.get('count',2),r.get('invited',False))
        elif r['action']=='move':room=framework.play_move(r['room_id'],'human','p0',r['move'],expected_revision=r['revision'])
        elif r['action']=='state':room=framework.get_room(r['room_id'])
        elif r['action']=='opponents':
            room=framework.get_room(r['room_id']);g=GAMES['carcassonne']
            while room['status']=='playing' and room['current_player_id']!='p0':
                a=next(p for p in room['participants'] if p['player_id']==room['current_player_id'])
                m=g.choose_local_npc_action(room['board_state'],a,room['participants'])
                room=framework.play_move(room['room_id'],a['role'],a['player_id'],m,expected_revision=room['revision'])
        else:raise ValueError('Unknown test fixture action')
        result={'ok':True,'room':framework.project_room_for_viewer(room,r.get('viewer','p0')),'timeline':[],'message':''}
        if r['action']=='seed':result['games']=game_catalog()
    except framework.DuelError as e:result={'ok':False,'message':str(e),'status':e.status_code}
    print(json.dumps(result,ensure_ascii=False))
