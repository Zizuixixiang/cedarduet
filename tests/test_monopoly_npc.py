"""Combined Monopoly decisions, using temporary SQLite and mocked providers."""
import asyncio
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database, framework, npc_controller
from app.games import GAMES
from app.npc_providers import (
    CedarToyBridgeNpcProvider, OpenAICompatibleNpcProvider, NpcProvider,
    ProviderDecision, parse_provider_decision,
)
from app.npc_runtime import begin_npc_full_turn, complete_npc_decision, reserve_npc_decision
from tests.test_npc_framework import write_persona
from tests import test_npc_speech as speech_fixtures


class DecisionProvider(NpcProvider):
    name = 'monopoly-test'

    def __init__(self, choose=None, message=None):
        self.choose = choose or (lambda r: r.legal_actions[0]['action'])
        self.message = message
        self.requests = []
        self.speeches = []

    async def decide(self, request):
        self.requests.append(request)
        action = self.choose(request)
        return ProviderDecision(None, self.message, action)

    async def speak(self, request):
        self.speeches.append(request)
        raise AssertionError('Monopoly must never call speech')


class MonopolyNpcTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='duel-monopoly-npc-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        db_patch = patch.object(database, 'DB_PATH', root / 'test.db')
        db_patch.start(); self.addCleanup(db_patch.stop)
        personas = root / 'personas'; personas.mkdir()
        write_persona(personas, 'quiet', '安静测试机', 'quiet persona')
        write_persona(personas, 'bright', '明亮测试机', 'bright persona')
        env_patch = patch.dict('os.environ', {'DUEL_NPC_PERSONAS_DIR': str(personas)})
        env_patch.start(); self.addCleanup(env_patch.stop)
        database.init_db()
        self.addAsyncCleanup(npc_controller.wait_for_npc_speech_tasks)

    def room(self, game_type='monopoly'):
        room = framework.create_room(
            game_type, 'ai_first', 'human', 'human-1', opponent_id='ai-1',
            ordered_participants=speech_fixtures.NpcSpeechCadenceTests.participants(),
            enforce_trusted_pair=True, first_player_id='npc:quiet',
        )
        if game_type == 'monopoly':
            room['board_state']['phase'] = 'manage'
            room = self.persist(room)
        return room

    def persist(self, room):
        with database.write_transaction() as conn:
            conn.execute('UPDATE rooms SET board_state=?, current_player_id=? WHERE room_id=?',
                         (json.dumps(room['board_state']), room['current_player_id'], room['room_id']))
        return framework.get_room(room['room_id'])

    def next_npc_turn(self, room):
        room['current_player_id'] = 'npc:quiet'
        room['board_state'].update(current_player_id='npc:quiet', turn_player_id='npc:quiet',
                                   phase='manage', trades_this_turn=0,
                                   turn_number=room['board_state']['turn_number'] + 1)
        return self.persist(room)

    async def run_npc(self, room, provider):
        result = await npc_controller.run_current_npc_turn(room['room_id'], provider=provider)
        await npc_controller.wait_for_npc_speech_tasks()
        self.assertEqual(result.status, 'applied')
        self.assertEqual(result.room['revision'], room['revision'] + 1)
        self.assertIsNone(result.speech_task)
        return result

    async def test_model_action_and_message_are_used_in_one_call(self):
        room = self.room()
        room['board_state']['tiles'][1]['owner'] = 'npc:quiet'
        room = self.persist(room)
        provider = DecisionProvider(lambda r: dict(action='mortgage', tile_id=1,
                                                   action_seq=r.action_spec['action_seq']), '先留些周转现金。')
        with patch.object(GAMES['monopoly'], 'choose_local_npc_action', side_effect=AssertionError('unexpected fallback')):
            result = await self.run_npc(room, provider)
        self.assertEqual(result.source, provider.name)
        self.assertTrue(result.room['board_state']['tiles'][1]['mortgaged'])
        self.assertEqual(result.message, '先留些周转现金。')
        self.assertNotIn(result.message, json.dumps(result.room['board_state'], ensure_ascii=False))
        self.assertTrue(any(e.get('text') == result.message for e in framework.list_timeline(room['room_id'])))
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(provider.speeches, [])

    async def test_single_choice_and_three_silent_turns_never_call_speech(self):
        room = self.room(); provider = DecisionProvider()
        for _ in range(4):
            self.assertEqual(len(GAMES['monopoly'].legal_actions(room['board_state'], 'npc:quiet')), 1)
            result = await self.run_npc(room, provider)
            self.assertEqual(result.source, provider.name)
            room = self.next_npc_turn(result.room)
        self.assertEqual(len(provider.requests), 4)
        self.assertEqual(provider.speeches, [])
        with database.connect() as conn:
            state = conn.execute('SELECT * FROM npc_speech_states WHERE room_id=? AND npc_player_id=?',
                                 (room['room_id'], 'npc:quiet')).fetchone()
            self.assertEqual(state['silent_completed_turns'], 4)
            self.assertEqual(state['speech_pending'], 0)
            self.assertIsNone(state['last_attempt_revision'])

    async def test_provider_errors_and_illegal_actions_use_local_without_speech(self):
        def raises(error):
            def fail(_): raise error
            return fail
        cases = [raises(RuntimeError('provider failed')), raises(TimeoutError('timeout')),
                 lambda r: parse_provider_decision('not json', action_response=True),
                 lambda r: dict(action='end_turn', action_seq=-1),
                 lambda r: dict(action='use_jail_card', action_seq=r.action_spec['action_seq']),
                 lambda r: dict(action='propose_trade', action_seq=r.action_spec['action_seq'],
                                to='human-1', give_cash=99999, take_cash=0, give_tiles=[], take_tiles=[])]
        for choose in cases:
            with self.subTest(choose=choose):
                room = self.room(); provider = DecisionProvider(choose, '非法动作的发言不能发送')
                game = GAMES['monopoly']
                with patch.object(game, 'choose_local_npc_action', wraps=game.choose_local_npc_action) as local:
                    for _ in range(3):
                        result = await self.run_npc(room, provider)
                        self.assertEqual(result.source, 'fallback')
                        self.assertEqual(result.action['action'], 'end_turn')
                        self.assertIsNone(result.message)
                        room = self.next_npc_turn(result.room)
                    self.assertEqual(local.call_count, 3)
                self.assertEqual(len(provider.requests), 3)  # No decision retry or speech retry.
                self.assertEqual(provider.speeches, [])

    async def test_hanging_provider_has_bounded_timeout_and_fallback(self):
        room = self.room(); provider = DecisionProvider()
        async def hang(request):
            provider.requests.append(request)
            await asyncio.Event().wait()
        provider.decide = hang
        with patch.object(npc_controller, 'MONOPOLY_DECISION_TIMEOUT_SECONDS', .01):
            result = await self.run_npc(room, provider)
        self.assertEqual(result.source, 'fallback')
        self.assertEqual(result.action['action'], 'end_turn')
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(provider.speeches, [])

    async def test_model_constructs_trade_and_receives_complete_safe_context(self):
        room = self.room(); state = room['board_state']
        state['tiles'][1]['owner'] = 'npc:quiet'
        for i in (6, 8): state['tiles'][i]['owner'] = 'human-1'
        state['players'][2]['jail_cards'] = ['chance']
        state['players'][0]['jail_cards'] = ['chest']
        state['_decks']['chance'].remove(0); state['_decks']['chest'].remove(0)
        state['trades'] = [dict(to='npc:bright', **{'from': 'ai-1'}, give_cash=9,
                                take_cash=0, give_tiles=[], take_tiles=[])]
        room = self.persist(room)
        framework.post_message(room['room_id'], 'human', 'human-1', '可以交换地产。')
        framework.post_message(room['room_id'], 'human', 'human-1', '私聊不能泄漏', visible_to_player_ids={'ai-1'})
        action = dict(action='propose_trade', to='human-1', give_cash=137, take_cash=51,
                      give_tiles=[1], take_tiles=[6, 8], action_seq=state['action_seq'])
        provider = DecisionProvider(lambda _: action, '我用这块地加些现金，换你那两块如何？')
        result = await self.run_npc(room, provider)
        self.assertEqual(result.source, provider.name)
        self.assertEqual(result.action, action)
        self.assertEqual(len(result.room['board_state']['trades']), 2)
        request = provider.requests[0]
        self.assertEqual(len(request.public_state['tiles']), 40)
        self.assertEqual(len(request.public_state['players']), 4)
        self.assertEqual(request.public_state['trades'], state['trades'])
        self.assertEqual(request.private_state['jail_cards'], 1)
        self.assertTrue(all('jail_cards' not in p for p in request.public_state['players']))
        for p in request.public_state['players']:
            self.assertTrue({'cash', 'position', 'jailed', 'jail_turns'} <= p.keys())
        for t in request.public_state['tiles']:
            self.assertTrue({'owner', 'level', 'mortgaged'} <= t.keys())
        terms = request.action_spec['parameterized']['propose_trade']
        self.assertEqual(terms['options']['give_tiles'], [1])
        self.assertEqual(terms['options']['take_tiles_by_player']['human-1'], [6, 8])
        self.assertNotIn('npc:bright', terms['options']['partners'])
        self.assertNotIn('propose_trade', [a['action']['action'] for a in request.legal_actions])
        serialized = json.dumps(request.payload(), ensure_ascii=False)
        for hidden in ('_decks', '_charges', '_trade_meta', '私聊不能泄漏'):
            self.assertNotIn(hidden, serialized)
        self.assertIn('可以交换地产', serialized)
        self.assertIn('最后存活者获胜', request.game_rules)
        self.assertIn('均衡建房', request.game_rules)
        self.assertIn('不要求每次发言', request.messages()[0]['content'])
        # The next combined decision sees the actual public offer/result event.
        provider.choose = lambda r: next(a['action'] for a in r.legal_actions if a['action']['action'] == 'end_turn')
        await self.run_npc(result.room, provider)
        self.assertIn('137', json.dumps(provider.requests[-1].recent_public_events))

    async def test_invalid_or_cooling_trade_cannot_bypass_engine(self):
        base = dict(action='propose_trade', to='human-1', give_cash=100, take_cash=0, give_tiles=[], take_tiles=[])
        for reason in ('occupied', 'ownership', 'building', 'bool_cash', 'cooldown'):
            with self.subTest(reason=reason):
                room = self.room(); state = room['board_state']; action = dict(base, action_seq=state['action_seq'])
                offer = {k: v for k, v in action.items() if k not in ('action', 'action_seq')}
                if reason == 'occupied': state['trades'] = [dict(offer, **{'from': 'ai-1'})]
                if reason == 'ownership': action['give_tiles'] = [1]
                if reason == 'building':
                    state['tiles'][1]['owner'] = 'npc:quiet'; state['tiles'][3]['level'] = 1
                    action['give_tiles'] = [1]
                if reason == 'bool_cash': action['give_cash'] = True
                if reason == 'cooldown': state['_trade_rejections'] = [dict(offer, **{'from': 'npc:quiet'}, remaining=3)]
                room = self.persist(room)
                result = await self.run_npc(room, DecisionProvider(lambda _: action))
                self.assertEqual(result.source, 'fallback')
                self.assertNotEqual(result.action['action'], 'propose_trade')

    async def test_recovered_decision_and_stale_reservation_never_charge_again(self):
        for completed in (True, False):
            with self.subTest(completed=completed):
                room = self.room(); provider = DecisionProvider()
                begin_npc_full_turn(room['room_id'], room['revision'], 'npc:quiet')
                with database.write_transaction() as conn:
                    conn.execute('UPDATE npc_speech_states SET silent_completed_turns=2, speech_pending=1 WHERE room_id=?', (room['room_id'],))
                ticket = reserve_npc_decision(room['room_id'], room['revision'], 'npc:quiet')
                if completed:
                    action = dict(action='end_turn', action_seq=room['board_state']['action_seq'])
                    complete_npc_decision(ticket, action, [action])
                else:
                    with database.write_transaction() as conn:
                        conn.execute("UPDATE npc_decisions SET updated_at='2000-01-01T00:00:00+00:00' WHERE room_id=?", (room['room_id'],))
                result = await self.run_npc(room, provider)
                self.assertEqual(result.source, 'recovered' if completed else 'fallback')
                self.assertEqual(provider.requests, [])
                self.assertEqual(provider.speeches, [])

    async def test_http_providers_parse_combined_action_message_only_for_monopoly(self):
        for kind in ('bridge', 'openai'):
            room = self.room(); calls = []
            async def handler(request):
                body = json.loads(request.content); calls.append(body)
                payload = json.loads(body['messages'][1]['content'])
                content = json.dumps(dict(action=payload['legal_actions'][0]['action'], message='轮到你了。'))
                return httpx.Response(200, json={'content': content} if kind == 'bridge' else {'choices': [{'message': {'content': content}}]})
            transport = httpx.MockTransport(handler)
            provider = (CedarToyBridgeNpcProvider(bridge_url='https://npc.test', bridge_token='test', transport=transport)
                        if kind == 'bridge' else OpenAICompatibleNpcProvider(api_base='https://npc.test', api_key='test', model='test', transport=transport))
            result = await self.run_npc(room, provider)
            self.assertEqual(result.source, provider.name)
            self.assertEqual(result.message, '轮到你了。')
            self.assertEqual(len(calls), 1)
        for value in ({'action': {'action': 'roll'}}, {'action_id': 'old', 'message': None},
                      {'action': {}, 'message': 5}, {'action': {}, 'message': 'x' * 201}):
            with self.assertRaises(RuntimeError):
                parse_provider_decision(json.dumps(value), action_response=True)
        with self.assertRaises(RuntimeError):
            parse_provider_decision('{"action":{},"message":null}')

    async def test_rummikub_and_carcassonne_keep_local_actions_and_independent_speech(self):
        for game_type in ('rummikub', 'carcassonne'):
            with self.subTest(game=game_type):
                room = self.room(game_type); game = GAMES[game_type]
                provider = speech_fixtures.NeverDecideProvider(speech_outcomes=['本地策略的桌边话。'])
                actor = next(p for p in room['participants'] if p['player_id'] == 'npc:quiet')
                expected = game.choose_local_npc_action(deepcopy(room['board_state']), actor, room['participants'])
                begin_npc_full_turn(room['room_id'], room['revision'], 'npc:quiet')
                with database.write_transaction() as conn:
                    conn.execute('UPDATE npc_speech_states SET silent_completed_turns=2 WHERE room_id=?', (room['room_id'],))
                first = None
                for _ in range(10):
                    result = await npc_controller.run_current_npc_turn(room['room_id'], provider=provider)
                    first = first or result
                    self.assertEqual(result.source, 'local')
                    if result.room['current_player_id'] != 'npc:quiet': break
                else: self.fail('local turn did not finish')
                await npc_controller.wait_for_npc_speech_tasks()
                self.assertEqual(first.action, expected)
                self.assertEqual(provider.decision_requests, [])
                self.assertEqual(len(provider.speech_requests), 1)
