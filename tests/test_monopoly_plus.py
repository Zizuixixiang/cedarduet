"""大富翁·改：规则单元测试（固定骰子与牌序，不打开任何数据库）。"""
import json
import random
import unittest
from copy import deepcopy
from unittest.mock import patch

from app.games import monopoly_plus_editions as editions
from app.games.monopoly_plus import MonopolyPlus, clean_text

EVENT_INDEX = {card[0] if card[0] != 'chat' else 'chat:' + card[1]: i for i, card in enumerate(editions.EVENTS_PLUS)}


class Script(random.Random):
    """randint pops scripted values (any range); shuffle keeps deck order."""

    def __init__(self, *values):
        super().__init__(7)
        self.values = list(values)

    def push(self, *values):
        self.values.extend(values)

    def randint(self, a, b):
        if self.values:
            value = self.values.pop(0)
            assert a <= value <= b, (value, a, b)
            return value
        return super().randint(a, b)

    def shuffle(self, values):
        return None


SPEED = {1: 0, 2: 1, 3: 2, 'bus': 3, 'mr': 5}


class PlusBase(unittest.TestCase):
    def game(self, n=4, start=True):
        self.rng = Script()
        self.g = MonopolyPlus(self.rng)
        self.ps = [dict(player_id=f'p{i}', display_name=f'玩家{i}', token=f'P{i + 1}',
                        participant_kind='human') for i in range(n)]
        self.s = self.g.prepare_opening_state(self.g.initialize(self.ps), 'p0', self.ps)
        if start:
            self.play('choose_edition', edition='classic_plus')
        return self.s

    def actor(self, pid=None):
        pid = pid or self.s['turn_player_id']
        return next(p for p in self.ps if p['player_id'] == pid)

    def play(self, action, pid=None, **params):
        self.result = self.g.apply_action(self.s, dict(action=action, action_seq=self.s['action_seq'], **params),
                                          self.actor(pid))
        self.s = self.result.state
        self.invariants()
        return self.s

    def rejects(self, action, pid=None, **params):
        with self.assertRaises(ValueError):
            self.g.apply_action(self.s, dict(action=action, action_seq=self.s['action_seq'], **params), self.actor(pid))

    def p(self, pid):
        return self.g.player(self.s, pid)

    def roll(self, d1, d2, speed=None):
        values = [d1, d2] + ([SPEED[speed]] if speed is not None else [])
        self.rng.push(*values)
        return self.play('roll')

    def own(self, ids, pid='p0'):
        for i in ids:
            self.s['tiles'][i]['owner'] = pid

    def texts(self):
        return [e['text'] for e in self.s['log']]

    def invariants(self):
        s = self.s
        self.assertEqual(len(s['tiles']), 40)
        self.assertLessEqual(sum(p['jailed'] for p in s['players']), 1)
        for p in s['players']:
            self.assertTrue(type(p['cash']) is int)
            self.assertLessEqual(len(p['items']), 3)
            self.assertTrue(0 <= p['position'] < 40)
        if s['phase'] != 'finished':
            self.assertIn(s['turn_player_id'], self.g.active(s))
            self.assertTrue(self.g.legal_actions(s, s['turn_player_id']))
            self.assertEqual(self.result.next_player_id if hasattr(self, 'result') else s['turn_player_id'],
                             s['turn_player_id'])
        self.assertEqual(json.loads(json.dumps(s)), s)


class EditionTests(PlusBase):
    def test_setup_offers_only_available_editions_and_greys_placeholders(self):
        self.game(start=False)
        self.assertEqual(self.s['phase'], 'setup')
        menu = self.g.public_state(self.s, self.ps)['editions']
        self.assertEqual([e['id'] for e in menu if e['available']], ['classic_plus'])
        self.assertGreaterEqual(len([e for e in menu if not e['available']]), 1)
        self.assertEqual(self.g.legal_actions(self.s, 'p0'),
                         [dict(action='choose_edition', edition='classic_plus', action_seq=0)])
        self.assertEqual(self.g.legal_actions(self.s, 'p1'), [])
        self.rejects('choose_edition', edition='metro')
        self.rejects('roll')
        self.play('choose_edition', edition='classic_plus')
        self.assertEqual(self.s['phase'], 'roll')
        self.assertEqual(self.s['edition_name'], '经典·改')
        tiles = self.s['tiles']
        self.assertEqual((tiles[17]['kind'], tiles[22]['kind'], tiles[20]['kind']), ('event', 'event', 'auction_house'))
        self.assertEqual(self.s['rules'], editions.CLASSIC_PLUS_RULES)
        self.assertEqual(len(self.s['_decks']['event']), len(editions.EVENTS_PLUS))
        self.assertEqual(len(self.s['_bus_pool']), 16)
        self.assertEqual(self.s['_bus_pool'].count('expire'), 3)

    def test_engine_reads_edition_config_switches(self):
        variant = deepcopy(editions.EDITIONS['classic_plus'])
        variant.update(name='测试无速度骰', tiles={})
        variant['rules'] = dict(variant['rules'], speed_die=False, start_cash=2000, bets=False)
        with patch.dict(editions.EDITIONS, {'test_variant': variant}):
            self.game(start=False)
            self.play('choose_edition', edition='test_variant')
            self.assertTrue(all(p['cash'] == 2000 for p in self.s['players']))
            self.assertEqual(self.s['tiles'][20]['kind'], 'parking')
            self.assertEqual(self.g.side_actions(self.s, 'p1'), [])
            self.p('p0')['passed_go'] = True
            self.roll(1, 2)
            self.assertIsNone(self.s['speed'])
            self.assertEqual(self.p('p0')['position'], 3)


