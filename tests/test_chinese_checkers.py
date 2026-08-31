import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from app import chips, database, framework
from app.games import game_catalog
from app.games.chinese_checkers import (
    ChineseCheckers,
    _CAMPS,
    _CAMP_BY_NODE,
    _COORDINATES,
    _COORD_BY_NODE,
    _DIRECTIONS,
    _NODE_BY_COORD,
)


def participants(count: int) -> list[dict]:
    values = [
        {
            "player_id": "human-1",
            "display_name": "南山",
            "role": "human",
            "participant_kind": "human",
            "seat_index": 0,
            "token": "P1",
        }
    ]
    values.extend(
        {
            "player_id": f"ai-{index}",
            "display_name": f"小机 {index}",
            "role": "ai",
            "participant_kind": "bound_machine",
            "seat_index": index,
            "token": f"P{index + 1}",
        }
        for index in range(1, count)
    )
    return values


class ChineseCheckersTopologyAndRulesTests(unittest.TestCase):
    def setUp(self):
        self.game = ChineseCheckers()

    def state_for(self, count: int = 2) -> dict:
        return self.game.initialize(deepcopy(participants(count)))

    @staticmethod
    def node(q: int, r: int) -> str:
        return _NODE_BY_COORD[(q, r)]

    def sparse_state(
        self,
        pieces: dict[tuple[int, int], str],
        *,
        count: int = 2,
        player_id: str = "human-1",
    ) -> dict:
        state = self.state_for(count)
        state["pieces"] = {
            self.node(q, r): token for (q, r), token in pieces.items()
        }
        self.game._update_progress(state)
        self.game._sync_turn(state, player_id)
        return state

    def test_standard_board_has_121_stable_nodes_and_six_direction_topology(self):
        state = self.state_for()
        self.assertEqual(state["node_count"], 121)
        self.assertEqual(len(state["nodes"]), 121)
        self.assertEqual(len({node["id"] for node in state["nodes"]}), 121)
        self.assertEqual([len(state["camps"][str(index)]) for index in range(6)], [10] * 6)
        row_widths = [
            sum(r == row for _q, r in _COORDINATES)
            for row in range(-8, 9)
        ]
        self.assertEqual(
            row_widths,
            [1, 2, 3, 4, 13, 12, 11, 10, 9, 10, 11, 12, 13, 4, 3, 2, 1],
        )
        coordinate_set = set(_COORDINATES)
        neighbor_counts = {
            point: sum(
                (point[0] + dq, point[1] + dr) in coordinate_set
                for dq, dr in _DIRECTIONS
            )
            for point in coordinate_set
        }
        self.assertEqual(neighbor_counts[(0, 0)], 6)
        self.assertTrue(all(1 <= count <= 6 for count in neighbor_counts.values()))

    def test_two_three_four_six_seat_camps_and_opposite_targets(self):
        expected = {
            2: [0, 3],
            3: [0, 2, 4],
            4: [0, 1, 3, 4],
            6: [0, 1, 2, 3, 4, 5],
        }
        for count, camps in expected.items():
            with self.subTest(count=count):
                state = self.state_for(count)
                order = state["participant_order"]
                starts = [state["start_camps_by_player"][item] for item in order]
                targets = [state["target_camps_by_player"][item] for item in order]
                self.assertEqual(starts, camps)
                self.assertEqual(targets, [(camp + 3) % 6 for camp in camps])
                self.assertEqual(len(state["pieces"]), count * 10)
                for index, player_id in enumerate(order):
                    token = state["tokens_by_player"][player_id]
                    self.assertTrue(all(
                        state["pieces"][node_id] == token
                        for node_id in _CAMPS[camps[index]]
                    ))
        with self.assertRaisesRegex(ValueError, "不支持 5 人"):
            self.state_for(5)

    def test_catalog_declares_discrete_players_npcs_and_multiplayer_stakes(self):
        item = {
            entry["game_type"]: entry for entry in game_catalog()
        }["chinese_checkers"]
        self.assertEqual(item["display_name"], "中国跳棋")
        self.assertEqual(item["category"], "board")
        self.assertEqual(item["allowed_player_counts"], [2, 3, 4, 6])
        self.assertEqual(item["recommended_players"], 4)
        self.assertTrue(item["supports_npcs"])
        self.assertTrue(item["supports_stakes"])
        self.assertTrue(item["supports_multiplayer_stakes"])

    def test_adjacent_step_moves_one_marble_and_ends_turn(self):
        state = self.state_for()
        move = next(item for item in state["legal_moves"] if item["kind"] == "step")
        before_count = len(state["pieces"])
        result = self.game.apply_action(
            state,
            {"from": move["from"], "to": move["to"], "kind": "step"},
            participants(2)[0],
        )
        self.assertEqual(len(result.state["pieces"]), before_count)
        self.assertNotIn(move["from"], result.state["pieces"])
        self.assertEqual(result.state["pieces"][move["to"]], "P1")
        self.assertFalse(result.retain_turn)
        self.assertEqual(result.next_player_id, "ai-1")

    def test_single_and_multi_jump_use_stable_bfs_canonical_path_without_capture(self):
        state = self.sparse_state({
            (0, 0): "P1",
            (1, 0): "P2",
            (3, 0): "P2",
        })
        origin = self.node(0, 0)
        single = self.node(2, 0)
        final = self.node(4, 0)
        jumps = {
            move["to"]: move
            for move in state["legal_moves"]
            if move["from"] == origin and move["kind"] == "jump"
        }
        self.assertEqual(jumps[single]["path"], [origin, single])
        self.assertEqual(jumps[final]["path"], [origin, single, final])
        self.assertEqual(
            self.game.legal_actions(state, "P1"),
            self.game.legal_actions(state, "P1"),
        )
        result = self.game.apply_move(
            state, {"from": origin, "to": final}, "P1"
        )
        self.assertEqual(result.state["last_move"]["kind"], "jump")
        self.assertEqual(result.state["last_move"]["path"], [origin, single, final])
        self.assertEqual(result.state["last_move"]["jump_count"], 2)
        self.assertEqual(result.state["pieces"][self.node(1, 0)], "P2")
        self.assertEqual(result.state["pieces"][self.node(3, 0)], "P2")
        self.assertEqual(len(result.state["pieces"]), 3)

    def test_step_and_jump_cannot_be_mixed_or_mislabeled(self):
        state = self.sparse_state({(0, 0): "P1", (2, 0): "P2"})
        origin = self.node(0, 0)
        adjacent = self.node(1, 0)
        mixed_only_endpoint = self.node(3, 0)
        with self.assertRaisesRegex(ValueError, "不能混用"):
            self.game.validate_move(
                state,
                {"from": origin, "to": adjacent, "kind": "jump"},
                "P1",
            )
        with self.assertRaisesRegex(ValueError, "不合法"):
            self.game.validate_move(
                state, {"from": origin, "to": mixed_only_endpoint}, "P1"
            )
        with self.assertRaisesRegex(ValueError, "不得提交 path"):
            self.game.validate_move(
                state,
                {"from": origin, "to": adjacent, "path": [origin, adjacent]},
                "P1",
            )

    def test_foreign_corner_cannot_be_endpoint_but_jump_path_may_cross_it(self):
        origin = self.node(0, -4)
        foreign = self.node(-2, -4)
        destination = self.node(-2, -2)
        state = self.sparse_state({
            (0, -4): "P1",
            (-1, -4): "P2",
            (-2, -3): "P2",
        })
        self.assertEqual(_CAMP_BY_NODE[foreign], 5)
        moves = {
            move["to"]: move
            for move in state["legal_moves"]
            if move["from"] == origin
        }
        self.assertNotIn(foreign, moves)
        self.assertIn(destination, moves)
        self.assertEqual(moves[destination]["path"], [origin, foreign, destination])

        simple = self.sparse_state({(0, -4): "P1"})
        self.assertNotIn(
            self.node(-1, -4),
            {
                move["to"] for move in simple["legal_moves"]
                if move["from"] == origin
            },
        )

    def test_marble_inside_target_can_only_land_inside_target(self):
        target = _CAMPS[3]
        boundary = next(
            node_id
            for node_id in sorted(target)
            if any(
                _NODE_BY_COORD.get((
                    _COORD_BY_NODE[node_id][0] + dq,
                    _COORD_BY_NODE[node_id][1] + dr,
                )) not in target
                for dq, dr in _DIRECTIONS
            )
        )
        q, r = _COORD_BY_NODE[boundary]
        state = self.sparse_state({(q, r): "P1"})
        actions = [
            move for move in state["legal_moves"] if move["from"] == boundary
        ]
        self.assertTrue(actions)
        self.assertTrue(all(move["to"] in target for move in actions))
        self.assertTrue(all(
            all(node_id in target for node_id in move["path"])
            for move in actions
        ))

    def test_anti_spoiling_requires_one_own_marble_and_only_original_owner_blockers(self):
        state = self.state_for(2)
        self.assertIsNone(self.game._winning_reason(state, "human-1"))
        target = sorted(_CAMPS[3])
        state["pieces"] = {
            node_id: ("P1" if index == 0 else "P2")
            for index, node_id in enumerate(target)
        }
        self.game._update_progress(state)
        self.assertEqual(self.game._winning_reason(state, "human-1"), "anti_spoiling")
        self.assertEqual(state["target_progress_by_player"]["human-1"], 1)

        six_player = self.state_for(6)
        target = sorted(_CAMPS[3])
        six_player["pieces"] = {
            node_id: (
                "P1" if index == 0 else "P2" if index == 1 else "P4"
            )
            for index, node_id in enumerate(target)
        }
        self.assertIsNone(
            self.game._winning_reason(six_player, "human-1")
        )
        six_player["pieces"] = {node_id: "P1" for node_id in target}
        self.assertEqual(
            self.game._winning_reason(six_player, "human-1"),
            "target_complete",
        )

    def test_npc_actions_are_compact_authoritative_and_legal(self):
        state = self.state_for(4)
        actor = participants(4)[0]
        actions = self.game.npc_legal_actions(state, actor, participants(4))
        self.assertTrue(actions)
        self.assertTrue(all(set(action) == {"from", "to", "kind"} for action in actions))
        authoritative = {
            (move["from"], move["to"], move["kind"])
            for move in state["legal_moves"]
        }
        self.assertEqual(
            {(move["from"], move["to"], move["kind"]) for move in actions},
            authoritative,
        )
        for action in actions:
            self.game.validate_action(state, action, actor)

    def test_explicit_npc_opener_uses_its_authoritative_opening_actions(self):
        room_participants = [
            {
                "player_id": "human-1",
                "display_name": "南山",
                "role": "human",
                "participant_kind": "human",
            },
            {
                "player_id": "npc:test",
                "display_name": "测试 NPC",
                "role": "ai",
                "participant_kind": "system_npc",
                "npc_persona_id": "test",
            },
        ]
        with tempfile.TemporaryDirectory(prefix="duel-chinese-npc-") as directory:
            with patch.object(database, "DB_PATH", Path(directory) / "test.db"):
                database.init_db()
                room = framework.create_room(
                    "chinese_checkers",
                    "human_first",
                    "human",
                    "human-1",
                    ordered_participants=room_participants,
                    first_player_id="npc:test",
                )
                self.assertEqual(room["current_player_id"], "npc:test")
                actor = next(
                    item for item in room["participants"]
                    if item["player_id"] == "npc:test"
                )
                actions = self.game.npc_legal_actions(
                    room["board_state"], actor, room["participants"]
                )
                self.assertTrue(actions)
                opening = room["board_state"]["legal_moves_by_player"]["npc:test"]
                self.assertEqual(len(actions), len(opening))


class ChineseCheckersFrameworkSettlementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-chinese-checkers-")
        self.addCleanup(self.temporary.cleanup)
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()

    def test_three_player_public_projection_and_persisted_turn_actions(self):
        room = framework.create_room(
            "chinese_checkers",
            "human_first",
            "human",
            "human-1",
            opponent_id="ai-1",
            ordered_participants=participants(3),
        )
        human_view = framework.project_room_for_viewer(room, "human-1")
        ai_view = framework.project_room_for_viewer(room, "ai-1")
        self.assertEqual(human_view["board_state"], ai_view["board_state"])
        self.assertEqual(human_view["private_state"], {})
        self.assertEqual(ai_view["private_state"], {})
        opening = room["board_state"]["legal_moves_by_player"]["human-1"][0]
        revision = room["revision"]
        room = framework.play_move(
            room["room_id"],
            "human",
            "human-1",
            {key: opening[key] for key in ("from", "to", "kind")},
            expected_revision=revision,
        )
        self.assertEqual(room["revision"], revision + 1)
        self.assertEqual(room["current_player_id"], "ai-1")
        self.assertNotIn("legal_moves_by_player", room["board_state"])
        self.assertTrue(room["board_state"]["legal_moves"])
        self.assertTrue(all(
            room["board_state"]["pieces"][move["from"]] == "P2"
            for move in room["board_state"]["legal_moves"]
        ))

    def test_six_player_resignation_is_immediate_minus_five_forfeit(self):
        room = framework.create_room(
            "chinese_checkers",
            "human_first",
            "human",
            "human-1",
            opponent_id="ai-1",
            ordered_participants=participants(6),
            stake=4,
        )
        for player_id in ("ai-1", "ai-2", "ai-3", "ai-4", "ai-5"):
            room = framework.respond_to_invitation(
                room["room_id"], "ai", player_id, "accept"
            )
        room = framework.resign(room["room_id"], "human", "human-1")
        self.assertEqual(room["status"], "finished")
        self.assertFalse(room["result"]["draw"])
        self.assertEqual(room["result"]["settlement_deltas"], {
            "human-1": -20,
            "ai-1": 4,
            "ai-2": 4,
            "ai-3": 4,
            "ai-4": 4,
            "ai-5": 4,
        })

    def test_four_player_revision_winner_tied_semantics_and_zero_sum_settlement(self):
        room = framework.create_room(
            "chinese_checkers",
            "human_first",
            "human",
            "human-1",
            opponent_id="ai-1",
            ordered_participants=participants(4),
            stake=5,
        )
        for player_id in ("ai-1", "ai-2", "ai-3"):
            room = framework.respond_to_invitation(
                room["room_id"], "ai", player_id, "accept"
            )
        game = ChineseCheckers()
        state = deepcopy(room["board_state"])
        target = _CAMPS[state["target_camps_by_player"]["human-1"]]
        final_node = next(
            node_id
            for node_id in sorted(target)
            if any(
                (
                    (neighbor := _NODE_BY_COORD.get((
                        _COORD_BY_NODE[node_id][0] + dq,
                        _COORD_BY_NODE[node_id][1] + dr,
                    ))) is not None
                    and neighbor not in target
                    and neighbor not in _CAMP_BY_NODE
                )
                for dq, dr in _DIRECTIONS
            )
        )
        fq, fr = _COORD_BY_NODE[final_node]
        source_node = next(
            neighbor
            for dq, dr in _DIRECTIONS
            if (
                (neighbor := _NODE_BY_COORD.get((fq + dq, fr + dr)))
                is not None
                and neighbor not in target
                and neighbor not in _CAMP_BY_NODE
            )
        )
        state["pieces"] = {
            node_id: "P1" for node_id in target if node_id != final_node
        }
        state["pieces"][source_node] = "P1"
        game._update_progress(state)
        game._sync_turn(state, "human-1")
        with database.write_transaction() as conn:
            conn.execute(
                """
                UPDATE rooms
                SET board_state = ?, current_player_id = 'human-1', turn = 'human'
                WHERE room_id = ?
                """,
                (
                    json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                    room["room_id"],
                ),
            )
        revision = room["revision"]
        room = framework.play_move(
            room["room_id"],
            "human",
            "human-1",
            {"from": source_node, "to": final_node, "kind": "step"},
            expected_revision=revision,
        )
        self.assertEqual(room["revision"], revision + 1)
        self.assertEqual(room["status"], "finished")
        self.assertEqual(room["winner_player_id"], "human-1")
        self.assertFalse(room["result"]["draw"])
        self.assertEqual(room["result"]["tied_player_ids"], [])
        self.assertEqual(
            room["result"]["settlement_deltas"],
            {"human-1": 15, "ai-1": -5, "ai-2": -5, "ai-3": -5},
        )
        # Invitation/room achievements are recorded in the same transaction;
        # assert the exact duel settlement entry as well as the resulting wallet.
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], 250)
        conn = database.connect()
        try:
            settlement = conn.execute(
                """
                SELECT ledger.amount
                FROM chip_ledger AS ledger
                JOIN chip_wallets AS wallet ON wallet.id = ledger.wallet_id
                WHERE wallet.subject_type = 'human'
                  AND wallet.subject_id = 'human-1'
                  AND ledger.idempotency_key = ?
                """,
                (f"duel_settlement:{room['room_id']}",),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(settlement)
        self.assertEqual(settlement["amount"], 15)


if __name__ == "__main__":
    unittest.main()
