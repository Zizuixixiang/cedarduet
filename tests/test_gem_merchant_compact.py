"""Response-driven gem MCP reconstruction, action coverage and size contracts."""
from copy import deepcopy
import json
import random
import unittest
from unittest.mock import patch

from app import framework
from app.games import GAMES
from app.games.gem_merchant import GemMerchant, compact_public
from tests.gem_merchant_compact_support import apply_delta, response_actions
from tests import test_gem_merchant as fixtures
from tests.test_gem_merchant import new_game, play, set_board, tokens


def canonical(actions):
    return {json.dumps(a, sort_keys=True) for a in actions}


class GemMerchantCompactTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.GemMerchantIntegrationTests.asyncSetUp
    asyncTearDown = fixtures.GemMerchantIntegrationTests.asyncTearDown

    async def call(self, room, **body):
        response = await self.client.post('/mcp/play', json=dict(
            room_id=room['room_id'], player_id='ai-1', **body))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def consume(self, board, response):
        for event in response.get('events', []):
            delta = event.get('gem_merchant_delta', {})
            for change in delta.get('players', {}).values():
                self.assertNotIn('purchased', change)
                if 'purchased_add' in change:
                    self.assertEqual(len(change['purchased_add']), 1)
            apply_delta(board, delta)

    def check_context(self, game, room, board, private):
        expected = compact_public(game.public_state(room['board_state'], room['participants']))
        for key, value in expected.items():
            self.assertEqual(board[key], value, key)
        if private is None:
            return
        self.assertNotIn('reserved', private)
        self.assertIn('blind_reserved', private)  # [] must clear the last blind card.
        public_reserves = board['players']['ai-1']['reserved']
        recovered = [c for c in public_reserves if not c.startswith('hidden ')] + private['blind_reserved']
        complete = framework.project_mcp_snapshot_for_viewer(room, 'ai-1')['private_state']
        self.assertEqual(sorted(recovered), sorted(complete['reserved']))
        actions = response_actions(board, private)
        self.assertEqual(canonical(actions), canonical(game._legal_actions_for(room['board_state'], 'ai-1')))
        for action in actions:
            game.validate_action(room['board_state'], action, {'player_id': 'ai-1'})
        if 'legal_summary' in private:
            summary = private['legal_summary']
            if 'take' in summary:
                self.assertIs(type(summary['take']), int)
            if 'use_privilege' in summary:
                self.assertIs(summary['use_privilege'], True)

    async def test_complete_asgi_games_reconstruct_every_delta_and_legal_choice(self):
        seen, phases = set(), set()
        for seed in (3, 11, 37, 71):
            rng = random.Random(seed + 1000)
            game = GemMerchant(random.Random(seed))
            with patch.dict(GAMES, gem_merchant=game):
                room = framework.create_room('gem_merchant', 'ai_first', 'ai', 'ai-1', 'human-1',
                                             require_confirmations=False)
                boot = (await self.call(room, action='state'))['room']
                board = deepcopy(boot['board_state'])
                for text in ('blind_reserved', 'legal_summary.take', '"joker_color"', '"gold"', '"cells"'):
                    self.assertIn(text, boot['move_format'])
                for text in ('board_set', 'purchased_add', 'pyramid_set'):
                    self.assertIn(text, board['delta_format'])
                for step in range(1000):
                    if room['status'] != 'playing':
                        break
                    pid = room['current_player_id']
                    legal = game._legal_actions_for(room['board_state'], pid)
                    # Match the sample policy independently of wire ordering.
                    chosen = rng.choice(legal)
                    if pid != 'ai-1':
                        room = framework.play_move(room['room_id'], 'human', pid, chosen,
                                                   expected_revision=room['revision'])
                        continue
                    reply = await self.call(room, action='state')
                    self.consume(board, reply)
                    self.check_context(game, room, board, reply['private_state'])
                    phases.add(room['board_state']['flow']['phase'])
                    actions = response_actions(board, reply['private_state'])
                    # Submit the actual decoded object, not the oracle object.
                    chosen = next(a for a in actions if a == chosen)
                    seen.add(chosen['action'])
                    moved = await self.call(room, action='move', revision=reply['revision'], move=chosen)
                    self.consume(board, moved)
                    room = framework.get_room(room['room_id'])
                    if room['status'] == 'playing':
                        self.check_context(game, room, board, moved.get('private_state'))
                    if step > 30 and step % 19 == 0 and room['status'] == 'playing':
                        # Discard all accumulated board memory and resume from full_state.
                        full = (await self.call(room, action='state', full_state=True))['snapshot']
                        self.assertEqual(full['move_format'], boot['move_format'])
                        self.assertIn('reserved', full['private_state'])
                        self.assertNotIn('blind_reserved', full['private_state'])
                        board = deepcopy(full['board_state'])
                self.assertEqual(room['status'], 'finished')
        self.assertTrue({'take', 'reserve', 'buy', 'use_privilege', 'refill', 'discard',
                         'choose_royal', 'steal', 'take_bonus_gem'} <= seen, seen)
        self.assertIn('resolve', phases)


class GemCompactEdgeTests(unittest.TestCase):
    def test_empty_board_pass_and_blind_list_clear_are_explicit(self):
        game, state = new_game()
        set_board(state, ['.....'] * 5)
        state['bag'] = tokens()
        full = game.mcp_private_state(game.private_state(state, {'player_id': 'p0'}, []), {}, [])
        private = game.mcp_turn_private_state(full, {})
        self.assertEqual(private, {'blind_reserved': [], 'legal_summary': {'pass': True}})
        self.assertEqual(response_actions({'board': ['.....'] * 5}, private), [{'action': 'pass'}])

    def test_small_delta_only_contains_changed_cells_and_appended_cards(self):
        game, state = new_game()
        before = compact_public(game.public_state(state, []))
        cell = next([r, c] for r, row in enumerate(before['board']) for c, gem in enumerate(row) if gem not in '.O')
        event = play(game, state, 'p0', {'action': 'take', 'cells': [cell]}).public_event['gem_merchant_delta']
        self.assertNotIn('board', event)
        self.assertEqual(event['board_set'], [[*cell, '.']])
        apply_delta(before, event)
        self.assertEqual(before, compact_public(game.public_state(state, [])))

    def test_ordinary_projection_keeps_blind_faces_without_mutating_full_state(self):
        game = GemMerchant()
        full = {'reserved': ['#1 L1 white+1 =U2', '#31 L2 blue+1 =W3 blind'],
                'legal_actions': [{'action': 'discard', 'gem': 'red'}, {'action': 'discard', 'gem': 'gold'}]}
        saved = deepcopy(full)
        private = game.mcp_turn_private_state(full, {})
        self.assertEqual(private['blind_reserved'], [full['reserved'][1]])
        self.assertEqual(private['legal_actions'], full['legal_actions'])
        self.assertEqual(full, saved)