class SpeedDieTests(PlusBase):
    def test_no_speed_die_before_first_pass_of_start(self):
        self.game()
        self.roll(2, 3)
        self.assertIsNone(self.s['speed'])
        self.assertEqual(self.p('p0')['position'], 5)

    def test_number_faces_add_steps(self):
        for face in (1, 2, 3):
            with self.subTest(face=face):
                self.game()
                self.p('p0')['passed_go'] = True
                self.roll(1, 3, face)
                self.assertEqual(self.s['speed'], face)
                self.assertEqual(self.p('p0')['position'], 4 + face)

    def test_bus_face_gives_ticket_and_moves_white_sum(self):
        self.game()
        self.p('p0')['passed_go'] = True
        self.s['_bus_pool'] = ['ticket'] * 13 + ['expire'] * 3
        self.roll(1, 3, 'bus')
        self.assertEqual(self.p('p0')['position'], 4)
        self.assertEqual(self.p('p0')['bus'], 1)
        self.assertEqual(len(self.s['_bus_pool']), 15)

    def test_bus_ticket_replaces_roll_within_current_side_and_refunds_bets(self):
        self.game()
        self.s['tiles'][12]['owner'] = 'p3'
        self.p('p0').update(bus=1, position=11)
        self.assertEqual(MonopolyPlus.bus_targets(11), list(range(12, 21)))
        self.assertEqual(MonopolyPlus.bus_targets(35), [36, 37, 38, 39, 0])
        self.play('bet', pid='p1', choice='big')
        self.assertEqual(self.p('p1')['cash'], 1450)
        self.rejects('use_bus', tile_id=21)
        self.play('use_bus', tile_id=19)
        self.assertEqual(self.p('p0')['position'], 19)
        self.assertEqual(self.p('p0')['bus'], 0)
        self.assertEqual(self.p('p1')['cash'], 1500)
        self.assertEqual(self.s['phase'], 'purchase')
        self.assertFalse(self.s['extra_roll'])

    def test_expire_ticket_voids_every_issued_ticket(self):
        self.game()
        self.p('p1')['bus'] = 2
        self.p('p0').update(passed_go=True, bus=1)
        self.s['_bus_pool'] = ['expire', 'ticket']
        self.roll(1, 3, 'bus')
        self.assertTrue(all(p['bus'] == 0 for p in self.s['players']))
        self.assertIn('作废', ' '.join(self.texts()))

    def test_mr_monopoly_moves_after_normal_landing_is_resolved(self):
        self.game()
        self.p('p0')['passed_go'] = True
        self.own([6, 8], 'p2')
        self.roll(1, 2, 'mr')            # lands on 3 (unowned): decide first
        self.assertEqual(self.s['phase'], 'purchase')
        self.assertEqual(self.p('p0')['position'], 3)
        self.play('buy')                 # then Mr. Monopoly → next unowned (5 东站)
        self.assertEqual(self.p('p0')['position'], 5)
        self.assertEqual(self.s['phase'], 'purchase')

    def test_mr_monopoly_goes_to_next_rent_tile_when_nothing_is_unowned(self):
        self.game()
        for t in self.s['tiles']:
            if t['price']:
                t['owner'] = 'p1'
        self.s['tiles'][3]['owner'] = 'p0'
        self.p('p0')['passed_go'] = True
        self.roll(1, 2, 'mr')
        self.assertEqual(self.p('p0')['position'], 5)
        self.assertLess(self.p('p0')['cash'], 1500)

    def test_triples_choose_any_destination_without_extra_roll(self):
        self.game()
        self.p('p0')['passed_go'] = True
        self.roll(2, 2, 2)
        self.assertEqual(self.s['phase'], 'choice')
        self.assertEqual(self.s['choice']['kind'], 'triple')
        self.assertEqual(len(self.g.legal_actions(self.s, 'p0')), 39)
        self.play('choose_destination', tile_id=39)
        self.assertEqual(self.p('p0')['position'], 39)
        self.assertEqual(self.s['phase'], 'purchase')
        self.assertFalse(self.s['extra_roll'])

    def test_doubles_use_white_dice_only_and_third_jails(self):
        self.game()
        self.p('p0')['passed_go'] = True
        self.roll(3, 3, 1)
        self.assertTrue(self.s['extra_roll'])
        self.s['doubles'] = 2
        self.s['phase'] = 'manage'
        self.roll(4, 4, 2)
        self.assertTrue(self.p('p0')['jailed'])


