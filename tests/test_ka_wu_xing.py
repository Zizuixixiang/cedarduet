import json
import os
import subprocess
import sys
import random
import tempfile
import unittest
from collections import Counter
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from app import chips, database, framework, invites
from app.games import GAMES, game_catalog
from app.games.ka_wu_xing import CODES, KaWuXing, seven_pairs, waits, winning
from tests.test_mahjong import participants

GAP = 'B1 B2 B3 T1 T2 T3 T7 T8 T9 B4 B6 J1 J1'.split()
PLAIN = 'B1 B2 B3 B4 B5 B6 T7 T8 T9 T2 T3 J1 J1'.split()


class KaWuXingRulesTests(unittest.TestCase):
    def setUp(self):
        self.game = KaWuXing(random.Random(5))
        self.players = participants()[:3]
        self.sequence = 0

    def tiles(self, codes):
        result = []
        for code in codes:
            self.sequence += 1
            result.append(dict(id=f'fixture-{self.sequence}', code=code))
        return result

    def state(self):
        s = self.game._empty_state(['p0', 'p1', 'p2'])
        s.update(phase='discard', turn_player_id='p0', wall=self.tiles(['J3'] * 3))
        s['flow']['phase'] = 'discard'
        return s

    def act(self, s, p, kind=None, action_id=None):
        a = next(a for a in self.game.legal_actions_for(s, p)
                 if (a['action_id'] == action_id if action_id else a['kind'] == kind))
        return self.game.apply_action(s, {'action': 'act', 'action_id': a['action_id']}, {'player_id': p}).state

    def evaluate(self, codes, tile, source='discard', revealed=False):
        s = self.state()
        s['hands']['p0'] = self.tiles(codes)
        t = self.tiles([tile])[0]
        if source == 'self_draw':
            s['hands']['p0'].append(t)
        if revealed:
            s['revealed']['p0'] = {'waits': [tile], 'hand': []}
        return self.game._fan_evaluation(s, 'p0', t, source_kind=source)

    def test_84_tiles_three_seats_deal_rotation_and_catalog(self):
        self.assertEqual(len(self.game.build_wall()), 84)
        self.assertEqual(set(t['code'] for t in self.game.build_wall()), set(CODES))
        self.assertEqual(set(Counter(t['code'] for t in self.game.build_wall()).values()), {4})
        s = self.game.initialize_for_first_player(self.players, 'p2')
        self.assertEqual(s['participant_order'], ['p2', 'p0', 'p1'])
        self.assertEqual([len(s['hands'][p]) for p in s['participant_order']], [14, 13, 13])
        self.assertEqual(len(s['wall']), 44)
        self.assertEqual(self.game._next_player(s, 'p1'), 'p2')
        self.assertEqual(self.game._distance_order(s, 'p1'), ['p2', 'p0'])
        self.assertEqual(self.game.initialize(self.players[:2])['phase'], 'waiting')
        with self.assertRaises(ValueError): self.game.initialize(participants())
        entry = next(g for g in game_catalog() if g['game_type'] == 'ka_wu_xing')
        self.assertEqual(entry['allowed_player_counts'], [3])
        self.assertTrue(entry['uses_local_npc_strategy'])

    def test_basic_seven_pairs_and_quad_pairs(self):
        self.assertTrue(winning(GAP + ['B5']))
        for codes, name, multiplier in [
            ('B1 B1 B3 B3 B7 B7 T1 T1 T4 T4 J1 J1 J2 J2', '七对', 4),
            ('B1 B1 B1 B1 B7 B7 T1 T1 T4 T4 J1 J1 J2 J2', '龙七对', 8),
            ('B1 B1 B1 B1 B7 B7 B7 B7 T4 T4 J1 J1 J2 J2', '双龙七对', 16),
        ]:
            hand = codes.split()
            self.assertTrue(seven_pairs(hand))
            e = self.evaluate(hand[:-1], hand[-1])
            self.assertEqual(e['multiplier'], multiplier)
            self.assertEqual([f['name'] for f in e['fans']], [name])
            self.assertIn(hand[-1], waits(hand[:-1]))
        self.assertFalse(winning(['B1'] * 5 + GAP[:9]))
        self.assertFalse(winning(GAP + ['W5']))
        self.assertFalse(winning(GAP + ['T5']))

    def test_actual_gap_both_suits_and_already_completed_456_counterexample(self):
        for hand, tile in [(GAP, 'B5'), ([c.translate(str.maketrans('BT', 'TB')) for c in GAP], 'T5')]:
            e = self.evaluate(hand, tile)
            self.assertEqual(e['multiplier'], 2)
            self.assertIn('卡五星', [f['name'] for f in e['fans']])
        e = self.evaluate(PLAIN, 'T4')
        self.assertEqual(e['multiplier'], 1)
        self.assertNotIn('卡五星', [f['name'] for f in e['fans']])
        # Ambiguous pair/sequence assignment: a valid gap decomposition is sufficient.
        e = self.evaluate('B4 B5 B6 B5 T1 T2 T3 T7 T8 T9 J1 J1 J1'.split(), 'B5')
        self.assertIn('卡五星', [f['name'] for f in e['fans']])

    def test_plain_discard_gate_self_draw_and_reveal_multiplier(self):
        self.assertFalse(self.evaluate(PLAIN, 'T4')['meets_minimum'])
        self.assertTrue(self.evaluate(PLAIN, 'T4', 'self_draw')['meets_minimum'])
        self.assertEqual(self.evaluate(PLAIN, 'T4', revealed=True)['multiplier'], 2)
        self.assertTrue(self.evaluate(PLAIN, 'T4', revealed=True)['meets_minimum'])
        self.assertEqual(self.evaluate(GAP, 'B5', revealed=True)['multiplier'], 4)

    def test_cap_before_reveal_and_patterns(self):
        e = self.evaluate('B1 B1 B1 B1 B2 B2 B2 B2 B3 B3 B4 B4 B5'.split(), 'B5', revealed=True)
        self.assertEqual(e['pattern_cap'], 16)
        self.assertEqual(e['multiplier'], 32)
        e = self.evaluate('J1 J1 J1 J2 J2 J2 J3 J3 J3 B1 B1 B1 T2'.split(), 'T2')
        self.assertEqual(e['multiplier'], 8)
        self.assertIn('大三元', [f['name'] for f in e['fans']])
        e = self.evaluate('J1 J1 J1 J2 J2 J2 J3 J3 B1 B1 B1 T2 T2'.split(), 'T2')
        self.assertIn('小三元', [f['name'] for f in e['fans']])
        self.assertNotIn('大三元', [f['name'] for f in e['fans']])

    def test_four_in_one_and_kong_context_multipliers(self):
        e = self.evaluate('B2 B2 B2 B1 B3 T1 T2 T3 T7 T8 T9 J1 J1'.split(), 'B2')
        self.assertIn('暗四归一', [f['name'] for f in e['fans']])
        s = self.state()
        s['hands']['p0'] = self.tiles('B1 B3 T1 T2 T3 T7 T8 T9 J1 J1'.split())
        s['melds']['p0'] = [dict(kind='peng', tiles=self.tiles(['B2']*3), engine_tile='B2', offer=1, source_player_id='p1')]
        e = self.game._fan_evaluation(s, 'p0', self.tiles(['B2'])[0], source_kind='discard')
        self.assertIn('明四归一', [f['name'] for f in e['fans']])
        s = self.state(); s['hands']['p0'] = self.tiles(GAP + ['B5'])
        s['draw_context'] = {'replacement': True, 'wall_last': True}
        e = self.game._fan_evaluation(s, 'p0', s['hands']['p0'][-1], source_kind='self_draw')
        self.assertEqual({f['name'] for f in e['fans']}, {'卡五星', '杠上开花', '海底自摸'})
        self.assertEqual(e['multiplier'], 8)

    def test_reveal_fixed_waits_no_changed_hand_or_kongs_and_hidden_draw(self):
        s = self.state()
        s['hands']['p0'] = self.tiles(GAP + ['J2'])
        discard = s['hands']['p0'][-1]['id']
        s = self.act(s, 'p0', action_id='liang:' + discard)
        self.assertEqual(s['revealed']['p0']['waits'], ['B5'])
        self.assertEqual(len(s['revealed']['p0']['hand']), 13)
        s['wall'] = self.tiles(['J2'])
        self.game._draw(s, 'p0', replacement=False)
        legal = self.game.legal_actions_for(s, 'p0')
        self.assertEqual([a['action_id'] for a in legal], ['discard:' + s['drawn_tile_id']])
        with self.assertRaises(ValueError):
            self.game.apply_action(s, {'action': 'act', 'action_id': 'discard:' + s['hands']['p0'][0]['id']}, {'player_id': 'p0'})
        public = self.game.public_state(s, self.players)
        self.assertNotIn(s['drawn_tile_id'], json.dumps(public))
        self.assertEqual(public['revealed']['p0']['waits'], ['B5'])

    def test_unready_cannot_reveal_no_chi_and_no_forged_action(self):
        s = self.game.initialize(self.players)
        with self.assertRaises(ValueError):
            self.game.apply_action(s, {'action': 'act', 'action_id': 'liang:fake'}, self.players[0])
        self.assertEqual(self.game._chi_actions(s, 'p0', {'code': 'B3'}), [])
        with self.assertRaises(ValueError):
            self.game.apply_action(s, {'action': 'act', 'action_id': 'discard:fake', 'multiplier': 32}, self.players[0])

    def test_response_order_single_winner_pass_hu_until_next_draw_and_restore(self):
        s = self.state()
        s['hands']['p0'] = self.tiles(['B5'])
        for p in ('p1', 'p2'): s['hands'][p] = self.tiles(GAP)
        s = self.act(s, 'p0', 'discard')
        self.assertEqual([e['player_id'] for e in s['response_window']['queue']], ['p1', 'p2'])
        restored = json.loads(json.dumps(s))
        self.assertEqual(self.game.legal_actions_for(restored, 'p1'), self.game.legal_actions_for(s, 'p1'))
        s = self.act(restored, 'p1', 'pass')
        self.assertIn('p1', s['passed_hu'])
        self.assertIsNone(self.game._hu_action(s, 'p1', self.tiles(['B5'])[0], 'discard'))
        self.assertIsNone(self.game._hu_action(s, 'p1', self.tiles(['B5'])[0], 'rob_kong'))
        end = self.act(s, 'p2', 'hu')
        self.assertEqual(end['game_result']['winner_player_ids'], ['p2'])
        self.assertEqual(end['game_result']['score_units'], {'p0': -2, 'p1': 0, 'p2': 2})
        self.game._draw(s, 'p1', replacement=False)
        self.assertNotIn('p1', s['passed_hu'])

    def test_hu_before_peng_and_last_discard_only_hu(self):
        s = self.state()
        s['hands']['p1'] = self.tiles(['B5', 'B5', 'B5'])
        s['hands']['p2'] = self.tiles(GAP)
        q = self.game._discard_response_queue(s, 'p0', self.tiles(['B5'])[0])
        self.assertEqual([(a['player_id'], a['priority']) for a in q], [('p2', 'hu'), ('p1', 'peng_gang')])
        s['wall'] = []
        self.assertEqual(len(self.game._discard_response_queue(s, 'p0', self.tiles(['B5'])[0])), 1)

    def test_kong_scores_direct_concealed_and_draw_retains(self):
        s = self.state(); s['hands']['p0'] = self.tiles(['B1']); s['hands']['p1'] = self.tiles(['B1'] * 3)
        s = self.act(s, 'p0', 'discard'); s = self.act(s, 'p1', 'ming_gang')
        self.assertEqual(s['kong_scores'], {'p0': -2, 'p1': 2, 'p2': 0})
        s = self.state(); s['hands']['p0'] = self.tiles(['B1'] * 4); s['drawn_tile_id'] = s['hands']['p0'][-1]['id']
        s = self.act(s, 'p0', 'concealed_gang')
        self.assertEqual(s['kong_scores'], {'p0': 4, 'p1': -2, 'p2': -2})
        public = self.game.public_state(s, self.players)
        self.assertEqual(public['melds']['p0'][0]['tiles'], [{'back': True}] * 4)
        s['wall'] = []
        s = self.act(s, 'p0', 'discard')
        self.assertTrue(s['game_result']['draw'])
        self.assertEqual(s['game_result']['score_units'], s['kong_scores'])

    def added_state(self, rob=False):
        s = self.state()
        s['melds']['p0'] = [dict(kind='peng', tiles=self.tiles(['B5']*3), engine_tile='B5', offer=1, source_player_id='p2')]
        s['hands']['p0'] = self.tiles(['B5']); s['drawn_tile_id'] = s['hands']['p0'][0]['id']
        if rob: s['hands']['p1'] = self.tiles(GAP)
        return s

    def test_added_kong_and_robbed_kong_never_double_charge(self):
        s = self.act(self.added_state(), 'p0', 'added_gang')
        self.assertEqual(s['kong_scores'], {'p0': 2, 'p1': -1, 'p2': -1})
        pending = self.act(self.added_state(True), 'p0', 'added_gang')
        self.assertEqual(pending['kong_scores'], {'p0': 0, 'p1': 0, 'p2': 0})
        robbed = self.act(json.loads(json.dumps(pending)), 'p1', 'hu')
        self.assertEqual(robbed['game_result']['win_type'], 'rob_kong')
        self.assertEqual(robbed['melds']['p0'][0]['kind'], 'peng')
        self.assertEqual(len(robbed['robbed_kong_tiles']), 1)
        self.assertEqual(robbed['kong_ledger'], [])
        passed = self.act(pending, 'p1', 'pass')
        self.assertEqual(passed['kong_scores'], {'p0': 2, 'p1': -1, 'p2': -1})
        self.assertEqual(len(passed['kong_ledger']), 1)
        with self.assertRaises(ValueError):
            self.game.apply_action(passed, {'action': 'act', 'action_id': 'pass:added_kong:0'}, {'player_id': 'p1'})

    def test_exhaustive_reveal_draw_liabilities_and_zero_stake(self):
        for count, expected in [(0, [0,0,0]), (1, [-2,1,1]), (2, [-1,-1,2]), (3, [0,0,0])]:
            s = self.state(); s['revealed'] = {p: {} for p in s['participant_order'][:count]}
            result = self.game._finish_draw(s)
            self.assertEqual(list(self.game.score_units(s, result).values()), expected)
            self.assertEqual(self.game.settlement_deltas(s, result, self.players, 0), dict.fromkeys(s['participant_order'], 0))
            self.assertEqual(sum(self.game.settlement_deltas(s, result, self.players, 5).values()), 0)

    def test_self_draw_and_resignation_settlement(self):
        s = self.state(); s['hands']['p0'] = self.tiles(GAP + ['B5']); s['drawn_tile_id'] = s['hands']['p0'][-1]['id']
        end = self.act(s, 'p0', 'hu')
        self.assertEqual(end['game_result']['score_units'], {'p0': 4, 'p1': -2, 'p2': -2})
        result = self.game.result_for_resignation(s, 'p1', self.players)
        self.assertEqual(result['score_units'], {'p0': 1, 'p1': -2, 'p2': 1})

    def test_npc_games_terminate_conserve_tiles_and_keep_hidden_state_private(self):
        for seed in range(12):
            g = KaWuXing(random.Random(seed)); s = g.initialize(self.players)
            for _ in range(240):
                public = g.public_state(s, self.players)
                if s['game_result']: break
                self.assertNotIn('hands', public); self.assertNotIn('wall', public)
                self.assertNotIn('terminal_hands', public)
                p = s['turn_player_id']; actor = {'player_id': p}
                private = g.private_state(s, actor, self.players)
                self.assertEqual({t['id'] for t in private['hand']}, {t['id'] for t in s['hands'][p]})
                move = g.choose_local_npc_action(s, actor, self.players)
                g.validate_action(s, move, actor)
                s = g.apply_action(s, move, actor).state
                zones = s['wall'] + [t for v in s['hands'].values() for t in v] + [t for v in s['discards'].values() for t in v] + [t for v in s['melds'].values() for m in v for t in m['tiles']] + s['robbed_kong_tiles']
                self.assertEqual(len(zones), 84); self.assertEqual(len({t['id'] for t in zones}), 84)
            self.assertIsNotNone(s['game_result'])
            self.assertEqual(sum(s['game_result']['score_units'].values()), 0)


class KaWuXingIntegrationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='kawuxing-tests-'); self.addCleanup(tmp.cleanup)
        p = patch.object(database, 'DB_PATH', Path(tmp.name) / 'duel.db'); p.start(); self.addCleanup(p.stop)
        database.init_db()

    def test_three_independent_ai_invites_persistence_terminal_and_idempotent_wallet(self):
        room = invites.create_invite('ka_wu_xing', 'ai', 'a101', target_player_count=3, stake=1, display_name='甲')
        invites.join_invite(room['invite_code'], 'ai', 'a202', display_name='乙')
        invites.join_invite(room['invite_code'], 'ai', 'a303', display_name='丙')
        room = invites.start_invite(room['room_id'], 'ai', 'a101')
        self.assertEqual(room['status'], 'playing')
        self.assertEqual({p['role'] for p in room['participants']}, {'ai'})
        # A genuinely fresh interpreter loads the persisted private/legal state.
        code = "from app import framework; import json,sys; r=framework.get_room(sys.argv[1]); print(json.dumps(framework.project_room_for_viewer(r,'a101')))"
        loaded = subprocess.run([sys.executable, '-c', code, room['room_id']],
                                env={**os.environ, 'DUEL_DB_PATH': str(database.DB_PATH)},
                                capture_output=True, text=True, check=True)
        restored = json.loads(loaded.stdout)
        self.assertEqual(restored['private_state'], framework.project_room_for_viewer(room, 'a101')['private_state'])
        for _ in range(240):
            room = framework.get_room(room['room_id'])  # persisted reload every action
            if room['status'] == 'finished': break
            actor = next(p for p in room['participants'] if p['player_id'] == room['current_player_id'])
            projection = framework.project_room_for_viewer(room, actor['player_id'])
            self.assertNotIn('hands', projection['board_state'])
            move = GAMES['ka_wu_xing'].choose_local_npc_action(room['board_state'], actor, room['participants'])
            room = framework.play_move(room['room_id'], 'ai', actor['player_id'], move, expected_revision=room['revision'])
        self.assertEqual(room['status'], 'finished')
        self.assertEqual(sum(room['result']['settlement_deltas'].values()), 0)
        with database.write_transaction() as conn:
            before = conn.execute('SELECT COUNT(*) FROM chip_ledger').fetchone()[0]
            self.assertFalse(chips.settle_duel_room(conn, room))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM chip_ledger').fetchone()[0], before)

    def test_persisted_draw_keeps_kong_scores_caps_wallet_and_settles_once(self):
        room = invites.create_invite('ka_wu_xing', 'ai', 'a101', target_player_count=3, stake=1, display_name='甲')
        for p in ('a202', 'a303'):
            invites.join_invite(room['invite_code'], 'ai', p, display_name=p)
        room = invites.start_invite(room['room_id'], 'ai', 'a101')
        actor = room['current_player_id']
        order = room['board_state']['participant_order']
        others = [p for p in order if p != actor]
        state = GAMES['ka_wu_xing']._empty_state(order)
        state.update(phase='discard', turn_player_id=actor)
        state['flow']['phase'] = 'discard'
        state['hands'][actor] = [{'id': 'B1-1', 'code': 'B1'}]
        state['kong_scores'] = {actor: 4, others[0]: -2, others[1]: -2}
        state['kong_ledger'] = [{'player_id': actor, 'kind': 'concealed_gang', 'payers': others, 'units': 2}]
        with database.write_transaction() as conn:
            conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?', (json.dumps(state), room['room_id']))
            for p, balance in zip(others, (1, 0)):
                wallet = chips._ensure_wallet(conn, 'ai', p)
                conn.execute('UPDATE chip_wallets SET balance=? WHERE id=?', (balance, wallet['id']))
        room = framework.play_move(room['room_id'], 'ai', actor,
                                   {'action': 'act', 'action_id': 'discard:B1-1'}, expected_revision=room['revision'])
        self.assertTrue(room['result']['draw'])
        self.assertEqual(room['result']['score_units'], state['kong_scores'])
        self.assertEqual(room['result']['settlement_deltas'], {actor: 1, others[0]: -1, others[1]: 0})
        with database.write_transaction() as conn:
            self.assertEqual(chips._wallet_row(conn, 'ai', others[0])['balance'], 0)
            self.assertEqual(chips._wallet_row(conn, 'ai', others[1])['balance'], 0)
            self.assertFalse(chips.settle_duel_room(conn, room))

    def test_wallet_caps_losses_and_distributes_rounding_zero_sum(self):
        ps = participants()[:3]
        with database.write_transaction() as conn:
            wallet = chips._ensure_wallet(conn, 'human', 'p0')
            conn.execute('UPDATE chip_wallets SET balance=3 WHERE id=?', (wallet['id'],))
            requested = {'p0': -32, 'p1': 16, 'p2': 16}
            actual = chips.cap_available_zero_sum_deltas(conn, ps, requested)
            self.assertEqual(actual, {'p0': -3, 'p1': 2, 'p2': 1})
            s = GAMES['ka_wu_xing'].initialize(ps)
            result = framework._attach_multiplayer_settlement(GAMES['ka_wu_xing'], {'participants': ps, 'stake': 1}, s, {'settlement_deltas': requested}, conn=conn)
            self.assertEqual(result['settlement_deltas'], actual)
            self.assertEqual(result['requested_settlement_deltas'], requested)
