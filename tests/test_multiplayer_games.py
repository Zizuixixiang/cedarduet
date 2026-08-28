import json
import random
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import httpx

from app import chips, database, framework
from app import main as main_module
from app.games import GAMES, game_catalog
from app.games.dots_boxes import DotsBoxes
from app.games.liars_dice import LiarsDice


def participants(count: int, *, npc_count: int = 0) -> list[dict]:
    values = [
        {
            "player_id": "human-1",
            "display_name": "人类一号",
            "role": "human",
            "participant_kind": "human",
        }
    ]
    bound_count = count - 1 - npc_count
    values.extend(
        {
            "player_id": f"ai-{index}",
            "display_name": f"小机 {index}",
            "role": "ai",
            "participant_kind": "bound_machine",
        }
        for index in range(1, bound_count + 1)
    )
    values.extend(
        {
            "player_id": f"npc:test-{index}",
            "display_name": f"NPC {index}",
            "role": "ai",
            "participant_kind": "system_npc",
            "npc_persona_id": f"test-{index}",
        }
        for index in range(1, npc_count + 1)
    )
    return values


class MultiplayerGameTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-acceptance-games-")
        self.addCleanup(self.temporary.cleanup)
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()

    def accept_bound_players(self, room: dict) -> dict:
        for participant in room["participants"]:
            if participant["participant_kind"] == "bound_machine":
                room = framework.respond_to_invitation(
                    room["room_id"], "ai", participant["player_id"], "accept"
                )
        return room

    @staticmethod
    def role_for(player_id: str) -> str:
        return "human" if player_id.startswith("human") else "ai"

    def replace_state(
        self,
        room: dict,
        state: dict,
        current_player_id: str,
        *,
        eliminated_player_ids: tuple[str, ...] = (),
    ) -> dict:
        role = self.role_for(current_player_id)
        with database.write_transaction() as conn:
            conn.execute(
                """
                UPDATE rooms
                SET board_state = ?, current_player_id = ?, turn = ?
                WHERE room_id = ?
                """,
                (
                    json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                    current_player_id,
                    role,
                    room["room_id"],
                ),
            )
            for player_id in eliminated_player_ids:
                conn.execute(
                    """
                    UPDATE room_participants
                    SET active = 0, activity_state = 'eliminated'
                    WHERE room_id = ? AND player_id = ?
                    """,
                    (room["room_id"], player_id),
                )
        return framework.get_room(room["room_id"])


