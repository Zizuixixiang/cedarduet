import json
import random
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app import database, framework
from app import main as main_module
from app.games import GAMES, game_catalog
from app.games.train_cards import (
    MAX_ACTIONS,
    TrainCards,
    build_train_deck,
    matching_rank,
)
from app.npc_controller import run_current_npc_turn


DECK_BY_ID = {card["id"]: card for card in build_train_deck()}


def cards(*card_ids):
    return [deepcopy(DECK_BY_ID[card_id]) for card_id in card_ids]


def seats(count):
    return [
        {
            "player_id": "human-1" if index == 0 else f"ai-{index}",
            "display_name": "人类" if index == 0 else f"小机 {index}",
            "role": "human" if index == 0 else "ai",
            "participant_kind": "human" if index == 0 else "bound_machine",
            "seat_index": index,
            "token": f"P{index + 1}",
        }
        for index in range(count)
    ]


class TrainCardsRuleTests(unittest.TestCase):
    def setUp(self):
        self.game = TrainCards(random.Random(20260830))

    def custom_state(self, hands, table=(), *, current_index=0):
        participants = seats(len(hands))
        state = self.game.initialize(participants)
        player_ids = [item["player_id"] for item in participants]
        state["cards"] = {
            "deck": [],
            "discard": cards(*table),
            "hands": {
                player_id: cards(*hands[index])
                for index, player_id in enumerate(player_ids)
            },
        }
        state["active_player_ids"] = list(player_ids)
        state["eliminated_player_ids"] = []
        state["turn_player_id"] = player_ids[current_index]
        state["winner_player_id"] = None
        state["draw_reason"] = None
        state["last_action"] = None
        state["last_collection"] = None
        state["action_history"] = []
        state["flow"].update({
            "phase": "playing", "round_number": 1, "turn_number": 0,
        })
        state["seen_position_hashes"] = [self.game._position_hash(state)]
        return participants, state

    def test_catalog_and_rules_lock_the_project_version(self):
        item = {entry["game_type"]: entry for entry in game_catalog()}["train_cards"]
        self.assertEqual(item["display_name"], "开火车")
        self.assertEqual(item["category"], "card")
        self.assertEqual(item["allowed_player_counts"], [2, 3, 4, 5, 6])
        self.assertTrue(item["supports_npcs"])
        self.assertTrue(item["uses_local_npc_strategy"])
        self.assertTrue(item["supports_stakes"])
        self.assertTrue(item["supports_multiplayer_stakes"])
        self.assertTrue(self.game.uses_custom_stake_settlement)
        rules = GAMES["train_cards"].rules_text
        for phrase in (
            "本项目采用版本",
            "各地规则并不统一",
            "54 张严格轮流版",
            "不采用收牌者连出和 J 全收",
            "大王与小王共同算作“王”",
            "都不是万能牌",
            "完整局面再次出现",
            "10000 次翻牌安全上限",
            "每名败者扣一份底注",
            "(人数-1) 份底注",
            "平局，所有参与者均结算 0",
        ):
            self.assertIn(phrase, rules)

    def test_54_cards_are_unique_and_all_dealt_for_supported_table_sizes(self):
        deck = build_train_deck()
        self.assertEqual(len(deck), 54)
        self.assertEqual(len({card["id"] for card in deck}), 54)
        self.assertEqual(sum(card["suit"] == "joker" for card in deck), 2)
        expected = {
            2: [27, 27],
            3: [18, 18, 18],
            4: [14, 14, 13, 13],
            6: [9, 9, 9, 9, 9, 9],
        }
        for count, expected_counts in expected.items():
            with self.subTest(count=count):
                state = TrainCards(random.Random(count)).initialize(seats(count))
                counts = [
                    len(state["cards"]["hands"][item["player_id"]])
                    for item in seats(count)
                ]
                self.assertEqual(counts, expected_counts)
                self.assertEqual(len(state["cards"]["deck"]), 0)
                self.assertEqual(len(state["cards"]["discard"]), 0)
                self.assertEqual(sum(counts), 54)

    def test_nonfirst_opener_starts_deal_and_turn_without_reordering_seats(self):
        table = seats(4)
        state = self.game.initialize_for_first_player(table, "ai-2")
        self.assertEqual(state["participant_order"], [
            "human-1", "ai-1", "ai-2", "ai-3",
        ])
        self.assertEqual(state["turn_player_id"], "ai-2")
        self.assertEqual(
            {player_id: len(hand) for player_id, hand in state["cards"]["hands"].items()},
            {"human-1": 13, "ai-1": 13, "ai-2": 14, "ai-3": 14},
        )

    def test_authoritative_flip_rotates_for_two_three_four_and_six_players(self):
        for count in (2, 3, 4, 6):
            with self.subTest(count=count):
                game = TrainCards(random.Random(100 + count))
                table = seats(count)
                state = game.initialize(table)
                for index, actor in enumerate(table):
                    self.assertEqual(state["turn_player_id"], actor["player_id"])
                    self.assertEqual(
                        game.legal_actions_for(state, actor["player_id"]),
                        [{"action": "flip"}],
                    )
                    applied = game.apply_action(state, {"action": "flip"}, actor)
                    state = applied.state
                    self.assertEqual(
                        state["turn_player_id"],
                        table[(index + 1) % count]["player_id"],
                    )

    def test_matching_card_collects_inclusive_range_in_public_order(self):
        table, state = self.custom_state(
            [("D4", "S7"), ("H8", "C9")],
            table=("S3", "H4", "C5"),
        )
        applied = self.game.apply_action(state, {"action": "flip"}, table[0])
        self.assertEqual(
            [card["id"] for card in applied.state["cards"]["discard"]], ["S3"]
        )
        self.assertEqual(
            [card["id"] for card in applied.state["cards"]["hands"]["human-1"]],
            ["S7", "H4", "C5", "D4"],
        )
        self.assertEqual(applied.state["last_collection"]["count"], 3)
        self.assertEqual(applied.next_player_id, "ai-1")

    def test_collected_cards_cycle_to_bottom_then_become_next_card(self):
        table, state = self.custom_state(
            [("D4",), ("S2", "S3")], table=("H4",)
        )
        first = self.game.apply_action(state, {"action": "flip"}, table[0])
        self.assertEqual(
            [card["id"] for card in first.state["cards"]["hands"]["human-1"]],
            ["H4", "D4"],
        )
        second = self.game.apply_action(
            first.state, {"action": "flip"}, table[1]
        )
        third = self.game.apply_action(
            second.state, {"action": "flip"}, table[0]
        )
        self.assertEqual(third.state["last_action"]["revealed_card"]["id"], "H4")

    def test_jokers_share_one_rank_but_j_has_no_special_effect(self):
        self.assertEqual(matching_rank(DECK_BY_ID["JOKER-S"]), "joker")
        self.assertEqual(matching_rank(DECK_BY_ID["JOKER-B"]), "joker")
        table, state = self.custom_state(
            [("JOKER-B", "S7"), ("H8", "C9")], table=("JOKER-S",)
        )
        joker = self.game.apply_action(state, {"action": "flip"}, table[0])
        self.assertEqual(joker.state["last_action"]["collected_count"], 2)
        self.assertEqual(joker.state["cards"]["discard"], [])

        table, state = self.custom_state(
            [("DJ", "S7"), ("H8", "C9")], table=("S3", "H4")
        )
        jack = self.game.apply_action(state, {"action": "flip"}, table[0])
        self.assertEqual(jack.state["last_action"]["collected_count"], 0)
        self.assertEqual(
            [card["id"] for card in jack.state["cards"]["discard"]],
            ["S3", "H4", "DJ"],
        )

    def test_empty_pile_eliminates_actor_and_last_player_wins(self):
        table, state = self.custom_state([("S3",), ("H4", "H5")])
        applied = self.game.apply_action(state, {"action": "flip"}, table[0])
        self.assertEqual(applied.participant_activity, {"human-1": "eliminated"})
        self.assertEqual(applied.state["active_player_ids"], ["ai-1"])
        self.assertEqual(applied.state["flow"]["phase"], "finished")
        self.assertEqual(applied.result["winner_player_id"], "ai-1")
        self.assertIsNone(applied.next_player_id)

    def test_exact_repeated_position_and_action_limit_are_draws(self):
        table, original = self.custom_state([
            ("S3", "S4"), ("H5", "H6"), ("C7", "C8"),
        ])
        probe = self.game.apply_action(
            deepcopy(original), {"action": "flip"}, table[0]
        ).state
        repeated_hash = self.game._position_hash(probe)
        original["seen_position_hashes"] = [repeated_hash]
        repeated = self.game.apply_action(
            original, {"action": "flip"}, table[0]
        )
        self.assertEqual(repeated.result, {
            "winner_player_id": None,
            "draw": True,
            "reason": "repeated_position",
        })
        self.assertEqual(repeated.state["flow"]["phase"], "finished")

        table, limited_state = self.custom_state([
            ("S3", "S4"), ("H5", "H6"), ("C7", "C8"),
        ])
        limited_state["flow"]["turn_number"] = MAX_ACTIONS - 1
        limited = self.game.apply_action(
            limited_state, {"action": "flip"}, table[0]
        )
        self.assertEqual(limited.state["draw_reason"], "action_limit")

    def test_stake_settlement_is_exact_zero_sum_for_two_to_six_and_draws(self):
        for count in (2, 3, 4, 5, 6):
            table = seats(count)
            winner = table[-1]["player_id"]
            with self.subTest(count=count):
                deltas = self.game.settlement_deltas(
                    {}, {"winner_player_id": winner, "draw": False}, table, 7
                )
                self.assertEqual(deltas[winner], (count - 1) * 7)
                self.assertTrue(all(
                    delta == -7
                    for player_id, delta in deltas.items()
                    if player_id != winner
                ))
                self.assertEqual(sum(deltas.values()), 0)
                self.assertEqual(
                    self.game.settlement_deltas(
                        {}, {"draw": True, "reason": "repeated_position"}, table, 7
                    ),
                    {item["player_id"]: 0 for item in table},
                )
                self.assertEqual(
                    self.game.settlement_deltas(
                        {}, {"draw": True, "reason": "action_limit"}, table, 7
                    ),
                    {item["player_id"]: 0 for item in table},
                )

    def test_public_and_private_projections_never_reveal_hidden_order(self):
        table = seats(4)
        state = self.game.initialize(table)
        hidden_ids = {
            card["id"]
            for hand in state["cards"]["hands"].values()
            for card in hand
        }
        public = self.game.public_state(state, table)
        self.assertEqual(public["table_cards"], [])
        self.assertEqual(sum(public["hand_counts"].values()), 54)
        self.assertNotIn("terminal_hands", public)
        self.assertNotIn("cards", public)
        self.assertNotIn("participant_order", public)
        self.assertNotIn("seen_position_hashes", public)
        private = self.game.private_state(state, table[0], table)
        self.assertEqual(private, {"legal_actions": [{"action": "flip"}]})
        encoded = json.dumps({"public": public, "private": private})
        self.assertTrue(all(card_id not in encoded for card_id in hidden_ids))
        self.assertEqual(
            self.game.private_state(state, table[1], table),
            {"legal_actions": []},
        )

        expected = {
            player_id: [card["id"] for card in hand]
            for player_id, hand in state["cards"]["hands"].items()
        }
        state["flow"]["phase"] = "finished"
        terminal = self.game.public_state(state, table)
        self.assertEqual(
            {
                player_id: [card["id"] for card in hand]
                for player_id, hand in terminal["terminal_hands"].items()
            },
            expected,
        )
        self.assertNotIn("cards", terminal)

    def test_npc_policy_selects_only_the_authoritative_forced_action(self):
        table = seats(3)
        state = self.game.initialize(table)
        actor = table[0]
        legal = self.game.npc_legal_actions(state, actor, table)
        self.assertEqual(legal, [{"action": "flip"}])
        self.assertEqual(
            self.game.choose_local_npc_action(state, actor, table), legal[0]
        )


