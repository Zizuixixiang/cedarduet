import base64
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import httpx

from app import chips, database, framework
from app import main as main_module
from app.games import GAMES, game_catalog
from app.games.xiangqi import Xiangqi


def move(from_row: int, from_col: int, to_row: int, to_col: int) -> dict:
    return {
        "from_row": from_row,
        "from_col": from_col,
        "to_row": to_row,
        "to_col": to_col,
    }


class XiangqiRuleTests(unittest.TestCase):
    def setUp(self):
        self.game = Xiangqi()

    def assert_move(
        self, state: dict, payload: dict, expected: bool, mark: str = "X"
    ):
        if expected:
            self.game.validate_move(state, payload, mark)
        else:
            with self.assertRaises(ValueError):
                self.game.validate_move(state, payload, mark)

    def test_initial_position_is_red_to_move_with_authoritative_targets(self):
        state = self.game.initial_state()
        self.assertEqual((state["rows"], state["cols"]), (10, 9))
        self.assertEqual(state["turn_color"], "r")
        self.assertEqual(len(state["legal_moves"]), 44)
        self.assertEqual(state["board"][9][4], "r:k")
        self.assertEqual(state["board"][0][4], "b:k")
        self.assert_move(state, move(9, 0, 8, 0), True)
        self.assert_move(state, move(0, 0, 1, 0), False)

    def test_coordinate_and_basic_illegal_move_errors(self):
        state = self.game.initial_state()
        for payload in (
            move(True, 0, 8, 0),
            move(9, 0, 10, 0),
            move(9, 0, 9, 0),
        ):
            with self.subTest(payload=payload):
                self.assert_move(state, payload, False)
        with self.assertRaisesRegex(ValueError, "起点没有棋子"):
            self.game.validate_move(state, move(8, 0, 7, 0), "X")
        with self.assertRaisesRegex(ValueError, "只能移动自己的棋子"):
            self.game.validate_move(state, move(0, 0, 1, 0), "X")

    def test_both_sides_can_make_legal_moves_in_turn(self):
        state = self.game.initial_state()
        red = self.game.apply_move(state, move(9, 0, 8, 0), "X")
        self.assertEqual(red.state["turn_color"], "b")
        self.game.validate_move(red.state, move(0, 0, 1, 0), "O")
        black = self.game.apply_move(red.state, move(0, 0, 1, 0), "O")
        self.assertEqual(black.state["turn_color"], "r")
        self.assertEqual(black.state["move_history"], ["a0a1", "a9a8"])

    def test_facing_generals_and_self_check_are_rejected(self):
        state = self.game.state_from_fen(
            "4k4/9/9/9/9/9/9/9/4R4/4K4 r - - 0 1"
        )
        self.assert_move(state, move(8, 4, 8, 3), False)
        self.assert_move(state, move(8, 4, 7, 4), True)

    def test_piece_cannot_expose_own_general_to_an_enemy_rook(self):
        state = self.game.state_from_fen(
            "4k4/9/9/9/4r4/9/9/9/4R4/4K4 r - - 0 1"
        )
        self.assertFalse(state["in_check"])
        self.assert_move(state, move(8, 4, 8, 3), False)
        self.assert_move(state, move(8, 4, 7, 4), True)

    def test_cannon_requires_exactly_one_screen_to_capture(self):
        one_screen = self.game.state_from_fen(
            "r3k4/9/9/9/P3P4/9/9/9/9/C3K4 r - - 0 1"
        )
        no_screen = self.game.state_from_fen(
            "r3k4/9/9/9/4P4/9/9/9/9/C3K4 r - - 0 1"
        )
        two_screens = self.game.state_from_fen(
            "r3k4/9/c8/9/P3P4/9/9/9/9/C3K4 r - - 0 1"
        )
        capture = move(9, 0, 0, 0)
        self.assert_move(one_screen, capture, True)
        self.assert_move(no_screen, capture, False)
        self.assert_move(two_screens, capture, False)

    def test_horse_leg_blocks_the_corresponding_knight_moves(self):
        free = self.game.state_from_fen(
            "4k4/9/9/9/4P4/9/9/2N6/9/4K4 r - - 0 1"
        )
        blocked = self.game.state_from_fen(
            "4k4/9/9/9/4P4/9/9/2NR5/9/4K4 r - - 0 1"
        )
        knight_move = move(7, 2, 6, 4)
        self.assert_move(free, knight_move, True)
        self.assert_move(blocked, knight_move, False)

    def test_elephant_eye_and_river_limit_are_enforced(self):
        free = self.game.state_from_fen(
            "4k4/9/9/9/4P4/9/9/9/9/2B1K4 r - - 0 1"
        )
        blocked = self.game.state_from_fen(
            "4k4/9/9/9/4P4/9/9/9/3R5/2B1K4 r - - 0 1"
        )
        elephant_move = move(9, 2, 7, 4)
        self.assert_move(free, elephant_move, True)
        self.assert_move(blocked, elephant_move, False)
        river_edge = self.game.state_from_fen(
            "4k4/9/9/9/4P4/2B6/9/9/9/4K4 r - - 0 1"
        )
        self.assert_move(river_edge, move(5, 2, 3, 0), False)

    def test_pawn_direction_palace_and_adviser_rules(self):
        before_river = self.game.state_from_fen(
            "4k4/9/9/9/9/9/4P4/9/9/4K4 r - - 0 1"
        )
        self.assert_move(before_river, move(6, 4, 5, 4), True)
        self.assert_move(before_river, move(6, 4, 6, 3), False)
        after_river = self.game.state_from_fen(
            "3k5/9/9/9/4P4/9/9/9/9/4K4 r - - 0 1"
        )
        self.assert_move(after_river, move(4, 4, 4, 3), True)
        self.assert_move(after_river, move(4, 4, 5, 4), False)

        palace = self.game.state_from_fen(
            "4k4/9/9/9/4P4/9/9/3K5/9/9 r - - 0 1"
        )
        self.assert_move(palace, move(7, 3, 7, 2), False)
        self.assert_move(palace, move(7, 3, 6, 3), False)
        initial = self.game.initial_state()
        self.assert_move(initial, move(9, 3, 8, 4), True)
        self.assert_move(initial, move(9, 3, 8, 3), False)

    def test_capture_and_check_are_recorded(self):
        state = self.game.state_from_fen(
            "4k4/9/9/9/4p4/4R4/9/9/9/3K5 r - - 0 1"
        )
        result = self.game.apply_move(state, move(5, 4, 4, 4), "X")
        self.assertIsNone(result.state["board"][5][4])
        self.assertEqual(result.state["board"][4][4], "r:r")
        self.assertEqual(result.state["last_move"]["captured"], "b:p")
        self.assertTrue(result.state["in_check"])
        self.assertEqual(result.note, "将军。")

    def test_checked_side_can_make_a_legal_response(self):
        state = self.game.state_from_fen(
            "4k4/4R4/9/9/9/9/9/9/9/3K5 b - - 0 1"
        )
        self.assertTrue(state["in_check"])
        self.assert_move(state, move(0, 4, 0, 5), True, mark="O")
        result = self.game.apply_move(state, move(0, 4, 0, 5), "O")
        self.assertFalse(result.state["in_check"])
        self.assertEqual(result.state["turn_color"], "r")

    def test_checkmate_and_stalemate_positions_are_distinct(self):
        checkmate = self.game.state_from_fen(
            "rnbakab1r/9/1c5c1/p1p5p/4p1p2/4P1P2/"
            "P1P3nCP/1C3A3/4NK3/RNB2AB1R r - - 0 1"
        )
        stalemate = self.game.state_from_fen(
            "3k5/R8/9/9/9/9/9/9/9/4K4 b - - 0 1"
        )
        self.assertTrue(checkmate["in_checkmate"])
        self.assertTrue(checkmate["in_check"])
        self.assertEqual(checkmate["legal_moves"], [])
        self.assertTrue(stalemate["in_stalemate"])
        self.assertFalse(stalemate["in_check"])
        self.assertEqual(stalemate["legal_moves"], [])

    def test_move_can_deliver_checkmate_or_stalemate(self):
        mate = self.game.state_from_fen(
            "3k5/4R4/3R5/9/9/9/9/9/9/3K5 r - - 0 1"
        )
        result = self.game.apply_move(mate, move(2, 3, 1, 3), "X")
        self.assertTrue(result.state["in_checkmate"])
        self.assertEqual(result.state["terminal_reason"], "checkmate")
        self.assertEqual(self.game.check_winner(result.state), "X")
        self.assertIn("将死", result.note)

        stale = self.game.state_from_fen(
            "3k5/9/R8/9/9/9/9/9/9/4K4 r - - 0 1"
        )
        result = self.game.apply_move(stale, move(2, 0, 1, 0), "X")
        self.assertTrue(result.state["in_stalemate"])
        self.assertEqual(result.state["terminal_reason"], "stalemate")
        self.assertEqual(self.game.check_winner(result.state), "X")
        self.assertIn("困毙", result.note)

    def test_casual_draw_rules_exclude_approximate_threefold_path(self):
        bridge = (
            Path(__file__).resolve().parents[1]
            / "third_party" / "xiangqi_js" / "bridge.js"
        ).read_text(encoding="utf-8")
        self.assertNotIn("game.in_draw()", bridge)
        self.assertNotIn("game.in_threefold_repetition()", bridge)
        self.assertIn("不以简单的三次重复直接判和", self.game.rules_text)

    def test_sixty_move_no_capture_and_insufficient_material_are_named_draws(self):
        no_capture = self.game.state_from_fen(
            "4k4/9/9/9/4p4/9/9/9/9/3K5 r - - 120 61"
        )
        self.assertTrue(no_capture["in_draw"])
        self.assertEqual(no_capture["draw_reason"], "sixty_move_no_capture")

        insufficient = self.game.state_from_fen(
            "3ak4/9/9/9/9/9/9/9/9/3K5 r - - 0 1"
        )
        self.assertTrue(insufficient["in_draw"])
        self.assertEqual(insufficient["draw_reason"], "insufficient_material")


class XiangqiFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-xiangqi-")
        self.addCleanup(self.temporary.cleanup)
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()
        self.game = GAMES["xiangqi"]

    def test_catalog_is_bound_pair_only_and_has_no_npc_capability(self):
        item = next(
            item for item in game_catalog()
            if item["game_type"] == "xiangqi"
        )
        self.assertEqual(item["allowed_player_counts"], [2])
        self.assertFalse(item["supports_npcs"])
        self.assertTrue(item["supports_stakes"])
        with self.assertRaisesRegex(framework.DuelError, "未启用 NPC"):
            framework.create_room(
                "xiangqi",
                "human_first",
                "human",
                "human-npc",
                "npc:test",
                ordered_participants=[
                    {
                        "player_id": "human-npc",
                        "role": "human",
                        "participant_kind": "human",
                    },
                    {
                        "player_id": "npc:test",
                        "role": "ai",
                        "participant_kind": "system_npc",
                        "npc_persona_id": "test",
                    },
                ],
            )
        with self.assertRaisesRegex(framework.DuelError, "真实绑定小机"):
            framework.create_room(
                "xiangqi",
                "human_first",
                "human",
                "human-one",
                "human-two",
                ordered_participants=[
                    {"player_id": "human-one", "role": "human"},
                    {"player_id": "human-two", "role": "human"},
                ],
            )

    def test_selected_opener_is_red_for_human_first_and_ai_first(self):
        human_first = framework.create_room(
            "xiangqi", "human_first", "human", "human-open", "ai-open"
        )
        ai_first = framework.create_room(
            "xiangqi", "ai_first", "human", "human-open", "ai-open"
        )
        for room, expected_id in (
            (human_first, "human-open"),
            (ai_first, "ai-open"),
        ):
            with self.subTest(room_id=room["room_id"]):
                self.assertEqual(room["current_player_id"], expected_id)
                opener = next(
                    item for item in room["participants"]
                    if item["player_id"] == expected_id
                )
                self.assertEqual(opener["token"], "X")
                self.assertEqual(room["board_state"]["turn_color"], "r")

    def test_room_reconnect_turn_and_move_history_use_common_framework(self):
        room = framework.create_room(
            "xiangqi", "human_first", "human", "human-save", "ai-save"
        )
        room = framework.play_move(
            room["room_id"], "human", "human-save", move(9, 0, 8, 0)
        )
        restored = framework.get_room(
            room["room_id"], "ai", "ai-save", "human-save"
        )
        self.assertEqual(restored["revision"], 1)
        self.assertEqual(restored["current_player_id"], "ai-save")
        self.assertEqual(restored["board_state"]["last_move"]["iccs"], "a0a1")
        self.assertEqual(restored["board_state"]["move_history"], ["a0a1"])
        timeline = framework.list_timeline(
            room["room_id"], viewer_player_id="ai-save"
        )
        move_event = next(
            item for item in timeline if item["event_type"] == "move"
        )
        self.assertEqual(move_event["move"], move(9, 0, 8, 0))
        self.assertIn("车", move_event["move_label"])

    def test_checkmate_sets_room_winner_and_uses_common_chip_settlement(self):
        mate_in_one = self.game.state_from_fen(
            "3k5/4R4/3R5/9/9/9/9/9/9/3K5 r - - 0 1"
        )
        with patch.object(
            self.game,
            "initial_state",
            side_effect=lambda: deepcopy(mate_in_one),
        ):
            room = framework.create_room(
                "xiangqi", "human_first", "human", "human-win", "ai-lose",
                stake=5,
            )
        room = framework.respond_to_invitation(
            room["room_id"], "ai", "ai-lose", "accept"
        )
        room = framework.play_move(
            room["room_id"], "human", "human-win", move(2, 3, 1, 3)
        )
        self.assertEqual(room["status"], "finished")
        self.assertEqual(room["winner"], "human")
        self.assertEqual(room["winner_player_id"], "human-win")
        self.assertEqual(room["board_state"]["terminal_reason"], "checkmate")
        human_settlements = [
            item for item in chips.list_ledger("human", "human-win")
            if item["transaction_type"] == "duel_win"
        ]
        ai_settlements = [
            item for item in chips.list_ledger("ai", "ai-lose")
            if item["transaction_type"] == "duel_loss"
        ]
        self.assertEqual([item["amount"] for item in human_settlements], [5])
        self.assertEqual([item["amount"] for item in ai_settlements], [-5])

    def test_stalemate_sets_room_winner_through_result_for(self):
        stalemate_in_one = self.game.state_from_fen(
            "3k5/9/R8/9/9/9/9/9/9/4K4 r - - 0 1"
        )
        with patch.object(
            self.game,
            "initial_state",
            side_effect=lambda: deepcopy(stalemate_in_one),
        ):
            room = framework.create_room(
                "xiangqi", "human_first", "human", "human-stale", "ai-stale"
            )
        room = framework.play_move(
            room["room_id"], "human", "human-stale", move(2, 0, 1, 0)
        )
        self.assertEqual(room["status"], "finished")
        self.assertEqual(room["winner_player_id"], "human-stale")
        self.assertEqual(room["board_state"]["terminal_reason"], "stalemate")
        self.assertEqual(
            room["result"],
            {"winner_player_id": "human-stale", "draw": False},
        )


class XiangqiRoomCreationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-xiangqi-api-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_module.app),
            base_url="http://duel.test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.db_patch.stop()
        self.temporary.cleanup()

    @staticmethod
    def trusted_headers() -> dict[str, str]:
        machines = json.dumps(
            [{"id": "machine-bound", "name": "真实小机"}],
            ensure_ascii=False,
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(machines).decode("ascii").rstrip("=")
        return {
            "X-Duel-Human-Player": "human-bound",
            "X-Duel-Human-Name": "%E7%8E%A9%E5%AE%B6",
            "X-Duel-Bound-Ais": encoded,
        }

    async def test_room_requires_exactly_one_trusted_bound_machine_and_no_npc(self):
        headers = self.trusted_headers()
        base_body = {
            "player_id": "human-bound",
            "game_type": "xiangqi",
            "mode": "human_first",
        }
        created = await self.client.post(
            "/api/rooms",
            headers=headers,
            json={**base_body, "ai_player": "machine-bound"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        participants = created.json()["room"]["participants"]
        self.assertEqual(
            [item["participant_kind"] for item in participants],
            ["human", "bound_machine"],
        )

        unbound = await self.client.post(
            "/api/rooms",
            headers=headers,
            json={**base_body, "ai_player": "machine-unbound"},
        )
        self.assertEqual(unbound.status_code, 403)
        self.assertIn("绑定清单", unbound.json()["message"])

        npc_fill = await self.client.post(
            "/api/rooms",
            headers=headers,
            json={
                **base_body,
                "ai_player": "machine-bound",
                "fill_with_npcs": True,
            },
        )
        self.assertEqual(npc_fill.status_code, 400)
        self.assertIn("只支持双人绑定人机对局", npc_fill.json()["message"])


if __name__ == "__main__":
    unittest.main()
