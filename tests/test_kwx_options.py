"""Isolated v2 room-rule regressions: no production DB, rooms, or credentials."""
import json
import random
import unittest
from collections import Counter
from copy import deepcopy

from app.games.ka_wu_xing import KaWuXing
from tests.test_mahjong import participants


GAP = 'B1 B2 B3 T1 T2 T3 T7 T8 T9 B4 B6 J1 J1'.split()


class KaWuXingOptionTests(unittest.TestCase):
    def setUp(self):
        self.game = KaWuXing(random.Random(271))
        self.players = participants()[:3]

    def state(self, hands=None, options=None, melds=None):
        """Allocate actual IDs from one 84-tile wall; fill unspecified seats."""
        state = self.game.configure(self.game._empty_state(['p0', 'p1', 'p2']), options)
        state['phase'] = state['flow']['phase'] = 'discard'
        pool = self.game.build_wall()
        hands = hands or {}
        for p, groups in (melds or {}).items():
            for code, kind in groups:
                tiles = self.take(pool, [code] * (3 if kind == 'peng' else 4))
                state['melds'][p].append(dict(kind=kind, tiles=tiles, engine_tile=code,
                                             offer=1, source_player_id='p2'))
        for p, codes in hands.items():
            state['hands'][p] = self.take(pool, codes.split() if isinstance(codes, str) else codes)
        for p in state['participant_order']:
            target = (14 if p == 'p0' else 13) - 3 * len(state['melds'][p])
            while len(state['hands'][p]) < target:
                state['hands'][p].append(pool.pop(0))
        state['wall'] = pool
        return state

    @staticmethod
    def take(pool, codes):
        out = []
        for code in codes:
            tile = next(t for t in pool if t['code'] == code)
            pool.remove(tile)
            out.append(tile)
        return out

    def choose(self, state, player, kind, *, code=None):
        actions = self.game.legal_actions_for(state, player)
        for action in actions:
            if action['kind'] != kind or (kind == 'discard' and action.get('declare_waits')):
                continue
            tile_code = action.get('code')
            if 'tile_id' in action:
                tile_code = next(t['code'] for t in state['hands'][player] if t['id'] == action['tile_id'])
            if code is None or code == tile_code:
                return action
        self.fail(f'Missing {kind}/{code} for {player}: {actions}')

    def apply(self, state, player, kind, *, code=None):
        action = self.choose(state, player, kind, code=code)
        return self.game.apply_action(state, {'action': 'act', 'action_id': action['action_id']},
                                      {'player_id': player})

    def double_state(self, **options):
        state = self.state({'p0': ['B5'], 'p1': GAP, 'p2': GAP},
                           {'double_hu': True, **options})
        return self.apply(state, 'p0', 'discard', code='B5').state

    def all_zones(self, state):
        return (state['wall'] + [t for h in state['hands'].values() for t in h]
                + [t for h in state['discards'].values() for t in h]
                + [t for groups in state['melds'].values() for m in groups for t in m['tiles']]
                + state['robbed_kong_tiles'] + state.get('horse_tiles', []))

    def test_option_types_enums_and_legacy_defaults(self):
        self.assertEqual(self.game.saved_options(self.game._empty_state(['p0', 'p1', 'p2'])),
                         self.game.OPTION_DEFAULTS)
        for options in ({'double_hu': 1}, {'piao': True}, {'piao': 2}, {'horse_count': 2},
                        {'rounds': 3}, {'rounds': 4}, {'rounds': 8}, {'rounds': True}, {'rounds': '1'}, {'joker': True}, {'joker': 'wild'}, {'unknown': 1}, []):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.game.normalize_options(options)

    def test_double_win_restore_river_ownership_and_individual_scores(self):
        state = self.double_state()
        first = self.apply(state, 'p1', 'hu')
        self.assertIsNone(first.result)
        for player in ('p0', 'p1', 'p2'):
            with self.assertRaisesRegex(ValueError, '确认胡牌'):
                self.game.apply_resignation(first.state, player, self.players)
        self.assertEqual(first.state['turn_player_id'], 'p2')
        restored = json.loads(json.dumps(first.state))
        self.assertEqual(self.game.legal_actions_for(restored, 'p2'),
                         self.game.legal_actions_for(first.state, 'p2'))
        self.assertEqual({a['kind'] for a in self.game.legal_actions_for(restored, 'p2')}, {'hu', 'pass'})
        ended = self.apply(restored, 'p2', 'hu')
        result = ended.result
        self.assertTrue(result['double_hu'])
        self.assertEqual(result['winner_player_ids'], ['p1', 'p2'])
        self.assertEqual(ended.state['phase'], 'finished')
        self.assertNotIn('next_dealer_player_id', result)
        self.assertEqual(result['score_units'], {'p0': -4, 'p1': 2, 'p2': 2})
        self.assertEqual(len(ended.state['discards']['p0']), 1)
        self.assertEqual(len({t['id'] for t in self.all_zones(ended.state)}), 84)
        self.assertEqual(len(self.all_zones(ended.state)), 84)
        self.assertEqual(len(result['winner_results']), 2)

    def test_double_hu_one_pass_both_orders_and_old_single_win(self):
        for first, second, winner in [('hu', 'pass', 'p1'), ('pass', 'hu', 'p2')]:
            with self.subTest(first=first):
                state = self.double_state()
                state = self.apply(state, 'p1', first).state
                result = self.apply(state, 'p2', second).result
                self.assertEqual(result['winner_player_ids'], [winner])
                self.assertEqual(result['score_units'][winner], 2)
                self.assertFalse(result['double_hu'])
        state = self.double_state(double_hu=False)
        ended = self.apply(state, 'p1', 'hu')
        self.assertEqual(ended.result['winner_player_ids'], ['p1'])
        self.assertEqual(self.game.legal_actions_for(ended.state, 'p2'), [])

    def test_accepted_win_closes_lower_priority_peng_and_kong(self):
        state = self.state({'p0': ['B5'], 'p1': 'B5 B5 B5 B1 B1 B2 B4 B7 T2 T4 T6 J2 J3',
                            'p2': GAP}, {'double_hu': True})
        state = self.apply(state, 'p0', 'discard', code='B5').state
        self.assertEqual(state['response_window']['queue'][0]['player_id'], 'p2')
        self.assertIn('peng_gang', [entry['priority'] for entry in state['response_window']['queue']])
        ended = self.apply(state, 'p2', 'hu')
        self.assertEqual(ended.result['winner_player_ids'], ['p2'])
        self.assertEqual(self.game.legal_actions_for(ended.state, 'p1'), [])
        self.assertEqual(ended.state['melds']['p1'], [])

    def test_double_liability_piao_joker_and_horse_only_self_draw(self):
        state = self.state({'p0': ['B5'], 'p1': GAP, 'p2': GAP},
                           dict(double_hu=True, liang_liability=True, piao=3,
                                horse_count=1, joker='red'))
        for p in ('p1', 'p2'):
            state['revealed'][p] = dict(waits=['B5'], hand=deepcopy(state['hands'][p]))
        state = self.apply(state, 'p0', 'discard', code='B5').state
        state = self.apply(state, 'p1', 'hu').state
        ended = self.apply(state, 'p2', 'hu')
        result = ended.result
        self.assertEqual(result['score_breakdown']['piao'], {'p0': -12, 'p1': 6, 'p2': 6})
        for win in result['winner_results']:
            self.assertEqual(result['score_breakdown']['hu'][win['winner_player_id']], win['multiplier'] * 2)
        self.assertEqual(result['score_breakdown']['horse'], dict.fromkeys(['p0', 'p1', 'p2'], 0))
        self.assertEqual(result['horse_tiles'], [])
        self.assertEqual(sum(result['score_units'].values()), 0)
        self.assertEqual(self.game.settlement_deltas(ended.state, result, self.players, 0),
                         dict.fromkeys(['p0', 'p1', 'p2'], 0))

    def test_kong_discard_bonus_only_on_replacement_discard(self):
        state = self.state({'p0': ['B5'], 'p1': GAP, 'p2': GAP}, {'gang_discard_double': True})
        normal = self.game._fan_evaluation(state, 'p1', state['hands']['p0'][0], source_kind='discard')
        self.assertNotIn('杠上炮', [f['name'] for f in normal['fans']])
        state['draw_context'] = dict(player_id='p0', replacement=True, wall_last=False)
        state = self.apply(state, 'p0', 'discard', code='B5').state
        action = self.choose(state, 'p1', 'hu')
        self.assertIn('杠上炮', [f['name'] for f in action['fans']])
        self.assertEqual(action['multiplier'], normal['multiplier'] * 2)
        tile = state['response_window']['tile']
        robbed = self.game._fan_evaluation(state, 'p1', tile, source_kind='rob_kong')
        self.assertIn('抢杠胡', [f['name'] for f in robbed['fans']])
        self.assertNotIn('杠上炮', [f['name'] for f in robbed['fans']])

    def test_consecutive_completed_kongs_double_1_2_4_and_reset(self):
        state = self.state({'p0': ['B1'] * 4 + ['B2'] * 4 + ['B3'] * 4 + ['J2', 'J3']},
                           {'consecutive_kong_double': True})
        state['drawn_tile_id'] = state['hands']['p0'][-1]['id']
        for code in ('B1', 'B2', 'B3'):
            state = self.apply(state, 'p0', 'concealed_gang', code=code).state
        self.assertEqual([entry['chain_multiplier'] for entry in state['kong_ledger']], [1, 2, 4])
        self.assertEqual([entry['units'] for entry in state['kong_ledger']], [2, 4, 8])
        self.assertEqual(state['kong_scores'], {'p0': 28, 'p1': -14, 'p2': -14})
        self.assertEqual(len(self.all_zones(state)), 84)
        self.assertEqual(len({t['id'] for t in self.all_zones(state)}), 84)
        self.game._before_discard(state, 'p0', {})
        self.assertIsNone(state['kong_chain'])
        self.game._draw(state, 'p1', replacement=False)
        self.assertIsNone(state['kong_discard'])
        self.assertIsNone(state['kong_chain'])

    def test_added_kong_rob_pass_and_restore_do_not_charge_twice(self):
        state = self.state({'p0': 'B5 T4 T4 T5 T5 T6 T6 J2 J2 J3 J3', 'p1': GAP},
                           dict(consecutive_kong_double=True, gang_discard_double=True, double_hu=True),
                           {'p0': [('B5', 'peng')]})
        state['drawn_tile_id'] = state['hands']['p0'][0]['id']
        state['kong_chain'] = dict(player_id='p0', count=1)
        pending = self.apply(state, 'p0', 'added_gang').state
        self.assertEqual(pending['kong_ledger'], [])
        self.assertEqual(sum(abs(v) for v in pending['kong_scores'].values()), 0)
        robbed = self.apply(json.loads(json.dumps(pending)), 'p1', 'hu')
        self.assertEqual(robbed.result['win_type'], 'rob_kong')
        self.assertEqual(robbed.state['kong_ledger'], [])
        self.assertEqual(len(robbed.state['robbed_kong_tiles']), 1)
        self.assertEqual(robbed.state['melds']['p0'][0]['kind'], 'peng')
        self.assertNotIn('杠上炮', [f['name'] for f in robbed.result['fans']])
        passed = self.apply(json.loads(json.dumps(pending)), 'p1', 'pass').state
        while passed['phase'] == 'response':
            passed = self.apply(passed, passed['turn_player_id'], 'pass').state
        self.assertEqual(len(passed['kong_ledger']), 1)
        self.assertEqual(passed['kong_ledger'][0]['units'], 2)
        self.assertEqual(passed['kong_scores'], {'p0': 4, 'p1': -2, 'p2': -2})

    def test_one_horse_wall_tail_empty_wall_and_piao_separate(self):
        for horse_code, points in [('T6', 6), ('J3', 10), (None, 0)]:
            with self.subTest(horse=horse_code):
                state = self.state({'p0': GAP + ['B5']}, dict(horse_count=1, piao=5))
                state['drawn_tile_id'] = state['hands']['p0'][-1]['id']
                if horse_code:
                    horse = next(t for t in state['wall'] if t['code'] == horse_code)
                    state['wall'].remove(horse)
                    state['wall'].append(horse)
                else:
                    state['wall'] = []
                wall_ids = [t['id'] for t in state['wall']]
                ended = self.apply(state, 'p0', 'hu')
                result = ended.result
                self.assertEqual(result['score_breakdown']['horse'], {'p0': points * 2, 'p1': -points, 'p2': -points})
                self.assertEqual(result['score_breakdown']['piao'], {'p0': 10, 'p1': -5, 'p2': -5})
                self.assertEqual(len(ended.state['wall']), max(0, len(wall_ids) - 1))
                if horse_code:
                    self.assertEqual(ended.state['horse_tiles'][0]['id'], wall_ids[-1])
                    self.assertEqual(len(self.all_zones(ended.state)), 84)
                    self.assertEqual(len({t['id'] for t in self.all_zones(ended.state)}), 84)
                self.assertEqual(self.game.legal_actions_for(ended.state, 'p0'), [])

    def test_joker_red_cannot_peng_kong_but_can_discard_and_natural_hu(self):
        state = self.state({'p0': ['J1'] * 4}, {'joker': 'red'})
        state['drawn_tile_id'] = state['hands']['p0'][0]['id']
        legal = self.game.legal_actions_for(state, 'p0')
        self.assertFalse(any(a['kind'] == 'concealed_gang' and a['code'] == 'J1' for a in legal))
        self.assertEqual(len([a for a in legal if a['kind'] == 'discard' and not a.get('declare_waits')
                             and a['tile_id'].startswith('J1-')]), 4)
        state = self.state({'p0': ['J1'], 'p1': ['J1'] * 3}, {'joker': 'red'})
        queue = self.game._discard_response_queue(state, 'p0', state['hands']['p0'][0])
        self.assertFalse(any(e['priority'] == 'peng_gang' for e in queue))
        natural = self.state({'p0': ['J1'], 'p1': 'B1 B2 B3 B4 B5 B6 T1 T2 T3 T7 T8 T9 J1'}, {'joker': 'red'})
        natural['revealed']['p1'] = dict(waits=['J1'], hand=deepcopy(natural['hands']['p1']))
        self.assertIsNotNone(self.game._hu_action(natural, 'p1', natural['hands']['p0'][0], 'discard'))
        needs_wild = self.state({'p0': ['J1'], 'p1': 'B1 B2 B3 B4 B5 B6 T1 T2 T3 T7 T8 J2 J2'}, {'joker': 'red'})
        self.assertIsNone(self.game._hu_action(needs_wild, 'p1', needs_wild['hands']['p0'][0], 'discard'))

    def test_joker_reveal_wait_lock_hidden_draw_and_npc_legal_action(self):
        hand = 'B1 B2 B3 B4 B5 B6 T1 T2 T3 T7 T8 J2 J2 T4'.split()
        state = self.state({'p0': hand}, {'joker': 'red'})
        tile = state['hands']['p0'][-1]
        reveal = next(a for a in self.game.legal_actions_for(state, 'p0')
                      if a.get('declare_waits') and a['tile_id'] == tile['id'])
        self.assertIn('J1', reveal['declare_waits'])
        state = self.game.apply_action(state, {'action': 'act', 'action_id': reveal['action_id']}, {'player_id': 'p0'}).state
        # Complete this discard's responses before a later, isolated draw.
        while state['phase'] == 'response':
            state = self.apply(state, state['turn_player_id'], 'pass').state
        red = next(t for t in state['wall'] if t['code'] == 'J1')
        state['wall'].remove(red)
        state['wall'].insert(0, red)
        self.game._draw(state, 'p0', replacement=False)
        legal = self.game.legal_actions_for(state, 'p0')
        self.assertEqual({a['kind'] for a in legal}, {'hu', 'discard'})
        self.assertEqual([a['tile_id'] for a in legal if a['kind'] == 'discard'], [red['id']])
        npc = self.game.choose_local_npc_action(state, {'player_id': 'p0'}, self.players)
        self.assertIn(npc['action_id'], [a['action_id'] for a in legal])
        self.assertNotIn(red['id'], json.dumps(self.game.public_state(state, self.players)))
        state['revealed']['p0']['waits'] = ['T9']
        self.assertFalse(any(a['kind'] == 'hu' for a in self.game.legal_actions_for(state, 'p0')))

    def test_single_hand_ends_and_cannot_start_another(self):
        for options in ({}, {'rounds': 1, 'piao': 1}):
            state = self.state({'p0': GAP + ['B5']}, options)
            state['drawn_tile_id'] = state['hands']['p0'][-1]['id']
            ended = self.apply(state, 'p0', 'hu')
            restored = json.loads(json.dumps(ended.state))
            self.assertEqual(restored['phase'], 'finished')
            self.assertEqual(restored['game_result'], ended.result)
            self.assertEqual(self.game.settlement_deltas(restored, ended.result, self.players, 3),
                             {p: v * 3 for p, v in ended.result['score_units'].items()})
            for key in ('round_results', 'cumulative_scores', 'round_number'):
                self.assertNotIn(key, restored)
            for p in ('p0', 'p1', 'p2'):
                self.assertEqual(self.game.legal_actions_for(restored, p), [])
                with self.assertRaises(ValueError):
                    self.game.apply_action(restored, {'action': 'act', 'action_id': 'next_round:1'}, {'player_id': p})

    def test_exhaustive_draw_ends_with_reveal_liability(self):
        for revealed in ([], ['p1'], ['p1', 'p2'], ['p0', 'p1', 'p2']):
            with self.subTest(revealed=revealed):
                state = self.state()
                state['revealed'] = {p: {'waits': [], 'hand': []} for p in revealed}
                state['wall'] = []
                state['drawn_tile_id'] = state['hands']['p0'][0]['id']
                state = self.apply(state, 'p0', 'discard').state
                while state['phase'] == 'response':
                    state = self.apply(state, state['turn_player_id'], 'pass').state
                self.assertEqual(state['phase'], 'finished')
                result = state['game_result']
                self.assertTrue(result['draw'])
                self.assertEqual(result['score_units'],
                                 {p: -(3 - len(revealed)) if p in revealed else len(revealed)
                                  for p in state['participant_order']})
                self.assertNotIn('next_dealer_player_id', result)

    def test_all_options_single_hand_npc_restore_each_action(self):
        for seed in (0, 1):
            with self.subTest(seed=seed):
                self.check_npc_match(seed)

    def check_npc_match(self, seed):
        self.game = KaWuXing(random.Random(seed))
        options = dict(double_hu=True, gang_discard_double=True, liang_liability=True,
                       consecutive_kong_double=True, horse_count=1, piao=3, joker='red')
        state = self.game.configure(self.game.initialize(self.players), options)
        for _ in range(400):
            player = state['turn_player_id']
            legal = self.game.legal_actions_for(state, player)
            actor = {'player_id': player}
            move = self.game.choose_local_npc_action(state, actor, self.players)
            self.assertIn(move['action_id'], [a['action_id'] for a in legal])
            applied = self.game.apply_action(state, move, actor)
            state = json.loads(json.dumps(applied.state))
            zones = self.all_zones(state)
            self.assertEqual(len(zones), 84)
            self.assertEqual(len({t['id'] for t in zones}), 84)
            self.assertEqual(set(Counter(t['code'] for t in zones).values()), {4})
            if state['phase'] not in ('round_end', 'finished'):
                public = self.game.public_state(state, self.players)
                self.assertNotIn('terminal_hands', public)
                self.assertNotIn('hands', public)
                self.assertNotIn('wall', public)
                # Prior hands are intentionally public history; inspect only
                # the current table to distinguish old IDs from unknown cards.
                current = json.dumps({k: v for k, v in public.items()
                                      if k not in ('round_results', 'round_result', 'game_result')})
                for p in state['participant_order']:
                    if p not in state['revealed']:
                        for tile in state['hands'][p]:
                            self.assertNotIn(json.dumps(tile['id']), current)
            if applied.result:
                self.assertEqual(state['phase'], 'finished')
                self.assertNotIn('round_results', state)
                self.assertEqual(applied.result['score_units'], state['game_result']['score_units'])
                self.assertEqual(sum(applied.result['score_units'].values()), 0)
                break
        else:
            self.fail('NPC match did not finish within 400 legal actions')

    def test_resignation_preserves_completed_kong_once(self):
        state = self.state({'p0': 'B1 B1 B1 B1 B2 B3 B4 T1 T2 T3 T7 T8 T9 J2'})
        state['drawn_tile_id'] = state['hands']['p0'][-1]['id']
        state = self.apply(state, 'p0', 'concealed_gang', code='B1').state
        before = deepcopy(state)
        result = self.game.result_for_resignation(state, 'p1', self.players)
        self.assertEqual(state, before)
        self.assertEqual(result['score_units'], {'p0': 5, 'p1': -4, 'p2': -1})
        self.assertEqual(self.game.settlement_deltas(state, result, self.players, 2),
                         {'p0': 10, 'p1': -8, 'p2': -2})



if __name__ == '__main__':
    unittest.main()
