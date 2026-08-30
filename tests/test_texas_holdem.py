import asyncio
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database, framework
from app import main as main_module
from app.npc_controller import run_current_npc_turn
from app.games import GAMES, game_catalog
from app.games.texas_holdem import BIG_BLIND, TexasHoldem
from third_party.pypokerengine.engine.card import Card
from third_party.pypokerengine.engine.game_evaluator import GameEvaluator
from third_party.pypokerengine.engine.hand_evaluator import HandEvaluator
from third_party.pypokerengine.engine.pay_info import PayInfo
from third_party.pypokerengine.engine.player import Player
from third_party.pypokerengine.engine.table import Table


def participants(count):
    return [
        {
            "player_id": f"player-{index}",
            "display_name": f"玩家{index}",
            "role": "human" if index == 0 else "ai",
            "participant_kind": "human" if index == 0 else "bound_machine",
            "seat_index": index,
            "active": True,
        }
        for index in range(count)
    ]


def card_faces(value):
    faces = []
    if isinstance(value, dict):
        if set(("rank", "suit")) <= set(value):
            faces.append({"rank": value["rank"], "suit": value["suit"]})
        for child in value.values():
            faces.extend(card_faces(child))
    elif isinstance(value, list):
        for child in value:
            faces.extend(card_faces(child))
    return faces


def cards(*values):
    return [Card.from_str(value) for value in values]


