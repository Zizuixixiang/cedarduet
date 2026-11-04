"""Mode, room lifecycle and transport regressions; all DBs are disposable."""
import json
import base64
import random
import unittest
from copy import deepcopy
from unittest.mock import patch

from app import framework, invites, full_state, npc_controller
from app.games import GAMES, game_catalog
from app.games.liars_dice import LiarsDice
from tests import test_multiplayer_games as support


class PartyRulesTests(unittest.TestCase):
    def setUp(self):
        self.game = LiarsDice(random.Random(51))
        self.people = support.participants(5)
        self.state = self.game.configure(self.game.initialize(self.people), {"mode": "party", "rush_open": False})

    def test_opening_thresholds_and_options(self):
        self.assertEqual(len(game_catalog()), 34)
        for count in range(2, 7):
            state = self.game.configure(self.game.initialize(support.participants(count)), {"mode": "party"})
            minimum = (3 * count + 1) // 2
            for exact, face, qty in [(False, 2, minimum), (True, 2, count), (False, 1, count)]:
                self.game.validate_action(state, dict(action='bid', quantity=qty, face=face, exact=exact), self.people[0])
                with self.assertRaises(ValueError):
                    self.game.validate_action(state, dict(action='bid', quantity=qty-1, face=face, exact=exact), self.people[0])
        for options in ({'mode':'bad'}, {'mode':'party','literal_dice':1}, {'mode':'classic','literal_dice':True}, {'mode':'party','team':True}):
            with self.assertRaises(ValueError):
                self.game.normalize_options(options)
        self.assertEqual(self.game.normalize_options({'mode':'party'}, 1)['mode'], 'party')

    def test_wild_exact_literal_and_challenge_outcomes(self):
        # Ten dice: five ones + two twos. A bid of four twos holds only with wild ones.
        for options, exact, face, expected in [
            ({'mode':'party'}, False, 2, 7),
            ({'mode':'party'}, True, 2, 2),
            ({'mode':'party','literal_dice':True}, False, 2, 7),
            ({'mode':'party'}, False, 1, 5),
        ]:
            state = self.game.configure(self.game.initialize(self.people[:2]), options)
            state['dice_by_player'] = {'human-1':[1,1,1,2,3], 'ai-1':[1,1,2,4,5]}
            bid = dict(action='bid', quantity=4, face=face, exact=exact)
            self.game.validate_action(state, bid, self.people[0])
            self.game.apply_action(state, bid, self.people[0])
            result = self.game.apply_action(state, {'action':'challenge'}, self.people[1])
            outcome = result.state['last_round_result']
            loser = 'ai-1' if expected >= 4 else 'human-1'
            self.assertEqual(outcome['actual_count'], expected)
            self.assertEqual(outcome['loser_player_id'], loser)
            self.assertEqual(state['records'][loser]['losses'], 1)
            self.assertEqual(state['scores'][loser], -1)
            self.assertEqual(sum(state['scores'].values()), 0)
            self.assertEqual(outcome['score_delta'], -1)
            self.assertEqual(outcome['score_after'], -1)
            self.assertEqual(sum(r['wins'] for r in state['records'].values()), 1)
            self.assertEqual(state['dice_counts'], {'human-1':5, 'ai-1':5})
            self.assertFalse(state['eliminated_player_ids'])
            self.assertIsNone(result.result)

    def test_exact_lock_and_authoritative_ranges(self):
        state = self.state
        self.game.apply_action(state, {'action':'bid','quantity':5,'face':1}, self.people[0])
        self.assertTrue(state['current_bid']['exact'])
        with self.assertRaises(ValueError):
            self.game.validate_action(state, {'action':'bid','quantity':4,'face':6,'exact':True}, self.people[1])
        actions = self.game.npc_legal_actions(state, self.people[1], self.people)
        for move in actions:
            self.game.validate_action(state, move, self.people[1])
            if move['action'] == 'bid':
                self.assertTrue(move['exact'])
        ranges = self.game.bid_ranges(state)
        expanded = [dict(action='bid', quantity=q, face=r['face'], exact=r['exact'])
                    for r in ranges for q in range(r['min_quantity'], self.game.total_dice(state)+1)]
        self.assertEqual({json.dumps(a,sort_keys=True) for a in actions if a['action']=='bid'},
                         {json.dumps(a,sort_keys=True) for a in expanded})
        # Omitting exact after a lock cannot secretly restore wild ones.
        self.game.apply_action(state, {'action':'bid','quantity':5,'face':2}, self.people[1])
        self.assertTrue(state['current_bid']['exact'])
        rules = self.game.npc_compact_rules(state, self.people[1], self.people)
        self.assertIn('本轮锁斋', rules)
        self.assertNotIn('零骰淘汰', rules)

    def test_old_unconfigured_state_is_classic(self):
        state = self.game.initialize(self.people[:2])
        state['dice_by_player'] = {'human-1':[1]*5, 'ai-1':[1]*5}
        self.game.validate_action(state, {'action':'bid','quantity':1,'face':2}, self.people[0])
        self.game.apply_action(state, {'action':'bid','quantity':1,'face':2}, self.people[0])
        self.game.apply_action(state, {'action':'challenge'}, self.people[1])
        self.assertEqual(state['last_round_result']['actual_count'], 0)
        self.assertEqual(state['dice_counts']['human-1'], 4)
        with self.assertRaises(ValueError):
            self.game.validate_action(self.game.initialize(self.people), {'action':'bid','quantity':1,'face':2,'exact':True}, self.people[0])


