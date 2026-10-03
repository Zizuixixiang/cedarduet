"""Ordinary-only compaction: lossless choices and cold full_state recovery."""
import json
import random
import unittest
from copy import deepcopy
from unittest.mock import patch

from app import database, framework, invites
from app.games import GAMES
from app.games.guandan import Guandan
from app.games.mahjong import Mahjong, build_wall
from third_party.rlcard_guandan.engine import GuandanEngine
from tests import test_legacy_full_state as legacy
from tests.guandan_mahjong_compact_support import (
    seats, guandan_moves, mahjong_moves, guandan_scenarios, mahjong_scenarios,
)


class GuandanCoverageTests(unittest.TestCase):
    def verify(self, game, state):
        pid = state.get('turn_player_id') or 'p0'
        players = seats()
        viewer = next(p for p in players if p['player_id'] == pid)
        core = GuandanEngine.legal_actions(state, pid)
        raw = game.private_state(state, viewer, players)
        full = game.mcp_private_state(raw, viewer, players)
        before = deepcopy(full)
        ordinary = game.mcp_turn_private_state(full, {})
        self.assertEqual(full, before)
        spec = full['legal_actions']
        self.assertEqual(set(ordinary['legal_actions']), {'format', 'action_id_prefix', 'options'})
        self.assertEqual(ordinary['legal_actions']['options'], spec['options'])
        self.assertEqual(ordinary['legal_action_count'], len(core))
        index_by_id = {card['id']: i for i, card in enumerate(raw['hand'])}
        rows = [dict(zip(spec['fields'], row)) for row in spec['options']]
        # Rebuild each semantic group independently from the authoritative oracle.
        # The wire decoder itself only uses the real response and recovered fields.
        def signature(action):
            indexes = [index_by_id[c] for c in action['card_ids']]
            p = action.get('pattern') or {}
            return (action['kind'], action.get('pattern_type'), p.get('main_rank'),
                    p.get('size', len(indexes)), sum(raw['hand'][i]['wild'] for i in indexes),
                    raw['hand'][indexes[0]]['suit'] if action.get('pattern_type') == 'straight_flush' else None)
        def row_signature(row):
            return tuple(row[k] for k in ('kind', 'pattern', 'main_rank', 'size', 'wild_count', 'suit'))
        by_signature = {row_signature(row): row for row in rows}
        self.assertEqual(len(by_signature), len(rows))
        self.assertEqual(set(by_signature), {signature(a) for a in core})
        _, options = game._mcp_options(raw['hand'], raw['legal_actions'])
        # Cache only expensive authority lookup/grouping, assert its entire input.
        # This explicitly catches the former raw-hand/wild_count mismatch.
        def cached_options(hand, actions):
            self.assertEqual(hand, raw['hand'])
            self.assertEqual(actions, raw['legal_actions'])
            return spec['action_id_prefix'], options
        aliases = set()
        # Plain callables avoid Mock retaining O(action_count**2) copied inputs.
        with patch.object(GuandanEngine, 'legal_actions', new=lambda state, pid: core), \
             patch.object(Guandan, '_mcp_options', new=staticmethod(cached_options)):
            for move in guandan_moves(ordinary, full):
                self.assertIn(game._canonical_action_id(state, pid, move['action_id']),
                              {a['action_id'] for a in core})
            for action in core:
                row = by_signature[signature(action)]
                indexes = sorted(index_by_id[c] for c in action['card_ids'])
                one = deepcopy(ordinary)
                one['legal_actions']['options'] = [spec['options'][rows.index(row)]]
                move = guandan_moves(one, full, {row['suffix']: indexes})[0]
                self.assertNotIn(move['action_id'], aliases)
                aliases.add(move['action_id'])
                self.assertEqual(game._canonical_action_id(state, pid, move['action_id']), action['action_id'])
        self.assertEqual(len(aliases), len(core))
        # A real unmocked submission for each option family, including wildcard rows.
        for row, move in zip(rows, guandan_moves(ordinary, full)):
            if row['wild_count'] or row['kind'] != 'play':
                game.validate_action(state, move, viewer)
                break
        return len(core)

    def test_every_core_action_has_unique_ordinary_alias_including_large_options(self):
        counts = []
        for seed in (7, 11, 37):
            game = Guandan(random.Random(seed))
            counts.append(self.verify(game, game.initialize(seats())))
        self.assertGreater(max(counts), 2000)

    def test_tribute_return_resistance_wind_upgrade_and_terminal(self):
        game = Guandan(random.Random(17))
        scenarios = guandan_scenarios(game)
        for name, state, _kind in scenarios:
            with self.subTest(stage=name):
                self.verify(game, state)
        states = {n: s for n, s, _ in scenarios}
        self.assertTrue(states['resist_double']['tribute']['countered'])
        self.assertTrue(states['resist_single']['tribute']['countered'])
        self.assertEqual(states['wind_partner']['turn_player_id'], 'p2')
        self.assertEqual(states['deal_upgrade']['team_levels']['A'], '5')
        self.assertEqual(states['match_finished']['phase'], 'finished')


