"""Stateless adapter for the vendored BSD-2-Clause xiangqi.js engine.

Each request runs in its own short-lived Node.js process.  That keeps concurrent
FastAPI requests isolated and avoids a resident worker, shared locks, or worker
restart state in the Python service.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_PATH = PROJECT_ROOT / "third_party" / "xiangqi_js" / "bridge.js"
ENGINE_TIMEOUT_SECONDS = 2.0


class XiangqiEngineError(RuntimeError):
    """The isolated rule worker could not serve an authoritative result."""


def _request(action: str, **payload: Any) -> dict[str, Any]:
    node = os.environ.get("DUEL_NODE_BINARY") or shutil.which("node")
    if not node:
        raise XiangqiEngineError("象棋规则引擎需要可用的 Node.js 运行时")
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
        raise XiangqiEngineError("象棋规则引擎响应超时") from exc
    except OSError as exc:
        raise XiangqiEngineError(f"无法启动象棋规则引擎：{exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"退出码 {completed.returncode}"
        raise XiangqiEngineError(f"象棋规则引擎异常退出：{detail}")
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise XiangqiEngineError("象棋规则引擎返回了无效 JSON") from exc
    if not isinstance(response, dict) or not response.get("ok"):
        error = response.get("error") if isinstance(response, dict) else None
        raise XiangqiEngineError(str(error or "规则引擎拒绝请求"))
    result = response.get("result")
    if not isinstance(result, dict):
        raise XiangqiEngineError("象棋规则引擎返回了无效结果")
    return result


def engine_state(fen: str | None = None) -> dict[str, Any]:
    result = _request("state", fen=fen)
    state = result.get("state")
    if not isinstance(state, dict):
        raise XiangqiEngineError("象棋规则引擎缺少局面")
    return state


def engine_apply(fen: str, move: str) -> tuple[dict[str, Any], dict[str, Any]]:
    result = _request("apply", fen=fen, move=move)
    state, applied = result.get("state"), result.get("move")
    if not isinstance(state, dict) or not isinstance(applied, dict):
        raise XiangqiEngineError("象棋规则引擎缺少走棋结果")
    return state, applied
