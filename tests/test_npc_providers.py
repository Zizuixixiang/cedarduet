import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import database, framework
from app.games import GAMES
from app.npc_controller import run_current_npc_turn
from app.npc_providers import (
    CedarToyBridgeNpcProvider,
    DisabledNpcProvider,
    GLOBAL_PLAYER_RULES,
    GLOBAL_SPEECH_RULES,
    NpcDecisionRequest,
    NpcProvider,
    NpcSpeechRequest,
    OpenAICompatibleNpcProvider,
    ProviderDecision,
    get_npc_provider,
    parse_provider_decision,
    parse_provider_speech,
    reset_npc_provider_cache,
)
from app.npc_runtime import reserve_npc_decision
from tests.test_npc_framework import DummyNpcMultiplayer, write_persona


def provider_request() -> NpcDecisionRequest:
    return NpcDecisionRequest(
        persona={"id": "quiet", "display_name": "测试", "persona": "简短人设"},
        game_rules="精简规则",
        participants=[{
            "player_id": "npc:quiet", "display_name": "测试", "seat_index": 0,
            "participant_kind": "system_npc", "activity_state": "active",
            "participant_summary": {},
        }],
        public_state={"round": 2},
        private_state={"hand": ["mine"]},
        recent_public_events=[],
        public_actions=[{"actor": "human-1", "action": "pass"}],
        legal_actions=[{"action_id": "a_step", "action": {"action": "step"}}],
    )


def speech_request() -> NpcSpeechRequest:
    return NpcSpeechRequest(
        persona={"id": "quiet", "display_name": "测试", "persona": "人设"},
        game_rules="完整规则",
        participants=[{
            "player_id": "npc:quiet", "display_name": "测试", "seat_index": 0,
            "participant_kind": "system_npc", "activity_state": "active",
            "participant_summary": {},
        }],
        public_state={"round": 3},
        private_state={"hand": ["mine"]},
        visible_timeline=[{
            "sequence": 1, "event_type": "result",
            "actor": {"player_id": "system"}, "text": "真实结果",
        }],
    )


class ProviderBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        reset_npc_provider_cache()

    async def test_global_rules_prioritize_winning_and_allow_public_inference(self):
        system_message = provider_request().messages()[0]
        self.assertEqual(system_message, {
            "role": "system", "content": GLOBAL_PLAYER_RULES,
        })
        for required in (
            "首要目标是理解规则并争取获胜",
            "己方私有信息、公共局面和其他玩家已公开行动",
            "允许依据公开信息正常推理和估计",
            "不得把对手隐藏状态当作已知事实",
            "不得以真实披露为目的直接报出自己的完整或具体隐藏牌、骰子及其他私有状态",
            "允许为了策略进行虚张声势、试探、模糊表达或真假难辨的误导",
            "不禁止吹牛骰子等玩法中的正常诈唬",
            "只能从权威合法行动列表选择",
            "人设只影响合理行动之间的选择、风险偏好和交流方式",
            "不得为了维持性格故意走明显坏棋",
            "只返回 JSON 对象",
            "不要返回分析、解释或思维过程",
        ):
            self.assertIn(required, GLOBAL_PLAYER_RULES)
        self.assertNotIn("不得推测其他玩家隐藏信息", GLOBAL_PLAYER_RULES)

        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        persona_guide = (
            root / "app" / "config" / "npc_personas" / "README.md"
        ).read_text(encoding="utf-8")
        for document in (readme, persona_guide):
            compact = "".join(document.split())
            self.assertIn("公开信息正常推理", compact)
            self.assertIn("不得把对手隐藏状态当作已知事实", compact)
            self.assertIn("不得以真实披露为目的直接报出自己的", compact)
            self.assertIn("虚张声势", compact)
            self.assertIn("正常诈唬不受禁止", compact)
            self.assertIn("不得为了维持性格故意走明显坏棋", compact)

    async def test_global_rules_encourage_natural_messages_without_leaks(self):
        for required in (
            "正常 NPC 行动时，多数回合应在 message 中附带一句简短、自然、"
            "符合 persona 的中文桌边话",
            "可以参考 recent_public_events，仅对其中的公开发言或公开局势作"
            "简短回应",
            "不要每回合机械重复",
            "无话可说时可以偶尔静默",
            "fallback 或异常恢复时也允许静默",
            "message 使用 null",
            '{"action_id":"...","message":null}',
            "不得通过 message 泄露任何未公开隐藏信息",
            "不要为了说话牺牲合法行动选择",
            "不要返回分析、解释或思维过程",
        ):
            self.assertIn(required, GLOBAL_PLAYER_RULES)

    async def test_provider_decision_keeps_null_message_compatibility(self):
        self.assertEqual(
            parse_provider_decision('{"action_id":"a_step","message":null}'),
            ProviderDecision("a_step", None),
        )

    async def test_speech_only_contract_is_private_and_requires_one_message(self):
        messages = speech_request().messages()
        self.assertEqual(
            messages[0], {"role": "system", "content": GLOBAL_SPEECH_RULES}
        )
        payload = json.loads(messages[1]["content"])
        self.assertEqual(set(payload), {
            "persona", "game_rules", "participants", "public_state",
            "private_state", "visible_timeline",
        })
        self.assertEqual(payload["visible_timeline"][-1]["text"], "真实结果")
        self.assertEqual(
            parse_provider_speech('{"message":"  看到了。  "}'), "看到了。"
        )
        self.assertIsNone(parse_provider_speech('{"message":null}'))
        for required in (
            "不得把对手隐藏状态当作已知事实",
            "不得以真实披露为目的",
            "正常诈唬",
            "不要返回分析、解释或思维过程",
        ):
            self.assertIn(required, GLOBAL_SPEECH_RULES)
        for prohibited in (
            "Yellow rolls", "I roll", "I move", "禁止复述动作", "尽量短"
        ):
            self.assertNotIn(prohibited, GLOBAL_SPEECH_RULES)

    async def test_disabled_is_default_and_exposes_no_configuration_secret(self):
        with patch.dict("os.environ", {"DUEL_NPC_PROVIDER": "disabled"}, clear=False):
            reset_npc_provider_cache()
            provider = get_npc_provider()
            self.assertIsInstance(provider, DisabledNpcProvider)
            self.assertFalse(provider.capabilities()["available"])
            self.assertNotIn("api_key", json.dumps(provider.capabilities()))
            with self.assertRaisesRegex(Exception, "未配置"):
                await provider.decide(provider_request())

    async def test_openai_compatible_uses_compact_json_and_never_exposes_key(self):
        captured = {}

        async def handler(request: httpx.Request):
            captured["url"] = str(request.url)
            captured["authorization"] = request.headers.get("authorization")
            captured["body"] = json.loads(request.content)
            user_payload = json.loads(
                captured["body"]["messages"][1]["content"]
            )
            content = (
                '{"message":"OpenAI 发言"}'
                if "visible_timeline" in user_payload
                else '{"action_id":"a_step","message":"走"}'
            )
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{
                        "message": {"content": content}
                    }]
                },
            )

        provider = OpenAICompatibleNpcProvider(
            api_base="https://provider.example/v1",
            api_key="never-log-this-key",
            model="test-model",
            transport=httpx.MockTransport(handler),
        )
        decision = await provider.decide(provider_request())
        self.assertEqual(decision, ProviderDecision("a_step", "走"))
        self.assertEqual(
            captured["url"], "https://provider.example/v1/chat/completions"
        )
        self.assertEqual(captured["authorization"], "Bearer never-log-this-key")
        self.assertEqual(captured["body"]["model"], "test-model")
        messages = captured["body"]["messages"]
        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        sent = json.loads(messages[1]["content"])
        self.assertEqual(set(sent), {
            "persona", "game_rules", "participants", "public_state",
            "private_state", "recent_public_events", "public_actions",
            "legal_actions",
        })
        serialized = json.dumps(captured, ensure_ascii=False)
        self.assertNotIn("never-log-this-key", messages[1]["content"])
        self.assertNotIn("思维链", messages[1]["content"])
        self.assertNotIn("never-log-this-key", json.dumps(provider.capabilities()))
        self.assertIn("不要返回分析", messages[0]["content"])
        self.assertIn("never-log-this-key", serialized)  # transport header only
        self.assertEqual(await provider.speak(speech_request()), "OpenAI 发言")

    async def test_bridge_and_openai_share_one_decision_shape(self):
        captured = {"bodies": []}

        async def handler(request: httpx.Request):
            body = json.loads(request.content)
            captured["body"] = body
            captured["bodies"].append(body)
            captured["authorization"] = request.headers.get("authorization")
            user_payload = json.loads(body["messages"][1]["content"])
            content = (
                '{"message":"落地后的发言"}'
                if "visible_timeline" in user_payload
                else '{"action_id":"a_step"}'
            )
            return httpx.Response(
                200,
                request=request,
                json={"content": content},
            )

        provider = CedarToyBridgeNpcProvider(
            bridge_url="http://127.0.0.1/internal/duel/npc-decision",
            bridge_token="internal-test-token",
            timeout=7,
            max_tokens=128,
            transport=httpx.MockTransport(handler),
        )
        self.assertEqual(
            await provider.decide(provider_request()),
            ProviderDecision("a_step", None),
        )
        self.assertEqual(captured["authorization"], "Bearer internal-test-token")
        self.assertEqual(captured["body"]["max_tokens"], 128)
        self.assertEqual(captured["body"]["timeout"], 7)
        self.assertEqual(await provider.speak(speech_request()), "落地后的发言")
        speech_payload = json.loads(
            captured["bodies"][1]["messages"][1]["content"]
        )
        self.assertEqual(speech_payload["visible_timeline"][-1]["text"], "真实结果")

    async def test_http_provider_global_concurrency_limit_allows_parallel_rooms(self):
        active = 0
        maximum = 0

        async def handler(request: httpx.Request):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.03)
            active -= 1
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{
                        "message": {"content": '{"action_id":"a_step"}'}
                    }]
                },
            )

        provider = OpenAICompatibleNpcProvider(
            api_base="https://provider.example/v1",
            api_key="test-key",
            model="test-model",
            max_concurrency=2,
            transport=httpx.MockTransport(handler),
        )
        await asyncio.gather(*(provider.decide(provider_request()) for _ in range(5)))
        self.assertEqual(maximum, 2)