class NeverNpcProvider:
    name = "must-not-run"

    def __init__(self):
        self.calls = 0

    async def decide(self, request):
        del request
        self.calls += 1
        raise AssertionError("开火车本地 NPC 不得调用模型 provider")


class TrainCardsFrameworkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="train-cards-framework-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        database.init_db()

    async def asyncTearDown(self):
        self.db_patch.stop()
        self.temporary.cleanup()

    async def test_system_npc_uses_local_authoritative_action_without_provider(self):
        room = framework.create_room(
            "train_cards",
            "ai_first",
            "human",
            "human-local",
            ordered_participants=[
                {"player_id": "human-local", "role": "human"},
                {
                    "player_id": "npc:local",
                    "display_name": "本地列车员",
                    "role": "ai",
                    "participant_kind": "system_npc",
                    "npc_persona_id": "not-loaded-for-local-policy",
                },
            ],
        )
        self.assertEqual(room["current_player_id"], "npc:local")
        provider = NeverNpcProvider()
        result = await run_current_npc_turn(room["room_id"], provider=provider)
        self.assertEqual(result.status, "applied")
        self.assertEqual(result.source, "local")
        self.assertEqual(result.action, {"action": "flip"})
        self.assertEqual(provider.calls, 0)
        self.assertEqual(result.room["current_player_id"], "human-local")


class TrainCardsMcpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="train-cards-mcp-")
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

    async def test_bootstrap_delta_and_full_state_are_complete_and_private_safe(self):
        started = await self.client.post(
            "/mcp/play",
            json={
                "action": "new",
                "player_id": "ai-train",
                "opponent_id": "human-train",
                "game_type": "train_cards",
                "mode": "ai_first",
            },
        )
        self.assertEqual(started.status_code, 200, started.text)
        bootstrap = started.json()
        self.assertTrue(bootstrap["bootstrap"])
        room = bootstrap["room"]
        self.assertEqual(room["board_state"]["board_kind"], "train_cards")
        self.assertEqual(sum(room["board_state"]["hand_counts"].values()), 54)
        self.assertEqual(room["board_state"]["table_cards"], [])
        self.assertEqual(
            room["private_state"], {"legal_actions": [{"action": "flip"}]}
        )
        for forbidden in ("cards", "participant_order", "seen_position_hashes"):
            self.assertNotIn(forbidden, room["board_state"])

        raw = framework.get_room(room["room_id"])["board_state"]
        hidden_before = {
            card["id"] for hand in raw["cards"]["hands"].values() for card in hand
        }
        moved = await self.client.post(
            "/mcp/play",
            json={
                "action": "move",
                "player_id": "ai-train",
                "room_id": room["room_id"],
                "move": {"action": "flip"},
                "revision": room["revision"],
            },
        )
        self.assertEqual(moved.status_code, 200, moved.text)
        delta_payload = moved.json()
        delta = delta_payload["events"][0]["train_cards_delta"]
        self.assertEqual(delta["action"], "flip")
        for required in (
            "revealed_card", "actor_player_id", "collected_cards",
            "collected_count", "table_cards", "hand_counts",
            "active_player_ids", "current_player_id", "phase",
            "winner_player_id", "draw_reason",
        ):
            self.assertIn(required, delta)
        revealed_id = delta["revealed_card"]["id"]
        self.assertIn(revealed_id, hidden_before)
        after_raw = framework.get_room(room["room_id"])["board_state"]
        still_hidden = {
            card["id"]
            for hand in after_raw["cards"]["hands"].values()
            for card in hand
        }
        encoded_delta = json.dumps(delta, ensure_ascii=False)
        self.assertTrue(all(card_id not in encoded_delta for card_id in still_hidden))

        snapshot_response = await self.client.post(
            "/mcp/play",
            json={
                "action": "state",
                "player_id": "ai-train",
                "room_id": room["room_id"],
                "full_state": True,
            },
        )
        self.assertEqual(snapshot_response.status_code, 200, snapshot_response.text)
        snapshot = snapshot_response.json()["snapshot"]
        self.assertEqual(snapshot["board_state"]["table_cards"], delta["table_cards"])
        self.assertEqual(snapshot["board_state"]["hand_counts"], delta["hand_counts"])
        self.assertEqual(snapshot["private_state"], {"legal_actions": []})
        encoded_snapshot = json.dumps(
            {
                "board_state": snapshot["board_state"],
                "private_state": snapshot["private_state"],
            },
            ensure_ascii=False,
        )
        self.assertNotIn("seen_position_hashes", encoded_snapshot)
        self.assertTrue(all(card_id not in encoded_snapshot for card_id in still_hidden))

        human_move = await self.client.post(
            f"/api/rooms/{room['room_id']}/move",
            json={
                "player_id": "human-train",
                "move": {"action": "flip"},
                "revision": delta_payload["revision"],
            },
        )
        self.assertEqual(human_move.status_code, 200, human_move.text)
        incremental = await self.client.post(
            "/mcp/play",
            json={
                "action": "state",
                "player_id": "ai-train",
                "room_id": room["room_id"],
            },
        )
        self.assertEqual(incremental.status_code, 200, incremental.text)
        second_delta = next(
            event["train_cards_delta"]
            for event in incremental.json()["events"]
            if "train_cards_delta" in event
        )
        final_snapshot = await self.client.post(
            "/mcp/play",
            json={
                "action": "state",
                "player_id": "ai-train",
                "room_id": room["room_id"],
                "full_state": True,
            },
        )
        self.assertEqual(
            second_delta["table_cards"],
            final_snapshot.json()["snapshot"]["board_state"]["table_cards"],
        )

    async def test_local_npc_fill_does_not_require_a_model_provider(self):
        persona = SimpleNamespace(id="conductor", display_name="列车员")
        with (
            patch.object(
                main_module,
                "npc_provider_capabilities",
                side_effect=AssertionError("本地策略不应检查模型 provider"),
            ),
            patch.object(main_module, "select_personas", return_value=[persona]),
        ):
            created = await self.client.post(
                "/mcp/play",
                json={
                    "action": "new",
                    "player_id": "ai-local-fill",
                    "opponent_id": "human-local-fill",
                    "game_type": "train_cards",
                    "mode": "human_first",
                    "target_player_count": 3,
                    "fill_with_npcs": True,
                },
            )
        self.assertEqual(created.status_code, 200, created.text)
        room = created.json()["room"]
        self.assertEqual(len(room["participants"]), 3)
        npc = next(
            item for item in room["participants"]
            if item["participant_kind"] == "system_npc"
        )
        self.assertEqual(npc["player_id"], "npc:conductor")
