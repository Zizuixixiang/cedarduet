import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import mcp.types as types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.local_mcp import TOOL_NAME, _call_tool, _list_tools, forward_play, play_input_schema


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LocalMcpAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_exposes_http_contract_without_identity_fields(self):
        schema = play_input_schema()
        self.assertEqual(schema["required"], ["action"])
        self.assertFalse(
            {"player_id", "opponent_id", "participant_ids"}
            & set(schema["properties"])
        )
        self.assertIn("revision", schema["properties"])
        self.assertIn("full_state", schema["properties"])
        self.assertIn("wait", schema["properties"])

        listed = await _list_tools(None, None)
        self.assertEqual([tool.name for tool in listed.tools], [TOOL_NAME])
        self.assertEqual(listed.tools[0].input_schema, schema)

    async def test_forwarder_overwrites_all_caller_controlled_identity(self):
        seen = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "status": "ok"})

        status, payload = await forward_play(
            {
                "action": "new",
                "player_id": "npc:evil",
                "opponent_id": "evil-human",
                "participant_ids": ["evil-human", "evil-ai"],
                "game_type": "tictactoe",
            },
            base_url="http://127.0.0.1:9999",
            transport=httpx.MockTransport(handler),
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True, "status": "ok"})
        self.assertEqual(seen[0]["player_id"], "local-ai")
        self.assertEqual(seen[0]["opponent_id"], "local-human")
        self.assertNotIn("participant_ids", seen[0])


    async def test_wait_heartbeats_are_hidden_and_retried_as_state(self):
        seen = []
        replies = [
            {"ok": True, "status": "still_waiting", "room_id": "ROOM1", "revision": 4},
            {"ok": True, "status": "still_waiting", "room_id": "ROOM1", "revision": 4},
            {
                "ok": True, "status": "playing", "room_id": "ROOM1",
                "revision": 5, "your_turn": True, "events": [{"move": {"row": 1, "col": 1}}],
            },
        ]

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(200, json=replies.pop(0))

        status, payload = await forward_play(
            {
                "action": "move", "room_id": "ROOM1", "wait": True,
                "move": {"row": 0, "col": 0}, "message": "下这里。",
            },
            base_url="http://127.0.0.1:9999",
            transport=httpx.MockTransport(handler),
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["your_turn"])
        self.assertEqual(len(seen), 3)
        self.assertEqual(seen[0]["action"], "move")
        self.assertEqual(seen[0]["move"], {"row": 0, "col": 0})
        self.assertEqual(seen[0]["message"], "下这里。")
        for followup in seen[1:]:
            self.assertEqual(followup, {
                "action": "state",
                "room_id": "ROOM1",
                "wait": True,
                "player_id": "local-ai",
                "opponent_id": "local-human",
            })

    async def test_wait_false_does_not_hide_still_waiting(self):
        calls = 0

        async def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={
                "ok": True, "status": "still_waiting", "room_id": "ROOM1", "revision": 4
            })

        status, payload = await forward_play(
            {"action": "state", "room_id": "ROOM1", "wait": False},
            base_url="http://127.0.0.1:9999",
            transport=httpx.MockTransport(handler),
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "still_waiting")
        self.assertEqual(calls, 1)

    async def test_wait_slot_downgrade_is_retried_without_hot_looping_action(self):
        seen = []
        replies = [
            {
                "ok": True, "status": "playing", "room_id": "ROOM1",
                "revision": 7, "your_turn": False, "wait_downgraded": True,
            },
            {
                "ok": True, "status": "playing", "room_id": "ROOM1",
                "revision": 8, "your_turn": True,
            },
        ]

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(200, json=replies.pop(0))

        with patch("app.local_mcp.anyio.sleep", return_value=None) as sleeper:
            status, payload = await forward_play(
                {"action": "state", "room_id": "ROOM1", "wait": True},
                base_url="http://127.0.0.1:9999",
                transport=httpx.MockTransport(handler),
            )

        self.assertEqual(status, 200)
        self.assertTrue(payload["your_turn"])
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1]["action"], "state")
        sleeper.assert_awaited_once()

    async def test_backend_error_is_returned_as_mcp_tool_error(self):
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={
                "ok": False, "status": "error", "message": "provider missing"
            })

        with patch(
            "app.local_mcp.forward_play",
            return_value=(503, {
                "ok": False, "status": "error", "message": "provider missing"
            }),
        ):
            result = await _call_tool(
                None,
                types.CallToolRequestParams(name=TOOL_NAME, arguments={"action": "catalog"}),
            )
        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content["message"], "provider missing")

        status, payload = await forward_play(
            {"action": "catalog"},
            base_url="http://127.0.0.1:9999",
            transport=httpx.MockTransport(handler),
        )
        self.assertEqual(status, 503)
        self.assertEqual(payload["message"], "provider missing")

    async def test_adapter_is_a_real_stdio_mcp_server(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.local_mcp"],
            env=env,
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                listed = await session.list_tools()
        self.assertEqual([tool.name for tool in listed.tools], [TOOL_NAME])
        self.assertFalse(
            {"player_id", "opponent_id", "participant_ids"}
            & set(listed.tools[0].input_schema["properties"])
        )


if __name__ == "__main__":
    unittest.main()
