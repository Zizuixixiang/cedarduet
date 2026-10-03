"""Independent hidden setup, with the shared revision and NPC cursor intact."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Barrier
import unittest
from unittest.mock import patch

from app import framework, invites, main
from app.games.bomb_plane import BombPlane
from app.npc_personas import NpcPersona
from app.npc_scheduler import NpcTurnScheduler
from tests import test_bomb_plane as fixtures

LAYOUT = fixtures.LAYOUT
OTHER_LAYOUT = [LAYOUT[1], LAYOUT[2], {'head': 'H6', 'direction': 'N'}]
MODIFICATIONS = [
    {'action': 'place', **LAYOUT[0]}, {'action': 'set_layout', 'planes': []},
    {'action': 'undo'}, {'action': 'clear'}, {'action': 'shuffle'},
    {'action': 'ready'}, {'action': 'auto_setup'},
]


def play_as(room, pid, move):
    actor = next(p for p in room['participants'] if p['player_id'] == pid)
    return framework.play_move(room['room_id'], actor['role'], pid, move,
                               expected_revision=room['revision'])


class SimultaneousRules(unittest.TestCase):
    def test_every_setup_edit_is_available_off_cursor_and_keeps_cursor_stable(self):
        game = BombPlane()
        ps = fixtures.players()
        state = game.initialize(ps)
        for move in ({'action': 'place', **LAYOUT[0]}, {'action': 'undo'},
                     {'action': 'set_layout', 'planes': LAYOUT}, {'action': 'clear'},
                     {'action': 'shuffle'}, {'action': 'auto_setup'}):
            state = game.apply_action(state, move, ps[1]).state
            self.assertEqual(state['active_player_id'], 'p0')
            self.assertEqual(state['planes']['p0'], [])
            self.assertEqual(state['phase'], 'setup')
        self.assertTrue(state['ready']['p1'])

    def test_waiting_for_second_seat_does_not_offer_setup(self):
        game = BombPlane()
        ps = fixtures.players()[:1]
        state = game.initialize(ps)
        self.assertEqual(game.private_state(state, ps[0], ps)['legal_actions'], [])
        self.assertEqual(game.private_state(state, ps[0], ps)['legal_action_spec'], {})
        self.assertEqual(game.npc_legal_actions(state, ps[0], ps), [])
        self.assertIsNone(game.choose_local_npc_action(state, ps[0], ps))
        with self.assertRaises(ValueError):
            game.apply_action(state, {'action': 'auto_setup'}, ps[0])

    def test_interleaved_layouts_lock_in_either_order_and_original_attack_first(self):
        for first in ('p0', 'p1'):
            for ready_first in ('p0', 'p1'):
                with self.subTest(first=first, ready_first=ready_first):
                    game = BombPlane()
                    ps = fixtures.players()
                    state = game.initialize_for_first_player(ps, first)
                    for index in range(3):
                        for actor in ps:
                            state = game.apply_action(state, {'action': 'place', **LAYOUT[index]}, actor).state
                            self.assertEqual(state['active_player_id'], first)
                            self.assertEqual(state['phase'], 'setup')
                    actor = next(p for p in ps if p['player_id'] == ready_first)
                    other = next(p for p in ps if p != actor)
                    state = game.apply_action(state, {'action': 'ready'}, actor).state
                    before = deepcopy(state)
                    private = game.private_state(state, actor, ps)
                    self.assertEqual(private['legal_actions'], [])
                    self.assertEqual(private['legal_action_spec'], {})
                    self.assertEqual(game.npc_legal_actions(state, actor, ps), [])
                    self.assertIsNone(game.choose_local_npc_action(state, actor, ps))
                    for move in MODIFICATIONS:
                        with self.assertRaises(ValueError):
                            game.apply_action(state, move, actor)
                        self.assertEqual(state, before)
                    for move in ({'action': 'clear'}, {'action': 'place', **LAYOUT[0]},
                                 {'action': 'shuffle'}, {'action': 'ready'}):
                        state = game.apply_action(state, move, other).state
                    self.assertEqual(state['phase'], 'play')
                    self.assertEqual(state['active_player_id'], first)
                    self.assertEqual(state['planes'][ready_first], LAYOUT)
                    off_turn = next(p for p in ps if p['player_id'] != first)
                    self.assertEqual(game.private_state(state, off_turn, ps)['legal_action_spec'], {})
                    with self.assertRaises(ValueError):
                        game.apply_action(state, {'action': 'attack', 'cell': 'A1'}, off_turn)

    def test_both_private_specs_and_npc_choices_ignore_cursor_without_leaking(self):
        game = BombPlane()
        ps = fixtures.players()
        state = game.initialize(ps)
        state = game.apply_action(state, {'action': 'set_layout', 'planes': LAYOUT}, ps[0]).state
        state = game.apply_action(state, {'action': 'set_layout', 'planes': OTHER_LAYOUT}, ps[1]).state
        public = game.public_state(state, ps)
        self.assertNotIn('planes', public)
        self.assertNotIn('revealed_planes', public)
        for actor in ps:
            private = game.private_state(state, actor, ps)
            self.assertEqual(private['planes'], state['planes'][actor['player_id']])
            self.assertEqual(set(private['legal_action_spec']), {'place', 'set_layout'})
            self.assertEqual({a['action'] for a in private['legal_actions']},
                             {'clear', 'undo', 'shuffle', 'ready', 'auto_setup'})
            self.assertEqual(game.npc_legal_actions(state, actor, ps), [{'action': 'auto_setup'}])
            self.assertEqual(game.choose_local_npc_action(state, actor, ps), {'action': 'auto_setup'})
            altered = deepcopy(state)
            enemy = next(p['player_id'] for p in ps if p != actor)
            altered['planes'][enemy] = []
            self.assertEqual(game.private_state(altered, actor, ps), private)
            self.assertEqual(game.public_state(altered, ps), public)
        self.assertEqual(game.private_state(state, {'player_id': 'outsider'}, ps), {})


class SimultaneousRooms(unittest.TestCase):
    setUp = fixtures.BombPlaneRooms.setUp
    db_snapshot = fixtures.BombPlaneRooms.db_snapshot

    def test_ordinary_and_invite_interleaving_either_ready_order(self):
        for kind in ('ordinary', 'invite'):
            for role in ('human', 'ai'):
                for ready_index in (0, 1):
                    with self.subTest(kind=kind, role=role, ready_index=ready_index):
                        if kind == 'ordinary':
                            room = framework.create_room('bomb_plane', 'human_first', 'human', 'p0',
                                ordered_participants=fixtures.players('human' if role == 'human' else 'bound_machine'))
                        else:
                            room = invites.create_invite('bomb_plane', role, 'owner', target_player_count=2)
                            invites.join_invite(room['invite_code'], role, 'friend')
                            room = invites.start_invite(room['room_id'], role, 'owner')
                        first = room['board_state']['first_player_id']
                        ids = [p['player_id'] for p in room['participants']]
                        for index in range(3):
                            for pid in ids:
                                room = play_as(room, pid, {'action': 'place', **LAYOUT[index]})
                                self.assertEqual(room['current_player_id'], first)
                        locked, other = ids[ready_index], ids[1 - ready_index]
                        room = play_as(room, locked, {'action': 'ready'})
                        self.assertEqual(room['current_player_id'], other)
                        before = self.db_snapshot()
                        for move in MODIFICATIONS:
                            with self.assertRaises(framework.DuelError):
                                play_as(room, locked, move)
                            self.assertEqual(self.db_snapshot(), before)
                        for move in ({'action': 'clear'}, {'action': 'place', **LAYOUT[0]},
                                     {'action': 'shuffle'}, {'action': 'ready'}):
                            room = play_as(room, other, move)
                        self.assertEqual(room['board_state']['phase'], 'play')
                        self.assertEqual(room['current_player_id'], first)
                        self.assertEqual(room['board_state']['active_player_id'], first)
                        before = self.db_snapshot()
                        with self.assertRaises(framework.DuelError) as error:
                            play_as(room, next(pid for pid in ids if pid != first), {'action': 'attack', 'cell': 'A1'})
                        self.assertEqual(error.exception.status_code, 409)
                        self.assertEqual(self.db_snapshot(), before)
                        framework.resign(room['room_id'], room['participants'][0]['role'], ids[0])

    def test_same_revision_opposite_writers_conflict_then_retry_preserves_both(self):
        for kind in ('ordinary', 'invite'):
            with self.subTest(kind=kind):
                if kind == 'ordinary':
                    room = framework.create_room('bomb_plane', 'human_first', 'human', 'p0',
                                                 ordered_participants=fixtures.players())
                else:
                    room = invites.create_invite('bomb_plane', 'human', 'p0', target_player_count=2)
                    invites.join_invite(room['invite_code'], 'ai', 'p1')
                    room = invites.start_invite(room['room_id'], 'human', 'p0')
                barrier = Barrier(2)
                layouts = {'p0': LAYOUT, 'p1': OTHER_LAYOUT}
                def attempt(pid):
                    barrier.wait(timeout=5)
                    try:
                        play_as(room, pid, {'action': 'set_layout', 'planes': layouts[pid]})
                        return pid, 200
                    except framework.DuelError as error:
                        return pid, error.status_code
                with ThreadPoolExecutor(2) as pool:
                    outcomes = dict(pool.map(attempt, layouts))
                self.assertEqual(sorted(outcomes.values()), [200, 409])
                latest = framework.get_room(room['room_id'])
                self.assertEqual(latest['revision'], room['revision'] + 1)
                loser = next(pid for pid, code in outcomes.items() if code == 409)
                self.assertEqual(latest['board_state']['planes'][loser], [])
                latest = play_as(latest, loser, {'action': 'set_layout', 'planes': layouts[loser]})
                self.assertEqual(latest['revision'], room['revision'] + 2)
                self.assertEqual(latest['board_state']['planes'], layouts)
                for pid in layouts:
                    view = framework.project_room_for_viewer(latest, pid)
                    self.assertEqual(view['private_state']['planes'], layouts[pid])
                    self.assertNotIn('planes', view['board_state'])
                    self.assertNotIn('revealed_planes', view['board_state'])
                    timeline = framework.list_timeline(room['room_id'], 100, pid)
                    for event in timeline:
                        if (event.get('move') or {}).get('action') == 'set_layout':
                            self.assertEqual(event['sender']['player_id'], pid)
                    latest = play_as(latest, pid, {'action': 'ready'})
                self.assertEqual(latest['board_state']['phase'], 'play')
                framework.resign(latest['room_id'], 'human', 'p0')

    def test_missing_revision_and_other_game_out_of_turn_still_rejected(self):
        for game in ('bomb_plane', 'gomoku'):
            room = framework.create_room(game, 'human_first', 'human', 'p0',
                                         ordered_participants=fixtures.players())
            before = self.db_snapshot()
            with self.assertRaises(framework.DuelError) as error:
                framework.play_move(room['room_id'], 'ai', 'p1',
                    {'action': 'shuffle'} if game == 'bomb_plane' else {'row': 0, 'col': 0},
                    expected_revision=None if game == 'bomb_plane' else room['revision'])
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(self.db_snapshot(), before)
            framework.resign(room['room_id'], 'human', 'p0')

    def test_existing_scheduler_handles_both_first_players_and_concurrent_human_edit(self):
        for mode in ('human_first', 'ai_first'):
            with self.subTest(mode=mode):
                room = framework.create_room('bomb_plane', mode, 'human', 'p0',
                                             ordered_participants=fixtures.players('system_npc'))
                first = room['board_state']['first_player_id']
                if first == 'p0':
                    room = play_as(room, 'p0', {'action': 'auto_setup'})
                async def run_scheduler():
                    changed = asyncio.Event()
                    async def human_edit_during_npc_delay(_delay):
                        current = framework.get_room(room['room_id'])
                        if not current['board_state']['ready']['p0']:
                            edited = play_as(current, 'p0', {'action': 'place', **LAYOUT[0]})
                            self.assertEqual(edited['current_player_id'], 'p1')
                    scheduler = NpcTurnScheduler(room_changed=lambda _: changed.set(),
                                                 action_sleeper=human_edit_during_npc_delay)
                    try:
                        await scheduler.start()  # Discovers the persisted NPC cursor.
                        await asyncio.wait_for(changed.wait(), timeout=5)
                    finally:
                        await scheduler.shutdown()
                with patch('app.npc_controller._finish_npc_action', return_value=None), \
                     patch('app.npc_controller.get_persona', return_value=NpcPersona('test-npc', '测试', '公开反馈')), \
                     patch('app.npc_controller.get_npc_provider', side_effect=AssertionError('no model')):
                    asyncio.run(run_scheduler())
                room = framework.get_room(room['room_id'])
                self.assertTrue(room['board_state']['ready']['p1'])
                self.assertEqual(len(room['board_state']['planes']['p1']), 3)
                self.assertEqual(room['current_player_id'], 'p0')
                if first == 'p1':
                    self.assertEqual(room['board_state']['phase'], 'setup')
                    self.assertEqual(room['board_state']['planes']['p0'], [LAYOUT[0]])
                    room = play_as(room, 'p0', {'action': 'shuffle'})
                    room = play_as(room, 'p0', {'action': 'ready'})
                self.assertEqual(room['board_state']['phase'], 'play')
                self.assertEqual(room['current_player_id'], first)
                framework.resign(room['room_id'], 'human', 'p0')


class SimultaneousTransport(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.BombPlaneTransport.asyncSetUp
    asyncTearDown = fixtures.BombPlaneTransport.asyncTearDown

    async def test_bound_machine_and_invited_machine_do_not_wait_for_setup_cursor(self):
        for kind in ('ordinary', 'invite'):
            with self.subTest(kind=kind):
                if kind == 'ordinary':
                    room = framework.create_room('bomb_plane', 'human_first', 'human', 'p0',
                                                 ordered_participants=fixtures.players())
                else:
                    room = invites.create_invite('bomb_plane', 'ai', 'p0', target_player_count=2)
                    invites.join_invite(room['invite_code'], 'ai', 'p1')
                    room = invites.start_invite(room['room_id'], 'ai', 'p0')
                pid = next(p['player_id'] for p in room['participants'] if p['player_id'] != room['current_player_id'])
                # Ordinary p1 is the authenticated bound machine; invite seats are both AI.
                actor = next(p for p in room['participants'] if p['player_id'] == pid)
                self.assertEqual(actor['role'], 'ai')
                framework.claim_mcp_bootstrap(room['room_id'], pid)
                async def call(**fields):
                    response = await self.client.post('/mcp/play', json={
                        'room_id': room['room_id'], 'player_id': pid, **fields})
                    self.assertEqual(response.status_code, 200, response.text)
                    return response.json()
                with patch.object(main, 'wait_for_revision', side_effect=AssertionError('unready player must not wait')):
                    state = await call(action='state', wait=True)
                    self.assertEqual(state['setup'], 'open')
                    self.assertIn('place', state['room']['private_state']['legal_action_spec'])
                    moved = await call(action='move', wait=True, revision=room['revision'],
                                       move={'action': 'set_layout', 'planes': LAYOUT})
                    self.assertEqual(moved['setup'], 'open')
                    self.assertEqual(moved['private']['planes'], LAYOUT)
                locked = await call(action='move', revision=moved['r'], move={'action': 'ready'})
                self.assertEqual(locked['setup'], 'locked')
                raw = framework.get_room(room['room_id'])
                self.assertFalse(main._participant_response_due(raw, pid))
                self.assertTrue(main._participant_response_due(raw, raw['current_player_id']))
                snapshot = (await call(action='state', full_state=True))['snapshot']
                self.assertEqual(snapshot['private_state'], {'planes': LAYOUT})
                self.assertTrue(snapshot['board_state']['ready'][pid])
                self.assertNotIn('planes', snapshot['board_state'])
                other = raw['current_player_id']
                view = framework.project_mcp_room_for_viewer(raw, other)
                self.assertNotIn('"head": "C1"', json.dumps(view))
                raw = play_as(raw, other, {'action': 'auto_setup'})
                denied = await self.client.post('/mcp/play', json={
                    'room_id': raw['room_id'], 'player_id': pid, 'action': 'move',
                    'revision': raw['revision'], 'move': {'action': 'attack', 'cell': 'A1'}})
                self.assertEqual(denied.status_code, 409)
                framework.resign(raw['room_id'], actor['role'], pid)

    async def test_web_noncurrent_human_can_place_with_private_projection(self):
        room = framework.create_room('bomb_plane', 'ai_first', 'human', 'p0',
                                     ordered_participants=fixtures.players())
        self.assertEqual(room['current_player_id'], 'p1')
        response = await self.client.post('/api/rooms/' + room['room_id'] + '/move',
            headers={'X-Duel-Human-Player': 'p0'},
            json={'player_id': 'p0', 'revision': room['revision'], 'move': {'action': 'place', **LAYOUT[0]}})
        self.assertEqual(response.status_code, 200, response.text)
        view = response.json()['room']
        self.assertEqual(view['private_state']['planes'], [LAYOUT[0]])
        self.assertIn('place', view['private_state']['legal_action_spec'])
        self.assertEqual(view['current_player_id'], 'p1')
        self.assertNotIn('planes', view['board_state'])


if __name__ == '__main__':
    unittest.main()
