"""Isolated adapter for the pinned MIT online-junqi rule core."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_PATH = PROJECT_ROOT / "third_party" / "online_junqi" / "bridge.js"
ENGINE_TIMEOUT_SECONDS = 2.0


class JunqiEngineError(RuntimeError):
    """The isolated upstream rule worker could not return a valid result."""


def _request(action: str, **payload: Any) -> dict[str, Any]:
    node = os.environ.get("DUEL_NODE_BINARY") or shutil.which("node")
    if not node:
        raise JunqiEngineError("军棋规则引擎需要可用的 Node.js 运行时")
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
        raise JunqiEngineError("军棋规则引擎响应超时") from exc
    except OSError as exc:
        raise JunqiEngineError(f"无法启动军棋规则引擎：{exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"退出码 {completed.returncode}"
        raise JunqiEngineError(f"军棋规则引擎异常退出：{detail}")
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise JunqiEngineError("军棋规则引擎返回了无效 JSON") from exc
    if not isinstance(response, dict) or not response.get("ok"):
        error = response.get("error") if isinstance(response, dict) else None
        raise JunqiEngineError(str(error or "规则引擎拒绝请求"))
    result = response.get("result")
    if not isinstance(result, dict):
        raise JunqiEngineError("军棋规则引擎返回了无效结果")
    return result


def engine_initial() -> dict[str, Any]:
    return _request("initial")


def engine_validate(board: dict[str, Any]) -> dict[str, bool]:
    return _request("validate", board=board)


def engine_moves(board: dict[str, Any], color: str) -> list[dict[str, Any]]:
    result = _request("moves", board=board, color=color)
    moves = result.get("moves")
    if not isinstance(moves, list) or any(not isinstance(item, dict) for item in moves):
        raise JunqiEngineError("军棋规则引擎返回了无效合法行动")
    return moves


def engine_swaps(board: dict[str, Any], color: str) -> list[dict[str, Any]]:
    result = _request("swaps", board=board, color=color)
    swaps = result.get("swaps")
    if not isinstance(swaps, list) or any(not isinstance(item, dict) for item in swaps):
        raise JunqiEngineError("军棋规则引擎返回了无效布阵行动")
    return swaps


def engine_shuffle(board: dict[str, Any], color: str) -> dict[str, Any]:
    result = _request("shuffle", board=board, color=color)
    updated = result.get("board")
    if not isinstance(updated, dict):
        raise JunqiEngineError("军棋规则引擎缺少洗牌后局面")
    return updated


def engine_swap(
    board: dict[str, Any], color: str, start: str, end: str
) -> dict[str, Any]:
    result = _request("swap", board=board, color=color, **{"from": start, "to": end})
    updated = result.get("board")
    if not isinstance(updated, dict):
        raise JunqiEngineError("军棋规则引擎缺少换位后局面")
    return updated


def engine_apply(
    board: dict[str, Any], color: str, start: str, end: str
) -> dict[str, Any]:
    result = _request("apply", board=board, color=color, **{"from": start, "to": end})
    if not isinstance(result.get("board"), dict) or not isinstance(result.get("move"), dict):
        raise JunqiEngineError("军棋规则引擎缺少走棋结果")
    return result
