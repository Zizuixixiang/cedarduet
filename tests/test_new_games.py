import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.framework import DuelError, create_room, play_move
from app.games import GAMES
from app.games.connect4 import Connect4
from app.games.dots_boxes import DotsBoxes
from app.games.jungle import Jungle
from app.games.othello import Othello


class OthelloTests(unittest.TestCase):
    def test_standard_move_flips_piece(self):
        game = Othello()
        state = game.initial_state()
        self.assertEqual(
            state["legal_moves_by_mark"]["X"],
            [
                {"row": 2, "col": 3}, {"row": 3, "col": 2},
                {"row": 4, "col": 5}, {"row": 5, "col": 4},
            ],
        )
        game.validate_move(state, {"row": 2, "col": 3}, "X")
        result = game.apply_move(state, {"row": 2, "col": 3}, "X")
        self.assertEqual(result.state["board"][3][3], "X")
        self.assertEqual(result.state["last_move"]["flipped"], 1)
        self.assertNotIn(
            {"row": 2, "col": 3},
            result.state["legal_moves_by_mark"]["O"],
        )

    def test_no_legal_move_is_automatically_skipped(self):
        game = Othello()
        state = game.initial_state()
        state["board"] = [["X" for _ in range(8)] for _ in range(8)]
        state["board"][0][0] = None
        state["board"][0][1] = "O"
        state["board"][0][3] = None
        state["board"][0][4] = "O"
        result = game.apply_move(state, {"row": 0, "col": 0}, "X")
        self.assertTrue(result.retain_turn)
        self.assertIn("自动跳过", result.note)
        self.assertIsNone(game.check_winner(result.state))

    def test_terminal_position_counts_pieces(self):
        game = Othello()
        state = game.initial_state()
        state["board"] = [["X" for _ in range(8)] for _ in range(8)]
        state["board"][0][0] = "O"
        self.assertEqual(game.check_winner(state), "X")
        self.assertEqual(state["scores"], {"X": 63, "O": 1})


class Connect4Tests(unittest.TestCase):
    def test_gravity_and_horizontal_win(self):
        game = Connect4()
        state = game.initial_state()
        for col in range(4):
            game.validate_move(state, {"col": col}, "X")
            game.apply_move(state, {"col": col}, "X")
        self.assertEqual([state["board"][5][col] for col in range(4)], ["X"] * 4)
        self.assertEqual(game.check_winner(state), "X")

    def test_rejects_full_column(self):
        game = Connect4()
        state = game.initial_state()
        for row in range(6):
            state["board"][row][2] = "X"
        with self.assertRaisesRegex(ValueError, "下满"):
            game.validate_move(state, {"col": 2}, "O")

    def test_only_column_is_accepted(self):
        game = Connect4()
        with self.assertRaisesRegex(ValueError, "只接受 col"):
            game.validate_move(game.initial_state(), {"row": 5, "col": 2}, "X")


class DotsBoxesTests(unittest.TestCase):
    def test_completing_box_scores_and_retains_turn(self):
        game = DotsBoxes()
        state = game.initial_state()
        state["horizontal_edges"][0][0] = "O"
        state["horizontal_edges"][1][0] = "X"
        state["vertical_edges"][0][0] = "O"
        move = {"orientation": "v", "row": 0, "col": 1}
        game.validate_move(state, move, "X")
        result = game.apply_move(state, move, "X")
        self.assertTrue(result.retain_turn)
        self.assertEqual(result.state["boxes"][0][0], "X")
        self.assertEqual(result.state["scores"]["X"], 1)

    def test_finished_board_uses_score(self):
        game = DotsBoxes()
        state = game.initial_state()
        state["boxes"] = [["X" for _ in range(4)] for _ in range(4)]
        state["scores"] = {"X": 10, "O": 6}
        self.assertEqual(game.check_winner(state), "X")

    def test_framework_honors_retained_turn(self):
        with tempfile.TemporaryDirectory(prefix="duel-dots-") as directory:
            with patch.object(database, "DB_PATH", Path(directory) / "test.db"):
                database.init_db()
                room = create_room(
                    "dots_boxes", "human_first", "human", "human", "ai"
                )
                moves = [
                    ("human", "human", {"orientation": "h", "row": 0, "col": 0}),
                    ("ai", "ai", {"orientation": "v", "row": 0, "col": 0}),
                    ("human", "human", {"orientation": "h", "row": 1, "col": 0}),
                    ("ai", "ai", {"orientation": "v", "row": 0, "col": 1}),
                ]
                for role, player, move in moves:
                    room = play_move(room["room_id"], role, player, move)
                self.assertEqual(room["turn"], "ai")
                self.assertIn("行动权保留", room["action_note"])