class JailTests(PlusBase):
    def jail(self, pid='p0'):
        p = self.p(pid)
        self.g._jail(self.s, p)
        self.s['phase'] = 'roll'
        return p

    def test_first_turn_can_pay_or_card_or_try_doubles(self):
        self.game()
        p = self.jail()
        p['jail_cards'] = ['chance']
        actions = {a['action'] for a in self.g.legal_actions(self.s, 'p0')}
        self.assertTrue({'roll', 'pay_bail', 'use_jail_card'} <= actions)
        self.roll(1, 2)
        self.assertTrue(p['jailed'] and self.p('p0')['position'] == 10)
        self.assertEqual(self.p('p0')['cash'], 1500)

    def test_second_turn_forces_the_fine_then_moves(self):
        self.game()
        self.jail()
        self.p('p0')['jail_turns'] = 1
        self.roll(1, 2)
        self.assertFalse(self.p('p0')['jailed'])
        self.assertEqual(self.p('p0')['position'], 13)
        self.assertEqual(self.p('p0')['cash'], 1450)

    def test_new_inmate_pushes_previous_one_to_visiting(self):
        self.game()
        old = self.p('p1')
        self.g._jail(self.s, old)
        self.g._jail(self.s, self.p('p0'))
        self.assertTrue(self.p('p0')['jailed'])
        self.assertFalse(old['jailed'])
        self.assertEqual(old['position'], 10)
        self.assertIn('挤出牢房', ' '.join(self.texts()))

    def test_jailed_owner_still_collects_rent(self):
        self.game()
        self.own([3], 'p1')
        self.g._jail(self.s, self.p('p1'))
        self.s['phase'] = 'roll'
        self.roll(1, 2)
        self.assertEqual(self.p('p1')['cash'], 1504)


class AuctionHouseTests(PlusBase):
    def test_lander_picks_an_unowned_lot_for_everyone_to_bid(self):
        self.game()
        self.p('p0')['position'] = 15
        self.roll(2, 3)
        self.assertEqual(self.s['choice']['kind'], 'pick_auction')
        self.play('pick_auction', tile_id=39)
        self.assertEqual(self.s['phase'], 'auction')
        self.assertEqual(self.s['auction']['tile_id'], 39)
        self.assertEqual(set(self.s['auction']['eligible']), {'p0', 'p1', 'p2', 'p3'})

    def test_skip_returns_to_the_turn(self):
        self.game()
        self.p('p0')['position'] = 15
        self.roll(2, 3)
        self.play('skip_choice')
        self.assertEqual((self.s['phase'], self.s['turn_player_id']), ('manage', 'p0'))


