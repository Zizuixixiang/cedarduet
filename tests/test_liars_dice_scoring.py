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

    def resolve(self, state, *, challenger=1, quantity=10, face=6, exact=False):
        self.people[0]['display_name'] = '唐焰'
        self.people[1]['display_name'] = '林舟'
        bid = dict(action='bid', quantity=quantity, face=face, exact=exact)
        self.game.validate_action(state, bid, self.people[0])
        self.game.apply_action(state, bid, self.people[0])
        move = {'action':'challenge'}
        applied = self.game.apply_action(state, move, self.people[challenger])
        self.game.progress_after_action(state, move, self.people[challenger], self.people, applied)
        result = state['last_round_result']
        self.assertEqual(applied.note, result['summary'])
        self.assertEqual(applied.note, f"第 1 轮：{result['result_summary']}")
        for field in ('penalties', 'rewards', 'score_changes', 'scoring_rules_version',
                      'companion', 'cup_bonuses', 'dark_reversed'):
            self.assertEqual(applied.public_event['round_result'][field], result[field])
        for unwanted in ('所在队', '累计', '原始', '倍→', '现为', 'score_', '实际'):
            self.assertNotIn(unwanted, applied.note)
        return result

    def test_individual_summary_final_winner_and_count_including_wild_bonus(self):
        for exact, quantity, count, verdict, scores in (
            (False, 6, 6, '叫点成功', '唐焰 +1分，林舟 -1分'),
            (True, 5, 4, '质疑成功', '林舟 +1分，唐焰 -1分'),
        ):
            with self.subTest(exact=exact):
                state, ids = self.make(2, False)
                state['dice_by_player'] = {ids[0]:[1,3,3,3,3], ids[1]:[2]*5}
                result = self.resolve(state, quantity=quantity, face=3, exact=exact)
                self.assertEqual(result['result_summary'],
                    f"唐焰叫{quantity}个3点{'（斋）' if exact else ''}，开骰计入{count}个，{verdict}。{scores}。")

    def test_team_summary_cross_same_and_multiplied_net(self):
        for challenger, cup, scores in ((2, False, '蓝队 +1分，红队 -1分'),
                                        (1, False, '红队 +0分'),
                                        (1, True, '红队 -1分')):
            with self.subTest(challenger=challenger, cup=cup):
                state, ids = self.make(cup_penalty=cup)
                state['dice_by_player'] = {pid:[2]*5 for pid in ids}
                result = self.resolve(state, challenger=challenger)
                self.assertEqual(result['result_summary'],
                    f'唐焰叫10个6点，开骰计入0个，质疑成功。{scores}。')
                legacy = deepcopy(result)
                legacy.pop('score_changes')
                self.assertIn(f'红队 {1 - (2 if cup else 1):+g}分' if challenger == 1
                              else '红队 -1分', self.game._party_round_summary(state, legacy, {}))

    def test_last_bidder_and_winning_team_are_not_assumed_from_first_seat(self):
        state, ids = self.make()
        self.people[1]['display_name'] = '唐焰'
        state['dice_by_player'] = {pid:[3]*5 for pid in ids}
        self.game.apply_action(state, {'action':'bid','quantity':4,'face':3,'exact':True}, self.people[0])
        self.game.apply_action(state, {'action':'bid','quantity':5,'face':3}, self.people[1])
        move = {'action':'challenge'}
        applied = self.game.apply_action(state, move, self.people[2])
        self.game.progress_after_action(state, move, self.people[2], self.people, applied)
        self.assertEqual(state['last_round_result']['result_summary'],
            '唐焰叫5个3点（斋），开骰计入28个，叫点成功。红队 +1分，蓝队 -1分。')

    def test_classic_summary_stays_unchanged(self):
        self.game = LiarsDice(random.Random(82))
        self.people = support.participants(2)
        state = self.game.initialize(self.people)
        state['dice_by_player'] = {p['player_id']:[2]*5 for p in self.people}
        self.game.apply_action(state, {'action':'bid','quantity':3,'face':6}, self.people[0])
        move = {'action':'challenge'}
        applied = self.game.apply_action(state, move, self.people[1])
        self.game.progress_after_action(state, move, self.people[1], self.people, applied)
        expected = ('小机 1 质疑 人类一号 的叫点“3 个 6 点”；实际有 0 个 6 点，'
                    '叫点失败；人类一号 输掉 1 枚骰，剩余 4 枚，未淘汰。')
        self.assertEqual(state['last_round_result']['result_summary'], expected)
        self.assertEqual(applied.note, f'第 1 轮：{expected}第 2 轮 · 由 人类一号 开叫。')

    def test_companion_other_team_combines_reward_and_penalty(self):
        state, ids = self.make(companion_penalty=True, cup_penalty=True)
        state['dice_by_player'] = {pid:[2]*5 for pid in ids}
        with patch.object(self.game, '_roll', return_value=[3]):
            result = self.resolve(state, challenger=2)
        self.assertEqual(result['result_summary'],
            '唐焰叫10个6点，开骰计入0个，质疑成功。蓝队 -1分，红队 -2分。')
        self.assertEqual(result['companion']['player_id'], ids[2])
        self.assertEqual(len(result['penalties']), 2)
        self.assertEqual(len(result['rewards']), 1)

    def test_dark_summary_uses_final_winner_in_both_directions(self):
        for quantity, holds, verdict, scores in (
            (3, True, '质疑成功', '林舟 +1分，唐焰 -1分'),
            (11, False, '叫点成功', '唐焰 +1分，林舟 -1分'),
        ):
            state, ids = self.make(2, False, dark_rule=True)
            state['dice_by_player'] = {pid:[1]*5 for pid in ids}
            result = self.resolve(state, quantity=quantity, face=2)
            self.assertEqual(result['bid_holds'], holds)
            self.assertEqual(result['result_summary'],
                f'唐焰叫{quantity}个2点，开骰计入10个，天黑黑反转，{verdict}。{scores}。')

    def test_legacy_summary_uses_only_recorded_deltas_without_reward(self):
        for teams in (False, True):
            state, ids = self.make(4, teams)
            state.pop('scoring_rules_version')
            state['dice_by_player'] = {pid:[2]*5 for pid in ids}
            result = self.resolve(state)
            label = '红队' if teams else '唐焰'
            expected = f'唐焰叫10个6点，开骰计入0个，质疑成功。{label} -1分。'
            self.assertEqual(result['result_summary'], expected)
            legacy = deepcopy(result)
            legacy.pop('score_changes')
            legacy.pop('rewards')
            names = {p['player_id']:p['display_name'] for p in self.people}
            for detailed in (True, False):
                if not detailed:
                    legacy.pop('penalties')
                before = deepcopy(legacy)
                self.assertEqual(self.game._party_round_summary(state, legacy, names), expected)
                self.assertEqual(legacy, before)

    def test_summary_prefers_ledger_and_fallback_merges_gross_rows(self):
        state, ids = self.make(companion_penalty=True, cup_penalty=True)
        state['dice_by_player'] = {pid:[2]*5 for pid in ids}
        with patch.object(self.game, '_roll', return_value=[3]):
            result = self.resolve(state, challenger=2)
        names = {p['player_id']:p['display_name'] for p in self.people}
        expected = result['result_summary']
        # Gross detail and cumulative totals must not override the net ledger.
        changed = deepcopy(result)
        changed['penalties'] = changed['rewards'] = []
        changed['score_delta'] = changed['score_after'] = 999
        for row in changed['score_changes']:
            row['score_delta'] = row['score_after'] = 999
        self.assertEqual(self.game._party_round_summary(state, changed, names), expected)
        legacy = deepcopy(result)
        legacy.pop('score_changes')
        text = self.game._party_round_summary(state, legacy, names)
        self.assertEqual(text, '唐焰叫10个6点，开骰计入0个，质疑成功。红队 -2分，蓝队 -1分。')
        for row in legacy['penalties'] + legacy['rewards']:
            row.pop('deduction_half_units', None)
            row.pop('reward_half_units', None)
        self.assertEqual(self.game._party_round_summary(state, legacy, names), text)

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
        self.assertIn('天黑黑反转，质疑成功。',result['result_summary'])
        self.assertIn('小机 1 +1分',result['result_summary'])

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
            for keep in (False, True):
                state, ids = self.make(4,teams,allow_surrender=True,companion_penalty=True,
                    cup_penalty=True,extreme_penalty=True,dark_rule=True,surrender_no_reroll=keep)
                before = deepcopy(state['dice_by_player'])
                self.game.apply_action(state,{'action':'bid','quantity':6,'face':2},self.people[0])
                move = {'action':'surrender'}
                with patch.object(self.game,'_roll',side_effect=AssertionError('no companion on surrender')):
                    applied = self.game.apply_action(state,move,self.people[1])
                event_keys = list(applied.public_event['round_result'])
                self.game.progress_after_action(state,move,self.people[1],self.people,applied)
                result = state['last_round_result']
                self.assertEqual(list(applied.public_event['round_result']), event_keys)
                self.assertEqual(applied.public_event['round_result'], result)
                self.assertEqual(result['rewards'],[])
                self.assertEqual(result['score_delta'],-.5)
                self.assertEqual(sum(state['score_half_units'].values()),-1)
                label = '红队' if teams else '小机 1'
                expected = f'小机 1投降。{label} -0.5分。' + (
                    '不揭骰，下一轮保留骰面。' if keep else '揭盅，确认后各盅重摇。')
                self.assertEqual(result['result_summary'], expected)
                self.assertEqual(applied.note, expected)
                self.assertEqual(result['revealed_dice_by_player'], {} if keep else before)
                with patch.object(self.game, '_roll', return_value=[6]*5) as roll:
                    self.game.acknowledge_round(state)
                    self.assertEqual(roll.call_count, 0 if keep else 4)
                if keep:
                    self.assertEqual(state['dice_by_player'], before)


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
                self.assertEqual(list(event), ['round', 'mode', 'winner_player_id', 'score_delta',
                    'score_after', 'loser_team_id', 'penalties', 'rewards', 'score_changes',
                    'scoring_rules_version', 'companion', 'cup_bonuses', 'dark_reversed', 'bid',
                    'actual_count', 'bid_holds', 'loser_player_id', 'loser_remaining_dice',
                    'eliminated', 'next_round', 'next_starter_player_id', 'revealed_dice_by_player'])
                for field in event:
                    if field != 'bid':
                        self.assertEqual(event[field], result[field])
                self.assertEqual(event['bid'], {k:v for k,v in result['bid'].items() if k != 'bidder_player_id'})
                self.assertEqual(delta['events'], [
                    ['human-1', {'action':'bid','quantity':10,'face':6}], {'round_result':event}])
                self.assertNotIn('dice', delta.get('private', {}))
                self.assertTrue(set(mcp.response(room['room_id'],'ai-1')) <= {'r','wait'})
                for row in result['score_changes']:
                    name = next(p['display_name'] for p in room['participants'] if p['player_id'] == row['unit_id'])
                    phrase = f"{name} {row['score_delta']:+g}分"
                    self.assertEqual(result['result_summary'].count(phrase), 1)
                self.assertEqual(room['board_state']['last_action_note'], result['summary'])
                self.assertEqual(result['summary'], f"第 1 轮：{result['result_summary']}")
                web = framework.project_room_for_viewer(room,'human-1')
                self.assertEqual(web['board_state']['scores'],expected)
                self.assertEqual({p['player_id']:p['game_metadata']['score'] for p in web['participants']},expected)
                self.assertEqual(web['board_state']['round_results'][0]['rewards'],result['rewards'])
                full = mcp.response(room['room_id'],'ai-1',full=True)
                self.assertEqual(full['snapshot']['board_state']['scores'],expected)
                self.assertEqual(full['snapshot']['board_state']['last_round_result'], result)
                self.assertEqual(full['snapshot']['private_state']['dice'], [3]*5)
                self.assertNotIn('dice_by_player', full['snapshot']['board_state'])
                self.assertNotIn('events', full)
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
