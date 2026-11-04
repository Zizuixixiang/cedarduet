"""Pregame team selection uses real room persistence and transport identities."""
import base64
import json
from unittest.mock import patch

from app import database, framework, invites, liars_dice_setup as setup
from app.npc_personas import NpcPersona
from tests import test_multiplayer_games as support

OPTIONS = {'mode':'party', 'team_mode':'teams', 'random_teams':False}


def expire(room):
    state = room['board_state']
    state['team_selection']['start_after'] = '2000-01-01T00:00:00+00:00'
    with database.write_transaction() as conn:
        conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?', (json.dumps(state),room['room_id']))
    return framework.get_room(room['room_id'])


def seat(room, pid, team, index, **extra):
    return setup.choose_seat(room['room_id'], 'human' if pid.startswith('human') else 'ai', pid,
        {'action':'choose_team_seat','team':team,'seat':index,**extra},room['revision'])


class TeamSelectionTests(support.MultiplayerGameTestCase):
    def create(self, count=6, real_count=None):
        return framework.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',
            ordered_participants=support.participants(real_count or count),game_options=OPTIONS,target_player_count=count)

    def test_manual_own_seat_swap_conflict_revision_and_start(self):
        room = self.create()
        self.assertEqual(room['status'],'waiting')
        self.assertFalse(room['team_selection']['can_start'])
        with self.assertRaises(framework.DuelError):
            setup.start_regular(room['room_id'],'human','human-1')
        view = framework.project_room_for_viewer(room,'human-1')
        self.assertEqual(view['private_state'],{})
        self.assertNotIn('dice_by_player',view['board_state'])
        self.assertNotIn('dice_counts',room['board_state'])
        room = seat(room,'human-1','red',1)
        stale = room
        room = seat(room,'ai-1','blue',1)
        with self.assertRaises(framework.DuelError):
            seat(stale,'human-1','red',2)
        with self.assertRaises(framework.DuelError):
            seat(room,'human-1','blue',1)
        with self.assertRaises(framework.DuelError):
            seat(room,'human-1','blue',2,player_id='ai-1')
        room = seat(room,'human-1','blue',2)
        self.assertIsNone(room['team_selection']['seats']['red'][0])
        room = seat(room,'ai-1','red',3)
        room = expire(room)
        with self.assertRaises(framework.DuelError):
            setup.start_regular(room['room_id'],'ai','ai-1')
        room = setup.start_regular(room['room_id'],'human','human-1')
        teams = room['board_state']['teams']
        self.assertEqual(teams['blue'][1],'human-1')
        self.assertEqual(teams['red'][2],'ai-1')
        self.assertEqual([len(v) for v in teams.values()],[3,3])
        self.assertEqual(set(sum(teams.values(),[])),{p['player_id'] for p in room['participants']})
        self.assertNotIn('team_selection',room['board_state'])
        self.assertNotIn('team_selection',room)
        self.assertEqual(len(room['board_state']['dice_by_player']),6)
        with self.assertRaises(framework.DuelError):
            seat(room,'human-1','red',1)
        for index, participant in enumerate(room['participants']):
            pid = participant['player_id']
            room['board_state']['dice_by_player'][pid] = [index+1]*5
        for participant in room['participants']:
            pid = participant['player_id']
            view = framework.project_room_for_viewer(room,pid)
            self.assertEqual(view['private_state']['dice'],room['board_state']['dice_by_player'][pid])
            self.assertNotIn('dice_by_player',view['board_state'])

    def test_npcs_only_join_on_start_and_preserve_manual_seats(self):
        room = self.create(real_count=2)
        self.assertEqual(len(room['participants']),2)
        room = seat(room,'human-1','blue',3)
        room = expire(room)
        with self.assertRaises(framework.DuelError):
            setup.start_regular(room['room_id'],'human','human-1')
        with patch('app.npc_personas.select_personas',side_effect=lambda n:[NpcPersona(f'test-{i}',f'NPC {i}','test') for i in range(n)]), patch('app.invites.select_personas',side_effect=lambda n:[NpcPersona(f'test-{i}',f'NPC {i}','test') for i in range(n)]), patch('app.invites.npc_provider_capabilities',return_value={'available':True}):
            room = setup.start_regular(room['room_id'],'human','human-1',True)
        self.assertEqual(sum(p['participant_kind']=='system_npc' for p in room['participants']),4)
        self.assertEqual(room['board_state']['teams']['blue'][2],'human-1')
        self.assertEqual([len(t) for t in room['board_state']['teams'].values()],[3,3])

    def test_random_regular_skips_selection_and_classic_and_solo_unchanged(self):
        with patch('app.games.liars_dice.random.SystemRandom.shuffle') as shuffled:
            room = framework.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',
                ordered_participants=support.participants(6),game_options={**OPTIONS,'random_teams':True})
        self.assertEqual(shuffled.call_count, 2)  # Teams and table seats are independent draws.
        self.assertEqual(room['status'],'playing')
        self.assertNotIn('team_selection',room['board_state'])
        self.assertEqual([len(t) for t in room['board_state']['teams'].values()],[3,3])
        for options, mode in [({},'classic'),({'mode':'party'},'party')]:
            r=framework.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',game_options=options)
            self.assertEqual(r['status'],'playing')
            self.assertEqual(r['board_state']['mode'],mode)
            self.assertNotIn('teams',r['board_state'])

    def test_manual_invite_joins_unselected_and_npc_fill(self):
        room = invites.create_invite('liars_dice','human','human-1',target_player_count=6,game_options=OPTIONS)
        room = invites.join_invite(room['invite_code'],'ai','ai-1')
        room = seat(room,'ai-1','red',3)
        room = seat(room,'human-1','blue',1)
        with self.assertRaises(framework.DuelError):
            invites.start_invite(room['room_id'],'human','human-1',fill_with_npcs=True)
        room=expire(room)
        with patch('app.npc_personas.select_personas',side_effect=lambda n:[NpcPersona(f'test-{i}',f'NPC {i}','test') for i in range(n)]), patch('app.invites.select_personas',side_effect=lambda n:[NpcPersona(f'test-{i}',f'NPC {i}','test') for i in range(n)]), patch('app.invites.npc_provider_capabilities',return_value={'available':True}):
            room=invites.start_invite(room['room_id'],'human','human-1',fill_with_npcs=True)
        self.assertEqual(room['board_state']['teams']['red'][2],'ai-1')
        self.assertEqual(room['board_state']['teams']['blue'][0],'human-1')
        self.assertEqual(len(room['participants']),6)

    def test_setup_leave_closes_without_partial_dice_or_wallet_settlement(self):
        room = self.create()
        result = framework.leave_room(room['room_id'],'ai','ai-1')
        self.assertEqual(result['status'],'cancelled')

    def test_archived_setup_has_no_dice_and_rejects_new_seating(self):
        room = self.create()
        with database.write_transaction() as conn:
            conn.execute("UPDATE rooms SET status='archived' WHERE room_id=?", (room['room_id'],))
        room = framework.get_room(room['room_id'])
        view = framework.project_mcp_room_for_viewer(room, 'ai-1')
        self.assertEqual(view['private_state'], {})
        self.assertNotIn('dice_by_player',view['board_state'])
        with self.assertRaises(framework.DuelError):
            seat(room,'human-1','red',1)

    def test_manual_rematch_defers_same_npcs_and_retains_rules(self):
        room = self.create(count=4,real_count=2)
        room = expire(room)
        with patch('app.invites.npc_provider_capabilities',return_value={'available':True}), patch('app.npc_personas.select_personas',return_value=[NpcPersona(f'test-{i}',f'NPC{i}','test') for i in range(2)]):
            room = setup.start_regular(room['room_id'],'human','human-1',True)
        room = framework.play_move(room['room_id'],'human','human-1',{'action':'end_session'},expected_revision=room['revision'])
        rematch = framework.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',
            ordered_participants=room['participants'],game_options=OPTIONS,rematch_of_room_id=room['room_id'])
        self.assertEqual(len(rematch['participants']),2)
        self.assertNotIn('deferred_npcs',framework.project_room_for_viewer(rematch,'human-1')['board_state']['team_selection'])
        rematch = expire(rematch)
        with patch('app.invites.npc_provider_capabilities',return_value={'available':True}):
            rematch = setup.start_regular(rematch['room_id'],'human','human-1',True)
        self.assertEqual({p['player_id'] for p in room['participants']},{p['player_id'] for p in rematch['participants']})
        self.assertEqual(rematch['game_options']['random_teams'],False)


