"""Public drawn-card presentation only; isolated engine and SQLite fixtures."""
import json
import random
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from app import database, framework
from app.games import GAMES
from app.games.monopoly import Monopoly, _CARDS


class CardDice(random.Random):
    def __init__(self):
        super().__init__(91)
        self.roll = 0

    def randint(self, a, b):
        if (a, b) == (1, 6):
            self.roll += 1
            return 3 if self.roll % 2 else 4
        return super().randint(a, b)


def drawn_case(deck, index, participants=None, *, position=None, cash=1500):
    participants = participants or [dict(player_id=f'human:{i}', display_name=f'玩家{i}',
                                        role='human', participant_kind='human', seat_index=i - 1)
                                    for i in range(1, 7)]
    game = Monopoly(CardDice())
    state = game.initialize(participants)
    state['players'][0].update(position=position if position is not None else 0 if deck == 'chance' else 10,
                               cash=cash)
    state['_decks'][deck] = [index] + [n for n in range(16) if n != index]
    if position == 29:
        state['_decks']['chest'] = [2] + [n for n in range(16) if n != 2]
    before = deepcopy(state)
    result = game.apply_action(state, dict(action='roll', action_seq=0), participants[0])
    return game, participants, before, result


def browser_card_cases(participants):
    cases = {}
    for label, deck, index, position in [('chance-money', 'chance', 6, None),
                                       ('chest-money', 'chest', 9, None),
                                       ('chance-fee', 'chance', 2, None),
                                       ('chance-move', 'chance', 10, None),
                                       ('chest-jail-card', 'chest', 0, None),
                                       ('chained-cards', 'chance', 4, 29)]:
        game, members, before, result = drawn_case(deck, index, participants, position=position)
        cases[label] = dict(before=game.public_state(before, members), after=game.public_state(result.state, members))
    return cases


class MonopolyCardEventTests(unittest.TestCase):
    def test_all_32_card_effects_have_only_drawn_public_presentation(self):
        for deck, cards in _CARDS.items():
            for index, (kind, text, _) in enumerate(cards):
                with self.subTest(deck=deck, index=index):
                    game, members, before, result = drawn_case(deck, index)
                    state = result.state
                    event = state['last_card_events'][0]
                    self.assertEqual(set(event), {'event_id', 'action_seq', 'deck', 'player_id', 'text', 'summary'})
                    self.assertEqual(event['event_id'], '1:1')
                    self.assertEqual((event['deck'], event['text'], event['player_id']), (deck, text, 'human:1'))
                    self.assertTrue(event['summary'])
                    public = game.public_state(state, members)
                    self.assertNotIn('_decks', public)
                    self.assertTrue(all('jail_cards' not in p for p in public['players']))
                    self.assertEqual(result.public_event['monopoly']['last_card_events'], public['last_card_events'])
                    self.assertEqual(json.loads(json.dumps(state)), state)
                    self.assertEqual(game.private_state(state, members[1], members)['jail_cards'], 0)
                    if kind == 'jail_card':
                        self.assertEqual(event['summary'], '出狱卡已收入手中')
                        self.assertEqual(game.private_state(state, members[0], members)['jail_cards'], 1)
                        self.assertEqual(len(state['_decks'][deck]), 15)
                    self.assertEqual(before['last_card_events'], [])

    def test_money_move_and_debt_summaries_are_server_authoritative(self):
        for deck, index, expected, cash, position in [('chance', 6, '现金 +50', 1550, 7),
                                                     ('chance', 2, '应付 15', 1485, 7),
                                                     ('chance', 10, '前往银街', 1500, 39),
                                                     ('chest', 9, '现金 +200', 1700, 17)]:
            _, _, _, result = drawn_case(deck, index)
            self.assertEqual(result.state['last_card_events'][0]['summary'], expected)
            self.assertEqual(result.state['players'][0]['cash'], cash)
            self.assertEqual(result.state['players'][0]['position'], position)
        _, _, _, result = drawn_case('chance', 2, cash=0)
        self.assertEqual(result.state['phase'], 'debt')
        self.assertEqual(result.state['players'][0]['cash'], 0)
        self.assertEqual(result.state['last_card_events'][0]['summary'], '应付 15')

    def test_chained_draws_have_distinct_ordered_ids_and_persist_until_next_draw(self):
        game, members, _, result = drawn_case('chance', 4, position=29)
        state = result.state
        events = deepcopy(state['last_card_events'])
        self.assertEqual([e['event_id'] for e in events], ['1:1', '1:2'])
        self.assertEqual([e['deck'] for e in events], ['chance', 'chest'])
        self.assertEqual(events[0]['summary'], '退后3格，抵达公益')
        self.assertEqual(events[1]['summary'], '现金 +50')
        self.assertEqual(state['players'][0]['position'], 33)
        state = game.apply_action(state, {'action': 'end_turn', 'action_seq': 1}, members[0]).state
        self.assertEqual(state['last_card_events'], events)
        state['players'][1]['position'] = 0
        state = game.apply_action(state, {'action': 'roll', 'action_seq': 2}, members[1]).state
        self.assertEqual(state['last_card_events'][0]['event_id'], '3:1')
        self.assertEqual(state['last_card_events'][0]['player_id'], 'human:2')

    def test_legacy_state_without_events_still_draws_and_delta_is_safe(self):
        game, members, state, _ = drawn_case('chest', 0)
        state.pop('last_card_events')
        self.assertEqual(game._public_delta(state)['monopoly']['last_card_events'], [])
        state = game.apply_action(state, {'action': 'roll', 'action_seq': 0}, members[0]).state
        self.assertEqual(state['last_card_events'][0]['event_id'], '1:1')

    def test_room_persistence_and_viewers_share_events_not_hidden_hands(self):
        with tempfile.TemporaryDirectory(prefix='monopoly-card-events-') as directory:
            with patch.object(database, 'DB_PATH', Path(directory) / 'test.db'), patch.dict(GAMES, {'monopoly': Monopoly(CardDice())}):
                database.init_db()
                seats = [dict(player_id='human:1', role='human', participant_kind='human'),
                         dict(player_id='ai:test', role='ai', participant_kind='bound_machine')]
                room = framework.create_room('monopoly', 'human_first', 'human', 'human:1', 'ai:test',
                                             require_confirmations=False, ordered_participants=seats)
                _, _, state, _ = drawn_case('chest', 0, room['participants'])
                with database.write_transaction() as conn:
                    conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?', (json.dumps(state), room['room_id']))
                room = framework.play_move(room['room_id'], 'human', 'human:1', {'action': 'roll', 'action_seq': 0}, expected_revision=room['revision'])
                restored = framework.get_room(room['room_id'])
                first = framework.project_room_for_viewer(restored, 'human:1')
                other = framework.project_room_for_viewer(restored, 'ai:test')
                self.assertEqual(first['board_state']['last_card_events'], other['board_state']['last_card_events'])
                self.assertEqual(first['board_state']['last_card_events'][0]['summary'], '出狱卡已收入手中')
                self.assertEqual(first['private_state']['jail_cards'], 1)
                self.assertEqual(other['private_state']['jail_cards'], 0)
                self.assertNotIn('_decks', first['board_state'])
