"""Household spectating uses web projections without participating in the game."""
import base64
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import httpx
from app import database, framework, invites, main
from app.games import GAMES


class SpectatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='duel-spectator-')
        self.db = patch.object(database, 'DB_PATH', Path(self.tmp.name) / 'test.db')
        self.db.start()
        database.init_db()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        self.db.stop()
        self.tmp.cleanup()

    def headers(self, ids=('101', '102'), human='human:owner'):
        machines = [{'id': pid, 'name': f'小机{pid}'} for pid in ids]
        return {'X-Duel-Human-Player': human, 'X-Duel-Bound-Ais':
                base64.urlsafe_b64encode(json.dumps(machines).encode()).decode()}

    def create(self, game='uno', ids=('101', '102', '999'), start=True):
        room = invites.create_invite(game, 'ai', ids[0], target_player_count=len(ids), display_name='小紫')
        for pid in ids[1:]:
            role = 'human' if pid.startswith('human:') else 'ai'
            invites.join_invite(room['invite_code'], role, pid, display_name=pid)
        if start:
            room = invites.start_invite(room['room_id'], 'ai', ids[0])
        return room

    def dump(self):
        with closing(database.connect()) as conn:
            return '\n'.join(conn.iterdump())

    async def read(self, room, perspective=None, headers=None, **query):
        if perspective is not None:
            query['perspective'] = perspective
        return await self.client.get('/api/rooms/' + room['room_id'],
            params=query, headers=self.headers() if headers is None else headers)

    async def test_list_deduplicates_sorts_and_excludes_own_and_foreign_rooms(self):
        playing = self.create()
        waiting = self.create(ids=('101', '999'), start=False)
        ended = self.create(ids=('102', '999'))
        framework.resign(ended['room_id'], 'ai', '102')
        self.create(ids=('888', '999'))
        self.create(ids=('101', 'human:owner'))
        before = self.dump()
        result = await self.client.get('/api/spectating/rooms', headers=self.headers())
        self.assertEqual(result.status_code, 200, result.text)
        rooms = result.json()['rooms']
        self.assertEqual([r['room_id'] for r in rooms], [playing['room_id'], waiting['room_id'], ended['room_id']])
        self.assertTrue(all(r['spectating'] for r in rooms))
        self.assertTrue(all('board_state' not in r and 'private_state' not in r for r in rooms))
        self.assertEqual(self.dump(), before)
        empty = await self.client.get('/api/spectating/rooms', headers=self.headers(()))
        self.assertEqual(empty.json()['rooms'], [])

    async def test_all_game_projections_and_special_private_protocols_are_read_only(self):
        # Includes all three four-color protocols, bomb plane, Junqi, Mahjong,
        # dice and every other renderer's public/private state contract.
        for game, plugin in GAMES.items():
            with self.subTest(game=game):
                database.DB_PATH = Path(self.tmp.name) / f'{game}.db'
                database.init_db()
                count = max(2, min(plugin.resolved_allowed_player_counts()))
                ids = ('101', '102') + tuple(str(900 + i) for i in range(count - 2))
                room = self.create(game, ids)
                before = self.dump()
                with patch.object(main, '_schedule_current_system_npc', side_effect=AssertionError('spectator scheduled NPC')):
                    for pid in ('101', '102'):
                        response = await self.read(room, pid, wait=True, after_revision=room['revision'])
                        self.assertEqual(response.status_code, 200, response.text)
                        data = response.json()
                        expected = framework.project_room_for_viewer(room, pid)
                        actual = data['room']
                        self.assertEqual(actual['board_state'], expected['board_state'])
                        self.assertEqual(actual['private_state'], expected['private_state'])
                        self.assertEqual(actual['viewer'], expected['viewer'])
                        self.assertEqual(sorted(p['id'] for p in actual['spectator_choices']), ['101', '102'])
                        self.assertEqual(data['poll_after_ms'], 3000)
                self.assertEqual(self.dump(), before)

    async def test_hidden_hands_and_private_timeline_are_isolated(self):
        room = self.create('liars_dice')
        with database.write_transaction() as conn:
            state = room['board_state']
            state['dice_by_player'].update({'101': [1]*5, '102': [2]*5, '999': [6]*5})
            conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?', (json.dumps(state), room['room_id']))
            for pid in ('101', '102', '999'):
                conn.execute('''INSERT INTO room_messages
                    (room_id,sender,sender_player_id,text,revision_at_send,created_at,visible_to_json)
                    VALUES (?,?,?,?,?,?,?)''',
                    (room['room_id'], 'ai', pid, 'private-' + pid, room['revision'], framework._now(), json.dumps([pid])))
        before = self.dump()
        for pid, dice in (('101', [1]*5), ('102', [2]*5)):
            data = (await self.read(room, pid)).json()
            self.assertEqual(data['room']['private_state']['dice'], dice)
            self.assertNotIn('dice_by_player', data['room']['board_state'])
            private = [e['text'] for e in data['timeline'] if e.get('text', '').startswith('private-')]
            self.assertEqual(private, ['private-' + pid])
        self.assertEqual(self.dump(), before)

    async def test_binding_revocation_and_forged_requests_fail_closed(self):
        room = self.create()
        self.assertEqual((await self.read(room, '101')).status_code, 200)
        for headers, query in [
            (self.headers(()), {'perspective': '101'}),
            (self.headers(('102',)), {'perspective': '101'}),
            (self.headers(), {'perspective': '999'}),
            (self.headers(), {'perspective': '101:6'}),
            (self.headers(), {'player_id': '101'}),
            ({}, {'perspective': '101'}),
        ]:
            with self.subTest(query=query, headers=headers):
                response = await self.read(room, headers=headers, **query)
                self.assertEqual(response.status_code, 403, response.text)
        one = (await self.read(room, headers=self.headers(('102',)))).json()['room']
        self.assertEqual(one['viewer']['player_id'], '102')
        self.assertEqual(len(one['spectator_choices']), 1)

    async def test_spectator_does_not_consume_existing_ai_incremental_context(self):
        room = self.create('four_color_tenhu', ('101', '102', '901', '902'))
        bootstrap = await self.client.post('/mcp/play', json={
            'action': 'state', 'room_id': room['room_id'], 'player_id': '101'})
        self.assertEqual(bootstrap.status_code, 200, bootstrap.text)
        framework.post_message(room['room_id'], 'ai', '102', '@101 未读消息')
        before = self.dump()
        for _ in range(2):
            response = await self.read(room, '101')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers['cache-control'], 'private, no-store')
        self.assertEqual(self.dump(), before)
        self.assertTrue(framework.has_new_room_events(room['room_id'], '101'))

    async def test_departed_machine_is_not_a_spectator_choice(self):
        room = self.create()
        with database.write_transaction() as conn:
            conn.execute("UPDATE room_participants SET join_status='left' WHERE room_id=? AND player_id='101'", (room['room_id'],))
        self.assertEqual((await self.read(room, '101')).status_code, 403)
        response = await self.read(room)
        self.assertEqual(response.json()['room']['spectator_choices'], [{'id': '102', 'name': '小机102'}])

    async def test_account_binding_covers_valid_save_slots_only(self):
        room = self.create(ids=('101:3', '999'))
        response = await self.read(room, '101:3', headers=self.headers(('101',)))
        self.assertEqual(response.status_code, 200, response.text)
        rooms = (await self.client.get('/api/spectating/rooms', headers=self.headers(('101',)))).json()['rooms']
        self.assertEqual([r['room_id'] for r in rooms], [room['room_id']])

    async def test_seated_human_always_uses_original_view_even_when_inactive(self):
        room = self.create(ids=('101', 'human:owner', '999'))
        for active in (1, 0):
            with database.write_transaction() as conn:
                conn.execute('UPDATE room_participants SET active=? WHERE room_id=? AND player_id=?',
                    (active, room['room_id'], 'human:owner'))
            response = await self.read(room, '101')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['room']['viewer']['player_id'], 'human:owner')
            self.assertNotIn('spectating', response.json()['room'])
        with database.write_transaction() as conn:
            conn.execute("UPDATE room_participants SET join_status='left' WHERE room_id=? AND player_id=?",
                (room['room_id'], 'human:owner'))
        self.assertEqual((await self.read(room, '101')).status_code, 403)

    async def test_waiting_terminal_and_expired_rooms_never_run_maintenance(self):
        room = self.create(ids=('101', '102'), start=False)
        self.assertEqual((await self.read(room)).json()['room']['status'], 'waiting')
        room = invites.start_invite(room['room_id'], 'ai', '101')
        room = framework.resign(room['room_id'], 'ai', '101')
        with database.write_transaction() as conn:
            conn.execute("UPDATE rooms SET terminal_at='2020-01-01T00:00:00+00:00', updated_at='2020-01-01T00:00:00+00:00' WHERE room_id=?", (room['room_id'],))
        before = self.dump()
        for _ in range(2):
            response = await self.read(room)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(response.json()['room']['status'], ('finished', 'archived'))
            await self.client.get('/api/spectating/rooms', headers=self.headers())
        self.assertEqual(self.dump(), before)

    async def test_direct_write_cannot_impersonate_a_real_human_seat(self):
        room = self.create(ids=('101', 'human:other'))
        before = self.dump()
        for action, extra in (
            ('move', {'move': {'action': 'draw'}, 'revision': room['revision']}),
            ('messages', {'message': 'forged'}), ('resign', {}), ('join', {}),
        ):
            response = await self.client.post(f"/api/rooms/{room['room_id']}/{action}",
                headers=self.headers(), json={'player_id': 'human:other', **extra})
            self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.dump(), before)

    async def test_spectating_grants_no_write_authority(self):
        room = self.create()
        before = self.dump()
        for action, body in [
            ('move', {'player_id': '101', 'move': {'action': 'draw'}, 'revision': room['revision']}),
            ('move', {'player_id': 'human:owner', 'move': {'action': 'draw'}, 'revision': room['revision']}),
            ('messages', {'player_id': '101', 'message': 'forged'}),
            ('messages', {'player_id': 'human:owner', 'message': 'forged'}),
            ('resign', {'player_id': '101'}), ('resign', {'player_id': 'human:owner'}),
            ('retention', {'preserved': True}), ('delete', {}), ('leave', {}),
            ('start', {}), ('reclaim', {}),
        ]:
            with self.subTest(action=action, body=body):
                response = await self.client.post(f"/api/rooms/{room['room_id']}/{action}", json=body, headers=self.headers())
                self.assertIn(response.status_code, (400, 403, 409), response.text)
        self.assertEqual(self.dump(), before)