class DotsBoxesMultiplayerTests(MultiplayerGameTestCase):
    def test_catalog_and_two_three_four_player_turns_use_real_ids(self):
        catalog = {item["game_type"]: item for item in game_catalog()}
        self.assertEqual(catalog["dots_boxes"]["allowed_player_counts"], [2, 3, 4])
        self.assertTrue(catalog["dots_boxes"]["supports_npcs"])
        for count in (2, 3, 4):
            with self.subTest(count=count):
                room = framework.create_room(
                    "dots_boxes",
                    "human_first",
                    "human",
                    "human-1",
                    opponent_id="ai-1",
                    ordered_participants=participants(count),
                )
                self.assertEqual(room["participant_count"], count)
                self.assertEqual(
                    set(room["board_state"]["scores_by_player"]),
                    {item["player_id"] for item in room["participants"]},
                )
                first = room["current_player_id"]
                room = framework.play_move(
                    room["room_id"], self.role_for(first), first,
                    {"orientation": "h", "row": 0, "col": 0},
                )
                self.assertEqual(
                    room["current_player_id"], room["turn_order"][1 % count]
                )
                self.assertEqual(
                    room["board_state"]["horizontal_edges"][0][0], first
                )
                if count == 2:
                    projected = framework.project_room_for_viewer(room, first)
                    self.assertEqual(
                        projected["board_state"]["horizontal_edges"][0][0],
                        room["participants"][0]["token"],
                    )

    def test_four_player_completed_box_retains_turn_and_roster_score(self):
        room = framework.create_room(
            "dots_boxes", "human_first", "human", "human-1",
            opponent_id="ai-1", ordered_participants=participants(4),
        )
        for player_id, move in (
            ("human-1", {"orientation": "h", "row": 0, "col": 0}),
            ("ai-1", {"orientation": "v", "row": 0, "col": 0}),
            ("ai-2", {"orientation": "h", "row": 1, "col": 0}),
            ("ai-3", {"orientation": "v", "row": 0, "col": 1}),
        ):
            room = framework.play_move(
                room["room_id"], self.role_for(player_id), player_id, move
            )
        self.assertEqual(room["current_player_id"], "ai-3")
        self.assertEqual(room["board_state"]["boxes"][0][0], "ai-3")
        projected = framework.project_room_for_viewer(room, "human-1")
        summary = {
            item["player_id"]: item["game_metadata"]["score"]
            for item in projected["participants"]
        }
        self.assertEqual(summary, {"human-1": 0, "ai-1": 0, "ai-2": 0, "ai-3": 1})

    def near_terminal(self, room: dict, scores: dict[str, int], actor: str) -> dict:
        state = deepcopy(room["board_state"])
        owner = room["participants"][1]["player_id"]
        state["horizontal_edges"] = [[owner for _ in range(4)] for _ in range(5)]
        state["vertical_edges"] = [[owner for _ in range(5)] for _ in range(4)]
        state["horizontal_edges"][0][0] = None
        state["boxes"] = [[owner for _ in range(4)] for _ in range(4)]
        state["boxes"][0][0] = None
        state["scores_by_player"] = dict(scores)
        token_by_player = {
            item["player_id"]: item["token"] for item in room["participants"]
        }
        state["scores"] = {
            token_by_player[player_id]: score for player_id, score in scores.items()
        }
        return self.replace_state(room, state, actor)

    def test_four_player_unique_winner_zero_sum_settlement(self):
        room = framework.create_room(
            "dots_boxes", "human_first", "human", "human-1",
            opponent_id="ai-1", ordered_participants=participants(4), stake=5,
        )
        room = self.accept_bound_players(room)
        room = self.near_terminal(
            room, {"human-1": 6, "ai-1": 4, "ai-2": 3, "ai-3": 2}, "human-1"
        )
        room = framework.play_move(
            room["room_id"], "human", "human-1",
            {"orientation": "h", "row": 0, "col": 0},
        )
        self.assertEqual(room["winner_player_id"], "human-1")
        self.assertEqual(
            room["result"]["settlement_deltas"],
            {"human-1": 15, "ai-1": -5, "ai-2": -5, "ai-3": -5},
        )
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], 215)

    def test_four_way_tie_refunds_stake(self):
        room = framework.create_room(
            "dots_boxes", "human_first", "human", "human-1",
            opponent_id="ai-1", ordered_participants=participants(4), stake=7,
        )
        room = self.accept_bound_players(room)
        room = self.near_terminal(
            room, {"human-1": 4, "ai-1": 4, "ai-2": 4, "ai-3": 3}, "ai-3"
        )
        room = framework.play_move(
            room["room_id"], "ai", "ai-3",
            {"orientation": "h", "row": 0, "col": 0},
        )
        self.assertEqual(room["winner"], "draw")
        self.assertEqual(room["result"]["settlement_deltas"], {
            "human-1": 0, "ai-1": 0, "ai-2": 0, "ai-3": 0,
        })
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], 200)

    def test_legacy_x_o_state_still_works(self):
        game = DotsBoxes()
        state = game.initial_state()
        state["horizontal_edges"][0][0] = "O"
        state["horizontal_edges"][1][0] = "X"
        state["vertical_edges"][0][0] = "O"
        result = game.apply_move(
            state, {"orientation": "v", "row": 0, "col": 1}, "X"
        )
        self.assertEqual(result.state["scores"], {"X": 1, "O": 0})
        self.assertEqual(result.state["boxes"][0][0], "X")