class PartyRoomTests(support.MultiplayerGameTestCase):
    def setUp(self):
        super().setUp()
        self.game = LiarsDice(random.Random(52))
        self.patch = patch.dict(GAMES, {'liars_dice':self.game})
        self.patch.start(); self.addCleanup(self.patch.stop)

    def create(self, count=3):
        return framework.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',
                                     ordered_participants=support.participants(count),game_options={'mode':'party'})

    def act(self, room, action, player=None):
        player = player or room['current_player_id']
        return framework.play_move(room['room_id'], self.role_for(player), player, action, expected_revision=room['revision'])

    def test_repeated_rounds_persistence_privacy_and_end_out_of_turn(self):
        room = self.create()
        for round_number in range(1, 9):
            room = self.act(room, {'action':'bid','quantity':15,'face':6})
            before = deepcopy(room['board_state']['dice_by_player'])
            room = self.act(room, {'action':'challenge'})
            self.assertEqual(room['board_state']['flow']['round_number'], round_number)
            self.assertEqual(sum(room['board_state']['scores'].values()), 0)
            self.assertEqual(room['board_state']['last_round_result']['revealed_dice_by_player'], before)
            self.assertTrue(all(p['active'] for p in room['participants']))
            self.assertEqual(room['status'], 'playing')
            self.assertEqual(room['current_player_id'], room['board_state']['last_round_result']['loser_player_id'])
            waiting = room
            room = self.act(room, {'action':'acknowledge_round'})
            with self.assertRaises(framework.DuelError):
                self.act(waiting, {'action':'acknowledge_round'})
            room = framework.get_room(room['room_id'])  # reload persisted state
            self.assertEqual(room['board_state']['flow']['round_number'], round_number + 1)
            self.assertTrue(all(n == 5 for n in room['board_state']['dice_counts'].values()))
            for p in room['participants']:
                view = framework.project_room_for_viewer(room,p['player_id'])
                public = view['board_state']
                self.assertNotIn('dice_by_player',public)
                self.assertNotIn('terminal_dice',public)
                self.assertEqual(public['last_round_result']['revealed_dice_by_player'],before)
                self.assertEqual(view['private_state']['dice'],room['board_state']['dice_by_player'][p['player_id']])
        snapshot = full_state.project_full_state(room,'ai-1')
        self.assertIn('始终5骰',snapshot['rules_text'])
        self.assertEqual(len(snapshot['board_state']['round_results']),8)
        player = next(p['player_id'] for p in room['participants'] if p['player_id'] != room['current_player_id'])
        room = self.act(room, {'action':'end_session'},player)
        self.assertEqual(room['status'],'finished')
        self.assertEqual(room['result']['reason'],'session_ended')
        self.assertIsNone(room['winner_player_id'])
        self.assertEqual(sum(r['losses'] for r in room['board_state']['records'].values()),8)
        with self.assertRaises(framework.DuelError):
            self.act(room, {'action':'end_session'},player)

    def test_invite_selection_waiting_start_and_ack(self):
        room = invites.create_invite('liars_dice','ai','ai-1', target_player_count=2,
                                     game_options={'mode':'party','literal_dice':True})
        view = framework.project_room_for_viewer(room,'ai-1')
        self.assertEqual(view['game_options'],self.game.normalize_options({'mode':'party','literal_dice':True}))
        self.assertIn('酒桌',invites.preview_invite(room['invite_code'])['game_name'])
        invites.join_invite(room['invite_code'],'ai','ai-2')
        room = invites.start_invite(room['room_id'],'ai','ai-1')
        self.assertTrue(room['board_state']['literal_dice'])
        room = self.act(room, {'action':'bid','quantity':10,'face':6})
        room = self.act(room, {'action':'challenge'})
        view = framework.project_room_for_viewer(room, room['current_player_id'])
        self.assertEqual(view['private_state']['legal_actions'],[{'action':'acknowledge_round'}])
        room = self.act(room, {'action':'acknowledge_round'})
        self.assertEqual(room['board_state']['flow']['round_number'],2)

    def test_end_during_reveal_and_npc_cannot_end(self):
        room = self.create()
        room = self.act(room, {'action':'bid','quantity':15,'face':6})
        room = self.act(room, {'action':'challenge'})
        actor = dict(room['participants'][0],participant_kind='system_npc')
        with self.assertRaises(ValueError):
            self.game.validate_action(room['board_state'],{'action':'end_session'},actor)
        room = self.act(room, {'action':'end_session'}, 'human-1')
        self.assertEqual(room['status'],'finished')
        self.assertIsNone(room['board_state']['pending_next_round'])
        self.assertEqual(len(room['board_state']['round_results']),1)

    def test_positive_stakes_and_npc_request_privacy(self):
        created = framework.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',
                                  game_options={'mode':'party'},stake=1)
        self.assertEqual(created['status'], 'pending')
        invited = invites.create_invite('liars_dice','ai','ai-1',target_player_count=2,game_options={'mode':'party'},stake=1)
        self.assertEqual(invited['stake'], 1)
        room = self.create()
        actor = room['participants'][1]
        actions = self.game.npc_legal_actions(room['board_state'],actor,room['participants'])
        req, mapping = npc_controller._decision_request(room,actor['player_id'],actions,temporary=True)
        self.assertEqual(req.private_state['dice'],room['board_state']['dice_by_player'][actor['player_id']])
        self.assertNotIn('dice_by_player',req.public_state)
        self.assertNotIn('terminal_dice',req.public_state)
        self.assertIn('每盅5骰',req.game_rules)
        for action in mapping.values():
            self.game.validate_action(room['board_state'],action,actor)

    def test_legacy_pairs_turn_order_cup_count_team_scores_and_privacy(self):
        for count in (4, 6):
            with patch('app.games.liars_dice.random.SystemRandom.shuffle', lambda rng, values: None):
                room = framework.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',
                    ordered_participants=support.participants(count),game_options={'mode':'party','team_mode':'pairs','rush_open':False})
            state = room['board_state']
            state.pop('team_rules_version')
            state.pop('scoring_rules_version')
            state['team_next_speakers'] = {team: members[0] for team, members in state['teams'].items()}
            state['dice_by_player'] = {members[0]: state['dice_by_player'][members[0]] for members in state['teams'].values()}
            room = self.replace_state(room,state,'human-1')
            self.assertEqual(len(state['dice_by_player']),2)
            self.assertEqual(self.game.total_dice(state),14)
            # Team members share only their own five dice, including the NPC projection.
            for team, members in state['teams'].items():
                first = framework.project_room_for_viewer(room,members[0])
                second = framework.project_room_for_viewer(room,members[1])
                self.assertEqual(first['private_state']['dice'],second['private_state']['dice'])
                self.assertEqual(first['private_state']['team_id'],team)
                self.assertNotIn('dice_by_player',second['board_state'])
                actor = next(p for p in room['participants'] if p['player_id'] == members[1])
                legal = self.game.npc_legal_actions(state,actor,room['participants'])
                request, _ = npc_controller._decision_request(room,members[1],legal,temporary=True)
                self.assertEqual(request.private_state['dice'],state['dice_by_player'][members[0]])
                self.assertNotIn('dice_by_player',request.public_state)
            # Explicitly seed distinguishable cups to catch accidental enemy sharing.
            for index, members in enumerate(state['teams'].values()):
                state['dice_by_player'][members[0]] = [index + 2] * 5
            room = self.replace_state(room,state,'human-1')
            for members in state['teams'].values():
                view = framework.project_room_for_viewer(room,members[1])
                self.assertEqual(view['private_state']['dice'],state['dice_by_player'][members[0]])
            # Two passes through the teams alternate speakers within each team.
            first_quantity = 3
            expected = [pid for pair in zip(*state['teams'].values()) for pid in pair]
            for i, pid in enumerate(expected):
                self.assertEqual(room['current_player_id'],pid)
                room = self.act(room, {'action':'bid','quantity':first_quantity,'face':i+1})
            room = self.act(room, {'action':'challenge'})
            result = room['board_state']['last_round_result']
            team = result['loser_team_id']
            self.assertEqual(room['board_state']['team_scores'][team],-1)
            self.assertEqual(sum(room['board_state']['team_scores'].values()),-1)
            self.assertEqual(result['actual_count'], 0)  # last face6/4; cups hold only2/3/4
            self.assertEqual(len(result['revealed_dice_by_player']),2)
            self.assertEqual(result['score_delta'],-1)
            for pid in room['board_state']['teams'][team]:
                self.assertEqual(room['board_state']['scores'][pid],-1)
            room = self.act(room, {'action':'acknowledge_round'})
            self.assertEqual(len(room['board_state']['dice_by_player']),2)
            self.assertTrue(all(len(dice) == 5 for dice in room['board_state']['dice_by_player'].values()))
            room = self.act(room, {'action':'end_session'},'human-1')
            self.assertEqual(room['result']['team_scores'][team],-1)
            self.assertFalse(any(p.get('settlement_delta') for p in room['participants']))

    def test_pairs_invitation_validation_start_reload_and_departure(self):
        for count in (2,3,5):
            with self.assertRaises(framework.DuelError):
                invites.create_invite('liars_dice','ai','ai-1',target_player_count=count,
                                      game_options={'mode':'party','team_mode':'pairs'})
        room = invites.create_invite('liars_dice','ai','ai-1',target_player_count=4,
                                     game_options={'mode':'party','team_mode':'pairs'})
        code = room['invite_code']
        for i in range(2,5):
            invites.join_invite(code,'ai',f'ai-{i}')
        self.assertEqual(invites.preview_invite(code)['game_options']['team_mode'],'teams')
        room = invites.start_invite(room['room_id'],'ai','ai-1')
        room = framework.get_room(room['room_id'])
        self.assertEqual(len(room['board_state']['teams']),2)
        room = self.act(room, {'action':'bid','quantity':10,'face':6})
        room = self.act(room, {'action':'challenge'})
        before = deepcopy(room['board_state']['team_scores'])
        # A departure cannot leave an invalid one-person team or orphaned action cursor.
        room = framework.leave_room(room['room_id'],'ai','ai-2')
        self.assertEqual(room['status'],'finished')
        self.assertIsNone(room['current_player_id'])
        self.assertEqual(room['result']['team_scores'],before)
        self.assertEqual(room['result']['reason'],'participant_departed')

    def test_older_party_save_scores_derived_once_and_no_wallet_debit(self):
        from app import database
        room = self.create(2)
        state = room['board_state']
        state.pop('scores')
        state.pop('score_half_units')
        state.pop('scoring_rules_version')
        state['records']['human-1']['losses'] = 3
        room = self.replace_state(room,state,'human-1')
        self.assertEqual(framework.project_room_for_viewer(room,'human-1')['board_state']['scores']['human-1'],-3)
        # Snapshot every wallet/ledger row: game score mutations must not write them.
        with database.connect() as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%wallet%' OR name LIKE '%ledger%')")]
            before = {name:list(conn.execute('SELECT * FROM ' + name)) for name in tables}
        room = self.act(room, {'action':'bid','quantity':10,'face':6})
        room = self.act(room, {'action':'challenge'})
        self.assertEqual(room['board_state']['scores']['human-1'],-4)
        room = self.act(room, {'action':'end_session'},'human-1')
        with database.connect() as conn:
            after = {name:list(conn.execute('SELECT * FROM ' + name)) for name in tables}
        # Neither local score deductions nor closing the technical draw may
        # create platform awards, wallets, or any ledger transaction.
        self.assertEqual(before, after)

    def test_party_create_finish_and_rematch_do_not_award_platform_chips(self):
        from app import database
        def wallets():
            with database.connect() as conn:
                return {table:[dict(row) for row in conn.execute('SELECT * FROM '+table)]
                        for table in ('chip_wallets','chip_ledger','chip_settlement_batches')}
        before=wallets()
        room=self.create(2)
        room=self.act(room,{'action':'end_session'},'human-1')
        rematch=framework.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',
            game_options={'mode':'party'},rematch_of_room_id=room['room_id'])
        rematch=self.act(rematch,{'action':'end_session'},'human-1')
        self.assertEqual(rematch['status'],'finished')
        self.assertEqual(wallets(),before)
        with database.connect() as conn:
            rows=conn.execute('SELECT normal_outcome FROM achievement_matches WHERE room_id IN (?,?)',
                              (room['room_id'],rematch['room_id'])).fetchall()
            self.assertEqual([row[0] for row in rows],[0,0])



