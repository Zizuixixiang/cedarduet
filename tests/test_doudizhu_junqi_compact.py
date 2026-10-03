"""Lossless ordinary-turn projections; real authority and temporary SQLite."""
from copy import deepcopy
import json
import re
import unittest

from app import framework
from app.games import GAMES
from app.games.junqi_engine import engine_moves
from tests.doudizhu_junqi_compact_support import (
    doudizhu_cases, junqi_cases, junqi_position, seats,
)
from tests import test_legacy_full_state as legacy


def moves_from_documented_examples(move_format, private):
    """Cold client: JSON templates come ONLY from received move_format text.

    No game module, pattern table, fixture state or canonical action is read.
    The field/selection rules below implement the accompanying prose contract.
    """
    examples = [json.loads(value) for value in re.findall(r'\{[^{}]*\}', move_format)]
    if 'legal_action_ids' in private:
        templates = {e['action_id'].split(':')[0]: e for e in examples if 'action_id' in e}
        for ids in private['legal_action_ids'].values():
            for action_id in ids:
                move = deepcopy(templates[action_id.split(':')[0]])
                move['action_id'] = action_id
                yield move
    elif 'legal_moves' in private:
        template = next(e for e in examples if e.get('action') == 'move')
        for start, end in private['legal_moves']:
            yield dict(template, **{'from': start, 'to': end})
    else:
        yield from deepcopy(private['legal_actions'])


class CompactAuthorityTests(unittest.TestCase):
    def test_doudizhu_every_id_reconstructs_canonical_action_and_validates(self):
        game = GAMES['doudizhu']
        families = set()
        max_actions = 0
        # Read rank order from the public protocol, not engine constants.
        ranks = re.search(r'顺序为 ([^；]+)', game.mcp_move_format).group(1).split(',')
        for name, state in doudizhu_cases(random_count=8):
            with self.subTest(position=name):
                private = game.private_state(state, seats(3)[0], seats(3))
                saved = deepcopy(private)
                compact = game.mcp_turn_private_state(private, game.public_state(state, seats(3)))
                self.assertEqual(private, saved)
                self.assertEqual(compact['hand'], [c['id'] for c in private['hand']])
                self.assertNotIn('legal_actions', compact)
                original = {a['action_id']: a for a in game.legal_actions_for(state, 'p0')}
                ids = [i for group in compact['legal_action_ids'].values() for i in group]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertEqual(set(ids), set(original))
                max_actions = max(max_actions, len(ids))
                reconstructed = {}
                for label, group in compact['legal_action_ids'].items():
                    for action_id in group:
                        kind = action_id.split(':')[0]
                        action = dict(action=kind, action_id=action_id)
                        if kind == 'bid':
                            score = int(action_id.split(':')[1])
                            action.update(score=score, label='不叫' if score == 0 else f'{score} 分')
                        elif kind == 'play':
                            _, pattern, main, cards = action_id.split(':', 3)
                            # Maximal card tokens preserve hyphenated jokers.
                            card_ids = re.findall(r'JOKER-[SB]|[SHCD](?:10|[3-9JQKA2])', cards)
                            self.assertEqual('-'.join(card_ids), cards)
                            action.update(card_ids=card_ids, pattern_type=pattern,
                                          pattern_label=label, main_rank=ranks[int(main)])
                            families.add(pattern)
                        reconstructed[action_id] = action
                self.assertEqual(reconstructed, original)
                for move in moves_from_documented_examples(game.mcp_move_format, compact):
                    game.validate_action(state, move, seats(3)[0])
                # Not a generated action, not accepted even with a valid prefix.
                with self.assertRaises(ValueError):
                    game.validate_action(state, {'action': 'play', 'action_id': 'play:solo:0:missing'}, seats(3)[0])
        self.assertGreater(max_actions, 350)
        self.assertTrue({'bomb', 'rocket', 'solo_chain_5', 'pair_chain_3',
                         'trio_chain_2', 'trio_chain_solo_2', 'trio_chain_pair_2',
                         'four_two_solo', 'four_two_pair', 'trio_solo', 'trio_pair'} <= families)

    def test_junqi_pairs_equal_engine_and_all_validate_with_complete_pieces(self):
        game = GAMES['junqi']
        for name, state in junqi_cases():
            with self.subTest(position=name):
                private = game.private_state(state, seats(2)[0], seats(2))
                saved = deepcopy(private)
                compact = game.mcp_turn_private_state(private, game.public_state(state, seats(2)))
                self.assertEqual(private, saved)
                self.assertEqual(compact['pieces'], private['pieces'])
                self.assertNotIn('legal_actions', compact)
                pairs = [tuple(p) for p in compact['legal_moves']]
                authority = [(a['from'], a['to']) for a in engine_moves(state['board'], 'b')]
                self.assertCountEqual(pairs, authority)
                self.assertEqual(len(pairs), len(set(pairs)))
                for move in moves_from_documented_examples(game.mcp_move_format, compact):
                    game.validate_action(state, move, seats(2)[0])
                if name == 'engineer_turn':
                    self.assertIn(('e2', 'a3'), pairs)
                    self.assertNotIn(('e2', 'a6'), pairs)
                elif name == 'rail_straight':
                    self.assertIn(('e2', 'a2'), pairs)
                    self.assertNotIn(('e2', 'a3'), pairs)
                elif name == 'road_camp_hq':
                    self.assertFalse(any(start in {'b1', 'a1', 'd1'} for start, _ in pairs))
                    self.assertNotIn(('c3', 'b3'), pairs)  # occupied enemy camp
                    self.assertIn(('d3', 'e4'), pairs)  # diagonal camp road
                elif name == 'road_adjacency':
                    self.assertIn(('c4', 'b4'), pairs)
                    self.assertNotIn(('c4', 'a4'), pairs)

    def test_setup_parametric_swaps_remain_exact_and_controls_operable(self):
        game = GAMES['junqi']
        for pid in ('p0', 'p1'):
            state = game.initialize_for_first_player(seats(2), 'p0')
            state['active_player_id'] = pid
            actor = next(p for p in seats(2) if p['player_id'] == pid)
            private = game.mcp_private_state(game.private_state(state, actor, seats(2)), actor, seats(2))
            compact = game.mcp_turn_private_state(private, game.public_state(state, seats(2)))
            self.assertEqual(compact, private)
            spec = compact['legal_action_spec']['swap']
            def allowed(square):
                rank = compact['pieces'][square]
                key = {0: '0_bomb', 10: '10_landmine', 11: '11_flag'}.get(rank, 'other')
                return spec['destinations_by_rank'][key]
            pairs = {(a, b) for a in spec['own_squares'] for b in spec['own_squares']
                     if a != b and b in allowed(a) and a in allowed(b)}
            legal = game.legal_actions_for(state, pid)
            self.assertEqual(pairs, {(a['from'], a['to']) for a in legal if a['action'] == 'swap'})
            self.assertEqual(compact['legal_actions'], [dict(action=a) for a in ('shuffle', 'ready', 'auto_setup')])
            start, end = sorted(pairs)[0]
            for move in compact['legal_actions'] + [dict(action='swap', **{'from': start, 'to': end})]:
                game.validate_action(state, move, actor)
                game.apply_action(deepcopy(state), move, actor)

    def test_no_actions_for_non_actor_or_finished_and_no_private_aliases(self):
        for name, state, count in [('doudizhu', next(doudizhu_cases())[1], 3),
                                   ('junqi', junqi_position(), 2)]:
            game = GAMES[name]
            private = game.private_state(state, seats(count)[1], seats(count))
            compact = game.mcp_turn_private_state(private, game.public_state(state, seats(count)))
            self.assertFalse(list(moves_from_documented_examples(game.mcp_move_format, compact)))
            key = 'hand' if name == 'doudizhu' else 'pieces'
            compact[key].clear()
            self.assertTrue(private[key])
            state['winner_player_id'] = 'p0'
            private = game.private_state(state, seats(count)[0], seats(count))
            compact = game.mcp_turn_private_state(private, game.public_state(state, seats(count)))
            self.assertFalse(list(moves_from_documented_examples(game.mcp_move_format, compact)))


class CompactHttpTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = legacy.LegacyFullStateTests.asyncSetUp
    asyncTearDown = legacy.LegacyFullStateTests.asyncTearDown
    room = legacy.LegacyFullStateTests.room
    call = legacy.LegacyFullStateTests.call
    replace = legacy.LegacyFullStateTests.replace

    async def test_cold_format_plus_one_turn_can_submit_over_http(self):
        fixtures = [('doudizhu', name, state) for name, state in doudizhu_cases(2)]
        fixtures += [('junqi', name, state) for name, state in junqi_cases()]
        for game_name, position, state in fixtures:
            with self.subTest(game=game_name, position=position):
                game = GAMES[game_name]
                room = self.replace(self.room(game_name), state, 'p0')
                boot = await self.call(room, action='state')
                self.assertTrue(boot['bootstrap'])
                self.assertEqual(boot['room']['move_format'], game.mcp_move_format)
                full = (await self.call(room, action='state', full_state=True))['snapshot']
                self.assertEqual(full['rules_text'], game.rules_text)
                self.assertEqual(full['move_format'], boot['room']['move_format'])
                self.assertEqual(full['private_state'], boot['room']['private_state'])
                self.assertIn('legal_actions', full['private_state'])
                turn = await self.call(room, action='state')
                self.assertNotIn('room', turn)
                # The cold client only receives the format and this turn.
                move = list(moves_from_documented_examples(full['move_format'], turn['private_state']))[-1]
                await self.call(room, action='move', move=move, revision=room['revision'])

    async def test_resync_chat_once_old_actions_not_replayed_for_both_games(self):
        for name in ('doudizhu', 'junqi'):
            room = self.room(name)
            await self.call(room, action='state')
            move = {'action': 'bid', 'action_id': 'bid:3'} if name == 'doudizhu' else {'action': 'ready'}
            room = framework.play_move(room['room_id'], 'ai', 'p0', move)
            framework.post_message(room['room_id'], 'ai', 'p1', '恢复前聊天')
            framework.post_message(room['room_id'], 'ai', 'p1', '第二条聊天')
            full = await self.call(room, action='state', full_state=True)
            chats = ['恢复前聊天', '第二条聊天']
            # These games may also emit referee text, and do not echo one's own
            # action annotations. Check delivered peer chat without changing that.
            self.assertEqual([e['message'] for e in full['events'] if e['message'] in chats], chats)
            self.assertTrue(all(set(e) == {'name', 'message'} for e in full['events']))
            self.assertNotIn('events', await self.call(room, action='state', full_state=True))
            self.assertNotIn('events', await self.call(room, action='state'))

    async def test_own_pieces_refresh_after_opponent_capture(self):
        state = junqi_position({'a6': ('b', 8), 'a7': ('r', 7),
                                'b2': ('b', 9), 'b1': ('b', 11), 'b12': ('r', 11)})
        state['active_player_id'] = 'p1'
        room = self.replace(self.room('junqi'), state, 'p1')
        await self.call(room, action='state')
        framework.play_move(room['room_id'], 'ai', 'p1', {'action': 'move', 'from': 'a7', 'to': 'a6'})
        turn = await self.call(room, action='state')
        self.assertEqual(turn['private_state']['pieces'], {'b1': 11, 'b2': 9})
        self.assertNotIn('a6', turn['private_state']['pieces'])
