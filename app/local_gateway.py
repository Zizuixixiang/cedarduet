"""Loopback-only trusted identity gateway for a standalone CedarDuet clone.

Production continues to run ``app.main:app`` directly.  This module wraps that
unchanged ASGI application and emulates only the identity injection performed
by CedarToy's trusted proxy.
"""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from .local_config import (
    LOCAL_AI_ID,
    LOCAL_HUMAN_ID,
    LOOPBACK_HOSTS,
    configure_local_environment,
    local_identity_headers,
)


configure_local_environment()

from .games import get_game  # noqa: E402  (local env must precede app imports)
from .main import app as production_app  # noqa: E402
from .npc_providers import npc_provider_capabilities  # noqa: E402


ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]], Awaitable[None]]
NPC_API_REQUIRED_MESSAGE = (
    "该游戏 NPC 需要配置 API/模型通道，或加入更多真实小机/减少 NPC"
)

_ROOM_IDENTITY_SUFFIXES = frozenset(
    {"join", "move", "messages", "resign", "leave", "invitation", "retention", "delete"}
)
_ROOM_SUFFIXES_WITH_OPPONENT = frozenset({"join", "move", "messages", "resign"})


def _room_suffix(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) == 4 and parts[:2] == ["api", "rooms"]:
        return parts[3] if parts[3] in _ROOM_IDENTITY_SUFFIXES else None
    return None


def _is_room_state_path(path: str) -> bool:
    parts = [part for part in path.split("/") if part]
    return len(parts) == 3 and parts[:2] == ["api", "rooms"]


def _canonical_body(path: str, method: str, raw: bytes) -> tuple[bytes, dict[str, Any] | None]:
    if method != "POST":
        return raw, None
    is_create = path.rstrip("/") == "/api/rooms"
    room_suffix = _room_suffix(path)
    is_mcp = path.rstrip("/") == "/mcp/play"
    if not is_create and room_suffix is None and not is_mcp:
        return raw, None
    try:
        value = json.loads(raw or b"{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw, None
    if not isinstance(value, dict):
        return raw, None
    if is_mcp:
        value.pop("participant_ids", None)
        value["player_id"] = LOCAL_AI_ID
        value["opponent_id"] = LOCAL_HUMAN_ID
    else:
        value["player_id"] = LOCAL_HUMAN_ID
        if is_create:
            value["opponent_id"] = LOCAL_AI_ID
            value["ai_player"] = LOCAL_AI_ID
            value["ai_players"] = [LOCAL_AI_ID]
        elif room_suffix in _ROOM_SUFFIXES_WITH_OPPONENT:
            value["opponent_id"] = LOCAL_AI_ID
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return encoded, value


def _needs_external_npc_provider(payload: dict[str, Any] | None, *, mcp: bool) -> bool:
    if not payload or (mcp and payload.get("action") != "new"):
        return False
    if payload.get("fill_with_npcs") is not True:
        return False
    game_type = payload.get("game_type")
    target = payload.get("target_player_count")
    if not isinstance(game_type, str) or isinstance(target, bool) or not isinstance(target, int):
        return False
    try:
        game = get_game(game_type)
        allowed = game.resolved_allowed_player_counts()
    except (TypeError, ValueError):
        return False
    npc_count = target - 2  # fixed local-human + local-ai real participants
    return bool(
        npc_count > 0
        and target in allowed
        and game.supports_npcs
        and not game.uses_local_npc_strategy
        and not npc_provider_capabilities()["available"]
    )


def _canonical_query(path: str, method: str, raw: bytes) -> bytes:
    if method != "GET" or not _is_room_state_path(path):
        return raw
    pairs = [
        (key, value)
        for key, value in parse_qsl(raw.decode("ascii"), keep_blank_values=True)
        if key not in {"player_id", "opponent_id"}
    ]
    pairs.extend((("player_id", LOCAL_HUMAN_ID), ("opponent_id", LOCAL_AI_ID)))
    return urlencode(pairs).encode("ascii")


def _host_is_loopback(scope: dict[str, Any]) -> bool:
    client = scope.get("client")
    if client:
        try:
            if not ipaddress.ip_address(client[0]).is_loopback:
                return False
        except ValueError:
            return False
    headers = dict(scope.get("headers", []))
    host_value = headers.get(b"host", b"").decode("latin-1").strip()
    if not host_value:
        return False
    try:
        hostname = urlsplit(f"//{host_value}").hostname
    except ValueError:
        return False
    return hostname in LOOPBACK_HOSTS


async def _json_error(send: Callable[..., Awaitable[Any]], message: str, status: int) -> None:
    body = json.dumps(
        {"ok": False, "status": "error", "message": message},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode("ascii"))],
    })
    await send({"type": "http.response.body", "body": body})


