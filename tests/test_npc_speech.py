import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database, framework
from app.games import GAMES
from app.games.base import MoveResult
from app.npc_controller import (
    NPC_CONTEXT_MESSAGE_LIMIT,
    _authoritative_legal_actions,
    _decision_request,
    _speech_request,
    run_current_npc_turn,
    wait_for_npc_speech_tasks,
)
from app.npc_providers import NpcProvider, ProviderDecision
from app.npc_runtime import complete_npc_full_turn
from tests.test_npc_framework import DummyNpcMultiplayer, write_persona


class SpeechTrackingProvider(NpcProvider):
    name = "speech-tracking"
    available = True
    max_concurrency = 4

    def __init__(self, *, decision_messages=None, speech_outcomes=None):
        self.decision_messages = list(decision_messages or [])
        self.speech_outcomes = list(speech_outcomes or [])
        self.decision_requests = []
        self.speech_requests = []

    async def decide(self, request):
        self.decision_requests.append(request)
        step = next((
            item for item in request.legal_actions
            if item["action"] == {"action": "step"}
        ), request.legal_actions[0])
        message = self.decision_messages.pop(0) if self.decision_messages else None
        return ProviderDecision(step["action_id"], message)

    async def speak(self, request):
        self.speech_requests.append(request)
        outcome = self.speech_outcomes.pop(0) if self.speech_outcomes else "轮到我说了。"
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class NeverDecideProvider(SpeechTrackingProvider):
    async def decide(self, request):
        self.decision_requests.append(request)
        raise AssertionError("decision provider must not be called")


class BlockingFailedSpeechProvider(SpeechTrackingProvider):
    def __init__(self):
        super().__init__()
        self.speech_started = asyncio.Event()
        self.release_speech = asyncio.Event()

    async def speak(self, request):
        self.speech_requests.append(request)
        self.speech_started.set()
        await self.release_speech.wait()
        raise TimeoutError("speech timeout")


class NpcSpeechCadenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-npc-speech-")
        root = Path(self.temporary.name)
        self.db_patch = patch.object(database, "DB_PATH", root / "test.db")
        self.db_patch.start()
        self.game_patch = patch.dict(
            GAMES, {DummyNpcMultiplayer.game_type: DummyNpcMultiplayer()}
        )
        self.game_patch.start()
        self.persona_dir = root / "personas"
        self.persona_dir.mkdir()
        write_persona(self.persona_dir, "quiet", "安静测试机", "quiet persona")
        write_persona(self.persona_dir, "bright", "明亮测试机", "bright persona")
        self.env_patch = patch.dict(
            "os.environ", {"DUEL_NPC_PERSONAS_DIR": str(self.persona_dir)}
        )
        self.env_patch.start()
        database.init_db()

    async def asyncTearDown(self):
        await wait_for_npc_speech_tasks()
        self.env_patch.stop()
        self.game_patch.stop()
        self.db_patch.stop()
        self.temporary.cleanup()

    @staticmethod
    def participants():
        return [
            {
                "player_id": "human-1", "role": "human",
                "participant_kind": "human", "display_name": "人类",
            },
            {
                "player_id": "ai-1", "role": "ai",
                "participant_kind": "bound_machine", "display_name": "小机",
            },
            {
                "player_id": "npc:quiet", "role": "ai",
                "participant_kind": "system_npc", "npc_persona_id": "quiet",
                "display_name": "安静测试机",
            },
            {
                "player_id": "npc:bright", "role": "ai",
                "participant_kind": "system_npc", "npc_persona_id": "bright",
                "display_name": "明亮测试机",
            },
        ]

    def room_at_quiet_npc(self):
        room = framework.create_room(
            DummyNpcMultiplayer.game_type,
            "human_first",
            "human",
            "human-1",
            opponent_id="ai-1",
            ordered_participants=self.participants(),
            enforce_trusted_pair=True,
        )
        room = framework.play_move(
            room["room_id"], "human", "human-1", {"action": "step"}
        )
        return framework.play_move(
            room["room_id"], "ai", "ai-1", {"action": "step"}
        )

    def advance_to_quiet_npc(self, room_id):
        room = framework.get_room(room_id)
        guard = 0
        while room["current_player_id"] != "npc:quiet":
            guard += 1
            self.assertLess(guard, 5)
            actor_id = room["current_player_id"]
            actor = next(
                item for item in room["participants"]
                if item["player_id"] == actor_id
            )
            room = framework.play_move(
                room_id, actor["role"], actor_id, {"action": "step"}
            )
        return room

    @staticmethod
    def speech_state(room_id, npc_player_id="npc:quiet"):
        conn = database.connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM npc_speech_states
                WHERE room_id = ? AND npc_player_id = ?
                """,
                (room_id, npc_player_id),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    async def complete_quiet_turn(self, room_id, provider):
        result = await run_current_npc_turn(room_id, provider=provider)
        self.assertIn(result.status, {"applied", "already_applied"})
        return result

    async def test_only_authoritative_action_skips_decision_provider(self):
        room = self.room_at_quiet_npc()
        plugin = GAMES[DummyNpcMultiplayer.game_type]
        provider = NeverDecideProvider()
        with patch.object(
            plugin, "npc_legal_actions", return_value=[{"action": "step"}]
        ):
            result = await self.complete_quiet_turn(room["room_id"], provider)
        self.assertEqual(result.source, "forced")
        self.assertEqual(result.action, {"action": "step"})
        self.assertEqual(provider.decision_requests, [])
        self.assertEqual(provider.speech_requests, [])

    async def test_real_choice_still_calls_decision_provider(self):
        room = self.room_at_quiet_npc()
        provider = SpeechTrackingProvider()
        result = await self.complete_quiet_turn(room["room_id"], provider)
        self.assertEqual(result.source, provider.name)
        self.assertEqual(len(provider.decision_requests), 1)
        self.assertEqual(len(provider.decision_requests[0].legal_actions), 2)

    async def test_local_strategy_never_calls_decision_provider_but_can_speak(self):
        room = self.room_at_quiet_npc()
        room_id = room["room_id"]
        plugin = GAMES[DummyNpcMultiplayer.game_type]
        provider = NeverDecideProvider(speech_outcomes=["本地策略也能发言。"])
        with (
            patch.object(plugin, "uses_local_npc_strategy", True),
            patch.object(
                plugin, "choose_local_npc_action", return_value={"action": "step"}
            ),
        ):
            for turn_index in range(3):
                result = await self.complete_quiet_turn(room_id, provider)
                self.assertEqual(result.source, "local")
                if turn_index < 2:
                    self.assertIsNone(result.speech_task)
                if turn_index < 2:
                    self.advance_to_quiet_npc(room_id)
            await wait_for_npc_speech_tasks()
        self.assertEqual(provider.decision_requests, [])
        self.assertEqual(len(provider.speech_requests), 1)
        messages = [
            item for item in framework.list_timeline(room_id)
            if item["event_type"] == "message"
            and item["sender_player_id"] == "npc:quiet"
        ]
        self.assertEqual([item["text"] for item in messages], ["本地策略也能发言。"])

    async def test_third_silent_full_turn_speaks_once_and_is_idempotent(self):
        room = self.room_at_quiet_npc()
        room_id = room["room_id"]
        for index in range(25):
            framework.post_message(
                room_id, "human", "human-1",
                f"强制发言长增量 {index:02d} " + "长" * 470,
            )
        provider = SpeechTrackingProvider(speech_outcomes=["第三回合。"])
        completion_revision = None
        for turn_index in range(3):
            result = await self.complete_quiet_turn(room_id, provider)
            completion_revision = result.room["revision"]
            if turn_index < 2:
                self.assertIsNone(result.speech_task)
                state = self.speech_state(room_id)
                self.assertEqual(state["silent_completed_turns"], turn_index + 1)
                self.assertEqual(state["speech_pending"], 0)
                self.advance_to_quiet_npc(room_id)
            else:
                self.assertIsNotNone(result.speech_task)
        await wait_for_npc_speech_tasks()
        self.assertEqual(len(provider.speech_requests), 1)
        self.assertLessEqual(
            len(provider.speech_requests[0].messages()[1]["content"]),
            NPC_CONTEXT_MESSAGE_LIMIT,
        )
        self.assertIn(
            "强制发言长增量 24",
            provider.speech_requests[0].messages()[1]["content"],
        )
        state = self.speech_state(room_id)
        self.assertEqual(state["silent_completed_turns"], 0)
        self.assertEqual(state["speech_pending"], 0)
        self.assertEqual(state["last_attempt_status"], "sent")
        self.assertIsNone(
            complete_npc_full_turn(room_id, "npc:quiet", completion_revision)
        )
        messages = [
            item for item in framework.list_timeline(room_id)
            if item["event_type"] == "message"
            and item["sender_player_id"] == "npc:quiet"
        ]
        self.assertEqual([item["text"] for item in messages], ["第三回合。"])

    async def test_normal_move_message_resets_silence_without_speech_only_call(self):
        room = self.room_at_quiet_npc()
        room_id = room["room_id"]
        provider = SpeechTrackingProvider(
            decision_messages=[None, None, "这回我已经说了。"]
        )
        for turn_index in range(3):
            result = await self.complete_quiet_turn(room_id, provider)
            self.assertIsNone(result.speech_task)
            if turn_index < 2:
                self.advance_to_quiet_npc(room_id)
        self.assertEqual(provider.speech_requests, [])
        state = self.speech_state(room_id)
        self.assertEqual(state["silent_completed_turns"], 0)
        self.assertEqual(state["speech_pending"], 0)

    async def test_extra_actions_under_same_owner_count_as_one_full_turn(self):
        room = self.room_at_quiet_npc()
        room_id = room["room_id"]
        plugin = GAMES[DummyNpcMultiplayer.game_type]
        provider = SpeechTrackingProvider()
        action_count = 0

        def retain_twice(state, move, actor):
            nonlocal action_count
            del move
            action_count += 1
            state["actions"].append(actor["player_id"])
            return MoveResult(state=state, retain_turn=action_count < 3)

        with patch.object(plugin, "apply_action", side_effect=retain_twice):
            first = await self.complete_quiet_turn(room_id, provider)
            second = await self.complete_quiet_turn(room_id, provider)
            third = await self.complete_quiet_turn(room_id, provider)
        self.assertEqual(first.room["current_player_id"], "npc:quiet")
        self.assertEqual(second.room["current_player_id"], "npc:quiet")
        self.assertNotEqual(third.room["current_player_id"], "npc:quiet")
        state = self.speech_state(room_id)
        self.assertEqual(state["silent_completed_turns"], 1)
        self.assertEqual(len(provider.decision_requests), 3)
        self.assertEqual(provider.speech_requests, [])

    async def test_speech_context_is_recent_current_and_viewer_safe(self):
        room = self.room_at_quiet_npc()
        room_id = room["room_id"]
        framework.post_message(
            room_id, "human", "human-1", "较早但当前 NPC 可见的私聊",
            visible_to_player_ids={"npc:quiet"},
        )
        framework.post_message(
            room_id, "human", "human-1", "绝不能泄漏给当前 NPC",
            visible_to_player_ids={"npc:bright"},
        )
        for index in range(25):
            framework.post_message(
                room_id, "human", "human-1", f"完整公开时间线 {index:02d}"
            )
        provider = SpeechTrackingProvider(speech_outcomes=["看完结果了。"])
        for turn_index in range(2):
            await self.complete_quiet_turn(room_id, provider)
            self.advance_to_quiet_npc(room_id)

        plugin = GAMES[DummyNpcMultiplayer.game_type]

        def action_with_public_result(state, move, actor):
            state["actions"].append(actor["player_id"])
            return MoveResult(
                state=state,
                note="当前 NPC 的真实动作结果",
                public_event={"dummy_delta": {"landed": True}},
            )

        with patch.object(plugin, "apply_action", side_effect=action_with_public_result):
            result = await self.complete_quiet_turn(room_id, provider)
        self.assertIsNotNone(result.speech_task)
        await wait_for_npc_speech_tasks()
        self.assertEqual(len(provider.speech_requests), 1)
        request = provider.speech_requests[0]
        serialized = json.dumps(request.messages(), ensure_ascii=False)
        self.assertLessEqual(len(request.visible_timeline), 12)
        self.assertNotIn("完整公开时间线 00", serialized)
        self.assertIn("完整公开时间线 24", serialized)
        self.assertNotIn("较早但当前 NPC 可见的私聊", serialized)
        self.assertNotIn("绝不能泄漏给当前 NPC", serialized)
        self.assertIn("当前 NPC 的真实动作结果", serialized)
        self.assertEqual(
            request.visible_timeline[-1]["move"],
            {"dummy_delta": {"landed": True}},
        )
        self.assertEqual(request.private_state["hand"], ["private:npc:quiet"])
        for hidden in (
            "private:human-1", "private:ai-1", "private:npc:bright"
        ):
            self.assertNotIn(hidden, serialized)
        self.assertIn("权威合法行动", request.game_rules)
        self.assertEqual(request.public_state["actions"][-1], "npc:quiet")
        self.assertLessEqual(
            len(request.messages()[1]["content"]), NPC_CONTEXT_MESSAGE_LIMIT
        )

    async def test_long_production_multiplayer_contexts_are_bounded_and_private(self):
        def participants(count):
            values = [
                {
                    "player_id": "human-long", "role": "human",
                    "participant_kind": "human", "display_name": "人类",
                },
                {
                    "player_id": "ai-long", "role": "ai",
                    "participant_kind": "bound_machine", "display_name": "小机",
                },
            ]
            personas = (("quiet", "安静测试机"), ("bright", "明亮测试机"))
            values.extend({
                "player_id": f"npc:{persona_id}", "role": "ai",
                "participant_kind": "system_npc",
                "npc_persona_id": persona_id, "display_name": name,
            } for persona_id, name in personas[:count - 2])
            return values

        for game_type, player_count in (
            ("aeroplane_chess", 4), ("doudizhu", 3), ("mahjong", 4),
        ):
            with self.subTest(game_type=game_type):
                room = framework.create_room(
                    game_type, "ai_first", "human", "human-long",
                    opponent_id="ai-long",
                    ordered_participants=participants(player_count),
                    enforce_trusted_pair=True,
                    first_player_id="npc:quiet",
                )
                for index in range(30):
                    framework.post_message(
                        room["room_id"], "human", "human-long",
                        f"长局增量 {index:02d} " + "长" * 480,
                    )
                with database.write_transaction() as conn:
                    row = conn.execute(
                        "SELECT board_state FROM rooms WHERE room_id = ?",
                        (room["room_id"],),
                    ).fetchone()
                    state = json.loads(row["board_state"])
                    state["action_history"] = [
                        {"turn": index, "public_note": "历史" * 200}
                        for index in range(500)
                    ]
                    conn.execute(
                        "UPDATE rooms SET board_state = ? WHERE room_id = ?",
                        (json.dumps(state, ensure_ascii=False), room["room_id"]),
                    )
                loaded = framework.get_room(room["room_id"])
                speech = _speech_request(loaded, "npc:quiet")
                content = speech.messages()[1]["content"]
                self.assertLessEqual(len(content), NPC_CONTEXT_MESSAGE_LIMIT)
                self.assertLessEqual(len(speech.visible_timeline), 12)
                self.assertNotIn("action_history", speech.public_state)
                self.assertNotIn("长局增量 00", content)
                self.assertIn("长局增量 29", content)
                if game_type == "aeroplane_chess":
                    self.assertNotIn("path_mappings", speech.public_state)
                if game_type == "doudizhu":
                    actor = next(
                        item for item in loaded["participants"]
                        if item["player_id"] == "npc:quiet"
                    )
                    legal = _authoritative_legal_actions(loaded, actor)
                    decision, _mapping = _decision_request(
                        loaded, "npc:quiet", legal
                    )
                    self.assertGreater(len(decision.legal_actions), 1)
                    self.assertLessEqual(
                        len(decision.messages()[1]["content"]),
                        NPC_CONTEXT_MESSAGE_LIMIT,
                    )
                    raw_hands = loaded["board_state"]["cards"]["hands"]
                    opponent_card_id = raw_hands["human-long"][0]["id"]
                    own_card_id = raw_hands["npc:quiet"][0]["id"]
                    self.assertIn(own_card_id, decision.messages()[1]["content"])
                    self.assertNotIn(
                        opponent_card_id, decision.messages()[1]["content"]
                    )
                    provider = SpeechTrackingProvider()
                    result = await run_current_npc_turn(
                        loaded["room_id"], provider=provider
                    )
                    self.assertEqual(result.source, provider.name)
                    self.assertEqual(len(provider.decision_requests), 1)
                    self.assertLessEqual(
                        len(provider.decision_requests[0].messages()[1]["content"]),
                        NPC_CONTEXT_MESSAGE_LIMIT,
                    )

    async def test_speech_failure_does_not_block_and_retries_next_full_turn(self):
        room = self.room_at_quiet_npc()
        room_id = room["room_id"]
        provider = BlockingFailedSpeechProvider()
        for turn_index in range(2):
            await self.complete_quiet_turn(room_id, provider)
            self.advance_to_quiet_npc(room_id)
        result = await self.complete_quiet_turn(room_id, provider)
        self.assertIsNotNone(result.speech_task)
        self.assertEqual(
            framework.get_room(room_id)["revision"], result.room["revision"]
        )
        await asyncio.wait_for(provider.speech_started.wait(), timeout=1)
        self.assertFalse(result.speech_task.done())
        provider.release_speech.set()
        await wait_for_npc_speech_tasks()
        state = self.speech_state(room_id)
        self.assertEqual(state["speech_pending"], 1)
        self.assertEqual(state["last_attempt_status"], "failed")

        self.advance_to_quiet_npc(room_id)
        retry_provider = SpeechTrackingProvider(speech_outcomes=["补上这句。"])
        retry = await self.complete_quiet_turn(room_id, retry_provider)
        self.assertIsNotNone(retry.speech_task)
        await wait_for_npc_speech_tasks()
        self.assertEqual(len(retry_provider.speech_requests), 1)
        state = self.speech_state(room_id)
        self.assertEqual(state["speech_pending"], 0)
        self.assertEqual(state["last_attempt_status"], "sent")

    def test_production_local_strategy_game_set_is_unchanged(self):
        local_games = {
            game_type for game_type, plugin in GAMES.items()
            if plugin.supports_npcs and plugin.uses_local_npc_strategy
        }
        self.assertEqual(
            local_games,
            {"go", "junqi", "train_cards", "texas_holdem", "mahjong"},
        )
        provider_games = {
            game_type for game_type, plugin in GAMES.items()
            if plugin.supports_npcs and not plugin.uses_local_npc_strategy
            and game_type != DummyNpcMultiplayer.game_type
        }
        self.assertEqual(provider_games, {
            "aeroplane_chess", "banqi", "blackjack", "checkers", "chess",
            "chinese_checkers", "dots_boxes", "doudizhu", "gandengyan",
            "guandan", "liars_dice", "uno", "yahtzee", "zhajinhua",
        })


if __name__ == "__main__":
    unittest.main()
