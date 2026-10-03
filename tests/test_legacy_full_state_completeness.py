"""Cold mid-game recovery: compare information, not one executable action."""
import random
import unittest
from unittest.mock import patch

from app import framework, full_state
from app.games import GAMES
from tests import test_legacy_full_state as legacy
from tests.full_state_support import LEGACY, choose


# Every field omitted by the previous app/full_state.py, recorded independently
# of production code so restoring only a subset cannot make this audit pass.
PREVIOUS_DELETIONS = {
    'banqi': ('draw_quiet_turns',),
    'blackjack': ('shoe_decks',),
    'texas_holdem': ('initial_stack', 'small_blind', 'big_blind'),
    'gandengyan': ('max_multiplier',),
    'guandan': ('engine', 'engine_version'),
    'yahtzee': ('categories', 'upper_bonus_threshold', 'upper_bonus_score', 'max_rolls'),
    'jungle': ('terrain',),
    'junqi': ('bunkers', 'headquarters', 'rail_lines', 'rules_version'),
    'zhajinhua': ('ante', 'raise_tiers', 'max_blind_unit', 'virtual_budget', 'max_rounds'),
}
PREVIOUS_ROSTER_GAMES = set(PREVIOUS_DELETIONS) | {'doudizhu', 'mahjong', 'uno'}
MAP_FIELDS = {
    'aeroplane_chess': ('path_mappings', 'ring_length', 'home_lane_length', 'finish_route_step'),
    'chinese_checkers': ('nodes', 'camps'),
}


class FullStateCompletenessTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = legacy.LegacyFullStateTests.asyncSetUp
    asyncTearDown = legacy.LegacyFullStateTests.asyncTearDown
    room = legacy.LegacyFullStateTests.room
    call = legacy.LegacyFullStateTests.call

    async def midgame(self, name, steps=None):
        game = GAMES[name]
        if hasattr(game, '_rng'):
            rng = patch.object(game, '_rng', random.Random(37))
            rng.start()
            self.addCleanup(rng.stop)
        room = self.room(name, 4 if name == 'blackjack' else None)
        # Simulate an already claimed bootstrap and then discard the response.
        for player in room['participants']:
            if player['role'] == 'ai':
                self.assertTrue((await self.call(room, player['player_id'], action='state'))['bootstrap'])
        steps = steps if steps is not None else (1 if name == 'blackjack' else 3 if name == 'texas_holdem' else 2 if name == 'tictactoe' else 7)
        for step in range(steps):
            pid = room['current_player_id']
            # Only fixture creation uses moves. No recovery assertion below is
            # satisfied by submitting a first legal action or consulting a guide.
            move = choose(framework.project_mcp_snapshot_for_viewer(room, pid), step)
            actor = next(p for p in room['participants'] if p['player_id'] == pid)
            room = framework.play_move(room['room_id'], actor['role'], pid, move, expected_revision=room['revision'])
        self.assertEqual(room['status'], 'playing')
        self.assertGreater(room['revision'], 0)
        return room

    async def cold_snapshot(self, room, pid='p0'):
        # During recovery no normal state or bootstrap helper may be consulted.
        with patch('app.main._bootstrap_ai_response', side_effect=AssertionError('bootstrap used')):
            first = await self.call(room, pid, action='state', full_state=True)
            second = await self.call(room, pid, action='state', full_state=True)
        self.assertNotIn('bootstrap', first)
        self.assertEqual(first['snapshot'], second['snapshot'])
        return second['snapshot']

    async def test_every_previous_deletion_restored_for_each_viewer_midgame(self):
        self.assertEqual(full_state.STATIC_FIELDS, {}, 'Any new omission needs a tested repeatable query contract')
        for name in sorted(LEGACY):
            with self.subTest(game=name):
                room = await self.midgame(name)
                for player in room['participants']:
                    if player['role'] != 'ai':
                        continue
                    pid = player['player_id']
                    actual = await self.cold_snapshot(room, pid)
                    game = GAMES[name]
                    self.assertEqual(actual['rules_text'], game.rules_text, (name, pid))
                    self.assertEqual(actual['move_format'],
                                     getattr(game, 'mcp_move_format', game.move_format), (name, pid))
                    reference = framework.project_mcp_snapshot_for_viewer(room, pid)
                    canonical = framework.project_room_for_viewer(room, pid)['board_state']
                    # Preserve ALL existing dynamic/public/private data, not just
                    # a selected legal move or the few fields being restored.
                    for key, value in reference['board_state'].items():
                        self.assertEqual(actual['board_state'][key], value, (name, pid, key))
                    self.assertEqual(actual['participants'], reference['participants'])
                    self.assertEqual(actual['private_state'], reference['private_state'])
                    self.assertEqual(actual['current_actor'], reference['current_actor'])
                    for key in PREVIOUS_DELETIONS.get(name, ()):
                        self.assertIn(key, actual['board_state'], (name, key))
                        self.assertEqual(actual['board_state'][key], reference['board_state'][key])
                    for key in MAP_FIELDS.get(name, ()):
                        self.assertEqual(actual['board_state'][key], canonical[key])
                    for roster in actual['participants']:
                        for key in ('role', 'kind', 'handle'):
                            self.assertIn(key, roster)

    async def test_recovery_reads_current_authority_and_prefers_mcp_format(self):
        room = await self.midgame('liars_dice', 1)
        game = GAMES['liars_dice']
        for version in ('first', 'updated'):
            with patch.object(game, 'rules_text', version), \
                 patch.object(game, 'mcp_move_format', {'mcp': version}, create=True):
                snapshot = await self.cold_snapshot(room)
                self.assertEqual(snapshot['rules_text'], game.rules_text)
                self.assertEqual(snapshot['move_format'], game.mcp_move_format)
                self.assertNotEqual(snapshot['move_format'], game.move_format)
        with patch.object(game, 'move_format', {'fallback': 'current'}):
            snapshot = await self.cold_snapshot(room)
            self.assertEqual(snapshot['rules_text'], game.rules_text)
            self.assertEqual(snapshot['move_format'], game.move_format)

    async def test_jungle_board_can_be_interpreted_with_all_terrain(self):
        room = await self.midgame('jungle')
        board = (await self.cold_snapshot(room))['board_state']
        terrain = board['terrain']
        self.assertEqual((len(board['board']), len(board['board'][0])), (9, 7))
        self.assertEqual(terrain['dens_by_owner'], {'O': [0, 3], 'X': [8, 3]})
        self.assertEqual(terrain['traps_by_owner'], {'O': [[0, 2], [0, 4], [1, 3]], 'X': [[7, 3], [8, 2], [8, 4]]})
        self.assertEqual(terrain['water']['rows'], [3, 4, 5])
        self.assertEqual(terrain['water']['cols'], [1, 2, 4, 5])
        self.assertTrue(terrain['coordinates'])
        self.assertTrue(terrain['semantics']['dens_by_owner'])
        self.assertTrue(terrain['semantics']['traps_by_owner'])

    async def test_junqi_own_layout_and_complete_safe_topology_survive_context_loss(self):
        room = await self.midgame('junqi')
        snapshot = await self.cold_snapshot(room)
        board, private = snapshot['board_state'], snapshot['private_state']
        self.assertEqual(board['phase'], 'play')
        self.assertEqual(len(board['board']), 60)
        self.assertEqual(len(board['bunkers']), 10)
        for key in ('bunkers', 'headquarters', 'rail_lines', 'rules_version'):
            self.assertTrue(board[key], key)
            self.assertEqual(board[key], GAMES['junqi'].public_state(room['board_state'], room['participants'])[key])
        own = {square: piece['rank'] for square, piece in room['board_state']['board'].items()
               if piece and piece['color'] == private['camp']}
        self.assertEqual(private['pieces'], own)
        for piece in board['board'].values():
            if piece and piece['color'] != private['camp'] and piece.get('rank') != 11:
                self.assertNotIn('rank', piece)

    async def test_yahtzee_all_categories_and_live_scorecard_recover_together(self):
        room = await self.midgame('yahtzee', 7)
        snapshot = await self.cold_snapshot(room, room['current_player_id'])
        board = snapshot['board_state']
        categories = {c['key']: c for c in board['categories']}
        self.assertEqual(set(categories), {'ones', 'twos', 'threes', 'fours', 'fives', 'sixes',
            'three_of_a_kind', 'four_of_a_kind', 'full_house', 'small_straight', 'large_straight', 'yahtzee', 'chance'})
        self.assertTrue(all(c['label'] and c['section'] in {'upper', 'lower'} for c in categories.values()))
        self.assertEqual((board['upper_bonus_threshold'], board['upper_bonus_score'], board['max_rolls']), (63, 35, 3))
        self.assertEqual(len(board['dice']), 5)
        self.assertTrue(any(board['scorecards'].values()))
        self.assertTrue(board['score_previews'])
        for scorecard in board['scorecards'].values():
            self.assertLessEqual(set(scorecard), set(categories))
        self.assertLessEqual(set(board['score_previews']), set(categories))

    async def test_inherited_map_omissions_restored_without_changing_legal_encoding(self):
        for name in MAP_FIELDS:
            room = await self.midgame(name)
            board = (await self.cold_snapshot(room))['board_state']
            if name == 'chinese_checkers':
                nodes = {node['id']: node for node in board['nodes']}
                self.assertEqual(len(nodes), 121)
                self.assertEqual(len(board['camps']), 6)
                self.assertLessEqual(set(board['pieces']), set(nodes))
                for camp in board['camps'].values():
                    self.assertEqual(len(camp), 10)
                    self.assertLessEqual(set(camp), set(nodes))
                self.assertTrue(all(set(move) <= {'from', 'to', 'kind'} for move in board['legal_moves']))
            else:
                self.assertEqual(board['ring_length'], 52)
                for color in board['color_by_player'].values():
                    self.assertEqual(len(set(board['path_mappings'][color]['ring_indices'])), 52)
                self.assertTrue(board['home_lane_length'])
                self.assertTrue(board['finish_route_step'])
                self.assertIn('legal_actions', board)
                self.assertNotIn('legal_moves', board)