class JungleTests(unittest.TestCase):
    def setUp(self):
        self.game = Jungle()

    def empty_state(self):
        state = self.game.initial_state()
        state["board"] = [[None for _ in range(7)] for _ in range(9)]
        return state

    def test_public_legal_moves_are_generated_by_the_same_validator(self):
        state = self.game.initial_state()
        public = self.game.public_state(state, [])
        self.assertTrue(public["legal_moves_by_mark"]["X"])
        for action in public["legal_moves_by_mark"]["X"]:
            self.game.validate_move(state, action, "X")
        self.assertNotIn(
            {"from_row": 6, "from_col": 0, "to_row": 5, "to_col": 1},
            public["legal_moves_by_mark"]["X"],
        )

    def test_rat_enters_water_and_elephant_cannot_capture_rat(self):
        state = self.empty_state()
        state["board"][3][0] = "X:R"
        self.game.validate_move(
            state,
            {"from_row": 3, "from_col": 0, "to_row": 3, "to_col": 1},
            "X",
        )
        state["board"][3][0] = None
        state["board"][2][0] = "X:E"
        state["board"][2][1] = "O:R"
        with self.assertRaisesRegex(ValueError, "不能吃"):
            self.game.validate_move(
                state,
                {"from_row": 2, "from_col": 0, "to_row": 2, "to_col": 1},
                "X",
            )

    def test_water_rat_cannot_capture_land_elephant(self):
        state = self.empty_state()
        state["board"][3][1] = "X:R"
        state["board"][3][0] = "O:E"
        with self.assertRaisesRegex(ValueError, "不能吃"):
            self.game.validate_move(
                state,
                {"from_row": 3, "from_col": 1, "to_row": 3, "to_col": 0},
                "X",
            )

    def test_lion_tiger_jump_and_rat_block(self):
        state = self.empty_state()
        state["board"][6][1] = "X:T"
        jump = {"from_row": 6, "from_col": 1, "to_row": 2, "to_col": 1}
        self.game.validate_move(state, jump, "X")
        state["board"][4][1] = "O:R"
        with self.assertRaisesRegex(ValueError, "阻挡"):
            self.game.validate_move(state, jump, "X")

    def test_enemy_in_own_trap_can_be_captured_by_any_beast(self):
        state = self.empty_state()
        state["board"][7][2] = "X:R"
        state["board"][7][3] = "O:E"
        self.game.validate_move(
            state,
            {"from_row": 7, "from_col": 2, "to_row": 7, "to_col": 3},
            "X",
        )

    def test_den_rules_and_no_pieces_win(self):
        state = self.empty_state()
        state["board"][8][2] = "X:C"
        with self.assertRaisesRegex(ValueError, "己方兽穴"):
            self.game.validate_move(
                state,
                {"from_row": 8, "from_col": 2, "to_row": 8, "to_col": 3},
                "X",
            )
        state["board"][8][2] = None
        state["board"][1][3] = "X:C"
        move = {"from_row": 1, "from_col": 3, "to_row": 0, "to_col": 3}
        result = self.game.apply_move(state, move, "X")
        self.assertEqual(self.game.check_winner(result.state), "X")
        self.assertIn("兽穴", result.note)

    def test_opponent_with_no_pieces_loses(self):
        state = self.empty_state()
        state["board"][4][0] = "X:C"
        result = self.game.apply_move(
            state,
            {"from_row": 4, "from_col": 0, "to_row": 5, "to_col": 0},
            "X",
        )
        self.assertEqual(self.game.check_winner(result.state), "X")
        self.assertIn("无棋子", result.note)


