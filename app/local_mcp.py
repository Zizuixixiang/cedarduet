"""Standard stdio MCP adapter for the standalone local AI identity."""

from __future__ import annotations

import json
import time
from typing import Any

import anyio
import httpx
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .local_config import LOCAL_AI_ID, LOCAL_HUMAN_ID, local_base_url
from .models import McpPlayBody


TOOL_NAME = "play"
MAX_CONTINUOUS_WAIT_SECONDS = 600.0
WAIT_SLOT_RETRY_SECONDS = 0.25
IDENTITY_FIELDS = frozenset({"player_id", "opponent_id", "participant_ids"})


def play_input_schema() -> dict[str, Any]:
    """Reuse the HTTP model schema while removing all caller-controlled identity."""
    schema = McpPlayBody.model_json_schema()
    properties = schema.get("properties", {})
    for field in IDENTITY_FIELDS:
        properties.pop(field, None)
    schema["required"] = [
        field for field in schema.get("required", []) if field not in IDENTITY_FIELDS
    ]
    return schema


async def forward_play(
    arguments: dict[str, Any],
    *,
    base_url: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    max_wait_seconds: float = MAX_CONTINUOUS_WAIT_SECONDS,
) -> tuple[int, dict[str, Any]]:
    payload = dict(arguments)
    for field in IDENTITY_FIELDS:
        payload.pop(field, None)
    payload["player_id"] = LOCAL_AI_ID
    payload["opponent_id"] = LOCAL_HUMAN_ID
    continue_waiting = payload.get("wait") is True
    deadline = time.monotonic() + max_wait_seconds
    timeout = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)

    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        while True:
            try:
                response = await client.post(
                    f"{base_url or local_base_url()}/mcp/play",
                    json=payload,
                    headers={"Accept": "application/json"},
                )
            except httpx.HTTPError as exc:
                return 503, {
                    "ok": False,
                    "status": "error",
                    "message": f"无法连接本地 CedarDuet gateway：{exc}",
                }
            try:
                result = response.json()
            except (UnicodeDecodeError, json.JSONDecodeError):
                result = {
                    "ok": False,
                    "status": "error",
                    "message": "本地 CedarDuet gateway 返回了非 JSON 响应",
                }
            if not isinstance(result, dict):
                result = {
                    "ok": False,
                    "status": "error",
                    "message": "本地 CedarDuet gateway 返回了非对象 JSON",
                }

            retryable_wait = (
                response.status_code < 400
                and continue_waiting
                and (
                    result.get("status") == "still_waiting"
                    or (
                        result.get("wait_downgraded") is True
                        and result.get("your_turn") is not True
                        and result.get("status")
                        not in {"finished", "archived", "cancelled", "left"}
                        and result.get("room_status")
                        not in {"finished", "archived", "cancelled"}
                    )
                )
            )
            if not retryable_wait or time.monotonic() >= deadline:
                return response.status_code, result

            room_id = result.get("room_id") or payload.get("room_id")
            if room_id in {None, ""}:
                return response.status_code, result

            # Never replay a move/message after an internal heartbeat.  From the
            # second request onward, only issue the canonical side-effect-free wait.
            payload = {
                "action": "state",
                "room_id": str(room_id),
                "wait": True,
                "player_id": LOCAL_AI_ID,
                "opponent_id": LOCAL_HUMAN_ID,
            }
            if result.get("wait_downgraded") is True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return response.status_code, result
                await anyio.sleep(min(WAIT_SLOT_RETRY_SECONDS, remaining))


async def _list_tools(_context, _params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=[types.Tool(
        name=TOOL_NAME,
        title="CedarDuet 本地游玩",
        description=(
            "以固定 local-ai 身份调用 CedarDuet 的 /mcp/play。"
            "身份字段由 adapter 强制注入；动作协议与生产 MCP 相同。"
        ),
        inputSchema=play_input_schema(),
        outputSchema={"type": "object", "additionalProperties": True},
    )])


async def _call_tool(_context, params: types.CallToolRequestParams) -> types.CallToolResult:
    if params.name != TOOL_NAME:
        payload = {"ok": False, "status": "error", "message": f"未知工具：{params.name}"}
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structuredContent=payload,
            isError=True,
        )
    arguments = params.arguments or {}
    if not isinstance(arguments, dict):
        arguments = {}
    status_code, payload = await forward_play(arguments)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structuredContent=payload,
        isError=not 200 <= status_code < 300,
    )


server = Server(
    "cedarduet-local",
    version="1.0.0",
    instructions="使用 play 工具控制固定的 local-ai；浏览器玩家固定为 local-human。",
    on_list_tools=_list_tools,
    on_call_tool=_call_tool,
)


async def _run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    anyio.run(_run)


if __name__ == "__main__":
    main()