class TexasHoldemCoreTests(unittest.TestCase):
    def setUp(self):
        self.game = TexasHoldem(random.Random(20260830))

    def new_state(self, count=3, opener="player-0"):
        roster = participants(count)
        return roster, self.game.initialize_for_first_player(roster, opener)

    def apply(self, state, roster, move=None):
        player_id = state["turn_player_id"]
        actor = next(item for item in roster if item["player_id"] == player_id)
        if move is None:
            legal = self.game.private_state(state, actor, roster)["legal_actions"]
            move = next(
                (item for item in legal if item["action"] in {"check", "call"}),
                legal[0],
            )
        result = self.game.apply_action(state, move, actor)
        return self.game.progress_after_action(
            state, move, actor, roster, result
        )

    def rig_stacks(self, state, stacks):
        engine = self.game._deserialize_engine(state)
        for player, initial in zip(engine["table"].seats.players, stacks):
            player.stack = int(initial) - int(player.pay_info.amount)
        state["initial_chip_total"] = sum(stacks)
        state["engine_state"] = self.game._serialize_engine(engine)

    def rig_cards(self, state, holes, board):
        engine = self.game._deserialize_engine(state)
        for player, values in zip(engine["table"].seats.players, holes):
            player.hole_card = cards(*values)
        board_cards = cards(*board)
        # One unused card keeps Deck.deserialize from restoring a fresh deck
        # after the river; draw_card pops from the end.
        engine["table"].deck.deck = cards("C3") + list(reversed(board_cards))
        state["engine_state"] = self.game._serialize_engine(engine)

    def test_catalog_and_real_vendored_runtime_cover_two_three_and_six(self):
        item = {entry["game_type"]: entry for entry in game_catalog()}[
            "texas_holdem"
        ]
        self.assertIsInstance(GAMES["texas_holdem"], TexasHoldem)
        self.assertEqual(item["allowed_player_counts"], [2, 3, 4, 5, 6])
        self.assertTrue(item["supports_npcs"])
        self.assertTrue(item["uses_local_npc_strategy"])
        self.assertFalse(item["supports_stakes"])
        self.assertEqual(
            HandEvaluator.__module__,
            "third_party.pypokerengine.engine.hand_evaluator",
        )
        for count in (2, 3, 6):
            with self.subTest(count=count):
                roster, state = self.new_state(count)
                engine = self.game._deserialize_engine(state)
                dealt = [
                    card.to_id()
                    for player in engine["table"].seats.players
                    for card in player.hole_card
                ]
                self.assertEqual(len(dealt), count * 2)
                self.assertEqual(len(dealt), len(set(dealt)))
                self.assertEqual(
                    sum(player.stack for player in engine["table"].seats.players)
                    + sum(player.pay_info.amount for player in engine["table"].seats.players),
                    200 * count,
                )
                self.assertEqual(len(roster), count)

    def test_heads_up_button_blinds_and_both_action_orders(self):
        roster, state = self.new_state(2)
        self.assertEqual(state["dealer_player_id"], "player-0")
        self.assertEqual(state["small_blind_player_id"], "player-0")
        self.assertEqual(state["big_blind_player_id"], "player-1")
        self.assertEqual(state["turn_player_id"], "player-0")
        self.apply(state, roster)
        self.assertEqual(state["turn_player_id"], "player-1")
        self.apply(state, roster)
        self.assertEqual(state["street"], "flop")
        self.assertEqual(len(state["visible_board"]), 3)
        self.assertEqual(
            state["turn_player_id"],
            "player-1",
            "heads-up big blind must act first postflop",
        )

    def test_complete_four_street_betting_and_showdown_conserve_chips(self):
        roster, state = self.new_state(3)
        seen_streets = []
        while state["game_result"] is None:
            seen_streets.append(state["street"])
            applied = self.apply(state, roster)
            self.assertIn("texas_holdem_delta", applied.public_event)
        public = self.game.public_state(state, roster)
        self.assertEqual(
            set(seen_streets), {"preflop", "flop", "turn", "river"}
        )
        self.assertEqual(len(public["board"]), 5)
        self.assertEqual(public["pot"], 0)
        self.assertEqual(public["total_pot"], 30)
        self.assertEqual(sum(player["stack"] for player in public["players"].values()), 600)
        self.assertEqual(len(public["showdown"]), 3)

    def test_all_in_calls_run_out_board_and_end_the_room(self):
        roster, state = self.new_state(2)
        legal = self.game.private_state(state, roster[0], roster)["legal_actions"]
        all_in = next(item for item in legal if item["action"] == "all_in")
        self.apply(state, roster, all_in)
        caller = next(item for item in roster if item["player_id"] == state["turn_player_id"])
        call = next(
            item for item in self.game.private_state(state, caller, roster)["legal_actions"]
            if item["action"] == "call"
        )
        result = self.apply(state, roster, call)
        self.assertIsNotNone(result.result)
        self.assertEqual(state["street"], "finished")
        self.assertEqual(len(state["visible_board"]), 5)
        self.assertEqual(sum(result.result["payout_by_player"].values()), 400)
        public = self.game.public_state(state, roster)
        self.assertEqual(sum(item["stack"] for item in public["players"].values()), 400)

    def test_fold_winner_does_not_reveal_or_run_out_public_cards(self):
        roster, state = self.new_state(2)
        result = self.apply(state, roster, {"action": "fold"})
        self.assertEqual(result.result["winner_player_id"], "player-1")
        self.assertEqual(result.result["finish_reason"], "last_player_standing")
        public = self.game.public_state(state, roster)
        self.assertEqual(public["board"], [])
        self.assertEqual(public["showdown"], {})
        self.assertFalse(card_faces(public))
        self.assertFalse(card_faces(result.public_event))

    def test_short_all_in_does_not_reopen_but_cumulative_short_raises_do(self):
        roster, state = self.new_state(4)
        self.rig_stacks(state, [200, 200, 40, 200])
        self.apply(state, roster, {"action": "raise", "amount": 30})
        self.apply(state, roster)
        short = next(
            item for item in self.game.private_state(state, roster[2], roster)["legal_actions"]
            if item["action"] == "all_in"
        )
        self.assertTrue(short["short_raise"])
        self.apply(state, roster, short)
        self.apply(state, roster)
        first_raiser_legal = self.game.private_state(state, roster[0], roster)["legal_actions"]
        self.assertNotIn("raise", {item["action"] for item in first_raiser_legal})
        self.assertNotIn("all_in", {item["action"] for item in first_raiser_legal})

        roster, state = self.new_state(5)
        self.rig_stacks(state, [200, 200, 40, 50, 200])
        self.apply(state, roster, {"action": "raise", "amount": 30})
        self.apply(state, roster)
        self.apply(state, roster, next(
            item for item in self.game.private_state(state, roster[2], roster)["legal_actions"]
            if item["action"] == "all_in"
        ))
        self.apply(state, roster, next(
            item for item in self.game.private_state(state, roster[3], roster)["legal_actions"]
            if item["action"] == "all_in"
        ))
        self.apply(state, roster)
        reopened = self.game.private_state(state, roster[0], roster)["legal_actions"]
        raise_action = next(item for item in reopened if item["action"] == "raise")
        self.assertEqual(raise_action["min_amount"], 70)

    def test_minimum_raise_tracks_last_complete_increment(self):
        roster, state = self.new_state(3)
        opening = self.game.private_state(state, roster[0], roster)["legal_actions"]
        self.assertEqual(
            next(item for item in opening if item["action"] == "raise")["min_amount"],
            20,
        )
        self.apply(state, roster, {"action": "raise", "amount": 40})
        next_legal = self.game.private_state(state, roster[1], roster)["legal_actions"]
        self.assertEqual(
            next(item for item in next_legal if item["action"] == "raise")["min_amount"],
            70,
        )

    def test_postflop_bet_exposes_call_raise_and_all_in_responses(self):
        roster, state = self.new_state(2)
        self.apply(state, roster)
        self.apply(state, roster)
        bettor = next(item for item in roster if item["player_id"] == state["turn_player_id"])
        legal = self.game.private_state(state, bettor, roster)["legal_actions"]
        bet = next(item for item in legal if item["action"] == "bet")
        self.assertEqual(bet["min_amount"], BIG_BLIND)
        self.apply(state, roster, bet)
        caller = next(item for item in roster if item["player_id"] == state["turn_player_id"])
        responses = self.game.private_state(state, caller, roster)["legal_actions"]
        self.assertTrue({"call", "raise", "all_in"} <= {
            item["action"] for item in responses
        })

    def test_check_then_subminimum_open_all_in_can_be_completed_to_big_blind(self):
        roster, state = self.new_state(3)
        self.rig_stacks(state, [200, 200, 15])
        self.apply(state, roster)
        self.apply(state, roster)
        self.apply(state, roster)
        self.assertEqual(state["street"], "flop")
        self.apply(state, roster, {"action": "check"})
        short = next(
            item for item in self.game.private_state(state, roster[2], roster)["legal_actions"]
            if item["action"] == "all_in"
        )
        self.assertEqual(short["amount"], 5)
        self.assertTrue(short["short_raise"])
        self.apply(state, roster, short)
        self.apply(state, roster)
        checker_actions = self.game.private_state(state, roster[1], roster)["legal_actions"]
        completion = next(item for item in checker_actions if item["action"] == "raise")
        self.assertEqual(completion["min_amount"], BIG_BLIND)

    def test_side_pots_use_vendored_layering_and_pay_different_winners(self):
        roster, state = self.new_state(3)
        self.rig_stacks(state, [50, 100, 200])
        self.rig_cards(
            state,
            [("SA", "HA"), ("SK", "HK"), ("S8", "H6")],
            ("C2", "D7", "H9", "SJ", "CQ"),
        )
        self.apply(state, roster, next(
            item for item in self.game.private_state(state, roster[0], roster)["legal_actions"]
            if item["action"] == "all_in"
        ))
        self.apply(state, roster, next(
            item for item in self.game.private_state(state, roster[1], roster)["legal_actions"]
            if item["action"] == "all_in"
        ))
        finished = self.apply(state, roster)
        self.assertEqual(
            [pot["amount"] for pot in finished.result["pots"]], [150, 100]
        )
        self.assertEqual(
            finished.result["payout_by_player"],
            {"player-0": 150, "player-1": 100, "player-2": 0},
        )
        self.assertEqual(
            [pot["winner_uuids"] for pot in finished.result["pots"]],
            [["player-0"], ["player-1"]],
        )
        public = self.game.public_state(state, roster)
        self.assertEqual([pot["name"] for pot in public["pots"]], ["main", "side_1"])
        self.assertEqual(sum(item["stack"] for item in public["players"].values()), 350)

    def test_board_only_best_hand_splits_pot_despite_different_holes(self):
        roster, state = self.new_state(3)
        self.rig_cards(
            state,
            [("C2", "D3"), ("C4", "D5"), ("C6", "D7")],
            ("HA", "HK", "HQ", "HJ", "HT"),
        )
        while state["game_result"] is None:
            self.apply(state, roster)
        result = state["game_result"]
        self.assertTrue(result["draw"])
        self.assertEqual(result["winner_player_ids"], ["player-0", "player-1", "player-2"])
        self.assertEqual(result["payout_by_player"], {
            "player-0": 10, "player-1": 10, "player-2": 10,
        })
        self.assertEqual(
            len({item["hand_type"] for item in state["showdown"].values()}), 1
        )

    def test_private_state_npc_and_refresh_projection_are_authoritative_and_safe(self):
        roster, state = self.new_state(3)
        public = self.game.public_state(state, roster)
        self.assertFalse(card_faces(public))
        private_hands = [
            self.game.private_state(state, viewer, roster)["hand"]
            for viewer in roster
        ]
        self.assertTrue(all(len(hand) == 2 for hand in private_hands))
        self.assertEqual(len({json.dumps(hand, sort_keys=True) for hand in private_hands}), 3)
        actor = roster[0]
        npc_legal = self.game.npc_legal_actions(state, actor, roster)
        self.assertEqual(
            npc_legal,
            self.game.private_state(state, actor, roster)["legal_actions"],
        )
        chosen = self.game.choose_local_npc_action(state, actor, roster)
        self.assertIn(chosen, npc_legal)
        self.game.validate_action(state, chosen, actor)

        room = {
            "room_id": "room-test",
            "game_type": "texas_holdem",
            "board_state": state,
            "participants": roster,
        }
        first = framework.project_room_for_viewer(room, "player-0")
        second = framework.project_room_for_viewer(room, "player-0")
        self.assertEqual(first["board_state"], second["board_state"])
        self.assertEqual(len(card_faces(first["private_state"])), 2)
        self.assertFalse(card_faces(first["board_state"]))


