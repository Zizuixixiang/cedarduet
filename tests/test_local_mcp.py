import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import mcp.types as types

from app.local_mcp import TOOL_NAME, _call_tool, _list_tools, forward_play, play_input_schema


PROJECT_ROOT = Path(__file__).resolve().parents[1]


STDIO_CLIENT_HARNESS = r"""
const {spawn} = require("node:child_process");

const child = spawn(process.argv[1], ["-m", "app.local_mcp"], {
  cwd: process.argv[2],
  stdio: ["pipe", "pipe", "pipe"],
  windowsHide: true,
});
let pending = "";
let stderr = "";
let result = null;
let failure = "";
let initialized = false;
let shutdownTimer = null;
let forcedShutdown = false;

function send(message) {
  if (!child.stdin.destroyed) {
    child.stdin.write(`${JSON.stringify(message)}\n`);
  }
}

function fail(message) {
  if (!failure) failure = message;
  child.kill();
}

child.stderr.setEncoding("utf8");
child.stderr.on("data", (chunk) => { stderr += chunk; });
child.stdin.on("error", (error) => {
  if (!result) fail(`stdin write failed: ${error.message}`);
});
child.stdout.setEncoding("utf8");
child.stdout.on("data", (chunk) => {
  pending += chunk;
  const lines = pending.split(/\r?\n/);
  pending = lines.pop();
  for (const line of lines) {
    if (!line.trim()) continue;
    let message;
    try {
      message = JSON.parse(line);
    } catch (error) {
      fail(`invalid JSON-RPC output: ${error.message}: ${line}`);
      return;
    }
    if (message.id === 1 && !initialized) {
      if (message.error) {
        fail(`initialize failed: ${JSON.stringify(message.error)}`);
        return;
      }
      initialized = true;
      send({jsonrpc: "2.0", method: "notifications/initialized", params: {}});
      send({jsonrpc: "2.0", id: 2, method: "tools/list", params: {}});
    } else if (message.id === 2) {
      if (message.error) {
        fail(`tools/list failed: ${JSON.stringify(message.error)}`);
        return;
      }
      if (!message.result || !Array.isArray(message.result.tools)) {
        fail(`tools/list returned an invalid result: ${JSON.stringify(message.result)}`);
        return;
      }
      result = message.result;
      child.stdin.end();
      shutdownTimer = setTimeout(() => {
        forcedShutdown = true;
        child.kill();
      }, 1000);
    }
  }
});
child.on("error", (error) => fail(`spawn failed: ${error.message}`));

const timer = setTimeout(() => fail("stdio MCP probe timed out"), 12000);
child.on("close", (code, signal) => {
  clearTimeout(timer);
  if (shutdownTimer) clearTimeout(shutdownTimer);
  if (!failure && (!result || (code !== 0 && !forcedShutdown))) {
    failure = `server exited before a complete tools/list response (code=${code}, signal=${signal})`;
  }
  if (failure) {
    process.stderr.write(`${failure}${stderr ? `\n${stderr}` : ""}\n`);
    process.exitCode = 1;
    return;
  }
  process.stdout.write(JSON.stringify(result));
});

send({
  jsonrpc: "2.0",
  id: 1,
  method: "initialize",
  params: {
    protocolVersion: "2025-11-25",
    capabilities: {},
    clientInfo: {name: "cedarduet-stdio-test", version: "1.0.0"},
  },
});
"""


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


class LocalMcpStdioTests(unittest.TestCase):
    def test_adapter_is_a_real_stdio_mcp_server(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required by the standalone runtime")
        completed = subprocess.run(
            [node, "-e", STDIO_CLIENT_HARNESS, sys.executable, str(PROJECT_ROOT)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        listed = json.loads(completed.stdout)
        self.assertEqual([tool["name"] for tool in listed["tools"]], [TOOL_NAME])
        self.assertFalse(
            {"player_id", "opponent_id", "participant_ids"}
            & set(listed["tools"][0]["inputSchema"]["properties"])
        )


if __name__ == "__main__":
    unittest.main()
