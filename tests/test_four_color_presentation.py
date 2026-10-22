"""Presentation contracts only: printed names and complete, nonduplicated wire guide."""
import json
from pathlib import Path
import random
import re
import tempfile
import unittest
from unittest.mock import patch

from app import database, framework, mcp_minimal
from app.games import GAMES
from app.games.four_color_cards.cards import DECK, label
from app.games.four_color_cards.mcp import FORMAT
from app.games.four_color_cards.tenhu import BgaTenHu
from app.games.four_color_cards.jiuzhi import Jiuzhi
from app.games.four_color_cards.qunhu import Qunhu
from tests.test_four_color_tenhu import players


class FourColorPresentation(unittest.TestCase):
    def test_all_printed_names_match_web_and_mcp_without_changing_ids(self):
        expected = dict(R='帥仕相俥傌炮兵', Y='帥仕相俥傌炮兵', G='將士象車馬包卒', W='將士象車馬包卒')
        script = (Path(__file__).resolve().parents[1] / 'app/static/games/four_color_tenhu.js').read_text()
        web_names = dict(re.findall(r'([RYGW]): "([帥仕相俥傌炮兵將士象車馬包卒]{7})"', script))
        self.assertEqual(web_names, expected)
        self.assertEqual(len(set(DECK)), 112)
        self.assertEqual(set(DECK), {f'{c}{r}-{i}' for c in expected for r in range(7) for i in range(1,5)})
        for color,names in expected.items():
            for rank,name in enumerate(names):
                for copy in range(1,5):
                    self.assertEqual(label(f'{color}{rank}-{copy}')[1:], name)
        self.assertIn('R/Y='+expected['R'], FORMAT)
        self.assertIn('G/W='+expected['G'], FORMAT)

    def test_complete_rules_once_then_only_delta_with_explicit_resync(self):
        with tempfile.TemporaryDirectory(prefix='four-color-presentation-') as tmp, patch.object(database,'DB_PATH',Path(tmp)/'test.db'):
            database.init_db()
            for cls in (BgaTenHu,Jiuzhi,Qunhu):
                g=cls(random.Random(9))
                with self.subTest(game=g.game_type), patch.dict(GAMES,{g.game_type:g}):
                    room=framework.create_room(g.game_type,'human_first','human','p0',ordered_participants=players(4))
                    first=mcp_minimal.response(room['room_id'],'p0')
                    self.assertEqual(first['room']['rules_text'],g.rules_text)
                    self.assertEqual(first['room']['board_state']['rules_version'], room['board_state']['rules_version'])
                    self.assertNotIn('rules_version',first['room'])  # version lives once in board_state
                    self.assertNotIn('move_format',first['room'])
                    self.assertEqual(json.dumps(first,ensure_ascii=False).count('"rules_text"'),1)
                    self.assertEqual(first['protocol_guide'].count(FORMAT),1)
                    for key in ('revision','from_revision','private.+/-','action_id','full_state=true','rules and state'):
                        self.assertIn(key,first['protocol_guide'])
                    repeated=mcp_minimal.response(room['room_id'],'p0')
                    self.assertEqual(repeated['r'],first['r'])
                    self.assertEqual(repeated['from_revision'],first['r'])
                    self.assertNotIn('room',repeated)
                    self.assertNotIn('rules_text',json.dumps(repeated))
                    self.assertNotIn('events',repeated)
                    self.assertNotIn('private',repeated)
                    ids=first['room']['private_state']['legal_actions']
                    self.assertTrue(ids)
                    # Exactly the ID a model reads, without asking the NPC to rederive it.
                    room=framework.play_move(room['room_id'],'human','p0',{'action':'act','action_id':ids[0]},expected_revision=first['r'])
                    delta=mcp_minimal.response(room['room_id'],'p0')
                    self.assertEqual(delta['from_revision'],first['r'])
                    self.assertEqual(delta['r'],room['revision'])
                    self.assertNotIn('room',delta)
                    self.assertNotIn('rules_text',json.dumps(delta))
                    restored=mcp_minimal.response(room['room_id'],'p0',full=True)
                    self.assertEqual(restored['snapshot']['rules_text'],g.rules_text)
                    self.assertEqual(restored['snapshot']['move_format'],FORMAT)
                    self.assertEqual(restored['snapshot']['private_state']['hand'],sorted(room['board_state']['hands']['p0']))