class MahjongEncodingTests(unittest.TestCase):
    def test_all_physical_ids_encode_code_and_readable_labels_survive(self):
        game = Mahjong()
        wall = build_wall()
        self.assertEqual(len({t['id'] for t in wall}), 136)
        for tile in wall:
            code, copy = tile['id'].rsplit('-', 1)
            self.assertEqual(code, tile['code'])
            self.assertIn(copy, ['1', '2', '3', '4'])
            detailed = game._public_tile(tile)
            raw = dict(hand=[detailed], own_melds=[], legal_actions=[],
                       shanten=None, shanten_basis='unavailable', drawn_tile_id=tile['id'])
            compact = game.mcp_turn_private_state(raw, {})
            self.assertEqual(compact['hand'], [[tile['id'], detailed['label']]])
            self.assertEqual(raw['hand'], [detailed])


class ColdRecoveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = legacy.LegacyFullStateTests.asyncSetUp
    asyncTearDown = legacy.LegacyFullStateTests.asyncTearDown
    call = legacy.LegacyFullStateTests.call
    room = legacy.LegacyFullStateTests.room

    def install(self, room, state):
        pid = state.get('turn_player_id')
        status = 'finished' if state['phase'] == 'finished' else 'playing'
        with database.write_transaction() as conn:
            conn.execute('UPDATE rooms SET board_state=?, current_player_id=?, status=?, winner=NULL, winner_player_id=NULL, result_json=NULL, revision=revision+1 WHERE room_id=?',
                         (json.dumps(state), pid, status, room['room_id']))
        return framework.get_room(room['room_id'])

    async def recover(self, room):
        pid = room['current_player_id'] or 'p0'
        game = GAMES[room['game_type']]
        # Earlier bootstraps are deliberately unavailable here. No chooser reads
        # engine state or a format constant while reconstructing from responses.
        with patch('app.main._bootstrap_ai_response', side_effect=AssertionError('bootstrap reused')):
            full = await self.call(room, pid, action='state', full_state=True)
            again = await self.call(room, pid, action='state', full_state=True)
        snapshot = full['snapshot']
        self.assertEqual(snapshot, again['snapshot'])
        self.assertEqual(snapshot['rules_text'], game.rules_text)
        self.assertEqual(snapshot['move_format'], game.mcp_move_format)
        reference = framework.project_mcp_snapshot_for_viewer(room, pid)
        self.assertEqual(snapshot['private_state'], reference['private_state'])
        # full_state can additionally restore canonical public recovery fields.
        for key, value in reference['board_state'].items():
            self.assertEqual(snapshot['board_state'][key], value, key)
        ordinary = await self.call(room, pid, action='state')
        if room['status'] != 'playing':
            return snapshot, []
        private = ordinary['private_state']
        detailed = snapshot['private_state']
        if room['game_type'] == 'guandan':
            for key in ('fields', 'pattern_labels', 'submit', 'coverage'):
                self.assertIn(key, detailed['legal_actions'])
                self.assertNotIn(key, private['legal_actions'])
            self.assertEqual(private['hand'], detailed['hand'])
            moves = guandan_moves(private, detailed)
            self.assertEqual(len(moves), private['option_count'])
        else:
            for tile in detailed['hand']:
                self.assertEqual(set(tile), {'id', 'code', 'label', 'suit', 'rank'})
            self.assertEqual(len(private['own_melds']), len(detailed['own_melds']))
            for meld in detailed['own_melds']:
                self.assertEqual(meld['tile_count'], len(meld['tiles']))
                self.assertTrue(all('code' in tile for tile in meld['tiles']))
            for key in ('drawn_tile_id', 'shanten', 'shanten_basis'):
                self.assertEqual(private[key], detailed[key])
            moves = mahjong_moves(private, snapshot)
        viewer = next(p for p in room['participants'] if p['player_id'] == pid)
        # All dynamic wire options are valid, not just one convenient example.
        core = (GuandanEngine.legal_actions(room['board_state'], pid)
                if room['game_type'] == 'guandan' else None)
        if core is not None:
            with patch.object(GuandanEngine, 'legal_actions', return_value=core):
                for move in moves:
                    game.validate_action(room['board_state'], move, viewer)
        else:
            for move in moves:
                game.validate_action(room['board_state'], move, viewer)
        # Opponents' cold and ordinary views must not inherit the actor's hand.
        other = next(p['player_id'] for p in room['participants'] if p['player_id'] != pid)
        other_full = (await self.call(room, other, action='state', full_state=True))['snapshot']
        self.assertEqual(other_full['private_state']['legal_actions'],
                         framework.project_mcp_snapshot_for_viewer(room, other)['private_state']['legal_actions'])
        self.assertNotIn('private_state', await self.call(room, other, action='state'))
        return snapshot, moves

    async def test_cold_midgame_normal_and_invite_rooms_chat_once_and_no_move_replay(self):
        for name in ('guandan', 'mahjong'):
            for invite in (False, True):
                with self.subTest(game=name, invite=invite), patch.object(GAMES[name], '_rng', random.Random(37)):
                    if invite:
                        room = invites.create_invite(name, 'ai', 'p0', target_player_count=4)
                        for pid in ('p1', 'p2', 'p3'):
                            invites.join_invite(room['invite_code'], 'ai', pid)
                        room = invites.start_invite(room['room_id'], 'ai', 'p0')
                    else:
                        room = self.room(name)
                    for pid in ('p0', 'p1', 'p2', 'p3'):
                        self.assertTrue((await self.call(room, pid, action='state'))['bootstrap'])
                    for _ in range(8):
                        pid = room['current_player_id']
                        snap = framework.project_mcp_snapshot_for_viewer(room, pid)
                        private = snap['private_state']
                        move = (guandan_moves(private, private)[0] if name == 'guandan'
                                else private['legal_actions'][0])
                        await self.call(room, pid, action='move', revision=room['revision'], move=move)
                        room = framework.get_room(room['room_id'])
                    pid = room['current_player_id']
                    sender = next(p for p in ('p0', 'p1', 'p2', 'p3') if p != pid)
                    framework.post_message(room['room_id'], 'ai', sender, '冷恢复前的聊天')
                    full = await self.call(room, pid, action='state', full_state=True)
                    self.assertEqual([e['message'] for e in full.get('events', [])].count('冷恢复前的聊天'), 1)
                    self.assertTrue(all(set(e) == {'name', 'message'} for e in full['events']))
                    self.assertNotIn('events', await self.call(room, pid, action='state', full_state=True))
                    self.assertNotIn('events', await self.call(room, pid, action='state'))
                    _snapshot, moves = await self.recover(room)
                    reply = await self.call(room, pid, action='move', revision=room['revision'], move=moves[0])
                    self.assertTrue(reply['ok'])

    async def test_cold_special_stages_restore_all_omitted_information_and_submit(self):
        for name, factory in [('guandan', guandan_scenarios), ('mahjong', mahjong_scenarios)]:
            game = GAMES[name]
            with patch.object(game, '_rng', random.Random(17)):
                scenarios = factory(game)
                room = self.room(name)
                for pid in ('p0', 'p1', 'p2', 'p3'):
                    await self.call(room, pid, action='state')
                seen = set()
                for stage, state, kind in scenarios:
                    with self.subTest(game=name, stage=stage):
                        room = self.install(room, state)
                        snapshot, moves = await self.recover(room)
                        if not moves:
                            continue
                        if name == 'guandan':
                            rows = [dict(zip(snapshot['private_state']['legal_actions']['fields'], row))
                                    for row in snapshot['private_state']['legal_actions']['options']]
                            index = next((i for i, row in enumerate(rows) if row['kind'] == kind), 0)
                        else:
                            seen.update(a['action_id'].split(':')[0] for a in moves)
                            index = next((i for i, move in enumerate(moves)
                                          if move['action_id'].split(':')[0] == kind), 0)
                        reply = await self.call(room, room['current_player_id'], action='move',
                                                revision=room['revision'], move=moves[index])
                        self.assertTrue(reply['ok'])
                if name == 'mahjong':
                    self.assertTrue({'peng', 'ming_gang', 'chi', 'pass', 'hu', 'added_gang', 'concealed_gang'} <= seen, seen)


if __name__ == '__main__':
    unittest.main()