class PartyMcpTests(support.LiarsDiceMcpTests):
    # Keep the existing HTTP compatibility tests alongside new mode requests.
    async def test_mcp_new_and_invite_selection_and_end(self):
        response = await self.client.post('/mcp/play',json={
            'action':'new','game_type':'liars_dice','player_id':'ai-1','opponent_id':'human-1',
            'move':{'mode':'party','literal_dice':True}})
        self.assertEqual(response.status_code,200,response.text)
        room = framework.list_ai_rooms('ai-1')[0]
        room = framework.get_room(room['room_id'])
        self.assertEqual(room['board_state']['mode'],'party')
        self.assertTrue(room['board_state']['literal_dice'])
        response = await self.client.post('/mcp/play',json={'action':'move','player_id':'ai-1',
            'room_id':room['room_id'],'revision':room['revision'],'move':{'action':'end_session'}})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(framework.get_room(room['room_id'])['status'],'finished')
        response = await self.client.post('/mcp/play',json={'action':'invite','game_type':'liars_dice',
            'player_id':'ai-1','target_player_count':2,'move':{'mode':'party'}})
        self.assertEqual(response.status_code,200,response.text)
        response = await self.client.post('/mcp/play',json={'action':'new','game_type':'liars_dice',
            'player_id':'ai-2','opponent_id':'human-2'})
        self.assertEqual(response.status_code,200,response.text)
        old = framework.list_ai_rooms('ai-2')[0]
        self.assertEqual(framework.get_room(old['room_id'])['board_state']['mode'],'classic')

    async def test_web_pairs_create_score_full_state_and_mcp_rematch(self):
        machines = [{'id':f'ai-{i}','name':f'小机{i}'} for i in range(1,4)]
        headers = {'X-Duel-Human-Player':'human-1',
                   'X-Duel-Bound-Ais':base64.b64encode(json.dumps(machines).encode()).decode()}
        with patch('app.games.liars_dice.random.SystemRandom.shuffle', lambda rng, values: None):
            response = await self.client.post('/api/rooms',headers=headers,json={
                'player_id':'human-1','game_type':'liars_dice','mode':'human_first',
                'ai_players':[m['id'] for m in machines], 'target_player_count':4,
                'game_options':{'mode':'party','team_mode':'pairs','literal_dice':True}})
        self.assertEqual(response.status_code,200,response.text)
        room = response.json()['room']
        self.assertEqual(room['board_state']['max_bid_quantity'],20)
        self.assertTrue(room['board_state']['literal_dice'])
        self.assertEqual(room['private_state']['team_id'],'red')
        response = await self.client.post(f"/api/rooms/{room['room_id']}/move",headers=headers,json={
            'player_id':'human-1','revision':room['revision'],'move':{'action':'bid','quantity':10,'face':6}})
        self.assertEqual(response.status_code,200,response.text)
        room = response.json()['room']
        self.assertEqual(room['current_player_id'],'ai-1')
        # Only the next seat acts; that seat may challenge its teammate.
        denied = await self.client.post('/mcp/play',json={'action':'move','player_id':'ai-2',
            'room_id':room['room_id'],'revision':room['revision'],'move':{'action':'challenge'}})
        self.assertEqual(denied.status_code,409)
        response = await self.client.post('/mcp/play',json={'action':'move','player_id':'ai-1',
            'room_id':room['room_id'],'revision':room['revision'],'move':{'action':'challenge'}})
        self.assertEqual(response.status_code,200,response.text)
        room = framework.get_room(room['room_id'])
        self.assertEqual(sum(room['board_state']['team_scores'].values()),0)
        response = await self.client.post('/mcp/play',json={'action':'state','player_id':'ai-1',
            'room_id':room['room_id'],'full_state':True})
        self.assertEqual(response.status_code,200,response.text)
        snapshot = response.json()['snapshot']
        self.assertEqual(snapshot['board_state']['round_results'][0]['score_delta'],0)
        self.assertNotIn('dice_by_player',snapshot['board_state'])
        normal = framework.project_mcp_snapshot_for_viewer(room,'ai-1')
        self.assertNotIn('round_results',normal['board_state'])
        self.assertIn('scores',normal['board_state'])
        self.assertEqual(room['current_player_id'],'human-1')
        response = await self.client.post(f"/api/rooms/{room['room_id']}/move",headers=headers,json={
            'player_id':'human-1','revision':room['revision'],'move':{'action':'acknowledge_round'}})
        self.assertEqual(response.status_code,200,response.text)
        room = response.json()['room']
        self.assertEqual(room['board_state']['flow']['round_number'],2)
        response = await self.client.post('/mcp/play',json={'action':'move','player_id':'ai-1',
            'room_id':room['room_id'],'revision':room['revision'],'move':{'action':'end_session'}})
        self.assertEqual(response.status_code,200,response.text)
        response = await self.client.post('/mcp/play',json={'action':'rematch','player_id':'ai-1',
            'opponent_id':'human-1','room_id':room['room_id']})
        self.assertEqual(response.status_code,200,response.text)
        new = framework.get_room(framework.list_ai_rooms('ai-1')[0]['room_id'])
        self.assertEqual(new['game_options'],LiarsDice.normalize_options({'mode':'party','literal_dice':True,'team_mode':'teams','random_teams':True}))
        self.assertTrue(all(value == 0 for value in new['board_state']['scores'].values()))

    async def test_web_party_ack_obeys_current_seat_and_revision(self):
        room = framework.create_room('liars_dice', 'ai_first', 'ai', 'ai-1',
                                     opponent_id='human-1', game_options={'mode':'party'})
        # Pick a face that cannot fill all ten dice, so the AI bidder must lose.
        face = room['board_state']['dice_by_player']['ai-1'][0] % 6 + 1
        room = framework.play_move(room['room_id'], 'ai', 'ai-1',
                                   {'action':'bid','quantity':10,'face':face}, expected_revision=room['revision'])
        headers = {'X-Duel-Human-Player':'human-1'}
        path = f"/api/rooms/{room['room_id']}/move"
        response = await self.client.post(path, headers=headers, json={
            'player_id':'human-1', 'revision':room['revision'], 'move':{'action':'challenge'}})
        self.assertEqual(response.status_code, 200, response.text)
        room = response.json()['room']
        self.assertEqual(room['current_player_id'], 'human-1')
        revision = room['revision']
        response = await self.client.post(path, headers=headers, json={
            'player_id':'human-1', 'revision':revision, 'move':{'action':'acknowledge_round'}})
        self.assertEqual(response.status_code, 200, response.text)
        with self.assertRaises(framework.DuelError):
            framework.acknowledge_liars_dice_round(room['room_id'], 'human-1', revision)
        response = await self.client.post('/mcp/play', json={'action':'move','player_id':'ai-1',
            'room_id':room['room_id'],'revision':revision,'move':{'action':'acknowledge_round'}})
        self.assertEqual(response.status_code, 409, response.text)
        updated = framework.get_room(room['room_id'])
        self.assertEqual(updated['current_player_id'], 'ai-1')
        self.assertEqual(updated['board_state']['flow']['round_number'], 2)
        self.assertEqual(updated['board_state']['scores'], {'ai-1':-1, 'human-1':1})

    async def test_mcp_pairs_invite_start_options_and_legacy_default(self):
        response = await self.client.post('/mcp/play',json={'action':'invite','game_type':'liars_dice',
            'player_id':'ai-1','target_player_count':4,'move':{'mode':'party','team_mode':'pairs'}})
        self.assertEqual(response.status_code,200,response.text)
        room = framework.get_room(framework.list_ai_rooms('ai-1')[0]['room_id'])
        for i in range(2,5):
            reply = await self.client.post('/mcp/play',json={'action':'join','player_id':f'ai-{i}',
                                                          'invite_code':room['invite_code']})
            self.assertEqual(reply.status_code,200,reply.text)
        reply = await self.client.post('/mcp/play',json={'action':'start','player_id':'ai-1','room_id':room['room_id']})
        self.assertEqual(reply.status_code,200,reply.text)
        started = framework.get_room(room['room_id'])
        self.assertEqual(started['board_state']['team_mode'],'teams')
        self.assertEqual(len(started['board_state']['dice_by_player']),4)


if __name__ == '__main__':
    unittest.main()
