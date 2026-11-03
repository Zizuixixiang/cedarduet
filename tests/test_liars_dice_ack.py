"""Party round acknowledgement authority; every database is temporary."""
import asyncio
from copy import deepcopy
from unittest.mock import patch

from app import database, framework, invites, npc_controller, takeover
from app.npc_personas import NpcPersona
from app.npc_scheduler import is_system_npc_turn
from tests.test_multiplayer_games import MultiplayerGameTestCase


class PartyAcknowledgementTests(MultiplayerGameTestCase):
    def create(self, human=True, second_human=False):
        host, role = ('human-1', 'human') if human else ('ai-1', 'ai')
        with patch('app.invites.npc_provider_capabilities', return_value={'available': True}):
            room = invites.create_invite('liars_dice', role, host,
                target_player_count=3,
                timeout_takeover_seconds=90,
                game_options={'mode': 'party', 'allow_surrender': True,
                              'surrender_no_reroll': True})
        invites.join_invite(room['invite_code'],
                            'human' if second_human else 'ai',
                            'human-2' if second_human else 'ai-2')
        with patch('app.invites.npc_provider_capabilities', return_value={'available': True}), patch(
                'app.invites.select_personas', return_value=[NpcPersona('test', '测试NPC', 'test')]):
            room = invites.start_invite(room['room_id'], role, host, fill_with_npcs=True)
        state = room['board_state']
        state['dice_by_player'] = {pid: [2] * 5 for pid in state['dice_by_player']}
        return self.replace_state(room, state, 'npc:test')

    def act(self, room, move, pid=None, **kwargs):
        pid = pid or room['current_player_id']
        return framework.play_move(room['room_id'], self.role_for(pid), pid, move,
                                   expected_revision=room['revision'], **kwargs)

    def settle(self, room, action='challenge'):
        room = self.act(room, {'action': 'bid', 'quantity': 10, 'face': 6, 'exact': True})
        return self.act(room, {'action': action})

    def test_npc_loser_waits_for_human_and_starts_after_ack(self):
        room = self.settle(self.create())
        state = deepcopy(room['board_state'])
        self.assertEqual(room['current_player_id'], 'human-1')
        self.assertEqual(state['pending_next_round']['starter_player_id'], 'npc:test')
        self.assertFalse(is_system_npc_turn(room))
        with self.assertRaises(framework.DuelError):
            asyncio.run(npc_controller.run_current_npc_turn(room['room_id']))
        for pid in ('npc:test', 'ai-2'):
            with self.assertRaises(framework.DuelError):
                self.act(room, {'action': 'acknowledge_round'}, pid)
        with self.assertRaises(framework.DuelError):
            self.act(room, {'action': 'acknowledge_round'}, takeover=True)
        # Exercise the actual timeout path with a live human and expired clock.
        with database.write_transaction() as conn:
            conn.execute("UPDATE room_invites SET turn_started_at='2000-01-01T00:00:00+00:00', turn_revision=? WHERE room_id=?",
                         (room['revision'], room['room_id']))
        framework.touch_room_presence(room['room_id'], 'human-1')
        self.assertIsNone(asyncio.run(takeover.run_timeout_turn(room['room_id'])))
        for _ in range(3):
            self.assertEqual(framework.get_room(room['room_id'])['board_state'], state)
        for pid in ('human-1', 'npc:test'):
            view = framework.project_room_for_viewer(room, pid)
            self.assertNotIn('dice_by_player', view['board_state'])
        with self.assertRaises(framework.DuelError):
            framework.acknowledge_liars_dice_round(room['room_id'], 'human-1', None)
        next_room = framework.acknowledge_liars_dice_round(room['room_id'], 'human-1', room['revision'])
        self.assertEqual(next_room['current_player_id'], 'npc:test')
        self.assertEqual(next_room['board_state']['flow']['round_number'], 2)
        self.assertEqual(next_room['board_state']['scores'], state['scores'])
        self.assertEqual(len(next_room['board_state']['round_results']), 1)
        view = framework.project_room_for_viewer(next_room, 'human-1')
        self.assertNotIn('dice_by_player', view['board_state'])
        self.assertEqual(view['private_state']['dice'], next_room['board_state']['dice_by_player']['human-1'])
        for ack in (lambda: self.act(room, {'action': 'acknowledge_round'}),
                    lambda: framework.acknowledge_liars_dice_round(room['room_id'], 'human-1', room['revision'])):
            with self.assertRaises(framework.DuelError):
                ack()

    def test_host_only_and_surrender_preserves_loser_and_dice(self):
        room = self.settle(self.create(second_human=True), 'surrender')
        state = deepcopy(room['board_state'])
        self.assertEqual(room['current_player_id'], 'human-1')
        for pid in ('human-2', 'npc:test'):
            with self.assertRaises(framework.DuelError):
                self.act(room, {'action': 'acknowledge_round'}, pid)
            with self.assertRaises(framework.DuelError):
                framework.acknowledge_liars_dice_round(room['room_id'], pid, room['revision'])
        room = self.act(room, {'action': 'acknowledge_round'})
        self.assertEqual(room['current_player_id'], state['pending_next_round']['starter_player_id'])
        self.assertEqual(room['board_state']['dice_by_player'], state['dice_by_player'])
        self.assertEqual(room['board_state']['scores'], state['scores'])

    def test_available_human_fallback_when_host_is_not_eligible(self):
        for changes in ({'initiator_player_id': 'npc:test'}, {'join_status': 'left'},
                        {'active': False}, {'activity_state': 'eliminated'}):
            with self.subTest(changes=changes):
                room = self.create(second_human=True)
                if 'initiator_player_id' in changes:
                    with database.write_transaction() as conn:
                        conn.execute('UPDATE rooms SET initiator_player_id=? WHERE room_id=?',
                                     ('npc:test', room['room_id']))
                else:
                    key, value = next(iter(changes.items()))
                    with database.write_transaction() as conn:
                        conn.execute(f'UPDATE room_participants SET {key}=? WHERE room_id=? AND player_id=?',
                                     (value, room['room_id'], 'human-1'))
                room = framework.get_room(room['room_id'])
                expected = framework._party_round_confirmer(room)
                self.assertIn(expected, {'human-1', 'human-2'} if 'initiator_player_id' in changes else {'human-2'})
                # Keep settlement actions on an eligible player in this fixture.
                room = self.act(room, {'action': 'bid', 'quantity': 10, 'face': 6, 'exact': True})
                room = self.replace_state(room, room['board_state'], 'human-2')
                room = self.act(room, {'action': 'challenge'})
                self.assertEqual(room['current_player_id'], expected)
                room = self.act(room, {'action': 'acknowledge_round'})
                self.assertEqual(room['current_player_id'], 'npc:test')

    def test_ai_only_npc_automatically_acknowledges(self):
        room = self.settle(self.create(human=False))
        self.assertEqual(room['current_player_id'], 'npc:test')
        self.assertTrue(is_system_npc_turn(room))
        asyncio.run(npc_controller.run_current_npc_turn(room['room_id']))
        updated = framework.get_room(room['room_id'])
        self.assertEqual(updated['board_state']['flow']['round_number'], 2)
        self.assertEqual(updated['current_player_id'], 'npc:test')
        self.assertEqual(updated['board_state']['scores'], room['board_state']['scores'])

    def test_ai_only_timeout_still_acknowledges(self):
        room = self.create(human=False)
        room = self.replace_state(room, room['board_state'], 'ai-1')
        room = self.settle(room)
        self.assertEqual(room['current_player_id'], 'ai-1')
        with database.write_transaction() as conn:
            conn.execute("UPDATE room_invites SET turn_started_at='2000-01-01T00:00:00+00:00', turn_revision=? WHERE room_id=?",
                         (room['revision'], room['room_id']))
        framework.touch_room_presence(room['room_id'], 'ai-2')
        updated = asyncio.run(takeover.run_timeout_turn(room['room_id']))
        self.assertEqual(updated['board_state']['flow']['round_number'], 2)
        self.assertEqual(updated['current_player_id'], 'ai-1')