class ItemTests(PlusBase):
    def give(self, pid, *kinds):
        for kind in kinds:
            self.p(pid)['items'].append(dict(kind=kind, deck='chance', card=16))

    def test_items_are_drawn_into_a_private_hand_capped_at_three(self):
        self.game()
        swap = next(i for i, c in enumerate(editions.CHANCE_PLUS) if c == ('item', '道具卡', 'swap'))
        self.s['_decks']['chance'] = [swap] + [i for i in self.s['_decks']['chance'] if i != swap]
        self.roll(3, 4)
        self.assertEqual([i['kind'] for i in self.p('p0')['items']], ['swap'])
        public = self.g.public_state(self.s, self.ps)
        self.assertEqual(public['players'][0]['items'], 1)
        self.assertNotIn('swap', json.dumps(public['last_card_events'], ensure_ascii=False))
        self.assertEqual(self.g.private_state(self.s, self.ps[0], self.ps)['items'], [dict(kind='swap', name='强行交易')])
        self.assertEqual(self.g.private_state(self.s, self.ps[1], self.ps)['items'], [])
        # A fourth item goes back to the bottom of the deck.
        self.give('p0', 'nope', 'nope')
        self.s.update(phase='roll', turn_player_id='p0', rolled_this_turn=False)
        self.p('p0')['position'] = 0
        nope = next(i for i, c in enumerate(editions.CHANCE_PLUS) if c == ('item', '道具卡', 'nope'))
        self.s['_decks']['chance'] = [nope] + [i for i in self.s['_decks']['chance'] if i != nope]
        self.roll(3, 4)
        self.assertEqual(len(self.p('p0')['items']), 3)
        self.assertEqual(self.s['_decks']['chance'][-1], nope)

    def test_swap_respects_set_building_and_mortgage_limits(self):
        self.game()
        self.give('p0', 'swap')
        self.own([1, 3], 'p0')        # brown set: not selectable
        self.own([6], 'p0')
        self.own([8], 'p1')
        self.own([11, 13, 14], 'p2')  # pink full set
        self.own([16], 'p3')
        self.s['tiles'][16]['mortgaged'] = True
        options = self.g.item_options(self.s, 'p0')['swap']
        self.assertEqual(options['mine'], [6])
        self.assertEqual(options['theirs'], [8])
        self.rejects('use_item', item='swap', tile_id=1, target_tile_id=8)
        self.rejects('use_item', item='swap', tile_id=6, target_tile_id=11)
        self.rejects('use_item', item='swap', tile_id=6, target_tile_id=16)
        public = self.g.public_state(self.s, self.ps)
        self.assertFalse(any(a['action'] == 'use_item' for a in public['legal_actions']))
        self.play('use_item', item='swap', tile_id=6, target_tile_id=8)
        # The target always confirms, so hands stay private.
        self.assertEqual((self.s['phase'], self.s['turn_player_id']), ('choice', 'p1'))
        self.assertEqual(self.g.legal_actions(self.s, 'p1'), [dict(action='decline_nope', action_seq=self.s['action_seq'])])
        self.play('decline_nope')
        self.assertEqual((self.s['tiles'][6]['owner'], self.s['tiles'][8]['owner']), ('p1', 'p0'))
        self.assertEqual((self.s['phase'], self.s['turn_player_id']), ('roll', 'p0'))
        self.assertEqual(self.p('p0')['items'], [])

    def test_nope_cancels_a_swap(self):
        self.game()
        self.give('p0', 'swap')
        self.give('p1', 'nope')
        self.own([6], 'p0')
        self.own([8], 'p1')
        self.play('use_item', item='swap', tile_id=6, target_tile_id=8)
        self.play('use_nope')
        self.assertEqual((self.s['tiles'][6]['owner'], self.s['tiles'][8]['owner']), ('p0', 'p1'))
        self.assertEqual(self.p('p1')['items'], [])
        self.assertIn('挡下', ' '.join(self.texts()))

    def test_acquire_only_after_paying_rent_on_an_eligible_lot(self):
        self.game()
        self.give('p0', 'acquire')
        self.own([3], 'p1')
        self.assertFalse(any(a['action'] == 'use_item' for a in self.g.legal_actions(self.s, 'p0')))
        self.roll(1, 2)
        self.assertEqual(self.p('p0')['cash'], 1496)
        self.assertIn(dict(action='use_item', item='acquire', action_seq=self.s['action_seq']),
                      self.g.legal_actions(self.s, 'p0'))
        self.play('use_item', item='acquire')
        self.play('decline_nope')
        self.assertEqual(self.s['tiles'][3]['owner'], 'p0')
        self.assertEqual(self.p('p0')['cash'], 1496 - 120)
        self.assertEqual(self.p('p1')['cash'], 1504 + 120)

    def test_acquire_is_refused_for_sets_and_built_lots(self):
        self.game()
        self.give('p0', 'acquire')
        self.own([1, 3], 'p1')
        self.roll(1, 2)
        self.assertFalse(any(a['action'] == 'use_item' for a in self.g.legal_actions(self.s, 'p0')))

    def test_nope_cancels_a_personal_fine(self):
        self.game()
        self.give('p0', 'nope')
        pay = next(i for i, c in enumerate(editions.CHANCE_PLUS) if c[0] == 'pay')
        self.s['_decks']['chance'] = [pay] + [i for i in self.s['_decks']['chance'] if i != pay]
        self.roll(3, 4)
        self.assertEqual(self.s['choice']['kind'], 'nope_fine')
        self.play('use_nope')
        self.assertEqual(self.p('p0')['cash'], 1500)
        self.assertEqual(self.s['phase'], 'manage')

    def test_declined_fine_is_charged(self):
        self.game()
        self.give('p0', 'nope')
        pay = next(i for i, c in enumerate(editions.CHANCE_PLUS) if c[0] == 'pay')
        self.s['_decks']['chance'] = [pay] + [i for i in self.s['_decks']['chance'] if i != pay]
        self.roll(3, 4)
        self.play('decline_nope')
        self.assertEqual(self.p('p0')['cash'], 1485)
        self.assertEqual(len(self.p('p0')['items']), 1)

    def test_deck_composition_matches_item_counts(self):
        items = [c[2] for c in editions.CHANCE_PLUS + editions.CHEST_PLUS if c[0] == 'item']
        self.assertEqual((items.count('swap'), items.count('acquire'), items.count('nope')), (2, 1, 3))
        self.assertEqual(editions.CHANCE_PLUS[0][0], 'jail_card')
        self.assertEqual(editions.CHEST_PLUS[0][0], 'jail_card')