async def _project_local_web_capability(
    wrapped: ASGIApp,
    scope: dict[str, Any],
    receive: Callable[..., Awaitable[Any]],
    send: Callable[..., Awaitable[Any]],
) -> None:
    """Let the unchanged Web UI attempt NPC fill; admission stays at create."""
    messages: list[dict[str, Any]] = []

    async def capture(message: dict[str, Any]) -> None:
        messages.append(message)

    await wrapped(scope, receive, capture)
    start = next(
        (message for message in messages if message["type"] == "http.response.start"),
        None,
    )
    bodies = [message for message in messages if message["type"] == "http.response.body"]
    if start is None or start.get("status") != 200 or not bodies:
        for message in messages:
            await send(message)
        return
    raw = b"".join(message.get("body", b"") for message in bodies)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        for message in messages:
            await send(message)
        return
    capability = value.get("npc_provider") if isinstance(value, dict) else None
    if not isinstance(capability, dict) or capability.get("available") is not False:
        for message in messages:
            await send(message)
        return
    capability["available"] = True
    capability["local_admission_deferred"] = True
    capability["reason"] = NPC_API_REQUIRED_MESSAGE
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = [
        (name, header_value)
        for name, header_value in start.get("headers", [])
        if name.lower() != b"content-length"
    ]
    headers.append((b"content-length", str(len(body)).encode("ascii")))
    local_start = dict(start)
    local_start["headers"] = headers
    await send(local_start)
    await send({"type": "http.response.body", "body": body})


class LocalIdentityGateway:
    def __init__(self, wrapped: ASGIApp) -> None:
        self.wrapped = wrapped

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.wrapped(scope, receive, send)
            return
        if not _host_is_loopback(scope):
            await _json_error(send, "本地 gateway 只接受 loopback 请求", 403)
            return

        local_scope = dict(scope)
        headers = [
            (name, value)
            for name, value in scope.get("headers", [])
            if not name.lower().startswith(b"x-duel-")
        ]
        headers.extend(local_identity_headers())
        local_scope["headers"] = headers
        local_scope["query_string"] = _canonical_query(
            scope.get("path", ""), scope.get("method", ""), scope.get("query_string", b"")
        )

        path = scope.get("path", "")
        method = scope.get("method", "")
        if method == "GET" and path.rstrip("/") == "/api/whoami":
            await _project_local_web_capability(
                self.wrapped, local_scope, receive, send
            )
            return
        should_read_body = bool(
            method == "POST"
            and (
                path.rstrip("/") in {"/api/rooms", "/mcp/play"}
                or _room_suffix(path) is not None
            )
        )
        if not should_read_body:
            await self.wrapped(local_scope, receive, send)
            return

        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                await self.wrapped(local_scope, receive, send)
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body, payload = _canonical_body(path, method, b"".join(chunks))
        headers = [(name, value) for name, value in headers if name.lower() != b"content-length"]
        headers.append((b"content-length", str(len(body)).encode("ascii")))
        local_scope["headers"] = headers

        if _needs_external_npc_provider(payload, mcp=path.rstrip("/") == "/mcp/play"):
            await _json_error(send, NPC_API_REQUIRED_MESSAGE, 503)
            return

        delivered = False

        async def canonical_receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.wrapped(local_scope, canonical_receive, send)


app = LocalIdentityGateway(production_app)
