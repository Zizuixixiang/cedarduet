#!/usr/bin/env python3
"""Cross-platform bootstrap and launcher for the standalone local runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import venv
import webbrowser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = PROJECT_ROOT / ".venv"
LOCAL_PERSONAS = PROJECT_ROOT / "local" / "npc_personas"
LOCAL_DATABASE = PROJECT_ROOT / "data" / "local-duel.db"
REQUIREMENT_INPUTS = (
    PROJECT_ROOT / "requirements.txt",
    PROJECT_ROOT / "requirements-local.txt",
    PROJECT_ROOT / "third_party" / "pymahjonggb" / "setup.py",
    PROJECT_ROOT / "third_party" / "pymahjonggb" / "pyproject.toml",
)
STAMP_PATH = VENV_DIR / ".cedarduet-local-requirements"
BRIDGE_PROBES = (
    (
        "象棋",
        PROJECT_ROOT / "third_party" / "xiangqi_js" / "bridge.js",
        {"action": "state", "fen": None},
    ),
    (
        "国际象棋",
        PROJECT_ROOT / "third_party" / "chess_js" / "bridge.js",
        {"action": "state", "history": []},
    ),
    (
        "军棋",
        PROJECT_ROOT / "third_party" / "online_junqi" / "bridge.js",
        {"action": "initial"},
    ),
    (
        "围棋",
        PROJECT_ROOT / "third_party" / "tenuki" / "bridge.js",
        {"action": "state", "history": [], "dead_stones": []},
    ),
)


class LocalSetupError(RuntimeError):
    pass


def venv_python() -> Path:
    return VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def load_local_env() -> None:
    for candidate in (PROJECT_ROOT / ".env.local", PROJECT_ROOT / ".env"):
        if not candidate.is_file():
            continue
        for raw_line in candidate.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if not key or not key.replace("_", "a").isalnum():
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ.setdefault(key, value)
        break


def require_supported_python() -> None:
    if sys.version_info < (3, 10):
        raise LocalSetupError("CedarDuet 本地版需要 Python 3.10 或更高版本")


def ensure_venv() -> Path:
    python = venv_python()
    if not python.is_file():
        print(f"正在创建虚拟环境：{VENV_DIR}")
        try:
            venv.EnvBuilder(with_pip=True).create(VENV_DIR)
        except Exception as exc:
            raise LocalSetupError(f"无法创建 .venv：{exc}") from exc
    return python


def requirements_fingerprint() -> str:
    digest = hashlib.sha256()
    digest.update(f"{sys.version_info.major}.{sys.version_info.minor}\n".encode("ascii"))
    for path in REQUIREMENT_INPUTS:
        digest.update(str(path.relative_to(PROJECT_ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def dependencies_importable(python: Path) -> bool:
    check = subprocess.run(
        [str(python), "-c", "import fastapi, httpx, MahjongGB, mcp"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return check.returncode == 0


def install_dependencies(python: Path) -> None:
    fingerprint = requirements_fingerprint()
    if (
        STAMP_PATH.is_file()
        and STAMP_PATH.read_text(encoding="ascii").strip() == fingerprint
        and dependencies_importable(python)
    ):
        return
    print("正在安装本地 Web/MCP 依赖，并编译 PyMahjongGB…")
    command = [str(python), "-m", "pip", "install", "-r", str(PROJECT_ROOT / "requirements-local.txt")]
    try:
        subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        if os.name == "nt":
            detail = (
                "PyMahjongGB 需要 Microsoft C++ Build Tools。请安装 Visual Studio Build Tools，"
                "勾选“使用 C++ 的桌面开发”和 Windows SDK，再重新运行 start-local。"
            )
        elif sys.platform == "darwin":
            detail = "PyMahjongGB 需要 Xcode Command Line Tools；请先运行 xcode-select --install。"
        else:
            detail = "PyMahjongGB 需要 C++ 编译器和 Python 开发头文件（如 g++/build-essential/python3-dev）。"
        raise LocalSetupError(f"本地依赖安装失败。{detail}") from exc
    if not dependencies_importable(python):
        raise LocalSetupError("依赖安装结束，但 fastapi/httpx/MahjongGB/mcp 导入检查失败")
    STAMP_PATH.write_text(fingerprint + "\n", encoding="ascii")


def resolve_node_binary() -> str:
    configured = os.getenv("DUEL_NODE_BINARY", "").strip()
    candidate = configured or shutil.which("node")
    if not candidate:
        raise LocalSetupError(
            "未找到 Node.js。请安装 Node.js，或将 DUEL_NODE_BINARY 指向 node/node.exe；"
            "象棋、国际象棋、军棋和围棋不会使用降级规则。"
        )
    path = Path(candidate).expanduser()
    if path.is_absolute() and not path.is_file():
        raise LocalSetupError(f"DUEL_NODE_BINARY 不存在：{path}")
    try:
        completed = subprocess.run(
            [str(path), "--version"], capture_output=True, text=True, timeout=10, check=False
        )
    except OSError as exc:
        raise LocalSetupError(f"无法启动 Node.js：{exc}") from exc
    if completed.returncode != 0:
        raise LocalSetupError(f"Node.js 版本检查失败：{completed.stderr.strip()}")
    return str(path.resolve()) if path.is_absolute() else str(Path(shutil.which(str(path)) or str(path)).resolve())


def check_node_bridges(node: str) -> None:
    for name, bridge, request in BRIDGE_PROBES:
        try:
            completed = subprocess.run(
                [node, str(bridge)],
                cwd=PROJECT_ROOT,
                input=json.dumps(request, ensure_ascii=False),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LocalSetupError(f"{name} Node bridge 无法运行：{exc}") from exc
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise LocalSetupError(f"{name} Node bridge 返回了无效 JSON") from exc
        if completed.returncode != 0 or not isinstance(response, dict) or not response.get("ok"):
            detail = response.get("error") if isinstance(response, dict) else completed.stderr.strip()
            raise LocalSetupError(f"{name} Node bridge 自检失败：{detail or '未知错误'}")


def local_port() -> int:
    raw = os.getenv("DUEL_LOCAL_PORT", "8772")
    try:
        port = int(raw)
    except ValueError as exc:
        raise LocalSetupError("DUEL_LOCAL_PORT 必须是 1–65535 的整数") from exc
    if not 1 <= port <= 65535:
        raise LocalSetupError("DUEL_LOCAL_PORT 必须是 1–65535 的整数")
    return port


def runtime_environment(node: str, port: int) -> dict[str, str]:
    env = os.environ.copy()
    env["DUEL_NODE_BINARY"] = node
    env["DUEL_LOCAL_DB_PATH"] = str(LOCAL_DATABASE)
    env["DUEL_LOCAL_PERSONAS_DIR"] = str(LOCAL_PERSONAS)
    env["DUEL_LOCAL_BASE_URL"] = f"http://127.0.0.1:{port}"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(PROJECT_ROOT), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    return env


def prepare() -> tuple[Path, str, int, dict[str, str]]:
    require_supported_python()
    load_local_env()
    python = ensure_venv()
    install_dependencies(python)
    node = resolve_node_binary()
    check_node_bridges(node)
    port = local_port()
    return python, node, port, runtime_environment(node, port)


def mcp_config(python: Path, env: dict[str, str]) -> dict[str, object]:
    return {
        "mcpServers": {
            "cedarduet": {
                # Keep the venv entry point itself: on POSIX it is commonly a
                # symlink, and resolving it would silently select system Python.
                "command": str(python.absolute()),
                "args": ["-m", "app.local_mcp"],
                "env": {
                    "PYTHONPATH": str(PROJECT_ROOT),
                    "DUEL_LOCAL_BASE_URL": env["DUEL_LOCAL_BASE_URL"],
                },
            }
        }
    }


def wait_until_healthy(process: subprocess.Popen, url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = "尚未响应"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise LocalSetupError(f"本地 gateway 提前退出，退出码 {process.returncode}")
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise LocalSetupError(f"本地 gateway 在 {timeout:g} 秒内未就绪：{last_error}")


def run_web(
    python: Path, port: int, env: dict[str, str], open_browser: bool
) -> int:
    url = env["DUEL_LOCAL_BASE_URL"]
    print(f"本地数据库：{LOCAL_DATABASE}")
    print(f"浏览器地址：{url}/")
    print("MCP 配置：")
    print(json.dumps(mcp_config(python, env), ensure_ascii=False, indent=2))
    process = subprocess.Popen(
        [
            str(python), "-m", "uvicorn", "app.local_gateway:app",
            "--host", "127.0.0.1", "--port", str(port), "--workers", "1",
        ],
        cwd=PROJECT_ROOT,
        env=env,
    )
    try:
        wait_until_healthy(process, url)
        if open_browser:
            webbrowser.open(f"{url}/")
        return process.wait()
    except KeyboardInterrupt:
        return 130
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CedarDuet standalone local launcher")
    parser.add_argument(
        "command", nargs="?", default="web", choices=("web", "setup", "doctor", "mcp-config")
    )
    parser.add_argument("--no-browser", action="store_true", help="启动 Web 但不自动打开浏览器")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        python, node, _port, env = prepare()
        if args.command == "web":
            return run_web(python, _port, env, not args.no_browser)
        if args.command == "mcp-config":
            print(json.dumps(mcp_config(python, env), ensure_ascii=False, indent=2))
        else:
            print(f"Python：{python}")
            print(f"Node：{node}")
            print("PyMahjongGB：可导入")
            print("四个 Node bridge：通过")
        return 0
    except LocalSetupError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
