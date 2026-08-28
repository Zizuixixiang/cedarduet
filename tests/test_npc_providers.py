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
    NpcDecisionRequest,
    NpcProvider,
    OpenAICompatibleNpcProvider,
    ProviderDecision,
    get_npc_provider,
    reset_npc_provider_cache,
)
from app.npc_runtime import reserve_npc_decision
from tests.test_npc_framework import DummyNpcMultiplayer, write_persona


def provider_request() -> NpcDecisionRequest:
    return NpcDecisionRequest(
        persona={"id": "quiet", "display_name": "测试", "persona": "简短人设"},
        game_rules="精简规则",
        public_state={"round": 2},
        private_state={"hand": ["mine"]},
        public_actions=[{"actor": "human-1", "action": "pass"}],
        legal_actions=[{"action_id": "a_step", "action": {"action": "step"}}],
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
            self.assertIn("不得为了维持性格故意走明显坏棋", compact)

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
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{
                        "message": {
                            "content": '{"action_id":"a_step","message":"走"}'
                        }
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
            "persona", "game_rules", "public_state", "private_state",
            "public_actions", "legal_actions",
        })
        serialized = json.dumps(captured, ensure_ascii=False)
        self.assertNotIn("never-log-this-key", messages[1]["content"])
        self.assertNotIn("思维链", messages[1]["content"])
        self.assertNotIn("never-log-this-key", json.dumps(provider.capabilities()))
        self.assertIn("不要返回分析", messages[0]["content"])
        self.assertIn("never-log-this-key", serialized)  # transport header only

    async def test_bridge_and_openai_share_one_decision_shape(self):
        captured = {}

        async def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            captured["authorization"] = request.headers.get("authorization")
            return httpx.Response(
                200,
                request=request,
                json={"content": '{"action_id":"a_step"}'},
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
            room["room_id"], "human", "human-1", {"action": "step"}
        )
        return framework.play_move(
            room["room_id"], "ai", "ai-1", {"action": "step"}
        )

    async def test_context_is_viewer_safe_and_invalid_output_falls_back_once(self):
        room = self.room_at_first_npc()
        provider = ScriptedProvider(["invalid", "invalid"])
        result = await run_current_npc_turn(room["room_id"], provider=provider)
        self.assertEqual(result.status, "applied")
        self.assertEqual(result.source, "fallback")
        self.assertEqual(len(provider.requests), 2)
        request = provider.requests[0]
        sent = json.dumps(request.messages(), ensure_ascii=False)
        self.assertIn("private:npc:quiet", sent)
        for hidden in (
            "private:human-1", "private:ai-1", "private:npc:bright"
        ):
            self.assertNotIn(hidden, sent)
        self.assertEqual(
            set(json.loads(request.messages()[1]["content"])),
            {
                "persona", "game_rules", "public_state", "private_state",
                "public_actions", "legal_actions",
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