class ScriptedProvider(NpcProvider):
    name = "scripted"
    available = True
    max_concurrency = 4

    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.requests = []

    async def decide(self, request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0) if self.outcomes else "first"
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome == "invalid":
            return ProviderDecision("not-authoritative", "bad")
        action_id = request.legal_actions[0]["action_id"]
        return ProviderDecision(action_id, "短消息")


class BlockingProvider(ScriptedProvider):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def decide(self, request):
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        step = next(
            item for item in request.legal_actions
            if item["action"] == {"action": "step"}
        )
        return ProviderDecision(step["action_id"], None)


class NeverCalledProvider(ScriptedProvider):
    async def decide(self, request):
        self.requests.append(request)
        raise AssertionError("stale recovery must not call a provider")


class NpcControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="duel-provider-")
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
        self.env_patch.stop()
        self.game_patch.stop()
        self.db_patch.stop()
        self.temporary.cleanup()

    def room_at_first_npc(self):
        participants = [
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
        room = framework.create_room(
            DummyNpcMultiplayer.game_type,
            "human_first", "human", "human-1",
            opponent_id="ai-1",
            ordered_participants=participants,
            enforce_trusted_pair=True,
        )
        room = framework.play_move(
            room["room_id"], "human", "human-1",
            {"action": "step", "secret": "human-move-secret"},
            message="人类公开发言",
        )
        return framework.play_move(
            room["room_id"], "ai", "ai-1", {"action": "step"},
            message="小机公开发言",
        )

    async def test_context_is_viewer_safe_and_invalid_output_falls_back_once(self):
        room = self.room_at_first_npc()
        framework.post_message(
            room["room_id"], "human", "human-1", "房间公开聊天"
        )
        framework.post_message(
            room["room_id"], "human", "human-1", "给当前 NPC 的私聊",
            visible_to_player_ids={"npc:quiet"},
        )
        framework.post_message(
            room["room_id"], "human", "human-1", "当前 NPC 不可见的事件",
            visible_to_player_ids={"npc:bright"},
        )
        with database.write_transaction() as conn:
            conn.execute(
                """
                UPDATE room_participants
                SET active = 0, activity_state = 'eliminated'
                WHERE room_id = ? AND player_id = ?
                """,
                (room["room_id"], "npc:bright"),
            )
        conn = database.connect()
        try:
            cursor_before = conn.execute(
                """
                SELECT last_event_id FROM room_event_cursors
                WHERE room_id = ? AND player_id = ?
                """,
                (room["room_id"], "npc:quiet"),
            ).fetchone()[0]
        finally:
            conn.close()
        provider = ScriptedProvider(["invalid", "invalid"])
        result = await run_current_npc_turn(room["room_id"], provider=provider)
        self.assertEqual(result.status, "applied")
        self.assertEqual(result.source, "fallback")
        self.assertEqual(len(provider.requests), 2)
        request = provider.requests[0]
        sent = json.dumps(request.messages(), ensure_ascii=False)
        self.assertIn("private:npc:quiet", sent)
        for hidden in (
            "private:human-1", "private:ai-1", "private:npc:bright",
            "human-move-secret", "给当前 NPC 的私聊", "当前 NPC 不可见的事件",
        ):
            self.assertNotIn(hidden, sent)
        self.assertEqual(request.private_state["hand"], ["private:npc:quiet"])
        directory = {item["player_id"]: item for item in request.participants}
        self.assertEqual(
            [item["player_id"] for item in request.participants],
            ["human-1", "ai-1", "npc:quiet", "npc:bright"],
        )
        self.assertEqual(
            (directory["human-1"]["display_name"],
             directory["human-1"]["seat_index"],
             directory["human-1"]["participant_kind"]),
            ("人类", 0, "human"),
        )
        self.assertEqual(
            (directory["ai-1"]["display_name"],
             directory["ai-1"]["seat_index"],
             directory["ai-1"]["participant_kind"]),
            ("小机", 1, "bound_machine"),
        )
        self.assertEqual(directory["npc:bright"]["activity_state"], "eliminated")
        self.assertTrue(all(
            item["participant_summary"] == {"action_count": 2}
            for item in request.participants
        ))
        events = request.recent_public_events
        self.assertEqual(
            [item["event_type"] for item in events],
            ["move", "move", "message"],
        )
        self.assertEqual(
            [item["actor"]["player_id"] for item in events],
            ["human-1", "ai-1", "human-1"],
        )
        self.assertEqual(
            [item["actor"]["display_name"] for item in events],
            ["人类", "小机", "人类"],
        )
        self.assertEqual(
            [item["actor"]["seat_index"] for item in events], [0, 1, 0]
        )
        self.assertEqual(events[0]["text"], "人类公开发言")
        self.assertEqual(events[0]["move"], {"action": "step"})
        self.assertEqual(events[1]["text"], "小机公开发言")
        self.assertEqual(events[2]["text"], "房间公开聊天")
        self.assertEqual(
            set(json.loads(request.messages()[1]["content"])),
            {
                "persona", "game_rules", "participants", "public_state",
                "private_state", "recent_public_events", "public_actions",
                "legal_actions",
            },
        )
        restored = framework.get_room(room["room_id"])
        self.assertEqual(restored["revision"], room["revision"] + 1)
        conn = database.connect()
        try:
            stored = conn.execute(
                "SELECT status, decision_json FROM npc_decisions"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(stored["status"], "completed")
        self.assertNotIn("private:", stored["decision_json"])
        cursor_after = database.connect()
        try:
            self.assertEqual(
                cursor_after.execute(
                    """
                    SELECT last_event_id FROM room_event_cursors
                    WHERE room_id = ? AND player_id = ?
                    """,
                    (room["room_id"], "npc:quiet"),
                ).fetchone()[0],
                cursor_before,
            )
        finally:
            cursor_after.close()

    async def test_context_keeps_only_latest_twenty_public_events_in_order(self):
        room = self.room_at_first_npc()
        for index in range(1, 23):
            framework.post_message(
                room["room_id"], "human", "human-1", f"公开消息 {index:02d}"
            )
        provider = ScriptedProvider()
        await run_current_npc_turn(room["room_id"], provider=provider)
        events = provider.requests[0].recent_public_events
        self.assertEqual(len(events), 20)
        self.assertEqual(
            [item["text"] for item in events],
            [f"公开消息 {index:02d}" for index in range(3, 23)],
        )
        self.assertEqual(
            [item["sequence"] for item in events],
            sorted(item["sequence"] for item in events),
        )

    async def test_retry_once_can_recover_with_a_valid_action(self):
        room = self.room_at_first_npc()
        provider = ScriptedProvider([ValueError("bad json"), "first"])
        result = await run_current_npc_turn(room["room_id"], provider=provider)
        self.assertEqual(result.source, "scripted")
        self.assertEqual(len(provider.requests), 2)

    async def test_same_revision_has_one_provider_call_and_one_landed_move(self):
        room = self.room_at_first_npc()
        provider = BlockingProvider()
        first = asyncio.create_task(
            run_current_npc_turn(room["room_id"], provider=provider)
        )
        await provider.started.wait()
        duplicate = await run_current_npc_turn(room["room_id"], provider=provider)
        self.assertEqual(duplicate.status, "in_progress")
        provider.release.set()
        applied = await first
        self.assertEqual(applied.status, "applied")
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(
            framework.get_room(room["room_id"])["revision"],
            room["revision"] + 1,
        )

    async def test_stale_reservation_recovers_with_no_second_provider_charge(self):
        room = self.room_at_first_npc()
        reserve_npc_decision(
            room["room_id"], room["revision"], "npc:quiet"
        )
        with database.write_transaction() as conn:
            conn.execute(
                "UPDATE npc_decisions SET updated_at = '2000-01-01T00:00:00+00:00'"
            )
        provider = NeverCalledProvider()
        result = await run_current_npc_turn(room["room_id"], provider=provider)
        self.assertEqual(result.source, "fallback")
        self.assertEqual(provider.requests, [])
        self.assertEqual(
            framework.get_room(room["room_id"])["revision"],
            room["revision"] + 1,
        )


if __name__ == "__main__":
    unittest.main()
