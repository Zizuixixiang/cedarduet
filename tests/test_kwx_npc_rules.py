"""Saved room rules through real projections/request builders, disposable DB only."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import database, framework, mcp_minimal, npc_controller
from app.games import GAMES
from app.npc_personas import NpcPersona
from app.npc_providers import NpcDecisionRequest, NpcSpeechRequest
from tests.test_mahjong import participants


ALL_ON = dict(double_hu=True, gang_discard_double=True, liang_liability=True,
              consecutive_kong_double=True, horse_count=1, piao=5, joker='red')
# User-supplied settings only; never look up the real room.
REPORTED_OPTIONS = dict(double_hu=False, gang_discard_double=True, liang_liability=True,
                        consecutive_kong_double=False, horse_count=0, piao=0, joker='none')
CASES = [('default', {}), ('reported_options', REPORTED_OPTIONS), ('all_on', ALL_ON)]
CASES += [(key, {key: value}) for key, value in ALL_ON.items()]
CASES += [('piao_1', {'piao': 1}), ('piao_3', {'piao': 3})]


class KaWuXingNpcRulesTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='kwx-npc-rules-')
        self.addCleanup(tmp.cleanup)
        for patcher in (
            patch.object(database, 'DB_PATH', Path(tmp.name) / 'duel.db'),
            patch.object(npc_controller, 'get_npc_provider',
                         side_effect=AssertionError('No model provider allowed')),
            patch.object(npc_controller, 'get_persona',
                         return_value=NpcPersona('kwx-test', '规则测试机', 'isolated test')),
            patch.object(framework, 'load_personas', return_value=[]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.game = GAMES['ka_wu_xing']

    def create_room(self, options):
        players = participants()[:3]
        players[1].update(participant_kind='system_npc', npc_persona_id='kwx-test')
        created = framework.create_room(
            'ka_wu_xing', 'ai_first', 'human', 'p0',
            ordered_participants=players, first_player_id='p1', game_options=options)
        return framework.get_room(created['room_id'])

    def assert_rules(self, room, options):
        state = room['board_state']
        before = deepcopy(state)
        npc = self.game.npc_compact_rules(state)
        full = self.game.room_rules(state)
        # Check both positive and negative forms, including a mixed room where
        # the old dynamic header omitted disabled boolean settings.
        for key, enabled, disabled, room_label in (
            ('double_hu', '弃牌一炮双响', '弃牌单响（单和）', '一炮双响'),
            ('gang_discard_double', '杠上炮开（×2，计入牌型封顶）',
             '杠上炮关（不额外翻倍）', '杠上炮×2'),
            ('liang_liability', '亮倒包赔开',
             '亮倒包赔关（接炮/抢杠来源付一份）', '亮倒包赔两份'),
            ('consecutive_kong_double', '连续杠分翻倍开',
             '连续杠分翻倍关（每杠按普通杠分）', '连续杠分翻倍'),
        ):
            self.assertIn(enabled if options[key] else disabled, npc)
            self.assertNotIn(disabled if options[key] else enabled, npc)
            self.assertEqual(room_label in full.splitlines()[0], options[key])
        for text in (npc, full):
            self.assertIn(f"买马{options['horse_count']}张", text)
            self.assertIn(f"固定漂{options['piao']}", text)
            self.assertIn('红中万能' if options['joker'] == 'red' else '红中普通字牌', text)
            self.assertNotIn('包赔默认关', text)
            self.assertNotIn('默认单和', text)
            for enabled, explanation in (
                (options['liang_liability'], '赢家亮倒接炮/抢杠时，来源付两份胡分和漂分。'),
                (options['horse_count'], '自摸取剩余墙尾一马，筒条点数/中发白10，两家各另付，空墙不买。'),
                (options['piao'], f"每个胡牌付款份额另付漂{options['piao']}，不乘胡牌倍数。"),
                (options['consecutive_kong_double'], '同人未弃牌前连续完成杠按1/2/4倍计本次杠分，抢杠未成不收费。'),
                (options['joker'] == 'red', '红中仅手里可万能；弃出可按普通红中接炮，禁止碰杠红中。'),
                (options['joker'] == 'red', '有红中七对只普通七对，所有胡型不计四归一；实5配自然4、6才卡五星；三元不能替代发白。'),
                (options['joker'] == 'red', '亮倒听口含自摸红中，接炮另按自然红中校验；亮倒锁听且禁碰杠。'),
                (options['horse_count'] or options['piao'] or options['consecutive_kong_double'],
                 '马/漂/杠分不参与胡牌封顶。'),
            ):
                self.assertEqual(explanation in text, bool(enabled), explanation)
        self.assertIn('抢补杠始终单和', npc)
        self.assertIn('之后赢家亮倒再×2', npc)
        self.assertIn('荒庄每位亮倒者向每位未亮倒者付1', npc)
        self.assertNotIn('【襄阳卡五星 · 基础规则】', npc)
        self.assertNotIn(self.game.rules_text, npc)
        self.assertLess(len(npc), 700)  # Characters, not token estimates.
        self.assertEqual(state, before)
        return npc, full

    def test_saved_options_requests_and_mcp_projections(self):
        summaries = set()
        for name, supplied in CASES:
            with self.subTest(case=name):
                room = self.create_room(supplied)
                options = {**self.game.OPTION_DEFAULTS, **supplied}
                self.assertEqual(room['board_state']['options'], options)
                self.assertEqual(room['game_options'], options)
                before = deepcopy(room)
                npc, full = self.assert_rules(room, options)
                summaries.add(npc)
                actor = next(p for p in room['participants'] if p['player_id'] == 'p1')
                self.assertEqual(room['current_player_id'], 'p1')
                legal = npc_controller._authoritative_legal_actions(room, actor)
                decision, _ = npc_controller._decision_request(room, 'p1', legal)
                speech = npc_controller._speech_request(room, 'p1')
                self.assertIsInstance(decision, NpcDecisionRequest)
                self.assertIsInstance(speech, NpcSpeechRequest)
                self.assertEqual(decision.game_rules, npc)
                self.assertEqual(speech.game_rules, npc)
                self.assertEqual(room['rules_text'].split('\n\n【聊天说明】')[0], full)
                self.assertEqual(framework.project_room_for_viewer(room, 'p2')['rules_text'],
                                 room['rules_text'])
                boot = mcp_minimal.response(room['room_id'], 'p2', consume=True)
                self.assertTrue(boot['bootstrap'])
                self.assertEqual(boot['protocol'], 2)
                self.assertEqual(boot['room']['rules_text'], full)
                snapshot = mcp_minimal.response(room['room_id'], 'p2', consume=True, full=True)
                self.assertTrue(snapshot['full_state'])
                self.assertEqual(snapshot['snapshot']['rules_text'], full)
                for projection in (boot['room'], snapshot['snapshot']):
                    self.assertEqual(projection['board_state']['options'], options)
                    self.assertNotIn('hands', projection['board_state'])
                    self.assertNotIn('wall', projection['board_state'])
                self.assertEqual(room, before)
                self.assertEqual(framework.get_room(room['room_id'])['board_state'], before['board_state'])
        self.assertEqual(len(summaries), len(CASES))

    def test_legacy_options_and_room_metadata_cannot_override_saved_state(self):
        room = self.create_room(REPORTED_OPTIONS)
        room['game_options'] = ALL_ON  # Only board_state.options is authoritative.
        expected = self.game.npc_compact_rules(room['board_state'])
        self.assertEqual(npc_controller._speech_request(room, 'p1').game_rules, expected)
        for options in (None, {}, {'rounds': 8}):
            with self.subTest(options=options):
                state = deepcopy(room['board_state'])
                if options is None:
                    state.pop('options')
                else:
                    state['options'] = options
                self.assert_rules({'board_state': state}, self.game.OPTION_DEFAULTS)


if __name__ == '__main__':
    unittest.main()
