"""Room-option persistence checks against disposable databases only."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import chips, database, framework, invites
from app.games import GAMES
from app.npc_personas import NpcPersona
from tests.test_mahjong import participants

OPTIONS = dict(double_hu=True, gang_discard_double=True, liang_liability=True,
               consecutive_kong_double=True, horse_count=1, piao=3, joker='red', rounds=4)


class KaWuXingOptionsWiringTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='kwx-options-wiring-')
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(database, 'DB_PATH', Path(tmp.name) / 'duel.db')
        patcher.start(); self.addCleanup(patcher.stop)
        database.init_db()

    def check_options(self, room):
        game = GAMES['ka_wu_xing']
        self.assertEqual(game.saved_options(room['board_state']), OPTIONS)
        self.assertEqual(room['game_options'], OPTIONS)
        self.assertIn('【本房】', room['rules_text'])
        self.assertIn('固定漂3', room['rules_text'])
        for p in room['participants']:
            if p['participant_kind'] == 'system_npc':
                continue
            for project in [framework.project_room_for_viewer,
                            framework.project_mcp_room_for_viewer,
                            framework.project_mcp_snapshot_for_viewer]:
                view = project(room, p['player_id'])
                self.assertNotIn('hands', view['board_state'])
                if room['status'] == 'playing':
                    self.assertEqual(view['board_state']['options'], OPTIONS)
                else:
                    self.assertEqual(view['game_options'], OPTIONS)
                    self.assertEqual(view['private_state'], {})

    def test_normal_room_stores_all_options(self):
        room = framework.create_room('ka_wu_xing', 'human_first', 'human', 'p0',
                    ordered_participants=participants()[:3], game_options=OPTIONS)
        self.check_options(framework.get_room(room['room_id']))

    def test_three_account_invitation_waiting_start_and_process_reload(self):
        room = invites.create_invite('ka_wu_xing', 'ai', 'a101',
                    target_player_count=3, game_options=OPTIONS)
        self.check_options(room)
        for p in ['a202', 'a303']:
            room = invites.join_invite(room['invite_code'], 'ai', p)
            self.check_options(room)
        room = invites.start_invite(room['room_id'], 'ai', 'a101')
        self.check_options(room)
        code = "from app import framework; import json,sys; r=framework.get_room(sys.argv[1]); print(json.dumps(framework.project_room_for_viewer(r,'a303')))"
        loaded = subprocess.run([sys.executable, '-c', code, room['room_id']],
                 env={**os.environ, 'DUEL_DB_PATH': str(database.DB_PATH)},
                 capture_output=True, text=True, check=True)
        restored = json.loads(loaded.stdout)
        self.assertEqual(restored['game_options'], OPTIONS)
        self.assertEqual(restored['board_state']['options'], OPTIONS)
        self.assertNotIn('hands', restored['board_state'])

    def test_invite_npc_fill_keeps_selected_options(self):
        room = invites.create_invite('ka_wu_xing', 'ai', 'a101',
                    target_player_count=3, game_options=OPTIONS)
        invites.join_invite(room['invite_code'], 'ai', 'a202')
        with patch('app.invites.select_personas', return_value=[NpcPersona('kwx-fixture', '临时NPC', 'test')]):
            room = invites.start_invite(room['room_id'], 'ai', 'a101', fill_with_npcs=True)
        self.check_options(room)
        self.assertEqual(sum(p['participant_kind'] == 'system_npc' for p in room['participants']), 1)

    def started_scoring_room(self, options=None):
        room = invites.create_invite('ka_wu_xing', 'ai', 'a101',
                    target_player_count=3, stake=1, game_options=options or {'rounds': 4})
        for pid in ('a202', 'a303'):
            invites.join_invite(room['invite_code'], 'ai', pid)
        room = invites.start_invite(room['room_id'], 'ai', 'a101')
        with database.write_transaction() as conn:
            balances = {p['player_id']: chips._ensure_wallet(conn, 'ai', p['player_id'])['balance']
                        for p in room['participants']}
        return room, balances

    def completed_kong_round(self):
        room, balances = self.started_scoring_room()
        state = room['board_state']
        source = room['current_player_id']
        others = [p for p in state['participant_order'] if p != source]
        # Exhaust the wall while preserving physical IDs, and seed already
        # earned kong accounting. Real framework actions finish the hand.
        state['discards'][source].extend(state['wall'])
        state['wall'] = []
        state['kong_scores'] = {source: 4, others[0]: -2, others[1]: -2}
        state['kong_ledger'] = [dict(player_id=source, kind='concealed_gang', payers=others, units=2)]
        with database.write_transaction() as conn:
            conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?', (json.dumps(state), room['room_id']))
        for _ in range(4):
            room = framework.get_room(room['room_id'])
            if room['board_state']['phase'] == 'round_end':
                break
            actor = room['current_player_id']
            action = next(a for a in GAMES['ka_wu_xing'].legal_actions_for(room['board_state'], actor)
                          if a['kind'] in ('discard', 'pass'))
            room = framework.play_move(room['room_id'], 'ai', actor,
                       {'action': 'act', 'action_id': action['action_id']}, expected_revision=room['revision'])
        self.assertEqual(room['board_state']['phase'], 'round_end')
        self.assertEqual(room['board_state']['cumulative_scores'], state['kong_scores'])
        return room, balances

    def archive_after_timeout(self, room):
        # Age only this row in the test's disposable DB, never production data.
        with database.write_transaction() as conn:
            conn.execute("UPDATE rooms SET last_move_at='2000-01-01T00:00:00+00:00' WHERE room_id=?",
                         (room['room_id'],))
        return framework.get_room(room['room_id'])

    def assert_paid_once(self, room, before, expected):
        self.assertEqual(room['result']['settlement_deltas'], expected)
        with database.write_transaction() as conn:
            for pid, delta in expected.items():
                self.assertEqual(chips._wallet_row(conn, 'ai', pid)['balance'], before[pid] + delta)
            count = conn.execute('SELECT COUNT(*) FROM chip_ledger').fetchone()[0]
            self.assertFalse(chips.settle_duel_room(conn, room))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM chip_ledger').fetchone()[0], count)
        framework.get_room(room['room_id'])
        with database.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM chip_ledger').fetchone()[0], count)

    def test_intermission_archive_keeps_cumulative_without_recharging_kongs(self):
        room, before = self.completed_kong_round()
        expected = dict(room['board_state']['cumulative_scores'])
        archived = self.archive_after_timeout(room)
        self.assertEqual(archived['status'], 'archived')
        self.assertEqual(archived['result']['score_units'], expected)
        self.assertEqual(set(archived['result']['round_score_units'].values()), {0})
        self.assertEqual(set(archived['result']['score_breakdown']['kong'].values()), {0})
        public = framework.project_room_for_viewer(archived, 'a101')['board_state']
        self.assertEqual(public['phase'], 'finished')
        self.assertIsNone(public['round_result'])
        self.assertEqual(public['cumulative_scores'], expected)
        self.assertEqual(public['game_result']['reason'], 'stale_archive')
        self.assert_paid_once(archived, before, expected)

    def test_pending_second_hu_archive_keeps_accepted_win_kongs_and_correct_winner(self):
        room, before = self.started_scoring_room({'rounds': 4, 'double_hu': True,
                                                 'liang_liability': True, 'piao': 3})
        game = GAMES['ka_wu_xing']
        state = room['board_state']
        source, first, second = state['participant_order']
        stock = game.build_wall()
        def take(code):
            tile = next(t for t in stock if t['code'] == code)
            stock.remove(tile)
            return tile
        gap = 'B1 B2 B3 T1 T2 T3 T7 T8 T9 B4 B6 J1 J1'.split()
        for pid in (first, second):
            state['hands'][pid] = [take(code) for code in gap]
        discard = take('B5')
        state['hands'][source] = [discard, *stock[:13]]
        del stock[:13]
        state['wall'] = stock
        state['drawn_tile_id'] = state['hands'][source][-1]['id']
        state['revealed'][first] = {'hand': list(state['hands'][first]), 'waits': ['B5']}
        state['kong_scores'] = {source: 4, first: -2, second: -2}
        state['cumulative_scores'] = {source: -3, first: 1, second: 2}
        state['round_results'] = [{'round_number': 1, 'score_units': dict(state['cumulative_scores'])}]
        state['round_number'] = 2
        with database.write_transaction() as conn:
            conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?', (json.dumps(state), room['room_id']))
        room = framework.play_move(room['room_id'], 'ai', source,
                    {'action': 'act', 'action_id': 'discard:' + discard['id']}, expected_revision=room['revision'])
        hu = next(a for a in game.legal_actions_for(room['board_state'], first) if a['kind'] == 'hu')
        room = framework.play_move(room['room_id'], 'ai', first,
                    {'action': 'act', 'action_id': hu['action_id']}, expected_revision=room['revision'])
        self.assertEqual(room['current_player_id'], second)
        archived = self.archive_after_timeout(room)
        expected = {source: -13, first: 13, second: 0}
        self.assertEqual(archived['status'], 'archived')
        self.assertEqual(archived['winner'], 'ai')
        self.assertEqual(archived['winner_player_id'], first)
        self.assertEqual(archived['result']['winner_player_ids'], [first])
        self.assertFalse(archived['result']['draw'])
        self.assertEqual(archived['result']['score_units'], expected)
        self.assertEqual(archived['result']['score_breakdown']['piao'], {source: -6, first: 6, second: 0})
        self.assertEqual(archived['result']['score_breakdown']['kong'], state['kong_scores'])
        public = framework.project_room_for_viewer(archived, second)['board_state']
        self.assertEqual(public['phase'], 'finished')
        self.assertIsNone(public['response_window'])
        self.assertEqual(public['cumulative_scores'], expected)
        self.assert_paid_once(archived, before, expected)

    def test_intermission_resignation_updates_public_final_scores_without_double_kongs(self):
        room, before = self.completed_kong_round()
        expected = dict(room['board_state']['cumulative_scores'])
        quitter = 'a202'
        for pid in expected:
            expected[pid] += -2 if pid == quitter else 1
        finished = framework.resign(room['room_id'], 'ai', quitter)
        self.assertEqual(finished['status'], 'finished')
        self.assertEqual(finished['result']['score_units'], expected)
        self.assertEqual(set(finished['result']['score_breakdown']['kong'].values()), {0})
        public = framework.project_room_for_viewer(finished, 'a101')['board_state']
        self.assertEqual(public['phase'], 'finished')
        self.assertIsNone(public['round_result'])
        self.assertEqual(public['cumulative_scores'], expected)
        self.assertEqual(public['game_result']['win_type'], 'resignation_liability')
        self.assertEqual(public['game_result']['score_units'], expected)
        self.assert_paid_once(finished, before, expected)

    def test_options_rejected_for_mcr_and_invalid_values_do_not_create_rooms(self):
        for game_type, options in [('mahjong', OPTIONS), ('ka_wu_xing', {'rounds': 3}),
                ('ka_wu_xing', {'double_hu': 1}), ('ka_wu_xing', {'piao': True}),
                ('ka_wu_xing', {'horse_count': 2}), ('ka_wu_xing', {'joker': 'wild'}),
                ('ka_wu_xing', {'unknown': True})]:
            with self.subTest(game_type=game_type, options=options):
                with self.assertRaises(framework.DuelError):
                    invites.create_invite(game_type, 'ai', 'a101',
                        target_player_count=4 if game_type == 'mahjong' else 3, game_options=options)
        with database.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM rooms').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