class TeamTransportTests(support.LiarsDiceMcpTests):
    async def test_mcp_manual_new_and_profile_without_public_schema_fields(self):
        reply = await self.client.post('/mcp/play',json={'action':'new','player_id':'ai-1','opponent_id':'human-1',
            'game_type':'liars_dice','target_player_count':4,'move':OPTIONS,
            'display_name':'可信昵称','display_avatar':{'type':'emoji','value':'🦊'}})
        self.assertEqual(reply.status_code,200,reply.text)
        room = reply.json()['room']
        self.assertEqual(room['status'],'waiting')
        ai = room['players']['ai-1']
        self.assertEqual(ai['display_name'],'可信昵称')
        self.assertEqual(ai['avatar'],{'type':'emoji','value':'🦊'})
        reply = await self.client.post('/mcp/play',json={'action':'start','player_id':'ai-1','room_id':room['room_id'],'fill_with_npcs':True})
        self.assertEqual(reply.status_code,409)
        from app.local_mcp import play_input_schema
        self.assertNotIn('display_avatar',play_input_schema()['properties'])

    async def test_web_and_mcp_self_only_seating_regular_table(self):
        headers={'X-Duel-Human-Player':'human-1',
                 'X-Duel-Human-Avatar':base64.b64encode(json.dumps({'type':'emoji','value':'🌲'}).encode()).decode(),
                 'X-Duel-Bound-Ais':base64.b64encode(json.dumps([{'id':'ai-1','name':'小机'}]).encode()).decode()}
        reply=await self.client.post('/api/rooms',headers=headers,json={'player_id':'human-1','ai_players':['ai-1'],
            'game_type':'liars_dice','target_player_count':4,'game_options':OPTIONS})
        self.assertEqual(reply.status_code,200,reply.text)
        room=reply.json()['room']
        self.assertEqual(room['status'],'waiting')
        self.assertEqual(next(p for p in room['participants'] if p['player_id']=='human-1')['avatar']['value'],'🌲')
        path=f"/api/rooms/{room['room_id']}/move"
        move={'action':'choose_team_seat','team':'red','seat':1}
        reply=await self.client.post(path,headers=headers,json={'player_id':'ai-1','move':move,'revision':room['revision']})
        self.assertEqual(reply.status_code,403)
        reply=await self.client.post(path,headers=headers,json={'player_id':'human-1','move':move,'revision':room['revision']})
        self.assertEqual(reply.status_code,200,reply.text)
        room=reply.json()['room']
        for action in ('move','state'):
            body={'action':action,'player_id':'ai-1','room_id':room['room_id'],'revision':room['revision']}
            if action=='move': body['move']={**move,'team':'blue'}
            reply=await self.client.post('/mcp/play',json=body)
            self.assertEqual(reply.status_code,200,reply.text)
            if action == 'move':
                self.assertTrue(reply.json()['bootstrap'])
                self.assertEqual(reply.json()['room']['selection']['seats']['blue'][0],'ai-1')
            else:
                self.assertEqual(set(reply.json()), {'r'})
        room=framework.get_room(room['room_id'])
        reply=await self.client.post('/mcp/play',json={'action':'move','player_id':'ai-1','room_id':room['room_id'],
            'revision':room['revision'],'move':{**move,'team':'blue','seat':2}})
        self.assertEqual(reply.status_code,200,reply.text)
        self.assertEqual(reply.json()['delta']['seats'],[['blue',1,None],['blue',2,'ai-1']])
        reply=await self.client.post('/mcp/play',json={'action':'move','player_id':'outsider','room_id':room['room_id'],
            'revision':room['revision'],'move':move})
        self.assertEqual(reply.status_code,403)
        room=expire(framework.get_room(room['room_id']))
        with patch('app.npc_personas.select_personas',side_effect=lambda n:[NpcPersona(f'test-{i}',f'NPC {i}','test') for i in range(n)]), patch('app.invites.select_personas',side_effect=lambda n:[NpcPersona(f'test-{i}',f'NPC {i}','test') for i in range(n)]), patch('app.invites.npc_provider_capabilities',return_value={'available':True}), patch('app.main._schedule_current_system_npc'):
            reply=await self.client.post(f"/api/rooms/{room['room_id']}/start",headers=headers,json={'fill_with_npcs':True})
        self.assertEqual(reply.status_code,200,reply.text)
        self.assertEqual(reply.json()['room']['status'],'playing')
