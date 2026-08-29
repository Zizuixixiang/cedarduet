import asyncio
import base64
import json
import random
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database, framework
from app import main as main_module
from app.games import GAMES, game_catalog
from app.games.base import GamePlugin, MoveResult
from app.games.tools import (
    advance_flow,
    draw_cards,
    ensure_card_zones,
    ensure_flow,
    private_hand,
    public_card_state,
    roll_dice,
    visible_dice_rolls,
)


class DummyMultiplayer(GamePlugin):
    """Test-only turn game. It is never added to the production registry."""

    game_type = "dummy_multiplayer"
    display_name = "测试多人轮转"
    rules_text = "测试专用。"
    move_format = '{"move":{"action":"step"}}'
    min_players = 3
    max_players = 4
    supports_stakes = False

    def initial_state(self):
        return {"actions": []}

    def validate_move(self, state, move, mark):
        if not isinstance(move.get("action"), str):
            raise ValueError("action 必须是字符串")

    def apply_move(self, state, move, mark):
        state["actions"].append({"token": mark, "action": move["action"]})
        return state

    def validate_action(self, state, move, actor):
        self.validate_move(state, move, actor["token"])

    def apply_action(self, state, move, actor):
        state["actions"].append(
            {"player_id": actor["player_id"], "action": move["action"]}
        )
        return MoveResult(
            state=state,
            skipped_player_ids=list(move.get("skip", [])),
            participant_activity=dict(move.get("activity", {})),
        )

    def check_winner(self, state):
        return None


class DummyPrivateMultiplayer(DummyMultiplayer):
    """Test-only hidden-information and explicit-settlement fixture."""

    game_type = "dummy_private_multiplayer"
    display_name = "测试私有多人轮转"
    supports_stakes = True
    supports_multiplayer_stakes = True

    def initialize(self, participants):
        state = {"actions": [], "legal_actions": {}}
        ensure_flow(state, phase="deal")
        player_ids = [item["player_id"] for item in participants]
        ensure_card_zones(
            state,
            [f"card-{index}" for index in range(16)],
            player_ids,
            rng=random.Random(20260828),
        )
        for player_id in player_ids:
            draw_cards(state, player_id, 2)
            state["legal_actions"][player_id] = [f"act-{player_id}"]
            roll_dice(
                state,
                roller_player_id=player_id,
                visible_to_player_ids={player_id},
                rng=random.Random(player_ids.index(player_id) + 7),
            )
        roll_dice(
            state,
            roller_player_id="system",
            rng=random.Random(42),
        )
        return state

    def public_state(self, state, participants):
        return {
            "actions": list(state["actions"]),
            "flow": dict(state["flow"]),
            "cards": public_card_state(state),
            "dice_rolls": visible_dice_rolls(state, "__public__"),
            "last_action_note": state.get("last_action_note", ""),
            "last_skipped_player_ids": list(
                state.get("last_skipped_player_ids", [])
            ),
        }

    def private_state(self, state, viewer, participants):
        player_id = viewer["player_id"]
        return {
            "hand": private_hand(state, player_id),
            "dice_rolls": visible_dice_rolls(state, player_id),
            "legal_actions": list(state["legal_actions"][player_id]),
        }

    def project_event(self, event, viewer, participants):
        move = event.get("move")
        if isinstance(move, dict) and move.get("reveal_to") != viewer["player_id"]:
            move.pop("secret", None)
        return event

    def apply_action(self, state, move, actor):
        state["actions"].append(
            {"player_id": actor["player_id"], "action": move["action"]}
        )
        result = None
        if move["action"] == "finish":
            result = {
                "winner_player_id": "human-1",
                "draw": False,
                "placements": ["human-1", "ai-1", "ai-2", "ai-3"],
            }
        return MoveResult(
            state=state,
            retain_turn=bool(move.get("retain")),
            next_player_id=move.get("next_player_id"),
            skipped_player_ids=list(move.get("skip", [])),
            participant_activity=dict(move.get("activity", {})),
            result=result,
        )

    def progress_after_action(self, state, move, actor, participants, applied):
        advance_flow(
            state,
            phase=move.get("phase"),
            next_round=bool(move.get("next_round")),
        )
        return applied

    def settlement_deltas(self, state, result, participants, stake):
        return {
            participant["player_id"]: (
                stake * 3 if participant["player_id"] == "human-1" else -stake
            )
            for participant in participants
        }


class DummySixPlayer(DummyMultiplayer):
    game_type = "dummy_six_player"
    display_name = "测试六人底座"
    min_players = 2
    max_players = 6
    allowed_player_counts = (2, 3, 4, 5, 6)
    recommended_players = 6


class DummyDiscretePlayers(DummyMultiplayer):
    game_type = "dummy_discrete_players"
    display_name = "测试离散桌型"
    min_players = 2
    max_players = 4
    allowed_player_counts = (2, 3, 4)


class DummyFourOnly(DummyMultiplayer):
    game_type = "dummy_four_only"
    display_name = "测试四人专用"
    min_players = 4
    max_players = 4
    allowed_player_counts = (4,)


class DummyTwoOrThree(DummyMultiplayer):
    game_type = "dummy_two_or_three"
    display_name = "测试二三人桌"
    min_players = 2
    max_players = 3
    allowed_player_counts = (2, 3)


def ordered_participants(count=4):
    return [
        {"player_id": "human-1", "role": "human", "display_name": "南山"},
        *[
            {
                "player_id": f"ai-{index}",
                "role": "ai",
                "display_name": f"小机 {index}",
            }
            for index in range(1, count)
        ],
    ]


class MultiplayerFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-multiplayer-")
        self.addCleanup(self.temporary.cleanup)
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.game_patch = patch.dict(
            GAMES,
            {
                DummyMultiplayer.game_type: DummyMultiplayer(),
                DummyPrivateMultiplayer.game_type: DummyPrivateMultiplayer(),
                DummySixPlayer.game_type: DummySixPlayer(),
                DummyDiscretePlayers.game_type: DummyDiscretePlayers(),
                DummyFourOnly.game_type: DummyFourOnly(),
                DummyTwoOrThree.game_type: DummyTwoOrThree(),
            },
        )
        self.game_patch.start()
        self.addCleanup(self.game_patch.stop)
        database.init_db()

    def create_four(self, **kwargs):
        return framework.create_room(
            DummyMultiplayer.game_type,
            "human_first",
            "human",
            "human-1",
            ordered_participants=ordered_participants(),
            **kwargs,
        )

    def move(self, room, player_id, **payload):
        role = "human" if player_id.startswith("human") else "ai"
        return framework.play_move(
            room["room_id"], role, player_id,
            {"action": "step", **payload},
        )

    def test_production_catalog_keeps_legacy_games_and_declares_new_tables(self):
        production = {
            item["game_type"]: item
            for item in game_catalog()
            if not item["game_type"].startswith("dummy_")
        }
        self.assertEqual(len(production), 21)
        for game_type in (
            "tictactoe", "gomoku", "othello", "connect4", "banqi",
            "checkers", "chess", "jungle", "xiangqi"
        ):
            self.assertEqual(production[game_type]["allowed_player_counts"], [2])
            self.assertEqual(production[game_type]["category"], "board")
        self.assertEqual(production["dots_boxes"]["category"], "board")
        self.assertEqual(production["liars_dice"]["category"], "dice")
        self.assertEqual(production["yahtzee"]["category"], "dice")
        self.assertEqual(production["uno"]["category"], "card")
        self.assertEqual(
            production["uno"]["allowed_player_counts"], [2, 3, 4, 5, 6]
        )
        self.assertTrue(production["uno"]["supports_multiplayer_stakes"])
        self.assertEqual(production["gandengyan"]["category"], "card")
        self.assertEqual(
            production["gandengyan"]["allowed_player_counts"], [2, 3, 4]
        )
        self.assertTrue(production["gandengyan"]["supports_npcs"])
        self.assertTrue(production["gandengyan"]["supports_multiplayer_stakes"])


        self.assertEqual(production["train_cards"]["category"], "card")
        self.assertEqual(
            production["train_cards"]["allowed_player_counts"], [2, 3, 4, 5, 6]
        )
        self.assertTrue(production["train_cards"]["supports_npcs"])
        self.assertTrue(production["train_cards"]["uses_local_npc_strategy"])
        self.assertFalse(production["train_cards"]["supports_stakes"])

        self.assertEqual(production["doudizhu"]["category"], "card")
        self.assertEqual(production["doudizhu"]["allowed_player_counts"], [3])
        self.assertTrue(production["doudizhu"]["supports_npcs"])
        self.assertFalse(production["doudizhu"]["supports_stakes"])


        self.assertEqual(production["guandan"]["category"], "card")
        self.assertEqual(production["guandan"]["allowed_player_counts"], [4])
        self.assertTrue(production["guandan"]["supports_npcs"])
        self.assertFalse(production["guandan"]["supports_stakes"])

        self.assertEqual(production["dots_boxes"]["allowed_player_counts"], [2, 3, 4])
        self.assertEqual(
            production["chinese_checkers"]["allowed_player_counts"], [2, 3, 4, 6]
        )
        self.assertTrue(production["chinese_checkers"]["supports_multiplayer_stakes"])
        self.assertEqual(
            production["liars_dice"]["allowed_player_counts"], [2, 3, 4, 5, 6]
        )
        self.assertEqual(
            production["yahtzee"]["allowed_player_counts"], [2, 3, 4, 5, 6]
        )
        self.assertTrue(production["yahtzee"]["supports_npcs"])
        self.assertFalse(production["yahtzee"]["supports_stakes"])
        self.assertEqual(production["blackjack"]["category"], "card")
        self.assertEqual(
            production["blackjack"]["allowed_player_counts"], [2, 3, 4, 5, 6]
        )
        self.assertTrue(production["blackjack"]["supports_npcs"])
        self.assertFalse(production["blackjack"]["supports_stakes"])
        self.assertEqual(production["aeroplane_chess"]["category"], "board")
        self.assertEqual(
            production["aeroplane_chess"]["allowed_player_counts"], [2, 3, 4]
        )
        self.assertEqual(production["aeroplane_chess"]["recommended_players"], 4)
        self.assertTrue(production["aeroplane_chess"]["supports_npcs"])
        self.assertTrue(production["aeroplane_chess"]["supports_stakes"])
        self.assertTrue(
            production["aeroplane_chess"]["supports_multiplayer_stakes"]
        )
        with self.assertRaisesRegex(framework.DuelError, "最多允许 2"):
            framework.create_room(
                "tictactoe", "human_first", "human", "human-1",
                ordered_participants=ordered_participants(3),
            )

    def test_frontend_uses_collapsed_custom_picker_only_for_multiplayer(self):
        root = Path(__file__).resolve().parents[1] / "app" / "static"
        script = (root / "app.js").read_text(encoding="utf-8")
        html = (root / "index.html").read_text(encoding="utf-8")
        self.assertIn("const multiplayer = maxPlayers > 2", script)
        self.assertNotIn("select.multiple = multiplayer", script)
        self.assertNotIn("selectedOptions", script)
        self.assertIn('id="aiMultiTrigger"', html)
        self.assertIn('aria-haspopup="listbox" aria-expanded="false"', html)
        self.assertIn('id="aiMultiMenu" class="ai-multi-menu hidden" role="listbox"', html)
        self.assertIn('aria-multiselectable="true"', html)
        self.assertIn("selectedMachineIds", script)
        self.assertIn("closeMachineMultiPicker", script)
        self.assertIn("ai_players: participantIds", script)
        self.assertIn('id="roomParticipants"', html)

    def test_four_seats_are_stable_and_turns_cycle_by_player_id(self):
        room = self.create_four()
        self.assertEqual(room["status"], "playing")
        self.assertEqual(room["turn_order"], ["human-1", "ai-1", "ai-2", "ai-3"])
        self.assertEqual(
            [item["seat"] for item in room["participants"]], [0, 1, 2, 3]
        )
        self.assertEqual(room["current_actor"]["player_id"], "human-1")
        self.assertEqual(room["current_actor_seat"], 0)

        observed = []
        for player_id in ("human-1", "ai-1", "ai-2", "ai-3"):
            room = self.move(room, player_id)
            observed.append(room["current_player_id"])
        self.assertEqual(observed, ["ai-1", "ai-2", "ai-3", "human-1"])

        room = self.move(room, "human-1")
        room = self.move(room, "ai-1", skip=["ai-2"])
        self.assertEqual(room["current_player_id"], "ai-3")
        self.assertEqual(room["board_state"]["last_skipped_player_ids"], ["ai-2"])
        room = self.move(
            room, "ai-3", activity={"ai-1": "eliminated"}
        )
        eliminated = next(
            item for item in room["participants"] if item["player_id"] == "ai-1"
        )
        self.assertFalse(eliminated["active"])
        self.assertEqual(eliminated["activity_state"], "eliminated")
        self.assertEqual(room["current_player_id"], "human-1")
        room = self.move(room, "human-1")
        self.assertEqual(room["current_player_id"], "ai-2")

    def test_multiplayer_confirmations_accept_one_by_one_and_reject_cancels(self):
        room = self.create_four(require_confirmations=True)
        self.assertEqual(room["status"], "pending")
        self.assertEqual(room["pending_for"], ["ai-1", "ai-2", "ai-3"])
        self.assertEqual(
            [item["join_status"] for item in room["participants"]],
            ["joined", "invited", "invited", "invited"],
        )
        for player_id in ("ai-1", "ai-2"):
            room = framework.respond_to_invitation(
                room["room_id"], "ai", player_id, "accept"
            )
            self.assertEqual(room["status"], "pending")
        room = framework.respond_to_invitation(
            room["room_id"], "ai", "ai-3", "accept"
        )
        self.assertEqual(room["status"], "playing")
        retried = framework.respond_to_invitation(
            room["room_id"], "ai", "ai-3", "accept"
        )
        self.assertEqual(retried["status"], "playing")
        self.assertTrue(all(
            item["join_status"] == "joined"
            and item["confirmation_status"] == "accepted"
            for item in room["participants"]
        ))

        rejected = framework.create_room(
            DummyMultiplayer.game_type,
            "human_first",
            "human",
            "human-2",
            ordered_participants=[
                {"player_id": "human-2", "role": "human"},
                {"player_id": "ai-a", "role": "ai"},
                {"player_id": "ai-b", "role": "ai"},
            ],
            require_confirmations=True,
        )
        cancelled = framework.respond_to_invitation(
            rejected["room_id"], "ai", "ai-b", "reject"
        )
        self.assertEqual(cancelled["status"], "cancelled")
        with self.assertRaisesRegex(framework.DuelError, "不存在"):
            framework.get_room(rejected["room_id"])

    def test_multiplayer_stake_is_rejected_without_settlement_policy(self):
        with self.assertRaisesRegex(framework.DuelError, "尚未定义筹码结算规则"):
            self.create_four(stake=3)
        conn = database.connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM chip_ledger").fetchone()[0], 0)
        finally:
            conn.close()

    def test_framework_reaches_six_but_never_expands_plugin_table_sizes(self):
        room = framework.create_room(
            DummySixPlayer.game_type,
            "human_first",
            "human",
            "human-1",
            ordered_participants=ordered_participants(6),
        )
        self.assertEqual(len(room["participants"]), 6)
        self.assertEqual(room["status"], "playing")
        self.assertEqual(
            room["turn_order"],
            ["human-1", "ai-1", "ai-2", "ai-3", "ai-4", "ai-5"],
        )
        observed = []
        for player_id in room["turn_order"]:
            room = self.move(room, player_id)
            observed.append(room["current_player_id"])
        self.assertEqual(observed, [
            "ai-1", "ai-2", "ai-3", "ai-4", "ai-5", "human-1",
        ])
        with self.assertRaisesRegex(framework.DuelError, "不能超过 6 人"):
            framework.create_room(
                DummySixPlayer.game_type,
                "human_first",
                "human",
                "human-1",
                ordered_participants=ordered_participants(7),
            )
        for count in (5, 6):
            with self.subTest(count=count):
                with self.assertRaisesRegex(framework.DuelError, "最多允许 4"):
                    framework.create_room(
                        DummyDiscretePlayers.game_type,
                        "human_first",
                        "human",
                        "human-1",
                        ordered_participants=ordered_participants(count),
                    )

    def test_discrete_player_count_contract_models_future_game_shapes(self):
        expected = {
            DummyDiscretePlayers.game_type: (2, 3, 4),
            DummyFourOnly.game_type: (4,),
            DummyTwoOrThree.game_type: (2, 3),
        }
        for game_type, counts in expected.items():
            with self.subTest(game_type=game_type):
                plugin = GAMES[game_type]
                self.assertEqual(plugin.resolved_allowed_player_counts(), counts)
                self.assertTrue(all(plugin.accepts_player_count(n) for n in counts))
                self.assertTrue(all(
                    not plugin.accepts_player_count(n)
                    for n in range(2, 7) if n not in counts
                ))

        waiting = framework.create_room(
            DummyFourOnly.game_type,
            "human_first", "human", "human-4",
            ordered_participants=[{"player_id": "human-4", "role": "human"}],
        )
        for player_id in ("ai-four-1", "ai-four-2"):
            waiting = framework.join_room(waiting["room_id"], "ai", player_id)
            self.assertEqual(waiting["status"], "waiting")
        started = framework.join_room(waiting["room_id"], "ai", "ai-four-3")
        self.assertEqual(started["status"], "playing")
        with self.assertRaisesRegex(framework.DuelError, "已经开始"):
            framework.join_room(started["room_id"], "ai", "ai-four-4")

    def test_waiting_leave_releases_and_reuses_seat_before_start(self):
        room = framework.create_room(
            DummyMultiplayer.game_type,
            "human_first",
            "human",
            "human-1",
            ordered_participants=ordered_participants(1),
        )
        self.assertEqual(room["status"], "waiting")
        room = framework.join_room(room["room_id"], "ai", "ai-old")
        self.assertEqual(room["status"], "waiting")
        self.assertEqual(room["turn_order"], ["human-1", "ai-old"])

        room = framework.leave_room(room["room_id"], "ai", "ai-old")
        self.assertEqual(room["turn_order"], ["human-1"])
        self.assertEqual(room["active_participant_count"], 1)
        room = framework.join_room(room["room_id"], "ai", "ai-new")
        room = framework.join_room(room["room_id"], "ai", "ai-third")
        self.assertEqual(room["status"], "playing")
        self.assertEqual(
            [(item["player_id"], item["seat"]) for item in room["participants"]],
            [("human-1", 0), ("ai-new", 1), ("ai-third", 2)],
        )
        self.assertEqual(room["current_player_id"], "human-1")

    def test_playing_leave_preserves_history_and_obeys_plugin_minimum(self):
        room = self.create_four()
        room = framework.leave_room(room["room_id"], "ai", "ai-2", "human-1")
        departed = next(
            item for item in room["participants"] if item["player_id"] == "ai-2"
        )
        self.assertEqual(departed["join_status"], "left")
        self.assertEqual(departed["activity_state"], "inactive")
        self.assertFalse(departed["active"])
        self.assertEqual(room["status"], "playing")
        self.assertEqual(room["current_player_id"], "human-1")
        self.assertEqual(room["active_participant_count"], 3)
        self.assertEqual(
            [item["event_type"] for item in framework.read_new_room_events(
                room["room_id"], "ai-1"
            )],
            ["leave"],
        )
        with self.assertRaisesRegex(framework.DuelError, "已经离开"):
            framework.post_message(
                room["room_id"], "ai", "ai-2", "我又回来了"
            )

        room = framework.leave_room(
            room["room_id"], "human", "human-1"
        )
        self.assertEqual(room["status"], "finished")
        self.assertEqual(room["winner"], "draw")
        self.assertIsNone(room["current_player_id"])
        self.assertEqual(room["result"]["reason"], "insufficient_players")
        self.assertEqual(room["active_participant_count"], 2)

    def test_duplicate_concurrent_leave_is_idempotent(self):
        room = self.create_four()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda _index: framework.leave_room(
                    room["room_id"], "ai", "ai-3"
                ),
                range(2),
            ))
        self.assertEqual({item["revision"] for item in results}, {1})
        timeline = framework.list_timeline(room["room_id"])
        self.assertEqual(
            [item["event_type"] for item in timeline].count("leave"), 1
        )
        self.assertEqual(framework.list_ai_rooms("ai-3"), [])

    def test_additive_migration_is_idempotent_and_preserves_seats(self):
        room = self.create_four()
        database.init_db()
        database.init_db()
        restored = framework.get_room(room["room_id"])
        self.assertEqual(restored["turn_order"], room["turn_order"])
        conn = database.connect()
        try:
            participant_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(room_participants)")
            }
            message_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(room_messages)")
            }
        finally:
            conn.close()
        self.assertTrue(
            {"join_status", "activity_state", "token"} <= participant_columns
        )
        self.assertIn("visible_to_json", message_columns)

    def test_each_cursor_is_independent_and_honors_event_visibility(self):
        room = self.create_four()
        room_id = room["room_id"]
        framework.post_message(room_id, "human", "human-1", "公开消息")
        self.assertEqual(
            [item["text"] for item in framework.read_new_room_events(room_id, "ai-1")],
            ["公开消息"],
        )
        self.assertEqual(
            [item["text"] for item in framework.read_new_room_events(room_id, "ai-2")],
            ["公开消息"],
        )
        framework.post_message(
            room_id, "human", "human-1", "只给一号",
            visible_to_player_ids={"ai-1"},
        )
        self.assertTrue(framework.has_new_room_events(room_id, "ai-1"))
        self.assertFalse(framework.has_new_room_events(room_id, "ai-2"))
        self.assertEqual(
            [item["text"] for item in framework.read_new_room_events(room_id, "ai-1")],
            ["只给一号"],
        )
        self.assertEqual(framework.read_new_room_events(room_id, "ai-2"), [])
        framework.post_message(room_id, "ai", "ai-3", "再次公开")
        self.assertEqual(
            [item["text"] for item in framework.read_new_room_events(room_id, "ai-1")],
            ["再次公开"],
        )
        self.assertEqual(
            [item["text"] for item in framework.read_new_room_events(room_id, "ai-2")],
            ["再次公开"],
        )

    def test_card_dice_and_flow_helpers_persist_without_rerandomizing(self):
        state = {}
        ensure_flow(state, phase="deal")
        zones = ensure_card_zones(
            state, list(range(12)), ["p1", "p2", "p3"],
            rng=random.Random(9),
        )
        original_deck = list(zones["deck"])
        first_hand = draw_cards(state, "p1", 3)
        private_roll = roll_dice(
            state, roller_player_id="p1", count=2,
            visible_to_player_ids={"p1"}, rng=random.Random(4),
        )
        public_roll = roll_dice(
            state, roller_player_id="p2", rng=random.Random(5),
        )
        advance_flow(state, phase="play", next_round=True)

        restored = json.loads(json.dumps(state))
        self.assertIs(ensure_card_zones(
            restored, ["replacement"], ["p1", "p2", "p3"]
        ), restored["cards"])
        self.assertEqual(restored["cards"]["deck"], original_deck[:-3])
        self.assertEqual(private_hand(restored, "p1"), first_hand)
        self.assertEqual(restored["flow"], {
            "phase": "play", "round_number": 2, "turn_number": 0,
        })
        self.assertEqual(
            visible_dice_rolls(restored, "p1"),
            [private_roll, public_roll],
        )
        self.assertEqual(visible_dice_rolls(restored, "p3"), [public_roll])
        public_cards = public_card_state(restored)
        self.assertNotIn("hands", public_cards)
        self.assertEqual(public_cards["hand_counts"]["p1"], 3)

    def test_nonparticipant_cannot_project_private_room_state(self):
        room = framework.create_room(
            DummyPrivateMultiplayer.game_type,
            "human_first",
            "human",
            "human-1",
            ordered_participants=ordered_participants(),
        )
        with self.assertRaisesRegex(framework.DuelError, "viewer"):
            framework.project_room_for_viewer(room, "outsider")

    def test_multiplayer_settlement_requires_complete_zero_sum_integer_map(self):
        plugin = GAMES[DummyPrivateMultiplayer.game_type]
        invalid_maps = (
            {"human-1": 15, "ai-1": -5, "ai-2": -5},
            {"human-1": 16, "ai-1": -5, "ai-2": -5, "ai-3": -5},
            {"human-1": 15, "ai-1": -5, "ai-2": -5, "ai-3": -5.0},
        )
        for index, invalid in enumerate(invalid_maps):
            with self.subTest(invalid=invalid):
                room = framework.create_room(
                    DummyPrivateMultiplayer.game_type,
                    "human_first", "human", "human-1",
                    ordered_participants=ordered_participants(),
                    stake=5, require_confirmations=True,
                )
                for player_id in ("ai-1", "ai-2", "ai-3"):
                    room = framework.respond_to_invitation(
                        room["room_id"], "ai", player_id, "accept"
                    )
                baseline_revision = room["revision"]
                with patch.object(plugin, "settlement_deltas", return_value=invalid):
                    with self.assertRaises(framework.DuelError):
                        framework.play_move(
                            room["room_id"], "human", "human-1",
                            {"action": "finish"},
                        )
                restored = framework.get_room(room["room_id"])
                self.assertEqual(restored["status"], "playing")
                self.assertEqual(restored["revision"], baseline_revision)


class MultiplayerApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-multi-api-")
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temporary.name) / "test.db"
        )
        self.db_patch.start()
        self.game_patch = patch.dict(
            GAMES,
            {
                DummyMultiplayer.game_type: DummyMultiplayer(),
                DummyPrivateMultiplayer.game_type: DummyPrivateMultiplayer(),
                DummySixPlayer.game_type: DummySixPlayer(),
                DummyDiscretePlayers.game_type: DummyDiscretePlayers(),
                DummyFourOnly.game_type: DummyFourOnly(),
                DummyTwoOrThree.game_type: DummyTwoOrThree(),
            },
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

    @staticmethod
    def headers(machine_count=3):
        machines = [
            {"id": f"ai-{index}", "name": f"小机 {index}"}
            for index in range(1, machine_count + 1)
        ]
        encoded = base64.urlsafe_b64encode(
            json.dumps(machines, ensure_ascii=False).encode("utf-8")
        ).decode("ascii").rstrip("=")
        return {
            "X-Duel-Human-Player": "human-1",
            "X-Duel-Human-Name": "%E5%8D%97%E5%B1%B1",
            "X-Duel-Bound-Ais": encoded,
        }

    @staticmethod
    def event_cursor(room_id, player_id):
        conn = database.connect()
        try:
            row = conn.execute(
                """
                SELECT last_event_id FROM room_event_cursors
                WHERE room_id = ? AND player_id = ?
                """,
                (room_id, player_id),
            ).fetchone()
            return row["last_event_id"]
        finally:
            conn.close()

    async def create_web_room(self):
        response = await self.client.post(
            "/api/rooms",
            headers=self.headers(),
            json={
                "player_id": "human-1",
                "ai_players": ["ai-1", "ai-2", "ai-3"],
                "game_type": DummyMultiplayer.game_type,
                "mode": "human_first",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["room"]

    async def test_web_multiselect_creates_only_trusted_stable_seats(self):
        room = await self.create_web_room()
        self.assertEqual(room["turn_order"], ["human-1", "ai-1", "ai-2", "ai-3"])
        rejected = await self.client.post(
            "/api/rooms",
            headers=self.headers(),
            json={
                "player_id": "human-1",
                "ai_players": ["ai-1", "unbound-ai"],
                "game_type": DummyMultiplayer.game_type,
            },
        )
        self.assertEqual(rejected.status_code, 403)

        two_player = await self.client.post(
            "/api/rooms",
            headers=self.headers(),
            json={
                "player_id": "human-1",
                "ai_players": ["ai-1", "ai-2"],
                "game_type": "tictactoe",
            },
        )
        self.assertEqual(two_player.status_code, 400)

    async def test_web_opening_preferences_resolve_to_exact_player_and_seat(self):
        base_body = {
            "player_id": "human-1",
            "ai_players": ["ai-1", "ai-2", "ai-3"],
            "game_type": DummyMultiplayer.game_type,
            "target_player_count": 4,
        }
        chosen_candidates = []

        def choose_second(candidates):
            chosen_candidates.append(list(candidates))
            return candidates[1]

        with patch.object(main_module, "secure_choice", side_effect=choose_second):
            machine_first = await self.client.post(
                "/api/rooms",
                headers=self.headers(),
                json={**base_body, "mode": "ai_first"},
            )
        self.assertEqual(machine_first.status_code, 200, machine_first.text)
        machine_payload = machine_first.json()
        self.assertEqual(chosen_candidates, [["ai-1", "ai-2", "ai-3"]])
        self.assertEqual(machine_payload["room"]["current_player_id"], "ai-2")
        self.assertEqual(machine_payload["room"]["current_actor_seat"], 2)
        self.assertEqual(machine_payload["room"]["turn"], "ai")
        self.assertEqual(machine_payload["room"]["mode"], "ai_first")
        self.assertIn("先手：小机 2", machine_payload["message"])

        chosen_candidates.clear()

        def choose_last(candidates):
            chosen_candidates.append(list(candidates))
            return candidates[-1]

        with patch.object(main_module, "secure_choice", side_effect=choose_last):
            random_first = await self.client.post(
                "/api/rooms",
                headers=self.headers(),
                json={**base_body, "mode": "random"},
            )
        self.assertEqual(random_first.status_code, 200, random_first.text)
        random_payload = random_first.json()
        self.assertEqual(
            chosen_candidates,
            [["human-1", "ai-1", "ai-2", "ai-3"]],
        )
        self.assertEqual(random_payload["room"]["current_player_id"], "ai-3")
        self.assertEqual(random_payload["room"]["current_actor_seat"], 3)
        self.assertEqual(random_payload["room"]["turn"], "ai")
        self.assertIn("先手：小机 3", random_payload["message"])

        human_first = await self.client.post(
            "/api/rooms",
            headers=self.headers(),
            json={**base_body, "mode": "human_first"},
        )
        self.assertEqual(human_first.status_code, 200, human_first.text)
        human_payload = human_first.json()
        self.assertEqual(human_payload["room"]["current_player_id"], "human-1")
        self.assertEqual(human_payload["room"]["current_actor_seat"], 0)
        self.assertEqual(human_payload["room"]["turn"], "human")
        self.assertEqual(human_payload["room"]["mode"], "human_first")
        self.assertIn("先手：南山", human_payload["message"])

    async def test_two_player_random_keeps_opener_and_x_token_in_sync(self):
        with patch.object(main_module, "secure_choice", return_value="ai-1") as chooser:
            response = await self.client.post(
                "/api/rooms",
                headers=self.headers(1),
                json={
                    "player_id": "human-1",
                    "ai_players": ["ai-1"],
                    "game_type": "tictactoe",
                    "mode": "random",
                    "target_player_count": 2,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        chooser.assert_called_once_with(["human-1", "ai-1"])
        room = response.json()["room"]
        self.assertEqual(room["current_player_id"], "ai-1")
        self.assertEqual(room["current_actor_seat"], 1)
        self.assertEqual(room["mode"], "ai_first")
        self.assertEqual(room["board_state"]["marks"]["ai"], "X")
        ai = next(item for item in room["participants"] if item["player_id"] == "ai-1")
        self.assertEqual(ai["token"], "X")

    async def test_web_and_mcp_enforce_discrete_counts_up_to_six(self):
        for target in (5, 6):
            with self.subTest(target=target):
                response = await self.client.post(
                    "/api/rooms",
                    headers=self.headers(5),
                    json={
                        "player_id": "human-1",
                        "ai_players": [
                            f"ai-{index}" for index in range(1, target)
                        ],
                        "game_type": DummyDiscretePlayers.game_type,
                        "target_player_count": target,
                    },
                )
                self.assertEqual(response.status_code, 400, response.text)

                mcp = await self.client.post(
                    "/mcp/play",
                    json={
                        "action": "new",
                        "player_id": "ai-1",
                        "opponent_id": "human-1",
                        "participant_ids": [
                            "human-1",
                            *[f"ai-{index}" for index in range(1, target)],
                        ],
                        "game_type": DummyDiscretePlayers.game_type,
                        "target_player_count": target,
                    },
                )
                self.assertEqual(mcp.status_code, 400, mcp.text)

        fixed_four = await self.client.post(
            "/api/rooms",
            headers=self.headers(3),
            json={
                "player_id": "human-1",
                "ai_players": ["ai-1", "ai-2"],
                "game_type": DummyFourOnly.game_type,
                "target_player_count": 3,
            },
        )
        self.assertEqual(fixed_four.status_code, 400, fixed_four.text)

        two_or_three = await self.client.post(
            "/api/rooms",
            headers=self.headers(3),
            json={
                "player_id": "human-1",
                "ai_players": ["ai-1", "ai-2", "ai-3"],
                "game_type": DummyTwoOrThree.game_type,
                "target_player_count": 4,
            },
        )
        self.assertEqual(two_or_three.status_code, 400, two_or_three.text)

        six = await self.client.post(
            "/api/rooms",
            headers=self.headers(5),
            json={
                "player_id": "human-1",
                "ai_players": [f"ai-{index}" for index in range(1, 6)],
                "game_type": DummySixPlayer.game_type,
                "target_player_count": 6,
            },
        )
        self.assertEqual(six.status_code, 200, six.text)
        self.assertEqual(
            six.json()["room"]["turn_order"],
            ["human-1", "ai-1", "ai-2", "ai-3", "ai-4", "ai-5"],
        )

        identity = await self.client.get("/api/whoami", headers=self.headers(5))
        catalog = {
            game["game_type"]: game for game in identity.json()["games"]
        }
        self.assertEqual(
            catalog[DummyDiscretePlayers.game_type]["allowed_player_counts"],
            [2, 3, 4],
        )
        self.assertEqual(
            catalog[DummyFourOnly.game_type]["allowed_player_counts"], [4]
        )
        self.assertEqual(
            catalog[DummyTwoOrThree.game_type]["allowed_player_counts"], [2, 3]
        )

    async def test_waiters_keep_events_until_each_participant_turn(self):
        room = await self.create_web_room()
        room_id = room["room_id"]
        player_ids = ("ai-1", "ai-2", "ai-3")
        for player_id in player_ids:
            bootstrap = await self.client.post(
                "/mcp/play",
                json={
                    "action": "state", "player_id": player_id,
                    "room_id": room_id,
                },
            )
            self.assertTrue(bootstrap.json()["bootstrap"])
        initial_cursors = {
            player_id: self.event_cursor(room_id, player_id)
            for player_id in player_ids
        }
        with patch.object(main_module, "MCP_WAIT_SECONDS", 0.8):
            waiters = {
                player_id: asyncio.create_task(self.client.post(
                    "/mcp/play",
                    json={
                        "action": "state", "player_id": player_id,
                        "room_id": room_id, "wait": True,
                    },
                ))
                for player_id in player_ids
            }
            await asyncio.sleep(0.02)
            sent = await self.client.post(
                f"/api/rooms/{room_id}/messages",
                json={"player_id": "human-1", "message": "大家可见"},
            )
            self.assertEqual(sent.status_code, 200, sent.text)
            framework.post_message(
                room_id, "human", "human-1", "只给二号",
                visible_to_player_ids={"ai-2"},
            )
            main_module.revision_events.notify(room_id)
            await asyncio.sleep(0.05)
            self.assertTrue(all(not waiter.done() for waiter in waiters.values()))
            self.assertEqual(
                {
                    player_id: self.event_cursor(room_id, player_id)
                    for player_id in player_ids
                },
                initial_cursors,
            )

            human_move = {"action": "step"}
            moved = await self.client.post(
                f"/api/rooms/{room_id}/move",
                headers=self.headers(),
                json={"player_id": "human-1", "move": human_move},
            )
            self.assertEqual(moved.status_code, 200, moved.text)
            ai_one = await asyncio.wait_for(waiters["ai-1"], timeout=1)
            self.assertEqual(ai_one.json()["current_actor"]["player_id"], "ai-1")
            self.assertEqual(ai_one.json()["events"], [
                {"name": "南山", "message": "大家可见"},
                {"name": "南山", "move": human_move},
            ])
            self.assertFalse(waiters["ai-2"].done())
            self.assertFalse(waiters["ai-3"].done())
            self.assertGreater(
                self.event_cursor(room_id, "ai-1"), initial_cursors["ai-1"]
            )
            self.assertEqual(
                self.event_cursor(room_id, "ai-2"), initial_cursors["ai-2"]
            )
            self.assertEqual(
                self.event_cursor(room_id, "ai-3"), initial_cursors["ai-3"]
            )

            ai_one_move = await self.client.post(
                "/mcp/play",
                json={
                    "action": "move", "player_id": "ai-1",
                    "room_id": room_id, "move": {"action": "step"},
                },
            )
            self.assertEqual(ai_one_move.status_code, 200, ai_one_move.text)
            self.assertNotIn("events", ai_one_move.json())
            ai_two = await asyncio.wait_for(waiters["ai-2"], timeout=1)
            self.assertEqual(ai_two.json()["current_actor"]["player_id"], "ai-2")
            self.assertEqual(ai_two.json()["events"], [
                {"name": "南山", "message": "大家可见"},
                {"name": "南山", "message": "只给二号"},
                {"name": "南山", "move": human_move},
                {"name": "小机 1", "move": {"action": "step"}},
            ])
            self.assertFalse(waiters["ai-3"].done())

            ai_two_move = await self.client.post(
                "/mcp/play",
                json={
                    "action": "move", "player_id": "ai-2",
                    "room_id": room_id, "move": {"action": "step"},
                },
            )
            self.assertEqual(ai_two_move.status_code, 200, ai_two_move.text)
            ai_three = await asyncio.wait_for(waiters["ai-3"], timeout=1)
            self.assertEqual(ai_three.json()["current_actor"]["player_id"], "ai-3")
            self.assertEqual(ai_three.json()["events"], [
                {"name": "南山", "message": "大家可见"},
                {"name": "南山", "move": human_move},
                {"name": "小机 1", "move": {"action": "step"}},
                {"name": "小机 2", "move": {"action": "step"}},
            ])

    async def test_web_waiter_still_wakes_for_a_visible_message(self):
        room = await self.create_web_room()
        room_id = room["room_id"]
        moved = await self.client.post(
            f"/api/rooms/{room_id}/move",
            headers=self.headers(),
            json={"player_id": "human-1", "move": {"action": "step"}},
        )
        self.assertEqual(moved.status_code, 200, moved.text)
        with patch.object(main_module, "MCP_WAIT_SECONDS", 0.5):
            human_waiter = asyncio.create_task(self.client.get(
                f"/api/rooms/{room_id}",
                headers=self.headers(),
                params={"player_id": "human-1", "wait": "true"},
            ))
            await asyncio.sleep(0.02)
            framework.post_message(
                room_id, "ai", "ai-1", "只给人类",
                visible_to_player_ids={"human-1"},
            )
            main_module.revision_events.notify(room_id)
            human_result = await asyncio.wait_for(human_waiter, timeout=1)
            self.assertEqual(human_result.status_code, 200)
            self.assertEqual(
                human_result.json()["timeline"][-1]["text"], "只给人类"
            )

    async def test_eliminated_participant_wakes_and_receives_cause(self):
        room = await self.create_web_room()
        room_id = room["room_id"]
        bootstrap = await self.client.post(
            "/mcp/play",
            json={
                "action": "state", "player_id": "ai-3",
                "room_id": room_id,
            },
        )
        self.assertTrue(bootstrap.json()["bootstrap"])
        with patch.object(main_module, "MCP_WAIT_SECONDS", 0.5):
            waiter = asyncio.create_task(self.client.post(
                "/mcp/play",
                json={
                    "action": "state", "player_id": "ai-3",
                    "room_id": room_id, "wait": True,
                },
            ))
            await asyncio.sleep(0.02)
            move = {"action": "step", "activity": {"ai-3": "eliminated"}}
            eliminated = await self.client.post(
                f"/api/rooms/{room_id}/move",
                headers=self.headers(),
                json={"player_id": "human-1", "move": move},
            )
            self.assertEqual(eliminated.status_code, 200, eliminated.text)
            resumed = await asyncio.wait_for(waiter, timeout=1)
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertEqual(resumed.json()["participant_status"], "eliminated")
        self.assertEqual(resumed.json()["events"], [{
            "name": "南山", "move": move,
        }])

    async def test_private_four_player_full_flow_and_explicit_settlement(self):
        room = framework.create_room(
            DummyPrivateMultiplayer.game_type,
            "human_first",
            "human",
            "human-1",
            ordered_participants=ordered_participants(),
            participant_names={
                item["player_id"]: item["display_name"]
                for item in ordered_participants()
            },
            stake=5,
            require_confirmations=True,
        )
        room_id = room["room_id"]
        for player_id in ("ai-1", "ai-2", "ai-3"):
            accepted = await self.client.post(
                "/mcp/play",
                json={
                    "action": "accept", "player_id": player_id,
                    "room_id": room_id,
                },
            )
            self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["status"], "playing")

        views = {"ai-3": accepted.json()["room"]}
        for player_id in ("ai-1", "ai-2"):
            response = await self.client.post(
                "/mcp/play",
                json={
                    "action": "state", "player_id": player_id,
                    "room_id": room_id,
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["bootstrap"])
            views[player_id] = response.json()["room"]
        self.assertEqual(len({
            tuple(view["private_state"]["hand"])
            for view in views.values()
        }), 3)
        for player_id, view in views.items():
            self.assertNotIn("hands", view["board_state"]["cards"])
            self.assertNotIn("legal_actions", view["board_state"])
            self.assertEqual(
                view["private_state"]["legal_actions"], [f"act-{player_id}"]
            )
            self.assertEqual(view["viewer"]["player_id"], player_id)

        unauthenticated = await self.client.get(f"/api/rooms/{room_id}")
        self.assertEqual(unauthenticated.status_code, 403)
        spoofed = await self.client.get(
            f"/api/rooms/{room_id}",
            headers=self.headers(),
            params={"player_id": "ai-1"},
        )
        self.assertEqual(spoofed.status_code, 403)
        web = await self.client.get(
            f"/api/rooms/{room_id}", headers=self.headers()
        )
        self.assertEqual(web.status_code, 200, web.text)
        self.assertEqual(web.json()["room"]["viewer"]["player_id"], "human-1")
        outsider = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "outsider", "room_id": room_id},
        )
        self.assertEqual(outsider.status_code, 403)
        forged_viewer = await self.client.post(
            "/mcp/play",
            json={
                "action": "state", "player_id": "ai-1", "room_id": room_id,
                "viewer": "ai-2",
            },
        )
        self.assertEqual(forged_viewer.status_code, 422)

        human_move = await self.client.post(
            f"/api/rooms/{room_id}/move",
            headers=self.headers(),
            json={
                "player_id": "human-1",
                "move": {
                    "action": "step", "secret": "only-ai-2",
                    "reveal_to": "ai-2",
                },
            },
        )
        self.assertEqual(human_move.status_code, 200, human_move.text)
        hidden_delta = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "ai-3", "room_id": room_id},
        )
        self.assertNotIn("events", hidden_delta.json())
        ai_one = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-1", "room_id": room_id,
                "move": {
                    "action": "step", "phase": "play", "next_round": True,
                    "activity": {"ai-3": "eliminated"},
                },
            },
        )
        self.assertEqual(ai_one.status_code, 200, ai_one.text)
        self.assertNotIn("events", ai_one.json())
        eliminated_delta = await self.client.post(
            "/mcp/play",
            json={"action": "state", "player_id": "ai-3", "room_id": room_id},
        )
        self.assertEqual(
            eliminated_delta.json()["participant_status"], "eliminated"
        )
        self.assertNotIn(
            "secret", eliminated_delta.json()["events"][0]["move"]
        )
        retained = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-2", "room_id": room_id,
                "move": {"action": "step", "retain": True},
            },
        )
        self.assertEqual(retained.json()["current_actor"]["player_id"], "ai-2")
        self.assertEqual(
            retained.json()["events"][0]["move"]["secret"],
            "only-ai-2",
        )
        terminal = await self.client.post(
            "/mcp/play",
            json={
                "action": "move", "player_id": "ai-2", "room_id": room_id,
                "move": {"action": "finish"},
            },
        )
        self.assertEqual(terminal.status_code, 200, terminal.text)
        self.assertEqual(terminal.json()["status"], "finished")
        restored = framework.get_room(room_id)
        self.assertEqual(restored["board_state"]["flow"], {
            "phase": "play", "round_number": 2, "turn_number": 2,
        })
        self.assertEqual(
            next(item for item in restored["participants"]
                 if item["player_id"] == "ai-3")["activity_state"],
            "eliminated",
        )
        self.assertEqual(
            restored["result"]["settlement_deltas"],
            {"human-1": 15, "ai-1": -5, "ai-2": -5, "ai-3": -5},
        )
        from app import chips
        self.assertEqual(chips.get_wallet("human", "human-1")["balance"], 250)
        for player_id in ("ai-1", "ai-2", "ai-3"):
            self.assertEqual(chips.get_wallet("ai", player_id)["balance"], 220)


if __name__ == "__main__":
    unittest.main()
