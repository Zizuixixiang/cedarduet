"""Stateless Python adapter for the vendored MIT Tenuki rules core.

Every request runs a short-lived Node.js worker and replays the persisted public
action history. Tenuki therefore remains authoritative for liberties, suicide,
captures, positional superko, dead-stone grouping, territory, and area scoring
without process-global mutable game state.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_PATH = PROJECT_ROOT / "third_party" / "tenuki" / "bridge.js"
ENGINE_TIMEOUT_SECONDS = 5.0


class GoEngineError(RuntimeError):
    """The isolated Tenuki worker could not return a trusted result."""


def _request(action: str, **payload: Any) -> dict[str, Any]:
    node = os.environ.get("DUEL_NODE_BINARY") or shutil.which("node")
    if not node:
        raise GoEngineError("围棋规则引擎需要可用的 Node.js 运行时")
    request = json.dumps(
        {"action": action, **payload},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        completed = subprocess.run(
            [node, str(BRIDGE_PATH)],
            cwd=PROJECT_ROOT,
            input=request,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=ENGINE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GoEngineError("围棋规则引擎响应超时") from exc
    except OSError as exc:
        raise GoEngineError(f"无法启动围棋规则引擎：{exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"退出码 {completed.returncode}"
        raise GoEngineError(f"围棋规则引擎异常退出：{detail}")
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise GoEngineError("围棋规则引擎返回了无效 JSON") from exc
    if not isinstance(response, dict) or not response.get("ok"):
        error = response.get("error") if isinstance(response, dict) else None
        raise GoEngineError(str(error or "围棋规则引擎拒绝请求"))
    result = response.get("result")
    if not isinstance(result, dict):
        raise GoEngineError("围棋规则引擎返回了无效结果")
    return result


def engine_state(
    history: list[dict[str, Any]] | None = None,
    dead_stones: list[dict[str, int]] | None = None,
) -> dict[str, Any]:
    result = _request(
        "state",
        history=history or [],
        dead_stones=dead_stones or [],
    )
    state = result.get("state")
    if not isinstance(state, dict):
        raise GoEngineError("围棋规则引擎缺少局面")
    return state


def engine_apply(
    history: list[dict[str, Any]],
    move: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    result = _request("apply", history=history, move=move, dead_stones=[])
    state = result.get("state")
    applied = result.get("move")
    next_history = result.get("history")
    if (
        not isinstance(state, dict)
        or not isinstance(applied, dict)
        or not isinstance(next_history, list)
    ):
        raise GoEngineError("围棋规则引擎缺少落子结果")
    return state, applied, next_history


def engine_toggle_dead(
    history: list[dict[str, Any]],
    dead_stones: list[dict[str, int]],
    row: int,
    col: int,
) -> tuple[dict[str, Any], list[dict[str, int]], list[dict[str, int]]]:
    result = _request(
        "toggle_dead",
        history=history,
        dead_stones=dead_stones,
        row=row,
        col=col,
    )
    state = result.get("state")
    added = result.get("dead_added")
    removed = result.get("dead_removed")
    if (
        not isinstance(state, dict)
        or not isinstance(added, list)
        or not isinstance(removed, list)
    ):
        raise GoEngineError("围棋规则引擎缺少死子标记结果")
    return state, added, removed
