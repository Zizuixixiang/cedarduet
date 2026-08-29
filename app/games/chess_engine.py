"""Stateless adapter for the vendored BSD-2-Clause chess.js engine.

Every request uses a short-lived Node.js process. The complete UCI history is
replayed from the game's starting FEN so repetition-dependent outcomes remain
authoritative without a resident worker or shared mutable engine state.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_PATH = PROJECT_ROOT / "third_party" / "chess_js" / "bridge.js"
ENGINE_TIMEOUT_SECONDS = 2.0
STANDARD_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class ChessEngineError(RuntimeError):
    """The isolated chess rules worker could not return a trusted result."""


def _request(action: str, **payload: Any) -> dict[str, Any]:
    node = os.environ.get("DUEL_NODE_BINARY") or shutil.which("node")
    if not node:
        raise ChessEngineError("国际象棋规则引擎需要可用的 Node.js 运行时")
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
        raise ChessEngineError("国际象棋规则引擎响应超时") from exc
    except OSError as exc:
        raise ChessEngineError(f"无法启动国际象棋规则引擎：{exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"退出码 {completed.returncode}"
        raise ChessEngineError(f"国际象棋规则引擎异常退出：{detail}")
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ChessEngineError("国际象棋规则引擎返回了无效 JSON") from exc
    if not isinstance(response, dict) or not response.get("ok"):
        error = response.get("error") if isinstance(response, dict) else None
        raise ChessEngineError(str(error or "规则引擎拒绝请求"))
    result = response.get("result")
    if not isinstance(result, dict):
        raise ChessEngineError("国际象棋规则引擎返回了无效结果")
    return result


def engine_state(
    starting_fen: str | None = None,
    history: list[str] | None = None,
) -> dict[str, Any]:
    result = _request(
        "state",
        starting_fen=starting_fen or STANDARD_FEN,
        history=history or [],
    )
    state = result.get("state")
    if not isinstance(state, dict):
        raise ChessEngineError("国际象棋规则引擎缺少局面")
    return state


def engine_apply(
    starting_fen: str,
    history: list[str],
    move: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = _request(
        "apply",
        starting_fen=starting_fen,
        history=history,
        move=move,
    )
    state, applied = result.get("state"), result.get("move")
    if not isinstance(state, dict) or not isinstance(applied, dict):
        raise ChessEngineError("国际象棋规则引擎缺少走棋结果")
    return state, applied
