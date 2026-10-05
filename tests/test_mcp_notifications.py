"""Shared notification delivery contract through the real MCP ASGI endpoint."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

import httpx

from app import database, framework, main, mcp_minimal, notifications
from app.games import GAMES
from app.wait_control import WaitControl


class McpNotificationDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='mcp-notification-delivery-')
        self.addCleanup(temporary.cleanup)
        for item in (
            patch.object(database, 'DB_PATH', Path(temporary.name) / 'test.db'),
            patch.object(main, 'revision_events', main.RevisionEvents()),
            patch.object(main, 'wait_control', WaitControl()),
        ):
            item.start()
            self.addCleanup(item.stop)
        database.init_db()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                        base_url='http://notifications.test')
        self.addAsyncCleanup(self.client.aclose)

    def room(self, game='gem_merchant', pid='ai-1'):
        seats = [dict(player_id=pid, role='ai')]
        seats += [dict(player_id=f'{pid}-human-{i}', role='human')
                  for i in range(1, max(2, GAMES[game].min_players))]
        return framework.create_room(game, 'ai_first', 'ai', pid,
                                     ordered_participants=seats, require_confirmations=False)

    def seed(self, category='loan', key='one', pid='ai-1', subject_type='ai'):
        with database.write_transaction() as conn:
            return notifications.create_notification(
                conn, subject_type, pid, category, 'test', key, 'New work', event_key=key)

    async def call(self, room, **body):
        response = await self.client.post('/mcp/play', json={
            'action': 'state', 'room_id': room['room_id'], 'player_id': 'ai-1', **body})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_every_legacy_game_delivers_once_and_full_state_does_not_repeat(self):
        for game in sorted(set(GAMES) - mcp_minimal.GAMES):
            with self.subTest(game=game):
                pid = f'ai-{game}'
                room = self.room(game, pid)
                self.seed(pid=pid)
                first = await self.call(room, player_id=pid)
                self.assertEqual(first['unread']['categories']['loan'], 1)
                for full in (False, True):
                    self.assertNotIn('unread', await self.call(room, player_id=pid, full_state=full))
                self.assertEqual(notifications.unread_summary('ai', pid)['categories']['loan'], 1)

    async def test_new_ids_all_categories_and_same_count_replacement(self):
        room = self.room()
        await self.call(room)
        # Remove any ordinary creation unlocks to isolate the fixture.
        for category in notifications.CATEGORIES:
            notifications.ack_notifications('ai', 'ai-1', category)
        self.assertNotIn('unread', await self.call(room))
        for index, category in enumerate(notifications.CATEGORIES):
            self.seed(category, key=f'new-{index}')
            expected = notifications.unread_summary('ai', 'ai-1')
            first = await self.call(room)
            self.assertEqual(first['unread'], expected)
            self.assertEqual(first['unread_hint'], notifications.unread_hint(expected))
            self.assertNotIn('unread', await self.call(room))
            self.assertFalse(self.seed(category, key=f'new-{index}'))
            self.assertNotIn('unread', await self.call(room))
        # A new ID must trigger even if the aggregate count stays identical.
        notifications.ack_notifications('ai', 'ai-1', 'loan')
        self.seed('loan', key='replacement')
        self.assertEqual((await self.call(room))['unread'], expected)
        notifications.ack_notifications('ai', 'ai-1', 'loan')
        self.assertNotIn('unread', await self.call(room))
        self.seed(pid='another-ai', key='other')
        self.seed(subject_type='human', key='human')
        self.assertNotIn('unread', await self.call(room))

    async def test_explicit_lists_keep_summary_and_shared_minimal_cursor(self):
        legacy = self.room()
        minimal = self.room('rummikub')
        await self.call(legacy)
        await self.call(minimal)
        self.seed()
        self.assertIn('unread', await self.call(legacy))
        self.assertNotIn('unread', await self.call(minimal))
        self.seed('exchange', key='second')
        self.assertIn('unread', await self.call(minimal))
        self.assertNotIn('unread', await self.call(legacy))
        expected = notifications.unread_summary('ai', 'ai-1')
        for action in ('rooms', 'chips', 'chips'):
            response = await self.client.post('/mcp/play', json=dict(
                action=action, player_id='ai-1', opponent_id='ai-1-human-1', op='status'))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['unread'], expected)
        self.assertEqual(notifications.unread_summary('ai', 'ai-1'), expected)

    async def test_invalid_move_and_failed_notification_transaction_allow_retry(self):
        room = self.room()
        await self.call(room)
        self.seed()
        response = await self.client.post('/mcp/play', json=dict(
            action='move', room_id=room['room_id'], player_id='ai-1', move={'action': 'invalid'}))
        self.assertGreaterEqual(response.status_code, 400)
        self.assertIn('unread', await self.call(room))
        self.seed(key='retry')

        @contextmanager
        def failing_transaction():
            with database.write_transaction() as conn:
                yield conn
                raise RuntimeError('commit failed')

        payload = {}
        with patch.object(notifications, 'write_transaction', failing_transaction):
            with self.assertRaisesRegex(RuntimeError, 'commit failed'):
                notifications.attach_mcp_unread(payload, 'ai-1')
        self.assertEqual(payload, {})
        self.assertIn('unread', await self.call(room))
        self.assertNotIn('unread', await self.call(room))

    async def test_parallel_legacy_and_minimal_claim_one_delivery(self):
        self.seed()
        barrier = Barrier(2)

        def claim(attach):
            barrier.wait(timeout=5)
            return attach({}, 'ai-1')

        with ThreadPoolExecutor(2) as pool:
            replies = list(pool.map(claim, (notifications.attach_mcp_unread,
                                           mcp_minimal.attach_new_notifications)))
        self.assertEqual(sum('unread' in reply for reply in replies), 1)
        self.assertEqual(notifications.unread_summary('ai', 'ai-1')['total'], 1)
        self.seed('achievement', key='later')
        self.assertEqual(notifications.attach_mcp_unread({}, 'ai-1')['unread']['total'], 2)

    async def test_heartbeat_and_slot_downgrade_deliver_through_real_adapter(self):
        from app import local_mcp
        for downgraded in (False, True):
            with self.subTest(downgraded=downgraded):
                room = self.room('tictactoe')
                await self.call(room)
                response = await self.client.post('/mcp/play', json=dict(
                    action='move', room_id=room['room_id'], player_id='ai-1', move={'row': 0, 'col': 0}))
                self.assertEqual(response.status_code, 200, response.text)
                self.seed(key=f'wait-{downgraded}')
                with patch.object(main, 'MCP_WAIT_SECONDS', 0.01), \
                     patch.object(main.revision_events, 'try_acquire_wait_slot', return_value=not downgraded), \
                     patch.object(local_mcp, 'LOCAL_AI_ID', 'ai-1'), \
                     patch.object(local_mcp, 'LOCAL_HUMAN_ID', 'ai-1-human-1'):
                    code, reply = await asyncio.wait_for(local_mcp.forward_play(
                        dict(action='state', room_id=room['room_id'], wait=True),
                        transport=httpx.ASGITransport(app=main.app), max_wait_seconds=5), 2)
                    self.assertEqual(code, 200)
                    self.assertIn('unread', reply)
                    self.assertEqual(reply['status'], 'playing')
                    self.assertNotIn('wait_downgraded', reply)
                    again = await self.call(room, wait=True)
                self.assertNotIn('unread', again)
                if downgraded:
                    self.assertTrue(again['wait_downgraded'])
                else:
                    self.assertEqual(again['status'], 'still_waiting')

    async def test_cancelled_wait_does_not_claim_fresh_notice(self):
        room = self.room('tictactoe')
        await self.call(room)
        framework.play_move(room['room_id'], 'ai', 'ai-1', {'row': 0, 'col': 0})
        waiter = asyncio.create_task(self.call(room, wait=True))
        async def waiting():
            while main.revision_events.waiting_count != 1:
                await asyncio.sleep(0.001)
        await asyncio.wait_for(waiting(), 2)
        self.seed()
        response = await self.client.post('/mcp/play', json=dict(
            action='cancel_wait', room_id=room['room_id'], player_id='ai-1'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual((await asyncio.wait_for(waiter, 2))['status'], 'wait_cancelled')
        self.assertIn('unread', await self.call(room))
