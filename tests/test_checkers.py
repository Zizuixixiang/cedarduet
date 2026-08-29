import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from app import database
from app.framework import (
    DuelError,
    create_room,
    get_room,
    list_timeline,
    play_move,
    project_room_for_viewer,
)
from app.games import GAMES, game_catalog
from app.games.checkers import Checkers


class CheckersRuleTests(unittest.TestCase):
    def setUp(self):
        self.game = Checkers()

    def position(
        self,
        pieces: dict[tuple[int, int], str],
        turn: str = "X",
    ) -> dict:
        state = self.game.initial_state()
        state["board"] = [[None for _ in range(8)] for _ in range(8)]
        for (row, col), piece in pieces.items():
            state["board"][row][col] = piece
        state["forced_piece"] = None
        state["captured_during_turn"] = []
        state["action_history"] = []
        state.pop("last_move", None)
        state.pop("winner_mark", None)
        state.pop("terminal_reason", None)
        self.game._sync_turn(state, turn)
        self.game._update_counts(state)
        self.game._reset_draw_tracking(state)
        return state

    def test_initial_position_is_8x8_dark_squares_with_twelve_each(self):
        state = self.game.initial_state()
        self.assertEqual(
            (state["size"], state["rows"], state["cols"]), (8, 8, 8)
        )
        self.assertEqual(state["board_kind"], "checkers")
        self.assertEqual(state["piece_counts"], {"X": 12, "O": 12})
        self.assertEqual(state["king_counts"], {"X": 0, "O": 0})
        self.assertEqual(state["turn_mark"], "X")
        self.assertFalse(state["must_capture"])
        self.assertEqual(len(state["legal_moves"]), 7)
        for row in range(8):
            for col in range(8):
                piece = state["board"][row][col]
                if (row + col) % 2 == 0:
                    self.assertIsNone(piece)
                elif row <= 2:
                    self.assertEqual(piece, "O:m")
                elif row >= 5:
                    self.assertEqual(piece, "X:m")

    def test_player_rules_explain_both_wcfd_automatic_draws(self):
        rules = self.game.rules_text
        self.assertIn("【胜负】", rules)
        self.assertIn("局面第三次出现", rules)
        self.assertIn("双方各自此前连续 40 手", rules)
        self.assertIn("王棋的普通移动不会中断", rules)
        for internal_name in ("draw_tracking", "position_counts", "schema"):
            self.assertNotIn(internal_name, rules)

    def test_men_move_only_forward_and_coordinates_are_strict(self):
        state = self.position({(5, 0): "X:m", (0, 1): "O:m"})
        move = {"from_row": 5, "from_col": 0, "to_row": 4, "to_col": 1}
        self.game.validate_move(state, move, "X")
        result = self.game.apply_move(state, move, "X")
        self.assertFalse(result.retain_turn)
        self.assertEqual(state["board"][4][1], "X:m")
        self.assertEqual(state["turn_mark"], "O")
        self.assertEqual(state["last_move"]["captured"], None)

        backward = self.position({(5, 2): "X:m", (0, 1): "O:m"})
        with self.assertRaisesRegex(ValueError, "不合法"):
            self.game.validate_move(
                backward,
                {"from_row": 5, "from_col": 2, "to_row": 6, "to_col": 1},
                "X",
            )
        with self.assertRaisesRegex(ValueError, "深色格"):
            self.game.validate_move(
                backward,
                {"from_row": 5, "from_col": 1, "to_row": 4, "to_col": 2},
                "X",
            )
        with self.assertRaisesRegex(ValueError, "四个字段"):
            self.game.validate_move(
                backward,
                {
                    "from_row": 5, "from_col": 2, "to_row": 4,
                    "to_col": 1, "capture": False,
                },
                "X",
            )
        with self.assertRaisesRegex(ValueError, "必须是整数"):
            self.game.validate_move(
                backward,
                {"from_row": True, "from_col": 2, "to_row": 4, "to_col": 1},
                "X",
            )

    def test_any_capture_forbids_every_ordinary_move_without_maximum_rule(self):
        state = self.position({
            (5, 0): "X:m", (5, 4): "X:m",
            (4, 1): "O:m", (4, 5): "O:m", (0, 1): "O:m",
        })
        expected = {
            (5, 0, 3, 2),
            (5, 4, 3, 6),
        }
        self.assertTrue(state["must_capture"])
        self.assertEqual(
            {
                (
                    move["from_row"], move["from_col"],
                    move["to_row"], move["to_col"],
                )
                for move in state["legal_moves"]
            },
            expected,
        )
        with self.assertRaisesRegex(ValueError, "有可吃子"):
            self.game.validate_move(
                state,
                {"from_row": 5, "from_col": 4, "to_row": 4, "to_col": 3},
                "X",
            )
        # English draughts permits either available capture; it does not force
        # the route that would eventually take the greatest number of pieces.
        self.game.validate_move(
            state,
            {"from_row": 5, "from_col": 0, "to_row": 3, "to_col": 2},
            "X",
        )

    def test_multi_jump_retains_turn_and_locks_the_same_piece(self):
        state = self.position({
            (6, 1): "X:m", (6, 7): "X:m",
            (5, 2): "O:m", (3, 4): "O:m", (0, 1): "O:m",
        })
        first = {"from_row": 6, "from_col": 1, "to_row": 4, "to_col": 3}
        result = self.game.apply_move(state, first, "X")
        self.assertTrue(result.retain_turn)
        self.assertEqual(state["turn_mark"], "X")
        self.assertEqual(state["forced_piece"], {"row": 4, "col": 3})
        self.assertEqual(state["legal_moves"], [{
            "from_row": 4, "from_col": 3, "to_row": 2, "to_col": 5,
        }])
        self.assertIsNone(state["board"][5][2])
        with self.assertRaisesRegex(ValueError, "同一枚棋"):
            self.game.validate_move(
                state,
                {"from_row": 6, "from_col": 7, "to_row": 5, "to_col": 6},
                "X",
            )

        second = {"from_row": 4, "from_col": 3, "to_row": 2, "to_col": 5}
        result = self.game.apply_move(state, second, "X")
        self.assertFalse(result.retain_turn)
        self.assertIsNone(state["forced_piece"])
        self.assertEqual(state["captured_during_turn"], [])
        self.assertEqual(state["turn_mark"], "O")
        self.assertEqual(state["last_move"]["jump_number"], 2)

    def test_king_moves_and_captures_in_both_directions_without_flying(self):
        state = self.position({
            (2, 3): "X:k", (3, 4): "O:m", (0, 1): "O:m",
        })
        backward_capture = {
            "from_row": 2, "from_col": 3, "to_row": 4, "to_col": 5,
        }
        self.assertEqual(state["legal_moves"], [backward_capture])
        self.game.validate_move(state, backward_capture, "X")
        with self.assertRaisesRegex(ValueError, "不合法"):
            self.game.validate_move(
                state,
                {"from_row": 2, "from_col": 3, "to_row": 5, "to_col": 6},
                "X",
            )

        simple = self.position({(3, 2): "X:k", (0, 1): "O:m"})
        self.game.validate_move(
            simple,
            {"from_row": 3, "from_col": 2, "to_row": 4, "to_col": 1},
            "X",
        )

    def test_reaching_king_row_promotes_and_ends_capture_turn(self):
        state = self.position({
            (2, 1): "X:m",
            (1, 2): "O:m",
            # A newly crowned king could jump this piece backwards under a
            # different variant, but English draughts ends the turn on crowning.
            (1, 4): "O:m",
            (6, 1): "O:m",
        })
        result = self.game.apply_move(
            state,
            {"from_row": 2, "from_col": 1, "to_row": 0, "to_col": 3},
            "X",
        )
        self.assertFalse(result.retain_turn)
        self.assertEqual(state["board"][0][3], "X:k")
        self.assertTrue(state["last_move"]["promoted"])
        self.assertFalse(state["last_move"]["chain_continues"])
        self.assertIsNone(state["forced_piece"])
        self.assertEqual(state["turn_mark"], "O")
        self.assertIn("本手结束", result.note)

    def test_no_pieces_or_no_legal_actions_loses(self):
        no_pieces = self.position({(4, 1): "X:m", (3, 2): "O:m"})
        result = self.game.apply_move(
            no_pieces,
            {"from_row": 4, "from_col": 1, "to_row": 2, "to_col": 3},
            "X",
        )
        self.assertEqual(self.game.check_winner(result.state), "X")
        self.assertEqual(result.state["terminal_reason"], "no_pieces")

        blocked = self.position({(2, 1): "X:m", (7, 0): "O:m"})
        result = self.game.apply_move(
            blocked,
            {"from_row": 2, "from_col": 1, "to_row": 1, "to_col": 0},
            "X",
        )
        self.assertEqual(self.game.check_winner(result.state), "X")
        self.assertEqual(result.state["terminal_reason"], "no_legal_moves")

    def test_npc_actions_are_the_same_authoritative_list_and_follow_chain(self):
        state = self.position({
            (6, 1): "X:m", (5, 2): "O:m", (3, 4): "O:m", (0, 1): "O:m",
        })
        actor = {"player_id": "npc:test", "token": "X"}
        participants = [actor, {"player_id": "human", "token": "O"}]
        actions = self.game.npc_legal_actions(state, actor, participants)
        self.assertEqual(actions, state["legal_moves"])
        actions[0]["to_row"] = 99
        self.assertNotEqual(actions, state["legal_moves"])
        self.assertEqual(
            self.game.npc_legal_actions(
                state, {"player_id": "other", "token": "O"}, participants
            ),
            [],
        )
        first = deepcopy(state["legal_moves"][0])
        self.game.apply_move(state, first, "X")
        self.assertEqual(
            self.game.npc_legal_actions(state, actor, participants),
            [{"from_row": 4, "from_col": 3, "to_row": 2, "to_col": 5}],
        )

    def test_public_board_projection_has_no_private_or_viewer_specific_state(self):
        state = self.game.initial_state()
        participants = [
            {"player_id": "human", "token": "X"},
            {"player_id": "ai", "token": "O"},
        ]
        human_public = self.game.public_state(state, participants)
        ai_public = self.game.public_state(state, list(reversed(participants)))
        self.assertEqual(human_public, ai_public)
        self.assertEqual(human_public["legal_moves"], state["legal_moves"])
        self.assertIsNot(human_public, state)
        for viewer in participants:
            self.assertEqual(self.game.private_state(state, viewer, participants), {})
        serialized = json.dumps(human_public, ensure_ascii=False)
        for hidden_key in ("private_state", "dice_by_player", "hands_by_player"):
            self.assertNotIn(hidden_key, serialized)

    def test_repetition_draws_on_third_occurrence_not_second(self):
        state = self.position({(5, 0): "X:k", (2, 7): "O:k"})
        cycle = (
            ("X", (5, 0, 4, 1)),
            ("O", (2, 7, 3, 6)),
            ("X", (4, 1, 5, 0)),
            ("O", (3, 6, 2, 7)),
        )

        for mark, coords in cycle:
            from_row, from_col, to_row, to_col = coords
            result = self.game.apply_move(state, {
                "from_row": from_row,
                "from_col": from_col,
                "to_row": to_row,
                "to_col": to_col,
            }, mark)
        initial_key = self.game._position_key(state)
        self.assertEqual(
            state["draw_tracking"]["position_counts"][initial_key], 2
        )
        self.assertIsNone(self.game.check_winner(state))
        self.assertNotIn("draw_reason", state)

        for mark, coords in cycle:
            from_row, from_col, to_row, to_col = coords
            result = self.game.apply_move(state, {
                "from_row": from_row,
                "from_col": from_col,
                "to_row": to_row,
                "to_col": to_col,
            }, mark)
        self.assertEqual(
            state["draw_tracking"]["position_counts"][initial_key], 3
        )
        self.assertEqual(state["draw_reason"], "threefold_repetition")
        self.assertEqual(state["terminal_reason"], "threefold_repetition")
        self.assertEqual(self.game.check_winner(state), "draw")
        self.assertIn("第三次", result.note)

    def test_position_key_covers_turn_rank_and_forced_jump_state(self):
        state = self.position({(5, 0): "X:k", (2, 7): "O:k"})
        base_key = self.game._position_key(state)

        other_turn = deepcopy(state)
        other_turn["turn_mark"] = "O"
        self.assertNotEqual(self.game._position_key(other_turn), base_key)

        other_rank = deepcopy(state)
        other_rank["board"][5][0] = "X:m"
        self.assertNotEqual(self.game._position_key(other_rank), base_key)

        forced = deepcopy(state)
        forced["forced_piece"] = {"row": 5, "col": 0}
        forced["captured_during_turn"] = [
            {"row": 4, "col": 1, "piece": "O:m"}
        ]
        self.assertNotEqual(self.game._position_key(forced), base_key)

    def test_both_players_reaching_forty_quiet_king_moves_draws(self):
        state = self.position({(5, 0): "X:k", (2, 7): "O:k"})
        state["draw_tracking"]["no_progress_moves"] = {"X": 39, "O": 40}
        result = self.game.apply_move(state, {
            "from_row": 5, "from_col": 0, "to_row": 4, "to_col": 1,
        }, "X")

        self.assertEqual(
            state["draw_tracking"]["no_progress_moves"], {"X": 40, "O": 40}
        )
        self.assertEqual(state["draw_reason"], "forty_move_rule")
        self.assertEqual(self.game.check_winner(state), "draw")
        self.assertIn("40 手", result.note)

    def test_man_advance_and_promotion_reset_only_the_movers_counter(self):
        ordinary = self.position({(5, 0): "X:m", (2, 7): "O:k"})
        ordinary["draw_tracking"]["no_progress_moves"] = {"X": 39, "O": 40}
        self.game.apply_move(ordinary, {
            "from_row": 5, "from_col": 0, "to_row": 4, "to_col": 1,
        }, "X")
        self.assertEqual(
            ordinary["draw_tracking"]["no_progress_moves"], {"X": 0, "O": 40}
        )
        self.assertIsNone(self.game.check_winner(ordinary))

        promotion = self.position({(1, 2): "X:m", (2, 7): "O:k"})
        promotion["draw_tracking"]["no_progress_moves"] = {"X": 39, "O": 40}
        self.game.apply_move(promotion, {
            "from_row": 1, "from_col": 2, "to_row": 0, "to_col": 1,
        }, "X")
        self.assertEqual(promotion["board"][0][1], "X:k")
        self.assertEqual(
            promotion["draw_tracking"]["no_progress_moves"], {"X": 0, "O": 40}
        )
        self.assertIsNone(self.game.check_winner(promotion))

    def test_capture_resets_the_movers_no_progress_counter(self):
        state = self.position({
            (5, 0): "X:k", (4, 1): "O:m", (0, 7): "O:k",
        })
        state["draw_tracking"]["no_progress_moves"] = {"X": 39, "O": 40}
        self.game.apply_move(state, {
            "from_row": 5, "from_col": 0, "to_row": 3, "to_col": 2,
        }, "X")
        self.assertEqual(
            state["draw_tracking"]["no_progress_moves"], {"X": 0, "O": 40}
        )
        self.assertIsNone(self.game.check_winner(state))

    def test_multi_jump_settles_draw_tracking_only_after_complete_turn(self):
        state = self.position({
            (6, 1): "X:k", (5, 2): "O:m", (3, 4): "O:m",
            (0, 7): "O:k",
        })
        state["draw_tracking"]["no_progress_moves"] = {"X": 39, "O": 40}
        before = deepcopy(state["draw_tracking"])

        first = self.game.apply_move(state, {
            "from_row": 6, "from_col": 1, "to_row": 4, "to_col": 3,
        }, "X")
        self.assertTrue(first.retain_turn)
        self.assertEqual(state["draw_tracking"], before)
        self.assertIsNone(self.game.check_winner(state))

        restored = json.loads(json.dumps(state, ensure_ascii=False))
        second = self.game.apply_move(restored, {
            "from_row": 4, "from_col": 3, "to_row": 2, "to_col": 5,
        }, "X")
        self.assertFalse(second.retain_turn)
        self.assertEqual(
            restored["draw_tracking"]["no_progress_moves"],
            {"X": 0, "O": 40},
        )
        self.assertEqual(
            sum(restored["draw_tracking"]["position_counts"].values()), 2
        )
        self.assertIsNone(self.game.check_winner(restored))

    def test_win_takes_priority_over_forty_move_draw_threshold(self):
        state = self.position({(5, 0): "X:k", (7, 0): "O:m"})
        state["draw_tracking"]["no_progress_moves"] = {"X": 39, "O": 40}
        self.game.apply_move(state, {
            "from_row": 5, "from_col": 0, "to_row": 4, "to_col": 1,
        }, "X")

        self.assertEqual(self.game.check_winner(state), "X")
        self.assertEqual(state["terminal_reason"], "no_legal_moves")
        self.assertNotIn("draw_reason", state)


class CheckersFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-checkers-")
        self.addCleanup(self.temporary.cleanup)
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()
        self.game = GAMES["checkers"]

    def install_position(self, room: dict, pieces: dict[tuple[int, int], str]) -> None:
        state = self.game.initial_state()
        state["board"] = [[None for _ in range(8)] for _ in range(8)]
        for (row, col), piece in pieces.items():
            state["board"][row][col] = piece
        state["marks"] = deepcopy(room["board_state"]["marks"])
        state["marks_by_player"] = deepcopy(
            room["board_state"]["marks_by_player"]
        )
        state["forced_piece"] = None
        state["captured_during_turn"] = []
        self.game._sync_turn(state, "X")
        self.game._update_counts(state)
        self.game._reset_draw_tracking(state)
        conn = database.connect()
        try:
            conn.execute(
                "UPDATE rooms SET board_state = ? WHERE room_id = ?",
                (json.dumps(state, ensure_ascii=False), room["room_id"]),
            )
            conn.commit()
        finally:
            conn.close()

    def test_catalog_declares_two_players_npcs_board_and_stakes(self):
        declared = next(
            item for item in game_catalog() if item["game_type"] == "checkers"
        )
        self.assertEqual(declared["display_name"], "西洋跳棋")
        self.assertEqual(declared["category"], "board")
        self.assertEqual(declared["allowed_player_counts"], [2])
        self.assertTrue(declared["supports_npcs"])
        self.assertTrue(declared["supports_stakes"])

    def test_framework_retains_same_player_for_each_required_jump(self):
        room = create_room(
            "checkers", "human_first", "human", "human-1", "ai-1"
        )
        self.install_position(room, {
            (6, 1): "X:m", (6, 7): "X:m", (5, 2): "O:m",
            (3, 4): "O:m", (0, 1): "O:m",
        })
        first = play_move(
            room["room_id"], "human", "human-1",
            {"from_row": 6, "from_col": 1, "to_row": 4, "to_col": 3},
            expected_revision=0,
        )
        self.assertEqual(first["status"], "playing")
        self.assertEqual(first["current_player_id"], "human-1")
        self.assertEqual(first["turn"], "human")
        self.assertEqual(first["revision"], 1)
        self.assertEqual(first["board_state"]["forced_piece"], {"row": 4, "col": 3})
        with self.assertRaisesRegex(DuelError, "同一枚棋"):
            play_move(
                room["room_id"], "human", "human-1",
                {"from_row": 6, "from_col": 7, "to_row": 5, "to_col": 6},
                expected_revision=1,
            )
        second = play_move(
            room["room_id"], "human", "human-1",
            {"from_row": 4, "from_col": 3, "to_row": 2, "to_col": 5},
            expected_revision=1,
        )
        self.assertEqual(second["current_player_id"], "ai-1")
        self.assertEqual(second["turn"], "ai")
        self.assertEqual(second["revision"], 2)

    def test_authenticated_viewers_receive_same_public_board_and_empty_private(self):
        room = create_room(
            "checkers", "human_first", "human", "human-view", "ai-view"
        )
        human = project_room_for_viewer(room, "human-view")
        ai = project_room_for_viewer(room, "ai-view")
        self.assertEqual(human["board_state"], ai["board_state"])
        self.assertEqual(human["private_state"], {})
        self.assertEqual(ai["private_state"], {})
        self.assertEqual(len(human["board_state"]["legal_moves"]), 7)

    def test_repetition_survives_database_reload_and_uses_result_event_flow(self):
        room = create_room(
            "checkers", "human_first", "human", "human-draw", "ai-draw"
        )
        self.install_position(room, {(5, 0): "X:k", (2, 7): "O:k"})
        cycle = (
            ("human", "human-draw", (5, 0, 4, 1)),
            ("ai", "ai-draw", (2, 7, 3, 6)),
            ("human", "human-draw", (4, 1, 5, 0)),
            ("ai", "ai-draw", (3, 6, 2, 7)),
        )

        for revision, (role, player_id, coords) in enumerate(cycle):
            from_row, from_col, to_row, to_col = coords
            room = play_move(
                room["room_id"], role, player_id,
                {
                    "from_row": from_row,
                    "from_col": from_col,
                    "to_row": to_row,
                    "to_col": to_col,
                },
                expected_revision=revision,
            )
        self.assertEqual(room["status"], "playing")
        restored = get_room(room["room_id"], "human", "human-draw")
        restored_key = self.game._position_key(restored["board_state"])
        self.assertEqual(
            restored["board_state"]["draw_tracking"]["position_counts"][
                restored_key
            ],
            2,
        )
        self.assertEqual(
            restored["board_state"]["draw_tracking"]["no_progress_moves"],
            {"X": 2, "O": 2},
        )

        for offset, (role, player_id, coords) in enumerate(cycle, start=4):
            from_row, from_col, to_row, to_col = coords
            room = play_move(
                room["room_id"], role, player_id,
                {
                    "from_row": from_row,
                    "from_col": from_col,
                    "to_row": to_row,
                    "to_col": to_col,
                },
                expected_revision=offset,
            )
            if offset < 7:
                self.assertEqual(room["status"], "playing")

        self.assertEqual(room["status"], "finished")
        self.assertEqual(room["winner"], "draw")
        self.assertEqual(room["result"], {"draw": True})
        self.assertIsNone(room["current_player_id"])
        self.assertEqual(
            room["board_state"]["terminal_reason"], "threefold_repetition"
        )
        result_events = [
            event
            for event in list_timeline(
                room["room_id"], viewer_player_id="human-draw"
            )
            if event["event_type"] == "result"
        ]
        self.assertEqual(len(result_events), 1)
        self.assertEqual(result_events[0]["text"], "和棋")


if __name__ == "__main__":
    unittest.main()
