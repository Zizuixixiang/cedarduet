"""大富翁·改：房间/HTTP/MCP/NPC 集成，只使用每个测试自己的临时 SQLite。"""
import json
import random
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database, framework, main as main_module, npc_controller
from app.games import GAMES, game_catalog
from app.games.monopoly_plus import MonopolyPlus
from tests import test_npc_speech as speech_fixtures
from tests.test_npc_framework import write_persona


class MonopolyPlusIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='duel-monopoly-plus-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        db_patch = patch.object(database, 'DB_PATH', root / 'test.db')
        db_patch.start()
        self.addCleanup(db_patch.stop)
        personas = root / 'personas'
        personas.mkdir()
        write_persona(personas, 'quiet', '安静测试机', 'quiet persona')
        env_patch = patch.dict('os.environ', {'DUEL_NPC_PERSONAS_DIR': str(personas)})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        database.init_db()
        self.game = MonopolyPlus(random.Random(5))
        games_patch = patch.dict(GAMES, {'monopoly_plus': self.game})
        games_patch.start()
        self.addCleanup(games_patch.stop)
        events_patch = patch.object(main_module, 'revision_events', main_module.RevisionEvents())
        events_patch.start()
        self.addCleanup(events_patch.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main_module.app), base_url='http://plus.test')
        self.addAsyncCleanup(self.client.aclose)
        self.addAsyncCleanup(npc_controller.wait_for_npc_speech_tasks)

    @staticmethod
    def seats(count):
        return [{'player_id': 'human-plus' if i == 0 else f'ai-plus-{i}', 'role': 'human' if i == 0 else 'ai',
                 'participant_kind': 'human' if i == 0 else 'bound_machine', 'display_name': f'席{i + 1}'}
                for i in range(count)]

    def ordinary(self, count=3):
        members = self.seats(count)
        room = framework.create_room('monopoly_plus', 'human_first', 'human', members[0]['player_id'],
                                     opponent_id=members[1]['player_id'], ordered_participants=members)
        self.assertEqual(room['status'], 'playing')
        return room

    def move(self, room, action, player=None, **params):
        player = player or next(p for p in room['participants'] if p['player_id'] == room['current_player_id'])
        return framework.play_move(room['room_id'], player['role'], player['player_id'],
                                   {'action': action, 'action_seq': room['board_state']['action_seq'], **params},
                                   expected_revision=room['revision'])

    async def mcp(self, **body):
        response = await self.client.post('/mcp/play', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_catalog_entry(self):
        entry = next(g for g in game_catalog() if g['game_type'] == 'monopoly_plus')
        self.assertEqual(entry['display_name'], '大富翁·改')
        self.assertEqual(entry['category'], 'tabletop')
        self.assertEqual(entry['allowed_player_counts'], [2, 3, 4, 5, 6])
        self.assertTrue(entry['supports_npcs'])
        self.assertFalse(entry['supports_stakes'])
        classic = next(g for g in game_catalog() if g['game_type'] == 'monopoly')
        self.assertEqual(classic['display_name'], '大富翁')

    async def test_credit_at_end_of_runtime_rules_web_mcp_and_guide(self):
        credit = '制作：顾屿、相顾｜小红书：苏苏脆脆'
        self.assertEqual(self.game.rules_text.rstrip().splitlines()[-1], credit)
        room = self.ordinary(2)
        web = await self.client.get(f"/api/rooms/{room['room_id']}",
                                   headers={'X-Duel-Human-Player': 'human-plus'})
        self.assertEqual(web.status_code, 200, web.text)
        self.assertTrue(web.json()['room']['rules_text'].startswith(self.game.rules_text))
        boot = await self.mcp(action='state', player_id='ai-plus-1', room_id=room['room_id'])
        self.assertIn(credit, boot['room']['rules_text'])
        full = await self.mcp(action='state', player_id='ai-plus-1', room_id=room['room_id'], full_state=True)
        self.assertEqual(full['snapshot']['rules_text'].rstrip().splitlines()[-1], credit)
        guide = (Path(__file__).resolve().parents[1] / 'docs/MCP_GUIDE.md').read_text()
        self.assertIn(credit, guide.split('## 大富翁·改 `monopoly_plus`', 1)[1])

    def test_cross_process_restore_preserves_state_and_budget_then_valid_move_resets(self):
        room = self.move(self.ordinary(2), 'choose_edition', edition='classic_plus')
        with database.write_transaction() as conn:
            conn.execute('UPDATE rooms SET npc_unattended_turns=4, npc_turn_actions=17 WHERE room_id=?',
                         (room['room_id'],))
        script = '''
import json, sys
from pathlib import Path
from app import database, framework
database.DB_PATH = Path(sys.argv[1])
database.init_db()
room = framework.get_room(sys.argv[2])
print(json.dumps(room, ensure_ascii=False))
framework.play_move(room['room_id'], 'ai', 'ai-plus-1',
    {'action': 'bet', 'choice': 'seven', 'action_seq': room['board_state']['action_seq']},
    expected_revision=room['revision'])
'''
        restored = subprocess.run([sys.executable, '-c', script, str(database.DB_PATH), room['room_id']],
                                  capture_output=True, text=True, check=True, timeout=30)
        before = json.loads(restored.stdout)
        self.assertEqual(before['board_state'], room['board_state'])
        self.assertEqual((before['npc_unattended_turns'], before['npc_turn_actions']), (4, 17))
        after = framework.get_room(room['room_id'])
        self.assertEqual((after['npc_unattended_turns'], after['npc_turn_actions']), (0, 0))
        self.assertEqual(after['board_state']['bets'], {'ai-plus-1': 'seven'})
        self.assertEqual(after['revision'], room['revision'] + 1)
        self.assertEqual(after['current_player_id'], room['current_player_id'])

    def test_spectator_bet_is_an_out_of_turn_action_that_keeps_the_turn(self):
        room = self.ordinary(3)
        human, machine, third = room['participants']
        room = self.move(room, 'choose_edition', edition='classic_plus')
        self.assertEqual(room['current_player_id'], human['player_id'])
        with self.assertRaises(framework.DuelError) as caught:
            self.move(room, 'roll', player=machine)
        self.assertEqual(caught.exception.status_code, 409)
        before = room['revision']
        room = self.move(room, 'bet', player=machine, choice='seven')
        self.assertEqual(room['revision'], before + 1)
        self.assertEqual(room['current_player_id'], human['player_id'])
        self.assertEqual(room['board_state']['bets'], {machine['player_id']: 'seven'})
        with self.assertRaises(framework.DuelError):
            self.move(room, 'bet', player=human, choice='big')
        with self.assertRaises(framework.DuelError):
            self.move(room, 'bet', player=machine, choice='big')
        room = self.move(room, 'roll')
        self.assertEqual(room['board_state']['bets'], {})
        self.assertEqual(len(room['board_state']['last_bets']), 1)
        with self.assertRaises(framework.DuelError):
            self.move(room, 'bet', player=third, choice='big')

    async def test_http_web_move_and_mcp_compact_projection(self):
        room = self.ordinary(2)
        human, machine = room['participants']
        response = await self.client.post(f"/api/rooms/{room['room_id']}/move",
                                          headers={'X-Duel-Human-Player': human['player_id']},
                                          json={'player_id': human['player_id'],
                                                'move': {'action': 'choose_edition', 'edition': 'classic_plus', 'action_seq': 0},
                                                'revision': room['revision']})
        self.assertEqual(response.status_code, 200, response.text)
        room = framework.get_room(room['room_id'])
        boot = await self.mcp(action='state', player_id=machine['player_id'], room_id=room['room_id'])
        board = boot['room']['board_state']
        self.assertEqual(len(board['map']), 40)
        self.assertNotIn('tiles', board)
        self.assertIn('delta_format', board)
        self.assertIn('side_actions', boot['room']['private_state'])
        # The machine bets through MCP although it is not its turn.
        reply = await self.mcp(action='move', player_id=machine['player_id'], room_id=room['room_id'],
                               revision=room['revision'],
                               move={'action': 'bet', 'choice': 'big', 'action_seq': room['board_state']['action_seq']})
        self.assertTrue(reply['ok'])
        room = framework.get_room(room['room_id'])
        self.assertEqual(room['current_player_id'], human['player_id'])
        room = self.move(room, 'roll')
        while room['current_player_id'] == human['player_id']:
            action = self.game.choose_local_npc_action(room['board_state'], human, room['participants'])
            room = framework.play_move(room['room_id'], 'human', human['player_id'], action,
                                       expected_revision=room['revision'])
        delta = await self.mcp(action='state', player_id=machine['player_id'], room_id=room['room_id'])
        self.assertTrue(delta['your_turn'])
        self.assertLess(len(json.dumps(delta, ensure_ascii=False)), 4000)
        results = [e for e in delta.get('events', []) if 'monopoly_plus_delta' in e]
        self.assertTrue(results)
        self.assertNotIn('tiles', json.dumps(results))
        full = await self.mcp(action='state', player_id=machine['player_id'], room_id=room['room_id'], full_state=True)
        self.assertEqual(len(full['snapshot']['board_state']['tiles']) if 'snapshot' in full
                         else len(full['room']['board_state']['tiles']), 40)

    async def test_system_npc_plays_choices_with_the_local_policy(self):
        room = framework.create_room(
            'monopoly_plus', 'human_first', 'human', 'human-1', opponent_id='ai-1',
            ordered_participants=speech_fixtures.NpcSpeechCadenceTests.participants(),
            enforce_trusted_pair=True, first_player_id='npc:quiet')
        self.assertEqual(room['current_player_id'], 'npc:quiet')
        result = await npc_controller.run_current_npc_turn(room['room_id'])
        self.assertEqual(result.status, 'applied')
        room = framework.get_room(room['room_id'])
        self.assertEqual(room['board_state']['phase'], 'roll')
        self.assertEqual(room['board_state']['edition'], 'classic_plus')
        for _ in range(8):
            if room['current_player_id'] != 'npc:quiet':
                break
            result = await npc_controller.run_current_npc_turn(room['room_id'])
            self.assertIn(result.status, ('applied', 'already_applied'))
            room = framework.get_room(room['room_id'])
        self.assertNotEqual(room['current_player_id'], 'npc:quiet')

    def test_side_action_keeps_the_invite_turn_timer(self):
        from app import invites
        members = self.seats(2)
        room = invites.create_invite('monopoly_plus', 'human', members[0]['player_id'], target_player_count=2,
                                     timeout_takeover=True)
        invites.join_invite(room['invite_code'], 'ai', members[1]['player_id'])
        with patch.object(invites.secrets, 'SystemRandom') as secure_random:
            secure_random.return_value.shuffle.side_effect = lambda players: None
            room = invites.start_invite(room['room_id'], 'human', members[0]['player_id'])
        room = self.move(room, 'choose_edition', edition='classic_plus')
        then = (datetime.now(timezone.utc) - timedelta(seconds=50)).isoformat()
        with database.write_transaction() as conn:
            conn.execute('UPDATE room_invites SET turn_started_at=? WHERE room_id=?', (then, room['room_id']))
        bettor = next(p for p in room['participants'] if p['player_id'] != room['current_player_id'])
        room = self.move(room, 'bet', player=bettor, choice='small')
        self.assertEqual(room['turn_started_at'] if 'turn_started_at' in room else then, then)
        conn = database.connect()
        try:
            row = conn.execute('SELECT turn_started_at, turn_revision FROM room_invites WHERE room_id=?',
                               (room['room_id'],)).fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], then)
        self.assertEqual(row[1], room['revision'])


if __name__ == '__main__':
    unittest.main()
