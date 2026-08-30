#!/usr/bin/env python3
"""Cross-platform bootstrap and launcher for the standalone local runtime."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = PROJECT_ROOT / ".venv"
LOCAL_PERSONAS = PROJECT_ROOT / "local" / "npc_personas"
LOCAL_DATABASE = PROJECT_ROOT / "data" / "local-duel.db"
PYMAHJONG_SOURCE_DIR = PROJECT_ROOT / "third_party" / "pymahjonggb"
PYMAHJONG_VERSION = "1.4.0"
PYMAHJONG_RELEASE_TAG = "pymahjonggb-wheels-v1.4.0-1"
PYMAHJONG_RELEASE_BASE_URL = (
    "https://github.com/Zizuixixiang/cedarduet/releases/download/"
    f"{PYMAHJONG_RELEASE_TAG}"
)
VIRTUALENV_REQUIREMENT = "virtualenv>=20.26,<21"
CORE_REQUIREMENT_INPUTS = (
    PROJECT_ROOT / "requirements-local.txt",
    PROJECT_ROOT / "requirements-common.txt",
)
PYMAHJONG_REQUIREMENT_INPUTS = (
    PROJECT_ROOT / "third_party" / "pymahjonggb" / "setup.py",
    PROJECT_ROOT / "third_party" / "pymahjonggb" / "pyproject.toml",
    *sorted(
        path
        for path in (PROJECT_ROOT / "third_party" / "pymahjonggb" / "MahjongGB").rglob("*")
        if path.is_file() and path.suffix.lower() in {".cpp", ".h", ".hpp"}
    ),
)
CORE_STAMP_PATH = VENV_DIR / ".cedarduet-local-core-requirements"
PYMAHJONG_STAMP_PATH = VENV_DIR / ".cedarduet-local-pymahjonggb"
CORE_IMPORTS = ("fastapi", "httpx", "numpy", "rlcard", "tzdata", "uvicorn", "mcp")
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


def _remove_new_venv(created_for_attempt: bool) -> None:
    if created_for_attempt and VENV_DIR.exists():
        shutil.rmtree(VENV_DIR, ignore_errors=True)


def _create_stdlib_venv() -> None:
    venv_module = importlib.import_module("venv")
    venv_module.EnvBuilder(with_pip=True).create(VENV_DIR)


def _create_bootstrapped_virtualenv() -> None:
    with tempfile.TemporaryDirectory(prefix="cedarduet-virtualenv-") as temporary:
        target = Path(temporary) / "packages"
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--ignore-installed",
            "--target",
            str(target),
            VIRTUALENV_REQUIREMENT,
        ]
        try:
            subprocess.run(command, cwd=PROJECT_ROOT, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise LocalSetupError(
                "当前 Python 缺少 stdlib venv，且无法用现有 pip 临时引导 virtualenv。"
                "请安装 python.org 的完整 Python（勾选 pip/venv），再重新运行 start-local。"
            ) from exc

        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [str(target), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
        )
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "virtualenv",
                    "--no-download",
                    "--app-data",
                    str(Path(temporary) / "app-data"),
                    str(VENV_DIR),
                ],
                cwd=PROJECT_ROOT,
                env=env,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise LocalSetupError(
                "临时 virtualenv 已下载，但无法创建仓库内 .venv。"
                "请安装 python.org 的完整 Python（勾选 pip/venv），再重新运行 start-local。"
            ) from exc


def ensure_venv() -> Path:
    python = venv_python()
    if python.is_file():
        return python

    print(f"正在创建虚拟环境：{VENV_DIR}")
    created_for_attempt = not VENV_DIR.exists()
    try:
        _create_stdlib_venv()
    except (ModuleNotFoundError, ImportError, OSError, subprocess.SubprocessError) as exc:
        _remove_new_venv(created_for_attempt)
        print(
            "当前 Python 的 stdlib venv 不可用；将用现有 pip 临时引导 virtualenv，"
            "只创建仓库内 .venv，不写入系统 Python。"
        )
        try:
            _create_bootstrapped_virtualenv()
        except LocalSetupError:
            _remove_new_venv(created_for_attempt)
            raise
    except Exception as exc:
        _remove_new_venv(created_for_attempt)
        raise LocalSetupError(
            "无法创建仓库内 .venv。请安装包含 pip 和 venv 的完整 Python 后重试："
            f"{exc}"
        ) from exc

    if not python.is_file():
        _remove_new_venv(created_for_attempt)
        raise LocalSetupError(
            "虚拟环境创建过程结束，但未找到可运行的 Python。"
            "请安装 python.org 的完整 Python（勾选 pip/venv）后重试。"
        )
    return python


def _fingerprint(paths: tuple[Path, ...], label: str) -> str:
    digest = hashlib.sha256()
    digest.update(f"{label}\n{sys.version_info.major}.{sys.version_info.minor}\n".encode("ascii"))
    for path in paths:
        digest.update(str(path.relative_to(PROJECT_ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def core_requirements_fingerprint() -> str:
    return _fingerprint(CORE_REQUIREMENT_INPUTS, "core-v1")


def pymahjong_requirements_fingerprint() -> str:
    return _fingerprint(PYMAHJONG_REQUIREMENT_INPUTS, f"pymahjonggb-{PYMAHJONG_RELEASE_TAG}")


def modules_importable(python: Path, modules: tuple[str, ...]) -> bool:
    imports = ", ".join(modules)
    check = subprocess.run(
        [str(python), "-c", f"import {imports}"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return check.returncode == 0


def core_dependencies_importable(python: Path) -> bool:
    return modules_importable(python, CORE_IMPORTS)


def pymahjong_importable(python: Path) -> bool:
    return modules_importable(python, ("MahjongGB",))


def dependencies_importable(python: Path) -> bool:
    return core_dependencies_importable(python) and pymahjong_importable(python)


def _stamp_matches(path: Path, fingerprint: str) -> bool:
    try:
        return path.read_text(encoding="ascii").strip() == fingerprint
    except (OSError, UnicodeError):
        return False


def install_core_dependencies(python: Path) -> None:
    fingerprint = core_requirements_fingerprint()
    if core_dependencies_importable(python) and (
        _stamp_matches(CORE_STAMP_PATH, fingerprint) or not CORE_STAMP_PATH.exists()
    ):
        if not CORE_STAMP_PATH.exists():
            CORE_STAMP_PATH.write_text(fingerprint + "\n", encoding="ascii")
        return
    print("正在安装本地 Web/MCP 普通依赖…")
    command = [str(python), "-m", "pip", "install", "-r", str(PROJECT_ROOT / "requirements-local.txt")]
    try:
        subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LocalSetupError(
            "本地 Web/MCP 普通依赖安装失败；尚未开始安装 PyMahjongGB。"
            "请检查网络、pip 和上方安装日志后重试。"
        ) from exc
    if not core_dependencies_importable(python):
        raise LocalSetupError(
            "普通依赖安装结束，但 fastapi/httpx/numpy/rlcard/uvicorn/mcp 导入检查失败；"
            "尚未开始安装 PyMahjongGB。"
        )
    CORE_STAMP_PATH.write_text(fingerprint + "\n", encoding="ascii")


def target_python_info(python: Path) -> dict[str, object]:
    script = (
        "import json, platform, struct, sys; "
        "print(json.dumps({'implementation': sys.implementation.name, "
        "'major': sys.version_info.major, 'minor': sys.version_info.minor, "
        "'platform': sys.platform, 'machine': platform.machine(), "
        "'pointer_bits': struct.calcsize('P') * 8}))"
    )
    try:
        completed = subprocess.run(
            [str(python), "-c", script],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        result = json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise LocalSetupError(f"无法识别虚拟环境 Python 平台：{exc}") from exc
    if not isinstance(result, dict):
        raise LocalSetupError("虚拟环境 Python 返回了无效的平台信息")
    return result


def windows_wheel_filename(info: dict[str, object]) -> str | None:
    machine = str(info.get("machine", "")).lower()
    if (
        info.get("platform") != "win32"
        or info.get("implementation") != "cpython"
        or info.get("major") != 3
        or info.get("minor") not in {10, 11, 12, 13}
        or info.get("pointer_bits") != 64
        or machine not in {"amd64", "x86_64"}
    ):
        return None
    tag = f"cp3{info['minor']}"
    return f"pymahjonggb-{PYMAHJONG_VERSION}-{tag}-{tag}-win_amd64.whl"


def pymahjong_release_base_url() -> str:
    return os.getenv("DUEL_PYMAHJONG_WHEEL_BASE_URL", PYMAHJONG_RELEASE_BASE_URL).rstrip("/")


def _download_release_asset(filename: str) -> bytes:
    url = f"{pymahjong_release_base_url()}/{filename}"
    request = urllib.request.Request(url, headers={"User-Agent": "CedarDuet-local-launcher"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _manifest_sha256(manifest: bytes, filename: str) -> str | None:
    try:
        text = manifest.decode("utf-8")
    except UnicodeDecodeError:
        return None
    for raw_line in text.splitlines():
        parts = raw_line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == filename:
            digest = parts[0].lower()
            if len(digest) == 64 and all(character in "0123456789abcdef" for character in digest):
                return digest
    return None


def install_windows_release_wheel(python: Path, filename: str) -> tuple[bool, str]:
    try:
        manifest = _download_release_asset("SHA256SUMS.txt")
        expected = _manifest_sha256(manifest, filename)
        if expected is None:
            return False, f"Release 校验清单中没有当前 Python 对应的 {filename}"
        wheel = _download_release_asset(filename)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return False, f"无法下载官方预编译 wheel：{exc}"

    actual = hashlib.sha256(wheel).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise LocalSetupError(
            f"官方预编译 wheel 的 SHA256 校验失败（{filename}）；为安全起见已停止安装。"
        )

    try:
        with tempfile.TemporaryDirectory(prefix="cedarduet-pymahjong-wheel-") as temporary:
            wheel_path = Path(temporary) / filename
            wheel_path.write_bytes(wheel)
            subprocess.run(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    "--force-reinstall",
                    str(wheel_path),
                ],
                cwd=PROJECT_ROOT,
                check=True,
            )
    except (OSError, subprocess.CalledProcessError) as exc:
        return False, f"官方预编译 wheel 下载并校验成功，但 pip 安装失败：{exc}"
    return True, ""


def cleanup_pymahjong_build_artifacts() -> None:
    shutil.rmtree(PYMAHJONG_SOURCE_DIR / "build", ignore_errors=True)
    for path in PYMAHJONG_SOURCE_DIR.glob("*.egg-info"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def _pymahjong_copy_ignore(_directory: str, names: list[str]) -> set[str]:
    ignored = {"build", "__pycache__"}
    ignored.update(name for name in names if name.endswith(".egg-info"))
    return ignored.intersection(names)


def install_pymahjong_from_source(python: Path) -> None:
    cleanup_pymahjong_build_artifacts()
    try:
        with tempfile.TemporaryDirectory(prefix="cedarduet-pymahjong-source-") as temporary:
            source_copy = Path(temporary) / "pymahjonggb"
            shutil.copytree(PYMAHJONG_SOURCE_DIR, source_copy, ignore=_pymahjong_copy_ignore)
            subprocess.run(
                [
                    str(python), "-m", "pip", "install", "--no-deps", "--force-reinstall",
                    str(source_copy),
                ],
                cwd=PROJECT_ROOT,
                check=True,
            )
    finally:
        cleanup_pymahjong_build_artifacts()


def install_pymahjong_dependency(python: Path) -> None:
    fingerprint = pymahjong_requirements_fingerprint()
    if pymahjong_importable(python) and (
        _stamp_matches(PYMAHJONG_STAMP_PATH, fingerprint) or not PYMAHJONG_STAMP_PATH.exists()
    ):
        if not PYMAHJONG_STAMP_PATH.exists():
            PYMAHJONG_STAMP_PATH.write_text(fingerprint + "\n", encoding="ascii")
        cleanup_pymahjong_build_artifacts()
        return

    info = target_python_info(python)
    wheel_filename = windows_wheel_filename(info)
    wheel_failure = ""
    installed = False
    if wheel_filename:
        print(f"正在安装官方预编译 PyMahjongGB wheel：{wheel_filename}")
        installed, wheel_failure = install_windows_release_wheel(python, wheel_filename)
        if wheel_failure:
            print(f"预编译 wheel 不可用：{wheel_failure}")
    elif info.get("platform") == "win32":
        wheel_failure = (
            "官方 wheel 仅覆盖 Windows x64 CPython 3.10/3.11/3.12/3.13；"
            "当前解释器不匹配"
        )
        print(f"预编译 wheel 不可用：{wheel_failure}")

    if not installed:
        print("正在从 vendored 原始源码构建 PyMahjongGB（构建目录使用系统临时目录）…")
        try:
            install_pymahjong_from_source(python)
        except (OSError, subprocess.CalledProcessError) as exc:
            if info.get("platform") == "win32":
                prefix = f"{wheel_failure}；" if wheel_failure else ""
                detail = (
                    f"{prefix}源码回退构建失败。请安装 Microsoft C++ Build Tools 14+，"
                    "勾选“使用 C++ 的桌面开发”和 Windows SDK 后重试。"
                    "普通 Web/MCP 依赖已经独立安装，不会被本次失败回滚。"
                )
            elif info.get("platform") == "darwin":
                detail = (
                    "PyMahjongGB 源码构建失败。请先安装 Xcode Command Line Tools"
                    "（xcode-select --install）。普通 Web/MCP 依赖已安装。"
                )
            else:
                detail = (
                    "PyMahjongGB 源码构建失败。请安装 C++ 编译器和 Python 开发头文件"
                    "（如 g++/build-essential/python3-dev）。普通 Web/MCP 依赖已安装。"
                )
            raise LocalSetupError(detail) from exc

    if not pymahjong_importable(python):
        raise LocalSetupError("PyMahjongGB 安装结束，但 MahjongGB 导入检查失败")
    PYMAHJONG_STAMP_PATH.write_text(fingerprint + "\n", encoding="ascii")
    cleanup_pymahjong_build_artifacts()


def install_dependencies(python: Path) -> None:
    install_core_dependencies(python)
    install_pymahjong_dependency(python)


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
    cleanup_pymahjong_build_artifacts()
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
