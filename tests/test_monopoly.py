"""Rule/state-machine tests; never open any production database."""
import json
import random
import unittest
from copy import deepcopy

from app.games.monopoly import Monopoly, _CARDS


class Dice:
    def __init__(self, *values):
        self.base = random.Random(91)
        self.values = list(values)

    def shuffle(self, values):
        self.base.shuffle(values)

    def randint(self, a, b):
        if self.values:
            return self.values.pop(0)
        return self.base.randint(a, b)


class MonopolyRulesTests(unittest.TestCase):
    def game(self, n=4, dice=()):
        self.g = Monopoly(Dice(*dice))
        self.ps = [dict(player_id=f'p{i}', display_name=f'玩家{i}', token=f'P{i+1}',
                        participant_kind='system_npc') for i in range(n)]
        self.s = self.g.initialize(self.ps)
        return self.s

    def play(self, action, **params):
        actor = next(p for p in self.ps if p['player_id'] == self.s['turn_player_id'])
        self.result = self.g.apply_action(self.s, dict(action=action, action_seq=self.s['action_seq'], **params), actor)
        self.s = self.result.state
        self.invariants()
        return self.s

    def invariants(self):
        self.assertEqual(len(self.s['tiles']), 40)
        self.assertEqual(len(set(p['player_id'] for p in self.s['players'])), len(self.ps))
        self.assertTrue(all(type(p['cash']) is int and p['cash'] >= 0 for p in self.s['players']))
        self.assertTrue(all(0 <= p['position'] < 40 for p in self.s['players']))
        self.assertTrue(all(0 <= t['level'] <= 5 and (not t['mortgaged'] or not t['level']) for t in self.s['tiles']))
        self.assertTrue(all(t['owner'] is None or t['owner'] in self.g.active(self.s) for t in self.s['tiles']))
        self.assertTrue(all(n >= 0 for n in self.g.supply(self.s).values()))
        for deck in _CARDS:
            held = sum(deck in p['jail_cards'] for p in self.s['players'])
            self.assertEqual(len(self.s['_decks'][deck]) + held, 16)
            self.assertEqual(len(set(self.s['_decks'][deck])), len(self.s['_decks'][deck]))
        if self.s['phase'] != 'finished':
            self.assertIn(self.s['turn_player_id'], self.g.active(self.s))
            self.assertTrue(self.g.legal_actions(self.s, self.s['turn_player_id']))
        self.assertEqual(json.loads(json.dumps(self.s)), self.s)

    def own(self, ids, pid='p0'):
        for i in ids:
            self.s['tiles'][i]['owner'] = pid

    def rejects(self, action, **params):
        actor = next(p for p in self.ps if p['player_id'] == self.s['turn_player_id'])
        before = deepcopy(self.s)
        with self.assertRaises(ValueError):
            self.g.apply_action(self.s, dict(action=action, action_seq=self.s['action_seq'], **params), actor)
        self.assertEqual(before, self.s)

    def test_opening_player_and_2_4_6_complete_turns(self):
        for n in (2, 4, 6):
            self.game(n, (1, 2) * n)
            for i in range(n):
                self.assertEqual(self.s['turn_player_id'], f'p{i}')
                self.play('roll')
                if self.s['phase'] == 'purchase':
                    self.play('buy')
                self.play('end_turn')
            self.assertEqual(self.s['turn_player_id'], 'p0')
            self.assertEqual(self.s['turn_number'], n + 1)
        s = self.g.prepare_opening_state(self.g.initialize(self.ps), 'p3', self.ps)
        self.assertEqual(s['turn_player_id'], 'p3')

    def test_buy_rent_salary_and_stale_replay(self):
        self.game(2, (1, 2, 1, 2))
        self.play('roll'); old = {'action': 'buy', 'action_seq': self.s['action_seq']}
        self.play('buy')
        self.assertEqual(self.s['players'][0]['cash'], 1440)
        with self.assertRaises(ValueError): self.g.apply_action(self.s, old, self.ps[0])
        self.play('end_turn'); self.play('roll')
        self.assertEqual([p['cash'] for p in self.s['players']], [1444, 1496])
        self.s['players'][1]['position'] = 39
        self.s['phase'] = 'roll'; self.g.rng = Dice(1, 3)
        self.play('roll')
        self.assertEqual(self.s['players'][1]['cash'], 1692)

    def test_group_build_even_sell_mortgage_redeem(self):
        self.game(); self.own([1, 3])
        self.assertEqual(self.g.rent(self.s, self.s['tiles'][3]), 8)
        self.play('build', tile_id=1)
        self.rejects('build', tile_id=1)
        self.rejects('mortgage', tile_id=3)
        self.play('build', tile_id=3)
        self.assertEqual(self.g.rent(self.s, self.s['tiles'][3]), 20)
        self.play('build', tile_id=1)
        self.rejects('sell_building', tile_id=3)
        self.play('sell_building', tile_id=1)
        self.play('sell_building', tile_id=3)
        self.play('sell_building', tile_id=1)
        self.play('mortgage', tile_id=1)
        self.assertEqual(self.g.rent(self.s, self.s['tiles'][1]), 0)
        self.rejects('build', tile_id=3)
        cash = self.s['players'][0]['cash']
        self.play('redeem', tile_id=1)
        self.assertEqual(self.s['players'][0]['cash'], cash - 33)
        self.rejects('redeem', tile_id=1)
        self.assertEqual(self.s['players'][0]['cash'], 1422)

    def test_house_hotel_stock_and_hotel_downgrade(self):
        self.game(); self.own([1, 3]); self.s['players'][0]['cash'] = 10000
        for _ in range(4):
            self.play('build', tile_id=1); self.play('build', tile_id=3)
        self.play('build', tile_id=1)
        self.assertEqual(self.g.supply(self.s), {'houses': 28, 'hotels': 11})
        self.play('sell_building', tile_id=1)
        self.assertEqual(self.g.supply(self.s), {'houses': 24, 'hotels': 12})
        # Eight more houses in six adjacent groups consume the bank's stock.
        for i in [6, 8, 9, 11, 13, 14]:
            self.own([i], 'p1'); self.s['tiles'][i]['level'] = 4
        self.assertEqual(self.g.supply(self.s)['houses'], 0)
        self.play('build', tile_id=1)  # Returning 4 houses to upgrade is legal.
        self.play('build', tile_id=3)
        for i in [16, 18]:
            self.own([i], 'p2'); self.s['tiles'][i]['level'] = 4
        self.rejects('sell_building', tile_id=1)  # No houses for hotel conversion.

    def test_rail_and_utilities(self):
        self.game(); self.own([5, 15, 25, 35]); self.s['dice'] = [3, 4]
        self.assertEqual(self.g.rent(self.s, self.s['tiles'][5]), 200)
        self.s['tiles'][15]['mortgaged'] = True
        self.assertEqual(self.g.rent(self.s, self.s['tiles'][5]), 200)
        self.own([12]); self.assertEqual(self.g.rent(self.s, self.s['tiles'][12]), 28)
        self.own([28]); self.assertEqual(self.g.rent(self.s, self.s['tiles'][12]), 70)

    def test_auction_withdraw_all_pass_and_one_winner(self):
        self.game(4, (1, 2)); self.play('roll'); self.play('auction')
        self.play('bid', amount=50)
        self.rejects('bid', amount=50); self.rejects('bid', amount=True)
        for _ in range(3): self.play('pass_bid')
        self.assertEqual(self.s['tiles'][3]['owner'], 'p0')
        self.assertEqual(self.s['players'][0]['cash'], 1450)
        self.assertEqual(self.s['turn_player_id'], 'p0')
        self.assertEqual(self.s['phase'], 'manage')
        self.game(2, (1, 2)); self.play('roll'); self.play('auction')
        self.play('pass_bid'); self.play('pass_bid')
        self.assertIsNone(self.s['tiles'][3]['owner'])

    def test_trade_exact_asset_transfer_confirmed_by_target(self):
        self.game(); self.own([1]); self.own([3], 'p1')
        self.play('mortgage', tile_id=1)
        self.play('propose_trade', to='p1', give_cash=100, take_cash=50, give_tiles=[1], take_tiles=[3])
        before = deepcopy(self.s)
        move = dict(action='respond_trade', accept=True, action_seq=self.s['action_seq'])
        for actor in (self.ps[0], self.ps[2]):
            with self.assertRaises(ValueError): self.g.apply_action(self.s, move, actor)
        self.assertEqual(self.s, before)
        self.s = json.loads(json.dumps(self.s))
        self.s['phase'] = 'manage'
        self.play('end_turn')
        self.play('respond_trade', accept=True)
        self.assertEqual([p['cash'] for p in self.s['players']][:2], [1480, 1550])
        self.assertEqual(self.s['tiles'][1]['owner'], 'p1')
        self.assertTrue(self.s['tiles'][1]['mortgaged'])
        self.assertEqual(self.s['tiles'][3]['owner'], 'p0')
        self.assertEqual(self.s['phase'], 'roll')
        with self.assertRaises(ValueError): self.g.apply_action(self.s, move, self.ps[1])

    def test_trade_validation_and_limit(self):
        self.game(); self.own([1])
        base = dict(to='p1', give_cash=0, take_cash=50, give_tiles=[1], take_tiles=[])
        for changes in (dict(to='p0'), dict(to='intruder'), dict(give_tiles=[1, 1]),
                        dict(give_tiles=[True]), dict(take_cash=-1), dict(take_cash=1.2),
                        dict(take_tiles=[3]), dict(give_cash=2000), dict(take_cash=True)):
            self.rejects('propose_trade', **{**base, **changes})
        self.own([3, 5])
        for tile_id in (1, 3, 5):
            self.play('propose_trade', **{**base, 'give_tiles': [tile_id]})
            self.play('mortgage', tile_id=tile_id)
            self.assertIsNone(self.s['trade'])
        self.rejects('propose_trade', **base)

    def test_pending_trade_keeps_proposer_and_gates_recipient(self):
        for phase in ('roll', 'manage'):
            for kind in ('human', 'bound_machine', 'system_npc'):
                for accept in (False, True):
                    with self.subTest(phase=phase, kind=kind, accept=accept):
                        self.game(3, (1, 2)); self.s['phase'] = phase
                        self.ps[1]['participant_kind'] = kind
                        self.own([1]); self.own([5], 'p1')
                        before = deepcopy(self.s)
                        self.play('propose_trade', to='p1', give_cash=100, take_cash=20,
                                  give_tiles=[1], take_tiles=[5])
                        for field in ('phase', 'current_player_id', 'turn_player_id'):
                            self.assertEqual(self.s[field], before[field])
                        self.assertEqual(self.s['trades_this_turn'], 1)
                        self.assertIn('roll' if phase == 'roll' else 'end_turn',
                                      [a['action'] for a in self.g.legal_actions(self.s, 'p0')])
                        self.assertEqual(self.g.legal_actions(self.s, 'p1'), [])
                        self.rejects('respond_trade', accept=True)
                        with self.assertRaisesRegex(ValueError, '已有待处理报价'):
                            self.g.validate_action(self.s, dict(action='propose_trade',
                                action_seq=self.s['action_seq']), self.ps[0])
                        if phase == 'roll':
                            self.play('roll'); self.play('buy')
                        self.play('end_turn')
                        self.assertEqual(self.s['current_player_id'], 'p1')
                        self.assertEqual(self.s['phase'], 'roll')
                        actions = self.g.legal_actions(self.s, 'p1')
                        self.assertEqual([a['action'] for a in actions], ['respond_trade'] * 2)
                        self.assertEqual([a['accept'] for a in actions], [False, True])
                        self.rejects('roll'); self.rejects('mortgage', tile_id=5)
                        assets = deepcopy((self.s['players'], self.s['tiles']))
                        seq = self.s['action_seq']
                        self.play('respond_trade', accept=accept)
                        self.assertEqual(self.s['action_seq'], seq + 1)
                        self.assertIsNone(self.result.public_event['monopoly']['trade'])
                        if accept:
                            self.assertEqual(self.s['players'][0]['cash'], assets[0][0]['cash'] - 80)
                            self.assertEqual(self.s['players'][1]['cash'], assets[0][1]['cash'] + 80)
                            self.assertEqual(self.s['tiles'][1]['owner'], 'p1')
                            self.assertEqual(self.s['tiles'][5]['owner'], 'p0')
                        else:
                            self.assertEqual((self.s['players'], self.s['tiles']), assets)
                        self.assertEqual(self.s['turn_player_id'], 'p1')
                        self.assertEqual(self.s['current_player_id'], 'p1')
                        self.assertEqual(self.s['phase'], 'roll')
                        self.play('roll')

    def test_pending_trade_invalid_at_recipient_turn_does_not_block(self):
        for invalid in ('give_cash', 'take_cash', 'owner', 'building', 'mortgage', 'empty'):
            with self.subTest(invalid=invalid):
                self.game(3); self.own([1, 3]); self.s['phase'] = 'manage'
                self.play('propose_trade', to='p1', give_cash=100, take_cash=100,
                          give_tiles=[1], take_tiles=[])
                if invalid == 'give_cash': self.s['players'][0]['cash'] = 99
                if invalid == 'take_cash': self.s['players'][1]['cash'] = 99
                if invalid == 'owner': self.s['tiles'][1]['owner'] = 'p2'
                if invalid == 'building': self.s['tiles'][3]['level'] = 1
                if invalid == 'mortgage': self.s['tiles'][1]['mortgaged'] = True
                if invalid == 'empty':
                    self.s['trade'].update(give_cash=0, take_cash=0, give_tiles=[])
                self.s = json.loads(json.dumps(self.s))
                self.play('end_turn')
                self.assertIsNone(self.s['trade'])
                self.assertIn('交易条件已失效', self.s['last_action_note'])
                public = self.g.public_state(self.s, self.ps)
                self.assertIn('交易条件已失效', public['last_action_note'])
                self.assertEqual(public['turn_player_id'], 'p1')
                self.assertIn('roll', [a['action'] for a in public['legal_actions']])

    def test_response_preserves_recipient_jail_and_authoritative_phase(self):
        for phase in ('roll', 'manage', 'purchase'):
            self.game(3); self.s['phase'] = 'manage'
            self.s['players'][1].update(jailed=True, position=10)
            self.play('propose_trade', to='p1', give_cash=10, take_cash=0, give_tiles=[], take_tiles=[])
            self.play('end_turn')
            self.rejects('pay_bail')
            # A restored normal continuation keeps its authoritative phase.
            self.s['phase'] = phase
            self.play('respond_trade', accept=False)
            self.assertEqual(self.s['phase'], phase)
            self.assertEqual(self.s['turn_player_id'], 'p1')
            self.assertEqual(self.s['current_player_id'], 'p1')
            self.assertTrue(self.s['players'][1]['jailed'])
            if phase == 'roll':
                self.play('pay_bail')
                self.assertEqual(self.s['phase'], 'roll')

    def test_accept_rechecks_assets_atomically(self):
        self.game(); self.own([1]); self.s['phase'] = 'manage'
        self.play('propose_trade', to='p1', give_cash=100, take_cash=10, give_tiles=[1], take_tiles=[])
        self.play('end_turn')
        self.s['tiles'][1]['owner'] = 'p2'
        self.rejects('respond_trade', accept=True)
        self.play('respond_trade', accept=False)
        self.assertEqual(self.s['turn_player_id'], 'p1')

    def test_legacy_trade_can_finish_or_decline_invalid_offer(self):
        for accept, invalid in ((True, False), (False, False), (False, True)):
            self.game(); self.own([1])
            self.s.update(phase='trade', turn_player_id='p1',
                trade=dict(to='p1', **{'from': 'p0'}, give_cash=10, take_cash=0,
                           give_tiles=[1], take_tiles=[]),
                _trade_return=dict(phase='manage', player_id='p0'))
            self.s.pop('_trade_assets'); self.s.pop('_trade_proposed_turn')
            if invalid: self.s['tiles'][1]['owner'] = 'p2'
            self.s = json.loads(json.dumps(self.s))
            self.assertTrue(self.g.public_state(self.s, self.ps)['legal_actions'])
            self.play('respond_trade', accept=accept)
            self.assertIsNone(self.s['trade'])
            self.assertEqual(self.s['phase'], 'manage')
            self.assertEqual(self.s['turn_player_id'], 'p0')

    def test_debt_liquidation_auto_settles_once(self):
        self.game(2, (1, 2)); self.s['players'][0].update(cash=0, position=0)
        self.own([3], 'p1'); self.own([1])
        self.play('roll'); self.assertEqual(self.s['phase'], 'debt')
        self.play('mortgage', tile_id=1)
        self.assertEqual([p['cash'] for p in self.s['players']], [26, 1504])
        self.assertEqual(self.s['phase'], 'manage')
        self.assertFalse(self.s['_charges'])
        self.play('end_turn'); self.assertEqual(self.s['players'][1]['cash'], 1504)

    def test_pending_trade_does_not_interrupt_debt_or_rescue_immediately(self):
        self.game(3, (1, 3)); self.s['players'][0]['cash'] = 10; self.own([39, 5])
        self.play('roll'); self.assertEqual(self.s['phase'], 'debt')
        self.play('propose_trade', to='p1', give_cash=0, take_cash=250, give_tiles=[39], take_tiles=[])
        self.assertEqual(self.s['phase'], 'debt')
        self.assertEqual(self.s['turn_player_id'], 'p0')
        self.play('mortgage', tile_id=5)
        self.assertEqual(self.s['phase'], 'debt')
        self.play('bankrupt')
        self.assertIsNone(self.s['trade'])
        self.assertEqual(self.s['phase'], 'auction')

    def test_bankruptcy_to_creditor_transfers_assets_and_clears_buildings(self):
        self.game(3, (1, 2)); self.s['players'][0]['cash'] = 0
        self.own([3], 'p1'); self.s['tiles'][3]['level'] = 5
        self.own([6, 8, 9]); self.s['tiles'][6]['level'] = 1
        self.own([12]); self.s['tiles'][12]['mortgaged'] = True
        self.play('roll'); self.play('bankrupt')
        self.assertTrue(self.s['players'][0]['bankrupt'])
        self.assertEqual(self.s['players'][1]['cash'], 1525)
        self.assertEqual(self.s['tiles'][6]['level'], 0)
        self.assertEqual(self.s['tiles'][12]['owner'], 'p1')
        self.assertTrue(self.s['tiles'][12]['mortgaged'])
        self.assertEqual(self.s['turn_player_id'], 'p1')
        self.assertEqual(self.result.participant_activity, {'p0': 'eliminated'})

    def test_bankruptcy_to_bank_auctions_all_properties_then_next_turn(self):
        self.game(3, (1, 3)); self.s['players'][0]['cash'] = 0; self.own([1, 3])
        self.play('roll'); self.play('bankrupt')
        self.assertEqual(self.s['phase'], 'auction')
        self.assertEqual(self.s['auction']['tile_id'], 1)
        self.play('bid', amount=10); self.play('pass_bid')
        self.assertEqual(self.s['auction']['tile_id'], 3)
        self.play('pass_bid'); self.play('pass_bid')
        self.assertEqual(self.s['phase'], 'roll')
        self.assertEqual(self.s['turn_player_id'], 'p1')
        self.assertEqual(self.s['tiles'][1]['owner'], 'p1')
        self.assertIsNone(self.s['tiles'][3]['owner'])

    def test_jail_doubles_bail_card_third_roll_and_recovery(self):
        self.game(2, (1, 2, 2, 3, 1, 2))
        p = self.s['players'][0]; p.update(position=10, jailed=True, cash=0)
        self.own([5])
        for _ in range(2):
            self.play('roll'); self.assertEqual(self.s['phase'], 'manage')
            # Return to same jailed player's next turn without unrelated moves.
            self.s['phase'] = 'roll'
        self.play('roll')
        self.assertEqual(self.s['phase'], 'debt')
        self.s = json.loads(json.dumps(self.s))
        self.play('mortgage', tile_id=5)
        self.assertEqual(self.s['players'][0]['position'], 13)
        self.assertFalse(self.s['players'][0]['jailed'])
        self.assertEqual(self.s['players'][0]['cash'], 50)
        self.assertEqual(self.s['phase'], 'purchase')
        self.game(2, (3, 3)); self.s['players'][0].update(position=10, jailed=True)
        self.play('roll'); self.assertFalse(self.s['extra_roll'])
        self.assertFalse(self.s['players'][0]['jailed'])
        self.game(); self.s['players'][0].update(position=10, jailed=True)
        self.play('pay_bail'); self.assertEqual(self.s['players'][0]['cash'], 1450)
        self.assertEqual(self.s['phase'], 'roll')
        self.s['players'][0].update(jailed=True, jail_cards=['chance'])
        self.s['_decks']['chance'].remove(0)
        self.play('use_jail_card'); self.assertEqual(self.s['_decks']['chance'][-1], 0)

    def test_three_doubles_send_to_jail_and_go_to_jail_no_salary(self):
        self.game(2, (1, 1, 2, 2, 3, 3))
        self.s['_decks']['chest'] = list(range(1, 16)) + [0]
        self.play('roll'); self.play('roll')
        self.play('buy'); self.play('roll')
        self.assertEqual(self.s['players'][0]['position'], 10)
        self.assertTrue(self.s['players'][0]['jailed'])
        self.assertFalse(self.s['extra_roll'])
        self.game(2, (1, 2)); self.s['players'][0]['position'] = 27
        self.play('roll')
        self.assertEqual(self.s['players'][0]['cash'], 1500)
        self.assertTrue(self.s['players'][0]['jailed'])

    def test_every_event_card_persists_and_private_decks_never_project(self):
        for deck, cards in _CARDS.items():
            for i, (kind, text, value) in enumerate(cards):
                with self.subTest(deck=deck, card=i):
                    self.game(4, (1, 2, 3, 4))
                    self.s['players'][0]['position'] = 4 if deck == 'chance' else 14
                    self.s['_decks'][deck] = [i] + [n for n in range(16) if n != i]
                    self.play('roll')
                    public = self.g.public_state(self.s, self.ps)
                    self.assertTrue(all(not k.startswith('_') for k in public))
                    self.assertTrue(all('jail_cards' not in p for p in public['players']))
                    other = self.g.private_state(self.s, self.ps[1], self.ps)
                    self.assertEqual(other['jail_cards'], 0)
                    self.assertEqual(self.s['last_event'], text)
                    self.assertEqual(json.loads(json.dumps(self.s)), self.s)

    def test_birthday_debt_changes_actor_then_resumes_event_owners_turn(self):
        self.game(3, (1, 2)); self.s['players'][0]['position'] = 14
        self.s['players'][1]['cash'] = 0; self.own([1], 'p1')
        self.s['_decks']['chest'] = [12] + [i for i in range(16) if i != 12]
        self.play('roll')
        self.assertEqual(self.s['turn_player_id'], 'p1')
        self.assertEqual(self.s['current_player_id'], 'p0')
        self.play('mortgage', tile_id=1)
        self.assertEqual([p['cash'] for p in self.s['players']], [1520, 20, 1490])
        self.assertEqual(self.s['turn_player_id'], 'p0')
        self.assertEqual(self.s['phase'], 'manage')

    def test_nearest_station_double_rent_and_utility_new_dice(self):
        self.game(2, (1, 2)); self.s['players'][0]['position'] = 4; self.own([15], 'p1')
        self.s['_decks']['chance'] = [7] + [i for i in range(16) if i != 7]
        self.play('roll'); self.assertEqual(self.s['players'][0]['cash'], 1450)
        self.game(2, (1, 2, 3, 4)); self.s['players'][0]['position'] = 4; self.own([12], 'p1')
        self.s['_decks']['chance'] = [5] + [i for i in range(16) if i != 5]
        self.play('roll'); self.assertEqual(self.s['players'][0]['cash'], 1430)
        self.assertEqual(self.s['event_dice'], [3, 4])

    def test_resign_during_trade_auction_and_debt(self):
        for who in ('p0', 'p1'):
            self.game(3); self.own([1])
            self.play('propose_trade', to='p1', give_cash=10, take_cash=0, give_tiles=[], take_tiles=[])
            self.g.apply_resignation(self.s, who, self.ps)
            self.invariants()
            self.assertIsNone(self.s['trade'])
            self.assertNotEqual(self.s['turn_player_id'], who)
        self.game(3, (1, 2)); self.play('roll'); self.play('auction'); self.play('bid', amount=100)
        self.g.apply_resignation(self.s, 'p0', self.ps)
        self.invariants(); self.assertEqual(self.s['auction']['bid'], 0)
        self.play('pass_bid'); self.play('pass_bid')
        self.assertEqual(self.s['phase'], 'roll')
        self.game(3, (1, 3)); self.s['players'][0]['cash'] = 0
        self.play('roll'); self.g.apply_resignation(self.s, 'p0', self.ps)
        self.invariants(); self.assertEqual(self.s['turn_player_id'], 'p1')

    def test_npc_and_temporary_assistance_trade_boundaries(self):
        self.game(); self.own([1]); self.own([3], 'p1')
        actions = self.g.npc_legal_actions(self.s, self.ps[0], self.ps)
        self.assertTrue(any(a['action'] == 'propose_trade' for a in actions))
        real = dict(self.ps[0], participant_kind='bound_machine')
        self.assertFalse(any(a['action'] == 'propose_trade' for a in self.g.npc_legal_actions(self.s, real, self.ps)))
        self.play('propose_trade', to='p1', give_cash=1000, take_cash=0, give_tiles=[], take_tiles=[])
        self.s['phase'] = 'manage'
        self.play('end_turn')
        real = dict(self.ps[1], participant_kind='human')
        self.assertFalse(self.g.choose_local_npc_action(self.s, real, self.ps)['accept'])

    def test_unrelated_resignation_preserves_pending_and_runs_bank_auctions(self):
        self.game(4); self.own([1], 'p2')
        self.play('propose_trade', to='p1', give_cash=10, take_cash=0, give_tiles=[], take_tiles=[])
        self.g.apply_resignation(self.s, 'p2', self.ps)
        self.assertEqual(self.s['phase'], 'auction')
        while self.s['phase'] == 'auction':
            self.assertNotIn('respond_trade', [a['action'] for a in self.g.legal_actions(self.s, self.s['turn_player_id'])])
            self.play('pass_bid')
        self.assertEqual(self.s['phase'], 'roll')
        self.assertEqual(self.s['turn_player_id'], 'p0')
        self.assertIsNotNone(self.s['trade'])

    def test_temporary_debt_actor_cannot_gate_normal_owner_mid_turn(self):
        self.game(3, (1, 2)); self.s['players'][0]['position'] = 14
        self.s['players'][1]['cash'] = 0; self.own([1], 'p1')
        self.s['_decks']['chest'] = [12] + [i for i in range(16) if i != 12]
        self.play('roll')
        self.play('propose_trade', to='p0', give_cash=0, take_cash=10, give_tiles=[], take_tiles=[])
        self.play('mortgage', tile_id=1)
        self.assertEqual(self.s['current_player_id'], 'p0')
        self.assertEqual(self.s['phase'], 'manage')
        self.assertIn('end_turn', [a['action'] for a in self.g.legal_actions(self.s, 'p0')])
        self.play('end_turn')  # Proposer's own normal turn must not clear the offer.
        self.assertEqual(self.s['turn_player_id'], 'p1')
        self.assertIsNotNone(self.s['trade'])
        self.g.rng = Dice(1, 2, 1, 2)
        self.s['players'][1]['position'] = self.s['players'][2]['position'] = 17
        self.play('roll'); self.play('end_turn')
        self.play('roll'); self.play('end_turn')
        self.assertEqual(self.s['turn_player_id'], 'p0')
        self.assertEqual({a['action'] for a in self.g.legal_actions(self.s, 'p0')}, {'respond_trade'})

    def test_complete_seeded_npc_games_and_json_restore_for_2_4_6(self):
        for n in (2, 4, 6):
            for seed in (19, 42):
                with self.subTest(count=n, seed=seed):
                    self.game(n); self.g.rng = random.Random(seed)
                    for step in range(12000):
                        actor = next(p for p in self.ps if p['player_id'] == self.s['turn_player_id'])
                        move = self.g.choose_local_npc_action(self.s, actor, self.ps)
                        self.g.validate_action(self.s, move, actor)
                        self.result = self.g.apply_action(self.s, move, actor)
                        self.s = self.result.state
                        if step % 31 == 0:
                            self.invariants(); self.s = json.loads(json.dumps(self.s))
                        if self.result.result: break
                    else: self.fail(f'NPC game did not finish in safety bound: n={n}, seed={seed}')
                    self.invariants()
                    self.assertEqual(len(self.g.active(self.s)), 1)
                    self.assertEqual(self.result.result['winner_player_id'], self.g.active(self.s)[0])


if __name__ == '__main__':
    unittest.main()