class LoanTests(PlusBase):
    def test_limit_is_half_of_unmortgaged_estate_value_capped(self):
        self.game()
        self.assertEqual(self.g.loan_limit(self.s, 'p0'), 0)
        self.own([37, 39, 31, 32, 34])
        self.s['tiles'][34]['mortgaged'] = True
        self.assertEqual(self.g.loan_limit(self.s, 'p0'), (350 + 400 + 300 + 300) // 2)
        self.own([26, 27, 29])
        self.assertEqual(self.g.loan_limit(self.s, 'p0'), 1000)
        self.rejects('take_loan', amount=1001)
        self.play('take_loan', amount=600)
        self.assertEqual(self.p('p0')['cash'], 2100)
        self.rejects('take_loan', amount=100)
        self.play('repay_loan', amount=100)
        self.assertEqual(self.p('p0')['loan']['balance'], 500)

    def test_interest_comes_out_of_salary_and_due_after_three_passes(self):
        self.game()
        self.own([1])
        self.s['rules']['speed_die'] = False   # keep the walk exact after passing start
        self.p('p0').update(loan=dict(principal=30, balance=30, laps_left=3), position=38)
        gain = next(i for i, c in enumerate(editions.CHEST_PLUS) if c == ('gain', '比赛奖励10', 10))
        self.s['_decks']['chest'] = [gain] + [i for i in self.s['_decks']['chest'] if i != gain]
        self.roll(1, 3)                 # 38 → 2 passes start once, chest +10
        loan = self.p('p0')['loan']
        self.assertEqual((loan['balance'], loan['laps_left']), (30, 2))
        self.assertEqual(self.p('p0')['cash'], 1500 + 200 - 3 + 10)
        self.assertIn('扣贷款利息3', ' '.join(self.texts()))
        for laps_left in (1, 0):
            self.s.update(phase='roll', rolled_this_turn=False)
            self.p('p0')['position'] = 38
            self.s['_decks']['chest'] = [gain] + [i for i in self.s['_decks']['chest'] if i != gain]
            self.roll(1, 3)
        # Third pass: interest again, then due and repaid in full from cash.
        self.assertIsNone(self.p('p0')['loan'])
        self.assertEqual(self.p('p0')['cash'], 1500 + 3 * (200 - 3 + 10) - 30)
        self.assertIn('已还清', ' '.join(self.texts()))

    def test_due_loan_takes_cash_then_auctions_estates_without_borrower(self):
        self.game()
        self.own([1, 3])
        p = self.p('p0')
        p.update(loan=dict(principal=900, balance=900, laps_left=1), position=36, cash=100)
        self.s['_decks']['chance'] = [i for i, c in enumerate(editions.CHANCE_PLUS) if c[0] == 'gain' and c[2] == 50] + \
            [i for i, c in enumerate(editions.CHANCE_PLUS) if not (c[0] == 'gain' and c[2] == 50)]
        self.roll(3, 2)                 # 36 → 1 passes GO, loan due
        self.assertEqual(self.s['phase'], 'auction')
        self.assertNotIn('p0', self.s['auction']['eligible'])
        self.assertEqual(self.s['auction']['tile_id'], 1)
        balance = self.p('p0')['loan']['balance']
        self.assertEqual(balance, 900 + 90 - 90 - 210)  # interest from salary, cash 210 taken
        self.assertEqual(self.p('p0')['cash'], 0)
        # p1 bids, the others pass: the first lot repays part of the loan.
        self.play('bid', amount=70)
        while self.s['auction'] and self.s['auction']['tile_id'] == 1:
            self.play('pass_bid')
        self.assertEqual(self.s['tiles'][1]['owner'], 'p1')
        self.assertEqual(self.p('p0')['loan']['balance'], balance - 70)
        self.assertEqual(self.s['auction']['tile_id'], 3)
        self.assertNotIn('p0', self.s['auction']['eligible'])
        # Nobody buys the last lot: the remainder becomes a bank debt.
        while self.s['phase'] == 'auction':
            self.play('pass_bid')
        self.assertIsNone(self.s['tiles'][3]['owner'])
        self.assertIsNone(self.p('p0')['loan'])
        self.assertEqual(self.s['phase'], 'debt')
        self.assertEqual(self.s['debt']['amount'], balance - 70)
        # The lander's purchase window closed because the lot was sold.
        self.play('bankrupt')
        self.assertTrue(self.p('p0')['bankrupt'])

    def test_bankruptcy_pays_the_bank_loan_first(self):
        self.game()
        self.own([1, 3], 'p0')
        self.own([39], 'p1')
        self.s['tiles'][39]['level'] = 0
        p = self.p('p0')
        p.update(loan=dict(principal=500, balance=500, laps_left=3), cash=10, position=36)
        self.s['_charges'].append(dict(payer='p0', amount=400, creditor='p1', reason='租金'))
        self.g._drain_charges(self.s)
        self.assertEqual(self.s['phase'], 'debt')
        self.play('bankrupt')
        # Loan unpaid → estates go to the bank auction, not to the creditor.
        self.assertNotEqual(self.s['tiles'][1]['owner'], 'p1')
        self.assertTrue(self.p('p0')['bankrupt'])
        self.assertIsNone(self.p('p0')['loan'])


class BetTests(PlusBase):
    def test_only_non_current_players_before_the_roll_once_each(self):
        self.game()
        self.assertEqual(self.g.side_actions(self.s, 'p0'), [])
        self.assertEqual(len(self.g.side_actions(self.s, 'p1')), 3)
        self.assertTrue(self.g.accepts_out_of_turn_action(self.s, dict(action='bet'), 'p1'))
        self.assertFalse(self.g.accepts_out_of_turn_action(self.s, dict(action='roll'), 'p1'))
        self.play('bet', pid='p1', choice='big')
        self.assertEqual(self.result.next_player_id, 'p0')
        self.assertEqual(self.s['turn_player_id'], 'p0')
        self.rejects('bet', pid='p1', choice='small')
        self.rejects('bet', pid='p0', choice='small')
        self.play('bet', pid='p2', choice='seven')
        self.play('bet', pid='p3', choice='small')
        self.roll(3, 4)
        self.assertEqual(self.p('p1')['cash'], 1450)
        self.assertEqual(self.p('p2')['cash'], 1450 + 200)
        self.assertEqual(self.p('p3')['cash'], 1450)
        self.assertEqual(self.g.side_actions(self.s, 'p1'), [])
        self.rejects('bet', pid='p1', choice='big')

    def test_big_and_small_pay_back_double(self):
        self.game()
        self.play('bet', pid='p1', choice='big')
        self.play('bet', pid='p2', choice='small')
        self.roll(6, 2)
        self.assertEqual(self.p('p1')['cash'], 1550)
        self.assertEqual(self.p('p2')['cash'], 1450)
        self.assertEqual([r['won'] for r in self.s['last_bets']], [True, False])

    def test_bets_settle_only_on_the_first_roll_of_a_turn(self):
        self.game()
        self.play('bet', pid='p1', choice='small')
        self.roll(2, 2)
        self.assertEqual(self.p('p1')['cash'], 1550)
        self.roll(1, 2)
        self.assertEqual(self.p('p1')['cash'], 1550)


class NamingTests(PlusBase):
    def test_sign_shows_on_rent_and_comes_down_on_transfer(self):
        self.game()
        self.own([3], 'p1')
        self.s['turn_player_id'] = self.s['current_player_id'] = 'p1'
        self.play('name_tile', tile_id=3, name='小窝', motto='欢迎光临，记得付钱')
        self.assertEqual((self.s['tiles'][3]['title'], self.s['tiles'][3]['motto']), ('小窝', '欢迎光临，记得付钱'))
        self.rejects('name_tile', tile_id=3, name='一二三四五六七八九', motto='')
        self.rejects('name_tile', tile_id=3, name='好地', motto='来 www.x.com')
        self.rejects('name_tile', tile_id=3, name='傻逼', motto='')
        self.rejects('name_tile', tile_id=1, name='不是我的', motto='')
        self.s['turn_player_id'] = self.s['current_player_id'] = 'p0'
        self.roll(1, 2)
        rent = next(e for e in reversed(self.s['log']) if e['kind'] == 'rent')
        self.assertIn('「小窝」', rent['text'])
        self.assertEqual(rent['motto'], '欢迎光临，记得付钱')
        # A forced swap moves ownership: the sign comes down.
        self.p('p0')['items'].append(dict(kind='swap', deck='chance', card=16))
        self.own([6], 'p0')
        self.play('use_item', item='swap', tile_id=6, target_tile_id=3)
        self.play('decline_nope')
        self.assertEqual(self.s['tiles'][3]['owner'], 'p0')
        self.assertEqual((self.s['tiles'][3]['title'], self.s['tiles'][3]['motto']), ('', ''))

    def test_clean_text(self):
        self.assertEqual(clean_text('  你好   世界 ', 8), '你好 世界')
        for bad in ('', 'a<b', 'http://x', '\x07'):
            with self.assertRaises(ValueError):
                clean_text(bad, 8)


class EventTests(PlusBase):
    def draw(self, key, *dice):
        self.s['_decks']['event'] = [EVENT_INDEX[key]] + [i for i in self.s['_decks']['event'] if i != EVENT_INDEX[key]]
        self.p(self.s['turn_player_id'])['position'] = 12
        self.roll(*(dice or (2, 3)))

    def test_every_event_card_resolves_without_error(self):
        for index, card in enumerate(editions.EVENTS_PLUS):
            with self.subTest(card=card[1]):
                self.game()
                self.own([1, 3, 6], 'p1')
                self.s['tiles'][1]['level'] = 0
                self.s['_decks']['event'] = [index] + [i for i in self.s['_decks']['event'] if i != index]
                self.p('p0')['position'] = 12
                self.roll(2, 3)
                guard = 0
                while self.s['phase'] in ('choice', 'auction', 'debt', 'purchase') and guard < 30:
                    guard += 1
                    actor = self.actor()
                    if self.s['phase'] == 'choice' and self.s['choice']['kind'] == 'chat' and self.s['choice']['stage'] == 'perform':
                        self.play('perform', text='一段测试发言，大家好')
                        continue
                    action = self.g.choose_local_npc_action(self.s, actor, self.ps)
                    self.result = self.g.apply_action(self.s, action, actor)
                    self.s = self.result.state
                self.assertIn(self.s['phase'], ('manage', 'roll'))

    def test_group_rent_doubles_and_expires_after_a_round(self):
        self.game()
        double = editions.EVENTS_PLUS.index(('group_rent', '旺季：随机一组街区本轮租金翻倍', 200))
        self.s['_decks']['event'] = [double] + [i for i in self.s['_decks']['event'] if i != double]
        self.p('p0')['position'] = 12
        self.rng.push(2, 3, 0)            # dice, then the brown group
        self.play('roll')
        self.own([1], 'p1')
        self.assertEqual(self.g.rent(self.s, self.s['tiles'][1]), 4)
        self.assertEqual(self.s['modifiers'][0]['until_turn'], self.s['turn_number'] + 4)
        for _ in range(4):
            self.s['phase'] = 'manage'
            self.play('end_turn')
        self.assertEqual(self.s['modifiers'], [])
        self.assertEqual(self.g.rent(self.s, self.s['tiles'][1]), 2)
        self.assertIn('本轮效果结束', ' '.join(self.texts()))

    def test_road_closure_pushes_the_landing_forward(self):
        self.game()
        self.s['modifiers'].append(dict(kind='road_closed', tile_id=3, text='x', until_turn=99))
        self.roll(1, 2)
        self.assertEqual(self.p('p0')['position'], 4)

    def test_welfare_moves_100_from_richest_to_poorest(self):
        self.game()
        self.p('p2')['cash'] = 3000
        self.p('p3')['cash'] = 200
        self.draw('welfare')
        self.assertEqual((self.p('p2')['cash'], self.p('p3')['cash']), (2900, 300))

    def test_shift_all_moves_everyone_one_tile_without_landing(self):
        self.game()
        self.p('p1')['position'] = 5
        self.draw('shift_all')
        self.assertEqual(self.p('p0')['position'], 18)
        self.assertEqual(self.p('p1')['position'], 6)
        self.assertEqual(self.s['phase'], 'manage')

    def test_gift_card(self):
        self.game()
        self.draw('gift')
        self.assertEqual(self.s['choice']['kind'], 'gift')
        self.rejects('give_gift', to='p1', amount=40)
        self.rejects('give_gift', to='p0', amount=60)
        self.play('give_gift', to='p2', amount=120)
        self.assertEqual((self.p('p0')['cash'], self.p('p2')['cash']), (1380, 1620))

    def test_chat_card_vote_per_yes(self):
        self.game()
        self.draw('chat:讲故事')
        self.assertEqual(self.s['choice']['stage'], 'perform')
        self.assertIn('玩家1', self.s['choice']['prompt'])
        self.rejects('perform', text='')
        self.play('perform', text='从前有座山。山里有座庙。庙里有个大富翁。')
        self.assertEqual(self.s['turn_player_id'], 'p1')
        self.assertFalse(self.result.turn_completed)
        self.play('vote', accept=True)
        self.assertFalse(self.result.turn_completed)
        self.play('vote', accept=False)
        self.assertFalse(self.result.turn_completed)
        self.play('vote', accept=True)
        self.assertFalse(self.result.turn_completed)
        self.assertEqual(self.p('p0')['cash'], 1600)
        self.assertEqual(self.p('p1')['cash'], 1450)
        self.assertEqual(self.p('p2')['cash'], 1500)
        self.assertEqual((self.s['phase'], self.s['turn_player_id']), ('manage', 'p0'))

    def test_chat_majority_and_timeout_assistance_votes_no(self):
        self.game(n=3)
        self.draw('chat:夸夸卡')
        self.play('perform', text='你们俩都是最棒的牌友！')
        takeover = dict(self.actor(), participant_kind='human')
        action = self.g.choose_local_npc_action(self.s, takeover, self.ps)
        self.assertEqual(action['accept'], False)
        self.play('vote', accept=True)
        self.play('vote', accept=False)
        self.assertEqual(self.p('p0')['cash'], 1500)


class NpcBuildingBudgetTests(PlusBase):
    def test_both_monopoly_auctions_complete_only_the_outer_turn(self):
        from app.games.monopoly import Monopoly
        for game_class in (Monopoly, MonopolyPlus):
            with self.subTest(game=game_class.game_type):
                g = game_class(Script())
                ps = [dict(player_id=f'p{i}', display_name=f'P{i}', participant_kind='system_npc') for i in range(4)]
                state = g.prepare_opening_state(g.initialize(ps), 'p0', ps)
                if game_class is MonopolyPlus:
                    result = g.apply_action(state, dict(action='choose_edition', edition='classic_plus', action_seq=0), ps[0])
                    self.assertFalse(result.turn_completed)
                    state = result.state
                state['players'][0]['position'] = 3
                state['phase'] = 'purchase'
                result = g.apply_action(state, dict(action='auction', action_seq=state['action_seq']), ps[0])
                self.assertFalse(result.turn_completed)
                state = result.state
                actors = set()
                while state['phase'] == 'auction':
                    actor = next(p for p in ps if p['player_id'] == state['turn_player_id'])
                    actors.add(actor['player_id'])
                    result = g.apply_action(state, dict(action='pass_bid', action_seq=state['action_seq']), actor)
                    self.assertFalse(result.turn_completed)
                    state = result.state
                self.assertEqual(len(actors), 4)
                self.assertEqual(state['turn_player_id'], 'p0')
                result = g.apply_action(state, dict(action='end_turn', action_seq=state['action_seq']), ps[0])
                self.assertTrue(result.turn_completed)
                self.assertEqual(result.next_player_id, 'p1')

    def test_npc_builds_past_four_until_strategy_cash_reserve(self):
        self.game()
        self.actor()['participant_kind'] = 'system_npc'
        self.s['phase'] = 'manage'
        for tile_id in (1, 3):
            self.s['tiles'][tile_id]['owner'] = 'p0'
        self.p('p0')['cash'] = 500
        builds = 0
        while True:
            action = self.g.choose_local_npc_action(self.s, self.actor(), self.ps)
            if action['action'] != 'build':
                break
            self.play('build', tile_id=action['tile_id'])
            self.assertFalse(self.result.turn_completed)
            builds += 1
        self.assertEqual(builds, 6)
        self.assertEqual(self.p('p0')['cash'], 200)
        self.play('end_turn')
        self.assertTrue(self.result.turn_completed)


class SeededGameTests(unittest.TestCase):
    def test_two_four_six_player_npc_games_finish_and_roundtrip(self):
        for n in (2, 4, 6):
            for seed in range(3):
                with self.subTest(n=n, seed=seed):
                    g = MonopolyPlus(random.Random(seed))
                    ps = [dict(player_id=f'p{i}', display_name=f'P{i}', participant_kind='system_npc') for i in range(n)]
                    s = g.prepare_opening_state(g.initialize(ps), 'p0', ps)
                    for _ in range(30000):
                        if s['phase'] == 'finished':
                            break
                        actor = next(p for p in ps if p['player_id'] == s['turn_player_id'])
                        move = g.choose_local_npc_action(s, actor, ps)
                        self.assertIn(move, g.npc_legal_actions(s, actor, ps))
                        s = json.loads(json.dumps(g.apply_action(s, move, actor).state))
                    self.assertEqual(s['phase'], 'finished')
                    self.assertEqual(len(g.active(s)), 1)


class McpProjectionTests(PlusBase):
    def test_bootstrap_compacts_the_map_and_delta_only_carries_changes(self):
        self.game()
        public = self.g.public_state(self.s, self.ps)
        boot = self.g.mcp_bootstrap_state(public, self.ps[0], self.ps)
        self.assertEqual(len(boot['map']), 40)
        self.assertTrue(all(isinstance(t, str) for t in boot['map']))
        self.assertNotIn('tiles', boot)
        self.assertLess(len(json.dumps(boot, ensure_ascii=False)), len(json.dumps(public, ensure_ascii=False)) / 3)
        self.roll(1, 2)
        delta = self.result.public_event['monopoly_plus_delta']
        self.assertIn('p', delta)
        self.assertEqual(len(delta['p']), 1)
        self.assertNotIn('tiles', delta)
        self.assertLess(len(json.dumps(delta, ensure_ascii=False)), 600)
        snapshot = self.g.mcp_snapshot_state(public, self.ps[0], self.ps)
        self.assertEqual(len(snapshot['tiles']), 40)

    def test_private_hand_never_reaches_other_viewers(self):
        self.game()
        self.p('p1')['items'].append(dict(kind='nope', deck='chest', card=17))
        for viewer in (self.ps[0], self.ps[2]):
            text = json.dumps([self.g.public_state(self.s, self.ps),
                               self.g.private_state(self.s, viewer, self.ps)], ensure_ascii=False)
            self.assertNotIn('nope', text)
            self.assertNotIn('不行', text.replace('「不行」', ''))


if __name__ == '__main__':
    unittest.main()
