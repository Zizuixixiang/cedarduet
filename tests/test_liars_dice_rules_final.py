"""Rule boundaries, half-point ledger, privacy and incremental transport."""
import json
import random
import unittest
from copy import deepcopy
from unittest.mock import patch

from app import database, framework, main
from app.games.liars_dice import LiarsDice
from app.games import liars_dice_party as rules
from tests import test_multiplayer_games as support
from tests.test_liars_dice_teams import OPTIONS, expire, seat


class PartyFinalRulesTests(unittest.TestCase):
    def make(self, count=2, **opts):
        self.game = LiarsDice(random.Random(82))
        self.people = support.participants(count)
        self.state = self.game.configure(self.game.initialize(self.people), {'mode':'party', **opts})
        return self.state

    def challenge(self, quantity=10, face=6, exact=False):
        move = dict(action='bid', quantity=quantity, face=face, exact=exact)
        self.game.validate_action(self.state,move,self.people[0])
        self.game.apply_action(self.state,move,self.people[0])
        self.game.validate_action(self.state,{'action':'challenge'},self.people[1])
        result = self.game.apply_action(self.state,{'action':'challenge'},self.people[1])
        self.game.progress_after_action(self.state,{'action':'challenge'},self.people[1],self.people,result)
        return result.state['last_round_result']

    def test_literal_preserves_wild_and_exact_with_cup_bonuses(self):
        for literal, exact, expected in [(False,False,13),(False,True,11),(True,False,10),(True,True,9)]:
            state=self.make(literal_dice=literal)
            state['dice_by_player']={'human-1':[1,2,2,2,2],'ai-1':[2]*5}
            result=self.challenge(4,2,exact)
            self.assertEqual(result['actual_count'],expected)
        self.assertEqual(rules.count_cup([1]*5,2,False,True),(5,0))
        self.assertEqual(rules.count_cup([1]*5,1,True,True),(7,2))

    def test_optional_flags_validate_and_five_person_opening(self):
        for n in (7,8):
            state=self.make(5,opening_five=n)
            self.assertTrue(self.game._valid_bid(state,n,2))
            self.assertFalse(self.game._valid_bid(state,n-1,2))
            self.assertTrue(self.game._valid_bid(state,5,2,True))
        for options in ({'mode':'party','surrender_no_reroll':True}, {'mode':'party','cup_penalty':1},
                        {'mode':'classic','dark_rule':True}, {'mode':'party','opening_five':True}):
            with self.assertRaises(ValueError): self.game.normalize_options(options)
        self.assertTrue(all(not self.make().get(k) for k in rules.BOOL_OPTIONS))

    def test_cup_extreme_companion_self_raw_eight_capped_four(self):
        state=self.make(cup_penalty=True,extreme_penalty=True,companion_penalty=True)
        state['dice_by_player']={'human-1':[2]*5,'ai-1':[3]*5}
        with patch.object(self.game,'_roll',return_value=[1]): result=self.challenge()
        p=result['penalties'][0]
        self.assertEqual(p['hits'],['cup_loser','under_half','companion_self'])
        self.assertEqual((p['raw_multiplier'],p['multiplier'],p['capped'],p['score_delta']),(8,4,True,-4))
        self.assertEqual(state['score_half_units'],{'human-1':-8,'ai-1':0})
        self.assertEqual(state['round_results'][0]['penalties'],result['penalties'])
        self.assertIn('原始8倍→4倍',result['result_summary'])

    def test_companion_other_same_cap_and_safe_mapping(self):
        state=self.make(3,cup_penalty=True,extreme_penalty=True,companion_penalty=True)
        state['dice_by_player']={p['player_id']:[2]*5 for p in self.people}
        with patch.object(self.game,'_roll',side_effect=[[6],[3]]): result=self.challenge()
        self.assertEqual(result['companion']['rolls'],[6,3])
        self.assertEqual(result['companion']['player_id'],'ai-2')
        self.assertEqual([p['score_delta'] for p in result['penalties']],[-4,-4])
        self.assertEqual(state['scores']['ai-1'],0)

    def test_strict_extreme_boundaries(self):
        for actual, quantity, expected in [(2,4,1),(1,4,2),(8,4,1),(9,4,2)]:
            state=self.make(literal_dice=True,extreme_penalty=True)
            dice=[6]*actual+[3]*(10-actual)
            state['dice_by_player']={'human-1':dice[:5],'ai-1':dice[5:]}
            result=self.challenge(quantity,6)
            self.assertEqual(result['penalties'][0]['multiplier'],expected)

    def test_dark_uses_natural_faces_even_with_wild_ones(self):
        state=self.make(literal_dice=True,dark_rule=True)
        state['dice_by_player']={'human-1':[1]*5,'ai-1':[1]*5}
        result=self.challenge(3,2)
        self.assertEqual(result['actual_count'],10)
        self.assertTrue(result['bid_holds'])
        self.assertTrue(result['dark_reversed'])
        self.assertEqual(result['loser_player_id'],'human-1')

    def test_surrender_half_units_retention_and_no_other_penalties(self):
        for keep in (False,True):
            state=self.make(allow_surrender=True,surrender_no_reroll=keep,
                            cup_penalty=True,extreme_penalty=True,dark_rule=True,companion_penalty=True)
            for _ in range(9):
                before=deepcopy(state['dice_by_player'])
                self.game.apply_action(state,{'action':'bid','quantity':3,'face':2},self.people[0])
                self.game.validate_action(state,{'action':'surrender'},self.people[1])
                result=self.game.apply_action(state,{'action':'surrender'},self.people[1])
                outcome=result.state['last_round_result']
                self.assertEqual(outcome['score_delta'],-.5)
                self.assertIsNone(outcome['companion'])
                self.assertEqual(outcome['revealed_dice_by_player'],{} if keep else before)
                with patch.object(self.game,'_roll',return_value=[6]*5) as roll:
                    self.game.acknowledge_round(state)
                    self.assertEqual(roll.call_count,0 if keep else 2)
                if keep: self.assertEqual(before,state['dice_by_player'])
            self.assertEqual(state['score_half_units']['ai-1'],-9)
            self.assertEqual(state['scores']['ai-1'],-4.5)

    def test_reroll_once_before_opening_and_only_own_cup(self):
        state=self.make()
        state['dice_by_player']['human-1']=[1,2,3,4,5]
        other=deepcopy(state['dice_by_player']['ai-1'])
        self.game.validate_action(state,{'action':'reroll'},self.people[0])
        with patch.object(self.game,'_roll',return_value=[2,3,4,5,6]):
            result=self.game.apply_action(state,{'action':'reroll'},self.people[0])
        self.assertTrue(result.retain_turn)
        self.assertEqual(state['dice_by_player']['ai-1'],other)
        with self.assertRaises(ValueError): self.game.validate_action(state,{'action':'reroll'},self.people[0])
        with self.assertRaises(ValueError): self.game.validate_action(state,{'action':'reroll','player_id':'ai-1'},self.people[0])
        state=self.make(literal_dice=True)
        state['dice_by_player']['human-1']=[1,2,3,4,5]
        with self.assertRaises(ValueError): self.game.validate_action(state,{'action':'reroll'},self.people[0])

    def test_shared_team_charged_once_and_shared_reroll_limit(self):
        with patch('app.games.liars_dice.random.SystemRandom.shuffle'):
            state=self.make(6,team_mode='teams',cup_penalty=True,extreme_penalty=True,companion_penalty=True)
        self.people[1]=self.people[3]  # blue challenger, not red teammate
        state['dice_by_player']={members[0]:[2]*5 for members in state['teams'].values()}
        with patch.object(self.game,'_roll',return_value=[2]): result=self.challenge()
        self.assertTrue(result['companion']['same_unit'])
        self.assertEqual(state['score_half_units'],{'red':-8,'blue':0})
        self.assertEqual(len(result['penalties']),1)
        self.assertTrue(all(state['scores'][pid]==-4 for pid in state['teams']['red']))
        self.game.acknowledge_round(state)
        state['dice_by_player']['human-1']=[1,2,3,4,5]
        self.game.apply_action(state,{'action':'reroll'},self.people[0])
        self.assertFalse(rules.can_reroll(state,self.game.cup_owner(state,'ai-1')))

    def test_old_party_keeps_no_bonus_and_classic_unchanged(self):
        state=self.make()
        state.pop('party_rules_version')
        state['dice_by_player']={'human-1':[2]*5,'ai-1':[3]*5}
        self.assertEqual(self.challenge(3,2)['actual_count'],5)
        self.assertFalse(rules.can_reroll(state,'human-1'))