class LiarsDiceTests(MultiplayerGameTestCase):
    def setUp(self):
        super().setUp()
        self.game = LiarsDice(rng=random.Random(20260828))
        self.game_patch = patch.dict(GAMES, {"liars_dice": self.game})
        self.game_patch.start()
        self.addCleanup(self.game_patch.stop)

    def create_liars(self, count: int, *, stake: int = 0, npc_count: int = 0) -> dict:
        room = framework.create_room(
            "liars_dice", "human_first", "human", "human-1",
            opponent_id="ai-1", ordered_participants=participants(count, npc_count=npc_count),
            stake=stake,
        )
        return self.accept_bound_players(room) if stake else room

    def test_catalog_and_two_four_six_player_bid_order(self):
        catalog = {item["game_type"]: item for item in game_catalog()}
        self.assertEqual(catalog["liars_dice"]["allowed_player_counts"], [2, 3, 4, 5, 6])
        for count in (2, 4, 6):
            with self.subTest(count=count):
                room = self.create_liars(count)
                order = room["turn_order"]
                for index, player_id in enumerate(order):
                    room = framework.play_move(
                        room["room_id"], self.role_for(player_id), player_id,
                        {"action": "bid", "quantity": 1, "face": index + 1},
                    )
                    self.assertEqual(
                        room["current_player_id"], order[(index + 1) % count]
                    )

    def test_bid_validation_and_first_call_cannot_be_challenged(self):
        room = self.create_liars(2)
        with self.assertRaisesRegex(framework.DuelError, "首叫"):
            framework.play_move(
                room["room_id"], "human", "human-1", {"action": "challenge"}
            )
        room = framework.play_move(
            room["room_id"], "human", "human-1",
            {"action": "bid", "quantity": 2, "face": 3},
        )
        for invalid in (
            {"action": "bid", "quantity": 1, "face": 6},
            {"action": "bid", "quantity": 2, "face": 3},
            {"action": "bid", "quantity": 11, "face": 1},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(framework.DuelError):
                framework.play_move(room["room_id"], "ai", "ai-1", invalid)
        raised = framework.play_move(
            room["room_id"], "ai", "ai-1",
            {"action": "bid", "quantity": 2, "face": 4},
        )
        self.assertEqual(raised["board_state"]["current_bid"]["face"], 4)

    def test_private_dice_isolation_and_compact_public_projection(self):
        room = self.create_liars(6)
        raw = room["board_state"]
        views = {
            item["player_id"]: framework.project_room_for_viewer(room, item["player_id"])
            for item in room["participants"]
        }
        for player_id, view in views.items():
            public_json = json.dumps(view["board_state"], ensure_ascii=False)
            self.assertNotIn("dice_by_player", view["board_state"])
            self.assertNotIn(str(raw["dice_by_player"]), public_json)
            self.assertEqual(view["private_state"]["dice"], raw["dice_by_player"][player_id])
            for other_id, dice in raw["dice_by_player"].items():
                if other_id != player_id:
                    self.assertNotEqual(view["private_state"]["dice"], dice)
            summaries = {
                item["player_id"]: item["game_metadata"]["dice_count"]
                for item in view["participants"]
            }
            self.assertEqual(set(summaries.values()), {5})

    def configured_round(
        self,
        room: dict,
        *,
        dice_counts: dict[str, int],
        dice_by_player: dict[str, list[int]],
        current_bid: dict,
        current_player_id: str,
    ) -> dict:
        state = deepcopy(room["board_state"])
        state["dice_counts"] = dict(dice_counts)
        state["dice_by_player"] = deepcopy(dice_by_player)
        state["current_bid"] = deepcopy(current_bid)
        state["round_actions"] = [{"round": 1, "action": "bid", **current_bid}]
        return self.replace_state(room, state, current_player_id)

    def test_false_bid_eliminates_bidder_and_next_survivor_starts(self):
        room = self.create_liars(4)
        room = self.configured_round(
            room,
            dice_counts={"human-1": 1, "ai-1": 5, "ai-2": 5, "ai-3": 5},
            dice_by_player={
                "human-1": [2], "ai-1": [2] * 5,
                "ai-2": [3] * 5, "ai-3": [4] * 5,
            },
            current_bid={"quantity": 1, "face": 6, "bidder_player_id": "human-1"},
            current_player_id="ai-1",
        )
        room = framework.play_move(
            room["room_id"], "ai", "ai-1", {"action": "challenge"}
        )
        self.assertEqual(room["current_player_id"], "ai-1")
        self.assertEqual(room["board_state"]["dice_counts"]["human-1"], 0)
        eliminated = next(
            item for item in room["participants"] if item["player_id"] == "human-1"
        )
        self.assertEqual(eliminated["activity_state"], "eliminated")
        public = framework.project_room_for_viewer(room, "ai-2")["board_state"]
        self.assertEqual(
            public["last_round_result"]["revealed_dice_by_player"]["human-1"], [2]
        )
        self.assertNotIn("dice_by_player", public)

    def test_true_bid_costs_challenger_and_challenger_starts_next_round(self):
        room = self.create_liars(4)
        room = self.configured_round(
            room,
            dice_counts={"human-1": 5, "ai-1": 5, "ai-2": 5, "ai-3": 5},
            dice_by_player={
                "human-1": [2] * 5, "ai-1": [2] * 5,
                "ai-2": [3] * 5, "ai-3": [4] * 5,
            },
            current_bid={"quantity": 6, "face": 2, "bidder_player_id": "human-1"},
            current_player_id="ai-1",
        )
        room = framework.play_move(
            room["room_id"], "ai", "ai-1", {"action": "challenge"}
        )
        self.assertEqual(room["current_player_id"], "ai-1")
        self.assertEqual(room["board_state"]["dice_counts"]["ai-1"], 4)
        self.assertTrue(room["board_state"]["last_round_result"]["bid_holds"])
        self.assertEqual(room["board_state"]["flow"]["round_number"], 2)
        self.assertEqual(room["board_state"]["flow"]["phase"], "bidding")

    def terminal_six_state(self, room: dict) -> dict:
        state = deepcopy(room["board_state"])
        order = state["participant_order"]
        bidder = order[1]
        state["dice_counts"] = {
            player_id: 5 if player_id == "human-1" else 1 if player_id == bidder else 0
            for player_id in order
        }
        state["dice_by_player"] = {
            player_id: [2] * count for player_id, count in state["dice_counts"].items()
        }
        state["eliminated_player_ids"] = order[2:]
        state["current_bid"] = {
            "quantity": 1, "face": 6, "bidder_player_id": bidder,
        }
        return self.replace_state(
            room, state, "human-1", eliminated_player_ids=tuple(order[2:])
        )

    def test_two_player_terminal_and_stale_revision_are_idempotent(self):
        room = self.create_liars(2, stake=7)
        state = deepcopy(room["board_state"])
        state["dice_counts"] = {"human-1": 1, "ai-1": 5}
        state["dice_by_player"] = {"human-1": [2], "ai-1": [2] * 5}
        room = self.replace_state(room, state, "human-1")
        revision = room["revision"]
        room = framework.play_move(
            room["room_id"], "human", "human-1",
            {"action": "bid", "quantity": 1, "face": 6},
            expected_revision=revision,
        )
        with self.assertRaisesRegex(framework.DuelError, "revision 已变化"):
            framework.play_move(
                room["room_id"], "human", "human-1",
                {"action": "bid", "quantity": 1, "face": 6},
                expected_revision=revision,
            )
        room = framework.play_move(
            room["room_id"], "ai", "ai-1", {"action": "challenge"},
            expected_revision=room["revision"],
        )
        self.assertEqual(room["winner_player_id"], "ai-1")
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], 193)
        self.assertEqual(chips.get_wallet("ai", "ai-1")["balance"], 207)

    def test_four_player_terminal_settlement_uses_whole_pot(self):
        room = self.create_liars(4, stake=3)
        state = deepcopy(room["board_state"])
        state["dice_counts"] = {
            "human-1": 5, "ai-1": 1, "ai-2": 0, "ai-3": 0,
        }
        state["dice_by_player"] = {
            "human-1": [2] * 5, "ai-1": [2], "ai-2": [], "ai-3": [],
        }
        state["eliminated_player_ids"] = ["ai-2", "ai-3"]
        state["current_bid"] = {
            "quantity": 1, "face": 6, "bidder_player_id": "ai-1",
        }
        room = self.replace_state(
            room, state, "human-1", eliminated_player_ids=("ai-2", "ai-3")
        )
        room = framework.play_move(
            room["room_id"], "human", "human-1", {"action": "challenge"}
        )
        self.assertEqual(room["winner_player_id"], "human-1")
        self.assertEqual(room["result"]["settlement_deltas"], {
            "human-1": 9, "ai-1": -3, "ai-2": -3, "ai-3": -3,
        })

    def test_six_player_pot_includes_npcs_without_creating_npc_wallets(self):
        room = self.create_liars(6, stake=5, npc_count=4)
        room = self.terminal_six_state(room)
        room = framework.play_move(
            room["room_id"], "human", "human-1", {"action": "challenge"}
        )
        self.assertEqual(room["winner_player_id"], "human-1")
        self.assertEqual(room["result"]["settlement_deltas"]["human-1"], 25)
        self.assertEqual(sum(room["result"]["settlement_deltas"].values()), 0)
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], 225)
        self.assertEqual(chips.get_wallet("ai", "ai-1")["balance"], 195)
        conn = database.connect()
        try:
            npc_wallets = conn.execute(
                "SELECT COUNT(*) FROM chip_wallets WHERE subject_id LIKE 'npc:%'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(npc_wallets, 0)

    def test_npc_legal_actions_are_authoritative_and_private_safe(self):
        room = self.create_liars(4)
        room = framework.play_move(
            room["room_id"], "human", "human-1",
            {"action": "bid", "quantity": 2, "face": 3},
        )
        actor = next(item for item in room["participants"] if item["player_id"] == "ai-1")
        legal = self.game.npc_legal_actions(
            deepcopy(room["board_state"]), deepcopy(actor), deepcopy(room["participants"])
        )
        self.assertIn({"action": "challenge"}, legal)
        for action in legal:
            self.game.validate_action(deepcopy(room["board_state"]), action, actor)
        public_actions = self.game.npc_public_actions(
            room["board_state"], actor, room["participants"]
        )
        self.assertNotIn("dice_by_player", json.dumps(public_actions, ensure_ascii=False))


class LiarsDiceMcpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-liars-mcp-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.game_patch = patch.dict(
            GAMES, {"liars_dice": LiarsDice(rng=random.Random(44))}
        )
        self.game_patch.start()
        database.init_db()
        self.original_events = main_module.revision_events
        main_module.revision_events = main_module.RevisionEvents()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_module.app),
            base_url="http://duel.test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        main_module.revision_events = self.original_events
        self.game_patch.stop()
        self.db_patch.stop()
        self.temporary.cleanup()

    async def test_mcp_state_is_private_and_move_delta_is_compact_revision_safe(self):
        room = framework.create_room(
            "liars_dice", "human_first", "human", "human-1",
            opponent_id="ai-1", ordered_participants=participants(4),
        )
        state_response = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "ai-1", "room_id": room["room_id"]},
        )
        self.assertEqual(state_response.status_code, 200, state_response.text)
        view = state_response.json()["room"]
        self.assertNotIn("dice_by_player", view["board_state"])
        self.assertEqual(
            view["private_state"]["dice"], room["board_state"]["dice_by_player"]["ai-1"]
        )

        room = framework.play_move(
            room["room_id"], "human", "human-1",
            {"action": "bid", "quantity": 1, "face": 1},
        )
        response = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-1", "room_id": room["room_id"],
                "revision": room["revision"],
                "move": {"action": "bid", "quantity": 1, "face": 2},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertNotIn("room", payload)
        self.assertNotIn("board_state", payload)
        self.assertEqual(payload["revision"], room["revision"] + 1)
        stale = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-1", "room_id": room["room_id"],
                "revision": room["revision"],
                "move": {"action": "bid", "quantity": 1, "face": 2},
            },
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertIn("revision 已变化", stale.json()["message"])


if __name__ == "__main__":
    unittest.main()