class VendoredEvaluatorTests(unittest.TestCase):
    def test_complete_kickers_wheel_and_board_tie(self):
        board = cards("HA", "HK", "HQ", "HJ", "HT")
        self.assertEqual(
            HandEvaluator.eval_hand(cards("C2", "D3"), board),
            HandEvaluator.eval_hand(cards("C9", "D9"), board),
        )
        pair_board = cards("C7", "D7", "H2", "S3", "C4")
        self.assertGreater(
            HandEvaluator.eval_hand(cards("SA", "HK"), pair_board),
            HandEvaluator.eval_hand(cards("SQ", "HJ"), pair_board),
        )
        wheel = HandEvaluator.gen_hand_rank_info(
            cards("SA", "D2"), cards("H3", "C4", "S5", "DK", "HQ")
        )
        self.assertEqual(wheel["hand"]["strength"], "STRAIGHT")
        self.assertEqual(wheel["hand"]["high"], 5)

    def test_odd_chip_is_not_lost_and_starts_left_of_button(self):
        table = Table()
        table.dealer_btn = 0
        table.set_blind_pos(1, 2)
        for index, hole in enumerate((
            ("C8", "D9"), ("C2", "D3"), ("C4", "D5")
        )):
            player = Player(f"p{index}", 0)
            player.hole_card = cards(*hole)
            player.pay_info.amount = 5
            table.seats.sitdown(player)
        table.seats.players[0].pay_info.status = PayInfo.FOLDED
        for card in cards("HA", "HK", "HQ", "HJ", "HT"):
            table.add_community_card(card)
        _winners, _info, prizes = GameEvaluator.judge(table)
        self.assertEqual(prizes, {0: 0, 1: 8, 2: 7})
        self.assertEqual(sum(prizes.values()), 15)


class TexasHoldemFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-texas-framework-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temporary.cleanup()

    def test_framework_turn_refresh_and_zero_stakes(self):
        roster = participants(3)
        room = framework.create_room(
            "texas_holdem", "human_first", "human", "player-0", "player-1",
            ordered_participants=roster,
        )
        self.assertEqual(room["current_player_id"], "player-0")
        projected = framework.project_room_for_viewer(room, "player-0")
        self.assertEqual(len(projected["private_state"]["hand"]), 2)
        self.assertFalse(card_faces(projected["board_state"]))
        call = next(
            item for item in projected["private_state"]["legal_actions"]
            if item["action"] == "call"
        )
        room = framework.play_move(room["room_id"], "human", "player-0", call)
        self.assertEqual(room["current_player_id"], "player-1")
        refreshed_room = framework.get_room(room["room_id"], "human", "player-0")
        refreshed = framework.project_room_for_viewer(refreshed_room, "player-0")
        current = framework.project_room_for_viewer(room, "player-0")
        self.assertEqual(refreshed["board_state"], current["board_state"])
        self.assertEqual(refreshed["private_state"], current["private_state"])

        with self.assertRaisesRegex(framework.DuelError, "尚未定义筹码结算"):
            framework.create_room(
                "texas_holdem", "human_first", "human", "stake-human", "stake-ai",
                stake=1,
            )

    def test_framework_fold_ends_single_hand_and_keeps_holes_out_of_public_result(self):
        room = framework.create_room(
            "texas_holdem", "human_first", "human", "fold-human", "fold-ai"
        )
        room = framework.play_move(
            room["room_id"], "human", "fold-human", {"action": "fold"}
        )
        self.assertEqual(room["status"], "finished")
        self.assertEqual(room["winner_player_id"], "fold-ai")
        folded = next(
            item for item in room["participants"]
            if item["player_id"] == "fold-human"
        )
        self.assertEqual(folded["activity_state"], "eliminated")
        view = framework.project_room_for_viewer(room, "fold-ai")
        self.assertEqual(view["board_state"]["showdown"], {})
        self.assertFalse(card_faces(view["board_state"]))

    def test_local_npc_turn_submits_an_exact_authoritative_action(self):
        roster = [
            {
                "player_id": "npc:poker", "display_name": "扑克小机",
                "role": "ai", "participant_kind": "system_npc",
                "npc_persona_id": "poker-test", "seat_index": 0,
            },
            {
                "player_id": "npc-human", "display_name": "玩家",
                "role": "human", "participant_kind": "human", "seat_index": 1,
            },
            {
                "player_id": "npc-bound", "display_name": "绑定小机",
                "role": "ai", "participant_kind": "bound_machine", "seat_index": 2,
            },
        ]
        room = framework.create_room(
            "texas_holdem", "ai_first", "human", "npc-human", "npc-bound",
            ordered_participants=roster, first_player_id="npc:poker",
        )
        before = framework.project_room_for_viewer(room, "npc:poker")
        authoritative = before["private_state"]["legal_actions"]
        result = asyncio.run(run_current_npc_turn(room["room_id"]))
        self.assertEqual(result.status, "applied")
        self.assertEqual(result.source, "local")
        self.assertIn(result.action, authoritative)
        self.assertEqual(result.action["action"], "call")
        self.assertEqual(result.room["current_player_id"], "npc-human")


class TexasHoldemMcpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-texas-mcp-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
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
        self.db_patch.stop()
        self.temporary.cleanup()

    async def test_bootstrap_delta_and_full_state_keep_hole_cards_private(self):
        response = await self.client.post(
            "/mcp/play",
            json={
                "action": "new",
                "player_id": "ai-mcp",
                "opponent_id": "human-mcp",
                "participant_ids": ["human-mcp", "ai-mcp", "ai-other"],
                "game_type": "texas_holdem",
                "mode": "ai_first",
                "target_player_count": 3,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        bootstrap = response.json()
        self.assertTrue(bootstrap["bootstrap"])
        room = bootstrap["room"]
        room_id = room["room_id"]
        self.assertEqual(room["current_actor"]["player_id"], "ai-mcp")
        self.assertFalse(card_faces(room["board_state"]))
        self.assertEqual(len(card_faces(room["private_state"])), 2)

        other = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "ai-other", "room_id": room_id},
        )
        self.assertEqual(other.status_code, 200, other.text)
        self.assertFalse(card_faces(other.json()["room"]["board_state"]))
        self.assertEqual(len(card_faces(other.json()["room"]["private_state"])), 2)
        self.assertNotEqual(
            room["private_state"]["hand"], other.json()["room"]["private_state"]["hand"]
        )

        call = next(
            item for item in room["private_state"]["legal_actions"]
            if item["action"] == "call"
        )
        moved = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-mcp", "room_id": room_id,
                "move": call, "revision": room["revision"],
            },
        )
        self.assertEqual(moved.status_code, 200, moved.text)
        payload = moved.json()
        public_delta = next(
            event["texas_holdem_delta"]
            for event in payload["events"]
            if "texas_holdem_delta" in event
        )
        self.assertEqual(public_delta["pot"], 25)
        self.assertFalse(card_faces(public_delta))

        snapshot_response = await self.client.post(
            "/mcp/play",
            json={
                "action": "state", "player_id": "ai-mcp", "room_id": room_id,
                "full_state": True,
            },
        )
        self.assertEqual(snapshot_response.status_code, 200, snapshot_response.text)
        snapshot = snapshot_response.json()["snapshot"]
        self.assertFalse(card_faces(snapshot["board_state"]))
        self.assertEqual(len(card_faces(snapshot["private_state"])), 2)
        self.assertNotIn("action_history", snapshot["board_state"])


if __name__ == "__main__":
    unittest.main()
