import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from app import chips, database, framework
from app.games import GAMES, game_catalog
from app.games.chess import Chess


def square(name: str) -> tuple[int, int]:
    return 8 - int(name[1]), ord(name[0]) - ord("a")


def move(uci: str) -> dict:
    from_row, from_col = square(uci[:2])
    to_row, to_col = square(uci[2:4])
    payload = {
        "from_row": from_row,
        "from_col": from_col,
        "to_row": to_row,
        "to_col": to_col,
    }
    if len(uci) == 5:
        payload["promotion"] = uci[4]
    return payload


class ChessRuleTests(unittest.TestCase):
    def setUp(self):
        self.game = Chess()

    def apply(self, state: dict, uci: str):
        mark = "X" if state["turn_color"] == "w" else "O"
        return self.game.apply_move(state, move(uci), mark)

    def assert_legal(
        self, state: dict, uci: str, expected: bool, mark: str | None = None
    ):
        mark = mark or ("X" if state["turn_color"] == "w" else "O")
        if expected:
            self.game.validate_move(state, move(uci), mark)
        else:
            with self.assertRaises(ValueError):
                self.game.validate_move(state, move(uci), mark)

    def test_initial_position_has_authoritative_twenty_moves(self):
        state = self.game.initial_state()
        self.assertEqual((state["rows"], state["cols"]), (8, 8))
        self.assertEqual(state["turn_color"], "w")
        self.assertEqual(len(state["legal_moves"]), 20)
        self.assertEqual(state["board"][7][4], "w:k")
        self.assertEqual(state["board"][0][4], "b:k")
        self.assert_legal(state, "e2e4", True)
        self.assert_legal(state, "e7e5", False)

    def test_coordinates_turn_owner_and_self_check_are_rejected(self):
        state = self.game.initial_state()
        for payload in (
            {"from_row": True, "from_col": 0, "to_row": 1, "to_col": 0},
            {"from_row": 8, "from_col": 0, "to_row": 7, "to_col": 0},
            {"from_row": 7, "from_col": 0, "to_row": 7, "to_col": 0},
            {**move("a2a3"), "promotion": "k"},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.game.validate_move(state, payload, "X")
        with self.assertRaisesRegex(ValueError, "起点没有棋子"):
            self.game.validate_move(state, move("e3e4"), "X")
        with self.assertRaisesRegex(ValueError, "只能移动自己的棋子"):
            self.game.validate_move(state, move("e7e6"), "X")

        pinned = self.game.state_from_fen(
            "4r1k1/8/8/8/8/8/4R3/4K3 w - - 0 1"
        )
        self.assert_legal(pinned, "e2d2", False)
        self.assert_legal(pinned, "e2e8", True)

    def test_castling_moves_rook_and_cannot_cross_attacked_square(self):
        state = self.game.state_from_fen(
            "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
        )
        self.assert_legal(state, "e1g1", True)
        self.assert_legal(state, "e1c1", True)
        result = self.apply(state, "e1g1")
        self.assertEqual(result.state["board"][7][6], "w:k")
        self.assertEqual(result.state["board"][7][5], "w:r")
        self.assertIsNone(result.state["board"][7][7])
        self.assertEqual(result.state["last_move"]["flags"], "k")

        attacked = self.game.state_from_fen(
            "4kr2/8/8/8/8/8/8/4K2R w K - 0 1"
        )
        self.assert_legal(attacked, "e1g1", False)

    def test_en_passant_capture_removes_the_bypassed_pawn(self):
        state = self.game.state_from_fen(
            "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2"
        )
        self.assert_legal(state, "e5d6", True)
        result = self.apply(state, "e5d6")
        self.assertEqual(result.state["board"][2][3], "w:p")
        self.assertIsNone(result.state["board"][3][3])
        self.assertEqual(result.state["last_move"]["captured"], "b:p")
        self.assertIn("e", result.state["last_move"]["flags"])

    def test_promotion_requires_and_preserves_all_four_choices(self):
        state = self.game.state_from_fen(
            "4k3/P7/8/8/8/8/8/4K3 w - - 0 1"
        )
        promotions = {
            item["promotion"]
            for item in state["legal_moves"]
            if item["uci"].startswith("a7a8")
        }
        self.assertEqual(promotions, {"q", "r", "b", "n"})
        self.assert_legal(state, "a7a8", False)
        result = self.apply(state, "a7a8n")
        self.assertEqual(result.state["board"][0][0], "w:n")
        self.assertEqual(result.state["last_move"]["promotion"], "n")

    def test_fools_mate_is_checkmate_with_black_winner(self):
        state = self.game.initial_state()
        for uci in ("f2f3", "e7e5", "g2g4", "d8h4"):
            result = self.apply(state, uci)
            state = result.state
        self.assertTrue(state["in_check"])
        self.assertTrue(state["in_checkmate"])
        self.assertEqual(state["legal_moves"], [])
        self.assertEqual(state["terminal_reason"], "checkmate")
        self.assertEqual(self.game.check_winner(state), "O")
        self.assertIn("将死", result.note)

    def test_stalemate_and_insufficient_material_are_draws(self):
        stalemate = self.game.state_from_fen(
            "k7/2Q5/2K5/8/8/8/8/8 w - - 0 1"
        )
        stale_result = self.apply(stalemate, "c7b6")
        self.assertTrue(stale_result.state["in_stalemate"])
        self.assertEqual(stale_result.state["terminal_reason"], "stalemate")
        self.assertEqual(self.game.check_winner(stale_result.state), "draw")

        material = self.game.state_from_fen(
            "k7/2b5/3B4/8/8/8/8/7K w - - 0 1"
        )
        material_result = self.apply(material, "d6c7")
        self.assertTrue(material_result.state["insufficient_material"])
        self.assertEqual(
            material_result.state["terminal_reason"], "insufficient_material"
        )
        self.assertEqual(self.game.check_winner(material_result.state), "draw")

    def test_threefold_repetition_replays_complete_history(self):
        state = self.game.initial_state()
        cycle = ("g1f3", "g8f6", "f3g1", "f6g8")
        for uci in (*cycle, *cycle):
            result = self.apply(state, uci)
            state = result.state
        self.assertTrue(state["in_threefold_repetition"])
        self.assertEqual(state["terminal_reason"], "threefold_repetition")
        self.assertEqual(state["move_history"], list((*cycle, *cycle)))
        self.assertEqual(self.game.check_winner(state), "draw")

        restored = self.game.state_from_fen(
            state["starting_fen"], state["move_history"]
        )
        self.assertTrue(restored["in_threefold_repetition"])
        self.assertEqual(restored["fen"], state["fen"])

    def test_fifty_move_rule_uses_halfmove_clock(self):
        state = self.game.state_from_fen(
            "r6k/8/8/8/8/8/8/R6K w - - 99 50"
        )
        result = self.apply(state, "a1b1")
        self.assertEqual(result.state["halfmove_clock"], 100)
        self.assertTrue(result.state["in_draw"])
        self.assertEqual(result.state["terminal_reason"], "fifty_move_rule")
        self.assertEqual(self.game.check_winner(result.state), "draw")

    def test_invalid_fen_and_corrupt_history_are_rejected_by_bridge(self):
        with self.assertRaisesRegex(ValueError, "必须恰有一个王"):
            self.game.state_from_fen("8/8/8/8/8/8/8/4K3 w - - 0 1")
        with self.assertRaisesRegex(ValueError, "无法重放第 1 手"):
            self.game.state_from_fen(
                "4k3/8/8/8/8/8/8/4K3 w - - 0 1", ["e1e8"]
            )

    def test_npc_actions_are_exact_authoritative_legal_payloads(self):
        state = self.game.initial_state()
        actions = self.game.npc_legal_actions(state, {"token": "X"}, [])
        self.assertEqual(len(actions), 20)
        self.assertEqual(
            {tuple(sorted(item.items())) for item in actions},
            {
                tuple(sorted(self.game._payload(item).items()))
                for item in state["legal_moves"]
            },
        )
        for action in actions:
            self.game.validate_move(state, action, "X")
        self.assertEqual(
            self.game.npc_legal_actions(state, {"token": "O"}, []), []
        )


class ChessFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-chess-")
        self.addCleanup(self.temporary.cleanup)
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()
        self.game = GAMES["chess"]

    def test_catalog_declares_two_player_stakes_and_npc_legal_contract(self):
        item = next(
            item for item in game_catalog() if item["game_type"] == "chess"
        )
        self.assertEqual(item["allowed_player_counts"], [2])
        self.assertTrue(item["supports_npcs"])
        self.assertTrue(item["supports_stakes"])

        room = framework.create_room(
            "chess",
            "ai_first",
            "human",
            "human-npc",
            "npc:rookie",
            ordered_participants=[
                {
                    "player_id": "human-npc",
                    "display_name": "人类",
                    "role": "human",
                    "participant_kind": "human",
                },
                {
                    "player_id": "npc:rookie",
                    "display_name": "车手",
                    "role": "ai",
                    "participant_kind": "system_npc",
                    "npc_persona_id": "rookie",
                },
            ],
        )
        actor = next(
            item
            for item in room["participants"]
            if item["player_id"] == room["current_player_id"]
        )
        self.assertEqual(actor["participant_kind"], "system_npc")
        self.assertEqual(actor["token"], "X")
        actions = self.game.npc_legal_actions(
            room["board_state"], actor, room["participants"]
        )
        self.assertEqual(len(actions), 20)

    def test_selected_opener_is_white_and_state_is_persisted(self):
        room = framework.create_room(
            "chess", "ai_first", "human", "human-open", "ai-open"
        )
        self.assertEqual(room["current_player_id"], "ai-open")
        opener = next(
            item for item in room["participants"]
            if item["player_id"] == "ai-open"
        )
        self.assertEqual(opener["token"], "X")
        self.assertEqual(room["board_state"]["turn_color"], "w")
        room = framework.play_move(
            room["room_id"], "ai", "ai-open", move("e2e4")
        )
        restored = framework.get_room(
            room["room_id"], "human", "human-open", "ai-open"
        )
        self.assertEqual(restored["revision"], 1)
        self.assertEqual(restored["board_state"]["move_history"], ["e2e4"])
        self.assertEqual(restored["board_state"]["last_move"]["san"], "e4")

    def test_checkmate_finishes_staked_room_and_settles_once(self):
        mate_in_one = self.game.state_from_fen(
            "7k/5Q2/6K1/8/8/8/8/8 w - - 0 1"
        )
        with patch.object(
            self.game,
            "initial_state",
            side_effect=lambda: deepcopy(mate_in_one),
        ):
            room = framework.create_room(
                "chess",
                "human_first",
                "human",
                "human-win",
                "ai-lose",
                stake=9,
            )
        room = framework.respond_to_invitation(
            room["room_id"], "ai", "ai-lose", "accept"
        )
        room = framework.play_move(
            room["room_id"], "human", "human-win", move("f7g7")
        )
        self.assertEqual(room["status"], "finished")
        self.assertEqual(room["winner_player_id"], "human-win")
        self.assertEqual(room["board_state"]["terminal_reason"], "checkmate")
        self.assertEqual(
            [
                item["amount"]
                for item in chips.list_ledger("human", "human-win")
                if item["transaction_type"] == "duel_win"
            ],
            [9],
        )
        self.assertEqual(
            [
                item["amount"]
                for item in chips.list_ledger("ai", "ai-lose")
                if item["transaction_type"] == "duel_loss"
            ],
            [-9],
        )

    def test_stalemate_finishes_room_as_draw(self):
        stale_in_one = self.game.state_from_fen(
            "k7/2Q5/2K5/8/8/8/8/8 w - - 0 1"
        )
        with patch.object(
            self.game,
            "initial_state",
            side_effect=lambda: deepcopy(stale_in_one),
        ):
            room = framework.create_room(
                "chess", "human_first", "human", "human-draw", "ai-draw"
            )
        room = framework.play_move(
            room["room_id"], "human", "human-draw", move("c7b6")
        )
        self.assertEqual(room["status"], "finished")
        self.assertEqual(room["winner"], "draw")
        self.assertEqual(room["result"], {"draw": True})
        self.assertEqual(room["board_state"]["terminal_reason"], "stalemate")


if __name__ == "__main__":
    unittest.main()
