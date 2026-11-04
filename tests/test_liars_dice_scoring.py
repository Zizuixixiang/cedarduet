"""Versioned win rewards: authoritative net ledgers and durable client results."""
import json
import os
import random
import subprocess
import sys
import unittest
from copy import deepcopy
from unittest.mock import patch

from app import database, framework, invites, liars_dice_mcp as mcp
from app.games.liars_dice import LiarsDice
from app.games import liars_dice_party as party
from tests import test_multiplayer_games as support


class ScoringLedgerTests(unittest.TestCase):
    def make(self, count=4, teams=True, **options):
        self.game = LiarsDice(random.Random(82))
        self.people = support.participants(count)
        ids = [p['player_id'] for p in self.people]
        state = self.game.configure(self.game.initialize(self.people),
            {'mode':'party', 'rush_open':False, **({'team_mode':'teams'} if teams else {}), **options},
            teams={'red':ids[:count//2], 'blue':ids[count//2:]} if teams else None)
        self.assertEqual(set(state['score_half_units'].values()), {0})
        return state, ids

    def test_unit_matrix_same_cross_team_sizes_companion_and_cap(self):
        for count, teams in ((2,False), (3,False), (4,True), (6,True)):
            for same_team in ((False,True) if teams else (False,)):
                for hit_count in range(3):
                    for picked_seat in (None, *range(1,count+1)):
                        with self.subTest(count=count, same=same_team, hits=hit_count, picked=picked_seat):
                            state, ids = self.make(count, teams, companion_penalty=picked_seat is not None)
                            loser, winner = ids[0], ids[1 if same_team or not teams else count//2]
                            unit = lambda pid: self.game.team_for(state,pid) or pid
                            key_l, key_w = unit(loser), unit(winner)
                            expected = {key:0 for key in state['score_half_units']}
                            expected[key_w] += 2
                            multiplier = 2 ** hit_count
                            picked = ids[picked_seat-1] if picked_seat else None
                            self_hit = picked and unit(picked) == key_l
                            expected[key_l] -= 2 * min(multiplier * (2 if self_hit else 1),4)
                            if picked and not self_hit:
                                expected[unit(picked)] -= 2 * multiplier
                            with patch.object(self.game,'_roll',return_value=[picked_seat]):
                                result = party.settle(self.game,state,loser,winner,
                                    hits=('cup_loser','under_half')[:hit_count])
                            self.assertEqual(state['score_half_units'],expected)
                            self.assertEqual(result['score_delta'],expected[key_l]/2)
                            self.assertEqual(result['score_after'],expected[key_l]/2)
                            self.assertEqual(result['rewards'][0]['score_delta'],1)
                            self.assertEqual(result['rewards'][0]['score_after'],expected[key_w]/2)
                            changes = result['score_changes']
                            self.assertEqual(len(changes),len({row['unit_id'] for row in changes}))
                            for row in changes:
                                self.assertEqual(row['delta_half_units'],expected[row['unit_id']])
                                self.assertEqual(row['score_after'],expected[row['unit_id']]/2)
                            for row in result['penalties']:
                                self.assertEqual(row['score_after'],expected[unit(row['player_id'])]/2)
                            self.assertEqual(state['scores'],{pid:expected[unit(pid)]/2 for pid in ids})
                            self.assertEqual(state['records'][winner]['wins'],1)
                            self.assertEqual(state['records'][loser]['losses'],1)
                            if self_hit and multiplier == 4:
                                self.assertEqual(result['penalties'][0]['raw_multiplier'],8)
                                self.assertTrue(result['penalties'][0]['capped'])

    def test_authoritative_dark_reversal_rewards_final_winner(self):
        state, ids = self.make(2,False,dark_rule=True,literal_dice=True)
        state['dice_by_player'] = {pid:[1]*5 for pid in ids}
        self.game.apply_action(state,{'action':'bid','quantity':3,'face':2},self.people[0])
        applied = self.game.apply_action(state,{'action':'challenge'},self.people[1])
        self.game.progress_after_action(state,{'action':'challenge'},self.people[1],self.people,applied)
        result = state['last_round_result']
        self.assertTrue(result['dark_reversed'])
        self.assertEqual(result['winner_player_id'],ids[1])
        self.assertEqual(state['scores'],{ids[0]:-1,ids[1]:1})
        self.assertEqual(state['round_results'][0]['rewards'],result['rewards'])
        self.assertIn('胜方+1',result['result_summary'])

    def test_authoritative_same_team_normal_double_quadruple(self):
        for count in (4,6):
            for multiplier, options in ((1,{}), (2,{'cup_penalty':True}),
                                        (4,{'cup_penalty':True,'extreme_penalty':True})):
                state, ids = self.make(count,True,**options)
                state['dice_by_player'] = {pid:[2]*5 for pid in ids}
                self.game.apply_action(state,{'action':'bid','quantity':10,'face':6},self.people[0])
                self.game.apply_action(state,{'action':'challenge'},self.people[1])
                result = state['last_round_result']
                self.assertEqual(result['penalties'][0]['multiplier'],multiplier)
                self.assertEqual(state['team_scores'],{'red':1-multiplier,'blue':0})
                self.assertEqual(len(result['score_changes']),1)
                self.assertEqual(state['round_results'][0]['score_delta'],1-multiplier)

    def test_room_guide_matches_persisted_version(self):
        state, _ = self.make()
        self.assertIn('胜方+1',self.game.room_rules(state))
        self.assertIn('同队净变动为0/-1/-3',self.game.room_rules(state))
        state.pop('scoring_rules_version')
        self.assertNotIn('胜方+1',self.game.room_rules(state))
        self.assertIn('旧房胜方不加分',self.game.room_rules(state))
        with self.assertRaises(ValueError):
            self.game.normalize_options({'mode':'party','scoring_rules_version':1})

    def test_surrender_never_rewards_or_runs_companion(self):
        for teams in (False,True):
            state, ids = self.make(4,teams,allow_surrender=True,companion_penalty=True,
                                   cup_penalty=True,extreme_penalty=True,dark_rule=True)
            self.game.apply_action(state,{'action':'bid','quantity':6,'face':2},self.people[0])
            with patch.object(self.game,'_roll',side_effect=AssertionError('no companion on surrender')):
                self.game.apply_action(state,{'action':'surrender'},self.people[1])
            result = state['last_round_result']
            self.assertEqual(result['rewards'],[])
            self.assertEqual(result['score_delta'],-.5)
            self.assertEqual(sum(state['score_half_units'].values()),-1)


class ScoringPersistenceTests(support.MultiplayerGameTestCase):
    def create(self, **options):
        with patch('app.games.liars_dice.random.SystemRandom.shuffle'):
            return framework.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',
                game_options={'mode':'party','rush_open':False,**options})

    def act(self, room, move):
        pid = room['current_player_id']
        return framework.play_move(room['room_id'],self.role_for(pid),pid,move,expected_revision=room['revision'])

    def test_versions_reconnect_mcp_web_archive_and_process_restart(self):
        for version in (None,1,2):
            with self.subTest(version=version):
                room = self.create()
                state = room['board_state']
                if version is None:
                    state.pop('scoring_rules_version')
                else:
                    state['scoring_rules_version'] = version
                state['dice_by_player'] = {'human-1':[2]*5,'ai-1':[3]*5}
                room = self.replace_state(room,state,'human-1')
                boot = mcp.response(room['room_id'],'ai-1')
                self.assertEqual(boot['room']['board_state']['scoring_rules_version'],version or 1)
                room = self.act(room,{'action':'bid','quantity':10,'face':6})
                revision = room['revision']
                room = self.act(room,{'action':'challenge'})
                expected = {'human-1':-1,'ai-1':1 if version == 2 else 0}
                result = room['board_state']['last_round_result']
                self.assertEqual(room['board_state']['scores'],expected)
                delta = mcp.response(room['room_id'],'ai-1')
                self.assertEqual(delta['delta']['scores'],expected)
                event = next(e['round_result'] for e in delta['events'] if isinstance(e,dict) and 'round_result' in e)
                self.assertEqual(event['rewards'],result['rewards'])
                self.assertEqual(event['score_changes'],result['score_changes'])
                web = framework.project_room_for_viewer(room,'human-1')
                self.assertEqual(web['board_state']['scores'],expected)
                self.assertEqual({p['player_id']:p['game_metadata']['score'] for p in web['participants']},expected)
                self.assertEqual(web['board_state']['round_results'][0]['rewards'],result['rewards'])
                full = mcp.response(room['room_id'],'ai-1',full=True)
                self.assertEqual(full['snapshot']['board_state']['scores'],expected)
                with self.assertRaises(framework.DuelError):
                    framework.play_move(room['room_id'],'ai','ai-1',{'action':'challenge'},expected_revision=revision)
                # A fresh Python process reads the actual persisted state without reconfiguration.
                code = 'import json,sys; from app.framework import get_room; print(json.dumps(get_room(sys.argv[1])["board_state"]))'
                saved = subprocess.check_output([sys.executable,'-c',code,room['room_id']],
                    env={**os.environ,'DUEL_DB_PATH':str(database.DB_PATH)},text=True)
                self.assertEqual(json.loads(saved),room['board_state'])
                room = self.act(room,{'action':'end_session'})
                self.assertEqual(room['result']['scores'],expected)
                original = deepcopy(room['result'])
                with database.write_transaction() as conn:
                    conn.execute("UPDATE rooms SET status='archived' WHERE room_id=?",(room['room_id'],))
                self.assertEqual(framework.get_room(room['room_id'])['result'],original)
                self.assertEqual(mcp.response(room['room_id'],'ai-1',full=True)['snapshot']['result'],original)

    def test_failed_companion_roll_rolls_back_entire_settlement(self):
        room = self.create(companion_penalty=True)
        room = self.act(room,{'action':'bid','quantity':3,'face':2})
        before = deepcopy(room['board_state'])
        with patch.object(framework.get_game('liars_dice'),'_roll',return_value=[6]):
            with self.assertRaises(framework.DuelError):
                self.act(room,{'action':'challenge'})
        reloaded = framework.get_room(room['room_id'])
        self.assertEqual(reloaded['revision'],room['revision'])
        self.assertEqual(reloaded['board_state'],before)

    def test_waiting_invite_pins_version_through_start(self):
        for legacy in (False,True):
            room = invites.create_invite('liars_dice','human','human-1',target_player_count=2,
                game_options={'mode':'party','rush_open':False})
            self.assertEqual(room['board_state']['scoring_rules_version'],2)
            if legacy:
                state = room['board_state']; state.pop('scoring_rules_version')
                with database.write_transaction() as conn:
                    conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?',(json.dumps(state),room['room_id']))
            room = invites.join_invite(room['invite_code'],'ai','ai-1')
            room = invites.start_invite(room['room_id'],'human','human-1')
            self.assertEqual(room['board_state']['scoring_rules_version'],1 if legacy else 2)