class RegistryErrorTests(unittest.TestCase):
    def test_every_plugin_has_shared_rules_and_move_format(self):
        self.assertEqual(len(GAMES), 25)

        for plugin in GAMES.values():
            self.assertTrue(plugin.rules_text)
            self.assertTrue(plugin.move_format)
            self.assertLessEqual(plugin.max_players, 6)
            if plugin.game_type == "guandan":
                self.assertEqual(plugin.min_players, 4)
                self.assertEqual(plugin.resolved_allowed_player_counts(), (4,))
            else:
                self.assertGreaterEqual(plugin.min_players, 2)
                self.assertIn(plugin.min_players, plugin.resolved_allowed_player_counts())

    def test_all_move_formats_follow_shared_mcp_action_layering(self):
        self.assertEqual(len(GAMES), 25)
        for game_type, plugin in GAMES.items():
            with self.subTest(game_type=game_type):
                self.assertNotIn('"revision":当前版本', plugin.move_format)

        for game_type in (
            "doudizhu", "mahjong", "texas_holdem", "zhajinhua",
        ):
            with self.subTest(params_move=game_type):
                self.assertIn("params.move", GAMES[game_type].move_format)

        uno = GAMES["uno"].move_format
        for action in (
            "challenge_wild_draw_four", "accept_draw_four", "catch_uno",
        ):
            with self.subTest(uno_action=action):
                self.assertRegex(uno, rf"move\.action[^\u3002；]*{action}|{action}[^\u3002；]*move\.action")

        for game_type in (
            "junqi", "go", "chinese_checkers", "chess",
        ):
            with self.subTest(wrapped_examples=game_type):
                self.assertIn('{"move":', GAMES[game_type].move_format)

    def test_rules_text_does_not_teach_mcp_submission_syntax(self):
        for game_type, plugin in GAMES.items():
            with self.subTest(game_type=game_type):
                for submission_term in (
                    "params.move", "duel action", '{"action"', '{"move"',
                    "legal_action_spec",
                ):
                    self.assertNotIn(submission_term, plugin.rules_text)


    def test_player_rules_use_the_light_structure_without_move_schema_terms(self):
        heading_pattern = re.compile(r"^【[^【】]+】$", re.MULTILINE)
        for game_type, plugin in GAMES.items():
            with self.subTest(game_type=game_type):
                rules = plugin.rules_text
                self.assertGreaterEqual(len(heading_pattern.findall(rules)), 3)
                self.assertIn("\n\n", rules)
                for developer_term in (
                    "row", "legal_actions", "legal_moves", '{"move"', "stake",
                ):
                    self.assertNotIn(developer_term, rules)

        for game_type in ("aeroplane_chess", "chinese_checkers", "yahtzee"):
            with self.subTest(long_rules=game_type):
                rules = GAMES[game_type].rules_text
                self.assertGreaterEqual(len(heading_pattern.findall(rules)), 4)
                self.assertIn("\n- ", rules)

    def test_long_variant_rules_keep_their_player_facing_distinctions(self):
        expectations = {
            "aeroplane_chess": (
                "只有掷出 6", "继续掷骰", "连续第三个 6", "前跳 4 格",
                "第 21 格", "第 33 格", "所有对手机", "点数刚好",
                "超出点数", "反向退回",
            ),
            "chinese_checkers": (
                "121 孔", "2、3、4、6 人", "连续跳跃", "其他四个角营不能作为回合终点",
                "不能离开", "anti-spoiling",
            ),
            "yahtzee": (
                "13 类", "63 分", "35 分", "重复快艇", "Joker", "不支持筹码",
            ),
            "liars_dice": (
                "每人初始 5 枚六面骰", "1 点不作万能点", "数量相同而点数更大",
                "质疑", "淘汰", "最后一名",
            ),
        }
        for game_type, phrases in expectations.items():
            with self.subTest(game_type=game_type):
                rules = GAMES[game_type].rules_text
                for phrase in phrases:
                    self.assertIn(phrase, rules)

    def test_unknown_game_lists_every_available_type(self):
        with self.assertRaises(DuelError) as caught:
            create_room("definitely_unknown", "human_first", "human", "human")
        for game_type in (
            "tictactoe", "gomoku", "othello",
            "connect4", "banqi", "checkers", "chess", "dots_boxes",
            "liars_dice", "yahtzee", "jungle", "junqi", "xiangqi",
            "aeroplane_chess", "chinese_checkers", "uno", "blackjack",
            "texas_holdem",

            "gandengyan", "train_cards", "doudizhu", "guandan",

        ):
            self.assertIn(game_type, caught.exception.message)


if __name__ == "__main__":
    unittest.main()