class PartyFinalTransportTests(support.LiarsDiceMcpTests):
    def create(self, **opts):
        return framework.create_room('liars_dice','ai_first','ai','ai-1',opponent_id='human-1',
                                     game_options={'mode':'party',**opts},require_confirmations=False)

    async def call(self,room,**body):
        response=await self.client.post('/mcp/play',json={'player_id':'ai-1','room_id':room['room_id'],**body})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    async def test_delta_roundtrip_full_resync_and_wait(self):
        room=self.create()
        boot=await self.call(room,action='state')
        self.assertTrue(boot['bootstrap'])
        self.assertEqual(boot['room']['private_state']['dice'],room['board_state']['dice_by_player']['ai-1'])
        self.assertNotIn('dice_by_player',json.dumps(boot))
        self.assertNotIn('legal_actions',boot['room']['private_state'])
        self.assertEqual(await self.call(room,action='state'),{'r':room['revision']})
        bid=await self.call(room,action='move',revision=room['revision'],move={'action':'bid','quantity':3,'face':2})
        self.assertEqual(bid['events'],[['ai-1',{'action':'bid','quantity':3,'face':2}]])
        self.assertNotIn('private',bid)
        room=framework.get_room(room['room_id'])
        room=framework.play_move(room['room_id'],'human','human-1',{'action':'challenge'},expected_revision=room['revision'])
        result=await self.call(room,action='state')
        self.assertEqual(len(result['events']),1)
        self.assertEqual(result['events'][0]['round_result']['penalties'],room['board_state']['last_round_result']['penalties'])
        full=await self.call(room,action='state',full_state=True)
        self.assertEqual(len(full['snapshot']['board_state']['round_results']),1)
        self.assertNotIn('dice_by_player',full['snapshot']['board_state'])
        room=framework.get_room(room['room_id'])
        pid=room['current_player_id']
        room=framework.play_move(room['room_id'],'ai' if pid=='ai-1' else 'human',pid,{'action':'acknowledge_round'},expected_revision=room['revision'])
        delta=await self.call(room,action='state')
        self.assertEqual(delta['delta']['round'],2)
        self.assertNotIn('round_results',json.dumps(delta))
        self.assertNotIn('revealed_dice',json.dumps(delta))
        self.assertEqual(delta['private']['dice'],room['board_state']['dice_by_player']['ai-1'])
        again=await self.call(room,action='state')
        self.assertTrue(set(again)<= {'r','wait'})
        if room['current_player_id']=='ai-1':
            await self.call(room,action='move',revision=room['revision'],move={'action':'bid','quantity':3,'face':2})
        with patch.object(main,'MCP_WAIT_SECONDS',0):
            waiting=await self.call(room,action='state',wait=True)
        self.assertNotIn('room',waiting)
        self.assertNotIn('private',waiting)

    async def test_no_repeated_charge_or_private_surrender_reveal(self):
        room=self.create(allow_surrender=True,surrender_no_reroll=True,companion_penalty=True)
        room=framework.play_move(room['room_id'],'ai','ai-1',{'action':'bid','quantity':3,'face':2},expected_revision=room['revision'])
        await self.call(room,action='state')
        old=room['revision']
        room=framework.play_move(room['room_id'],'human','human-1',{'action':'surrender'},expected_revision=old)
        with self.assertRaises(framework.DuelError):
            framework.play_move(room['room_id'],'human','human-1',{'action':'surrender'},expected_revision=old)
        reply=await self.call(room,action='state')
        self.assertEqual(reply['events'][0]['round_result']['revealed_dice_by_player'],{})
        self.assertEqual(framework.get_room(room['room_id'])['board_state']['scores']['human-1'],-.5)
        full=await self.call(room,action='state',full_state=True)
        self.assertEqual(full['snapshot']['board_state']['round_results'][0]['score_delta'],-.5)

    async def test_team_mcp_private_cup_and_unread_events_survive_full_state(self):
        with patch('app.games.liars_dice.random.SystemRandom.shuffle'):
            room=framework.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',
                ordered_participants=support.participants(4),game_options={'mode':'party','team_mode':'teams'})
        state=room['board_state']
        state['dice_by_player']={'human-1':[2,3,4,5,6],'ai-2':[6,6,6,6,6]}
        with database.write_transaction() as conn:
            conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?',(json.dumps(state),room['room_id']))
        boot=await self.call(room,action='state')
        self.assertEqual(boot['room']['private_state']['dice'],[2,3,4,5,6])
        self.assertNotIn('[6, 6, 6, 6, 6]',json.dumps(boot))
        room=framework.play_move(room['room_id'],'human','human-1',{'action':'bid','quantity':3,'face':2},expected_revision=room['revision'])
        room=framework.play_move(room['room_id'],'ai','ai-2',{'action':'bid','quantity':4,'face':2},expected_revision=room['revision'])
        delta=await self.call(room,action='state')
        self.assertEqual([e[1]['quantity'] for e in delta['events']],[3,4])
        self.assertEqual(delta.get('private'),{'can_reroll':False})
        framework.post_message(room['room_id'],'human','human-1','仅交付一次的聊天')
        full=await self.call(room,action='state',full_state=True)
        self.assertEqual([e['message'] for e in full['events']],['仅交付一次的聊天'])
        self.assertEqual(full['snapshot']['private_state']['dice'],[2,3,4,5,6])
        self.assertNotIn('[6, 6, 6, 6, 6]',json.dumps(full))
        again=await self.call(room,action='state')
        self.assertNotIn('events',again)

    async def test_mcp_catalog_stays_34(self):
        response=await self.client.post('/mcp/play',json={'action':'catalog','player_id':'ai-1','game_type':'liars_dice'})
        self.assertEqual(response.status_code,200)
        # The actual platform's get_guide route is tested in tests_toy. Catalog
        # remains compatible and doesn't invent a 35th game.
        self.assertEqual(len(response.json()['games']),34)
