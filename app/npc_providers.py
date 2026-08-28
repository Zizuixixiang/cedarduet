"""Server-only NPC completion providers.

The provider boundary carries an already viewer-projected decision request and
returns only a stable legal-action id plus an optional short message. Provider
responses and secrets are never persisted here; room-level idempotency lives in
``npc_runtime``.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

import httpx


GLOBAL_PLAYER_RULES = (
    "你是回合制游戏中的当前 NPC 玩家。只能依据给定公共状态、自己的私有状态"
    "和权威合法行动列表选择一步；不得推测其他玩家隐藏信息。只返回 JSON 对象："
    '{"action_id":"...","message":"可选短消息"}。不要返回分析、解释或思维过程。'
)
MAX_PROVIDER_MESSAGE_LENGTH = 200


class NpcProviderError(RuntimeError):
    pass


class NpcProviderUnavailable(NpcProviderError):
    pass


class NpcProviderResponseError(NpcProviderError):
    pass


@dataclass(frozen=True)
class NpcDecisionRequest:
    persona: dict[str, str]
    game_rules: str
    public_state: dict[str, Any]
    private_state: dict[str, Any]
    public_actions: list[dict[str, Any]]
    legal_actions: list[dict[str, Any]]

    def messages(self) -> list[dict[str, str]]:
        payload = {
            "persona": self.persona,
            "game_rules": self.game_rules,
            "public_state": self.public_state,
            "private_state": self.private_state,
            "public_actions": self.public_actions,
            "legal_actions": self.legal_actions,
        }
        return [
            {"role": "system", "content": GLOBAL_PLAYER_RULES},
            {
                "role": "user",
                "content": json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
            },
        ]


@dataclass(frozen=True)
class ProviderDecision:
    action_id: str
    message: str | None = None


def parse_provider_decision(content: str) -> ProviderDecision:
    if not isinstance(content, str) or not content.strip():
        raise NpcProviderResponseError("NPC provider 返回空内容")
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise NpcProviderResponseError("NPC provider 必须返回 JSON") from exc
    if not isinstance(value, dict) or set(value) - {"action_id", "message"}:
        raise NpcProviderResponseError("NPC provider 返回字段无效")
    action_id = value.get("action_id")
    if not isinstance(action_id, str) or not 1 <= len(action_id.strip()) <= 128:
        raise NpcProviderResponseError("NPC provider action_id 无效")
    message = value.get("message")
    if message is not None:
        if not isinstance(message, str):
            raise NpcProviderResponseError("NPC provider message 无效")
        message = message.strip() or None
        if message is not None and len(message) > MAX_PROVIDER_MESSAGE_LENGTH:
            raise NpcProviderResponseError("NPC provider message 过长")
    return ProviderDecision(action_id.strip(), message)


class NpcProvider:
    name = "disabled"
    available = False
    unavailable_reason = "NPC provider 未配置"
    max_concurrency = 0

    async def decide(self, request: NpcDecisionRequest) -> ProviderDecision:
        del request
        raise NpcProviderUnavailable(self.unavailable_reason)

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "available": self.available,
            "reason": None if self.available else self.unavailable_reason,
            "max_concurrency": self.max_concurrency,
        }


class DisabledNpcProvider(NpcProvider):
    def __init__(self, reason: str = "NPC provider 未配置") -> None:
        self.unavailable_reason = reason


class _HttpNpcProvider(NpcProvider):
    def __init__(
        self,
        *,
        timeout: float,
        max_tokens: int,
        max_concurrency: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.max_concurrency = max_concurrency
        self.transport = transport
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_loop: asyncio.AbstractEventLoop | None = None

    def _limit(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._semaphore_loop is not loop:
            self._semaphore = asyncio.Semaphore(self.max_concurrency)
            self._semaphore_loop = loop
        return self._semaphore

    async def _post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        async with self._limit():
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport
            ) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                value = response.json()
        if not isinstance(value, dict):
            raise NpcProviderResponseError("NPC provider 响应必须是对象")
        return value


class OpenAICompatibleNpcProvider(_HttpNpcProvider):
    name = "openai_compatible"
    available = True

    def __init__(
        self,
        *,
        api_base: str,
        api_key: str,
        model: str,
        timeout: float = 20,
        max_tokens: int = 512,
        max_concurrency: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            timeout=timeout,
            max_tokens=max_tokens,
            max_concurrency=max_concurrency,
            transport=transport,
        )
        self.endpoint = (
            api_base.rstrip("/")
            if api_base.rstrip("/").endswith("/chat/completions")
            else f"{api_base.rstrip('/')}/chat/completions"
        )
        self.api_key = api_key
        self.model = model

    async def decide(self, request: NpcDecisionRequest) -> ProviderDecision:
        value = await self._post_json(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.api_key}"},
            payload={
                "model": self.model,
                "messages": request.messages(),
                "temperature": 0,
                "max_tokens": self.max_tokens,
                "response_format": {"type": "json_object"},
            },
        )
        try:
            content = value["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise NpcProviderResponseError("OpenAI-compatible 响应结构无效") from exc
        return parse_provider_decision(content)


class CedarToyBridgeNpcProvider(_HttpNpcProvider):
    name = "cedartoy_bridge"
    available = True

    def __init__(
        self,
        *,
        bridge_url: str,
        bridge_token: str,
        timeout: float = 20,
        max_tokens: int = 512,
        max_concurrency: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            timeout=timeout,
            max_tokens=max_tokens,
            max_concurrency=max_concurrency,
            transport=transport,
        )
        self.bridge_url = bridge_url
        self.bridge_token = bridge_token

    async def decide(self, request: NpcDecisionRequest) -> ProviderDecision:
        value = await self._post_json(
            self.bridge_url,
            headers={"Authorization": f"Bearer {self.bridge_token}"},
            payload={
                "messages": request.messages(),
                "max_tokens": self.max_tokens,
                "timeout": self.timeout,
            },
        )
        content = value.get("content")
        if not isinstance(content, str):
            raise NpcProviderResponseError("CedarToy bridge 响应结构无效")
        return parse_provider_decision(content)


def _number(name: str, default: str, cast, minimum, maximum):
    try:
        value = cast(os.getenv(name, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 配置无效") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须位于 {minimum}–{maximum}")
    return value


def _provider_from_environment() -> NpcProvider:
    name = os.getenv("DUEL_NPC_PROVIDER", "disabled").strip().lower()
    try:
        timeout = _number("DUEL_NPC_TIMEOUT_SECONDS", "20", float, 1, 60)
        max_tokens = _number("DUEL_NPC_MAX_TOKENS", "512", int, 1, 4096)
        concurrency = _number("DUEL_NPC_MAX_CONCURRENCY", "4", int, 1, 32)
    except ValueError as exc:
        return DisabledNpcProvider(str(exc))
    if name in {"", "disabled"}:
        return DisabledNpcProvider()
    if name == "openai_compatible":
        api_base = os.getenv("DUEL_NPC_API_BASE", "").strip()
        api_key = os.getenv("DUEL_NPC_API_KEY", "").strip()
        model = os.getenv("DUEL_NPC_MODEL", "").strip()
        if not api_base or not api_key or not model:
            return DisabledNpcProvider(
                "openai_compatible 需要 DUEL_NPC_API_BASE/API_KEY/MODEL"
            )
        return OpenAICompatibleNpcProvider(
            api_base=api_base,
            api_key=api_key,
            model=model,
            timeout=timeout,
            max_tokens=max_tokens,
            max_concurrency=concurrency,
        )
    if name == "cedartoy_bridge":
        bridge_url = os.getenv(
            "DUEL_NPC_BRIDGE_URL",
            "http://127.0.0.1:8012/internal/duel/npc-decision",
        ).strip()
        bridge_token = os.getenv("DUEL_NPC_BRIDGE_TOKEN", "").strip()
        if not bridge_url or not bridge_token:
            return DisabledNpcProvider(
                "cedartoy_bridge 需要 DUEL_NPC_BRIDGE_URL/BRIDGE_TOKEN"
            )
        return CedarToyBridgeNpcProvider(
            bridge_url=bridge_url,
            bridge_token=bridge_token,
            timeout=timeout,
            max_tokens=max_tokens,
            max_concurrency=concurrency,
        )
    return DisabledNpcProvider(f"未知 NPC provider：{name}")


_provider_cache: tuple[tuple[str, ...], NpcProvider] | None = None


def _environment_signature() -> tuple[str, ...]:
    return tuple(
        os.getenv(name, "")
        for name in (
            "DUEL_NPC_PROVIDER",
            "DUEL_NPC_API_BASE",
            "DUEL_NPC_API_KEY",
            "DUEL_NPC_MODEL",
            "DUEL_NPC_BRIDGE_URL",
            "DUEL_NPC_BRIDGE_TOKEN",
            "DUEL_NPC_TIMEOUT_SECONDS",
            "DUEL_NPC_MAX_TOKENS",
            "DUEL_NPC_MAX_CONCURRENCY",
        )
    )


def get_npc_provider() -> NpcProvider:
    global _provider_cache
    signature = _environment_signature()
    if _provider_cache is None or _provider_cache[0] != signature:
        _provider_cache = (signature, _provider_from_environment())
    return _provider_cache[1]


def npc_provider_capabilities() -> dict[str, Any]:
    return get_npc_provider().capabilities()


def reset_npc_provider_cache() -> None:
    global _provider_cache
    _provider_cache = None
