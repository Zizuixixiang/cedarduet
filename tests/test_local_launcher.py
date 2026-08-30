import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "cedarduet_local_launcher", PROJECT_ROOT / "scripts" / "local.py"
)
assert SPEC and SPEC.loader
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class LocalLauncherTests(unittest.TestCase):
    def test_launcher_does_not_import_venv_at_module_load(self):
        source = (PROJECT_ROOT / "scripts" / "local.py").read_text(encoding="utf-8")
        self.assertNotIn("\nimport venv\n", source)

    def test_missing_stdlib_venv_uses_repo_local_virtualenv_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            local_venv = Path(temporary) / ".venv"

            def create_fallback():
                python = launcher.venv_python()
                python.parent.mkdir(parents=True)
                python.touch()

            output = io.StringIO()
            with patch.object(launcher, "VENV_DIR", local_venv), patch.object(
                launcher, "_create_stdlib_venv", side_effect=ModuleNotFoundError("venv")
            ), patch.object(
                launcher, "_create_bootstrapped_virtualenv", side_effect=create_fallback
            ) as fallback, redirect_stdout(output):
                python = launcher.ensure_venv()

            self.assertEqual(
                python,
                local_venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python"),
            )
            fallback.assert_called_once_with()
            self.assertIn("stdlib venv 不可用", output.getvalue())
            self.assertIn("不写入系统 Python", output.getvalue())

    def test_missing_venv_and_failed_bootstrap_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            launcher, "VENV_DIR", Path(temporary) / ".venv"
        ), patch.object(
            launcher, "_create_stdlib_venv", side_effect=ModuleNotFoundError("venv")
        ), patch.object(
            launcher,
            "_create_bootstrapped_virtualenv",
            side_effect=launcher.LocalSetupError("请安装 python.org 的完整 Python（勾选 pip/venv）"),
        ), self.assertRaisesRegex(launcher.LocalSetupError, "完整 Python.*pip/venv"):
            launcher.ensure_venv()

    def test_virtualenv_bootstrap_is_temporary_and_repo_local(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            launcher, "VENV_DIR", Path(temporary) / "repo" / ".venv"
        ), patch.object(launcher.subprocess, "run") as run:
            launcher._create_bootstrapped_virtualenv()

        self.assertEqual(run.call_count, 2)
        pip_command = run.call_args_list[0].args[0]
        virtualenv_command = run.call_args_list[1].args[0]
        self.assertIn("--target", pip_command)
        self.assertIn("--ignore-installed", pip_command)
        self.assertNotIn("--user", pip_command)
        self.assertEqual(virtualenv_command[-1], str(Path(temporary) / "repo" / ".venv"))
        self.assertIn("--app-data", virtualenv_command)
        self.assertIn("PYTHONPATH", run.call_args_list[1].kwargs["env"])

    def test_main_prints_setup_error_without_traceback(self):
        stderr = io.StringIO()
        with patch.object(
            launcher, "parse_args", return_value=launcher.argparse.Namespace(command="mcp-config")
        ), patch.object(
            launcher, "prepare", side_effect=launcher.LocalSetupError("需要完整 Python/venv")
        ), redirect_stderr(stderr):
            self.assertEqual(launcher.main(), 1)
        self.assertIn("错误：需要完整 Python/venv", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_mcp_config_uses_absolute_venv_python_and_loopback(self):
        python = launcher.venv_python()
        env = {
            "DUEL_LOCAL_BASE_URL": "http://127.0.0.1:8772",
        }
        config = launcher.mcp_config(python, env)["mcpServers"]["cedarduet"]
        self.assertTrue(Path(config["command"]).is_absolute())
        self.assertEqual(config["command"], str(python.absolute()))
        self.assertIn(".venv", Path(config["command"]).parts)
        self.assertEqual(config["args"], ["-m", "app.local_mcp"])
        self.assertEqual(
            config["env"]["DUEL_LOCAL_BASE_URL"], "http://127.0.0.1:8772"
        )
        self.assertEqual(config["env"]["PYTHONPATH"], str(PROJECT_ROOT))

    def test_all_four_vendored_node_bridges_pass(self):
        node = launcher.resolve_node_binary()
        launcher.check_node_bridges(node)

    def test_missing_node_has_a_clear_no_fallback_error(self):
        with patch.dict(os.environ, {"DUEL_NODE_BINARY": ""}), patch.object(
            launcher.shutil, "which", return_value=None
        ):
            with self.assertRaisesRegex(
                launcher.LocalSetupError, "未找到 Node.js.*不会使用降级规则"
            ):
                launcher.resolve_node_binary()

    def test_all_platform_entry_points_delegate_to_one_python_launcher(self):
        entry_points = {
            "start-local.sh": "local.py",
            "start-local.ps1": "local.py",
            "start-local.cmd": "local.py",
        }
        for filename, marker in entry_points.items():
            path = PROJECT_ROOT / "scripts" / filename
            self.assertTrue(path.is_file(), filename)
            self.assertIn(marker, path.read_text(encoding="utf-8"), filename)

    def test_local_environment_file_preserves_explicit_process_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / ".env.local"
            env_file.write_text("DUEL_LOCAL_PORT=9001\nDUEL_NPC_MODEL=file-model\n", encoding="utf-8")
            original_root = launcher.PROJECT_ROOT
            original_port = os.environ.get("DUEL_LOCAL_PORT")
            original_model = os.environ.get("DUEL_NPC_MODEL")
            try:
                launcher.PROJECT_ROOT = Path(temporary)
                os.environ["DUEL_LOCAL_PORT"] = "9002"
                os.environ.pop("DUEL_NPC_MODEL", None)
                launcher.load_local_env()
                self.assertEqual(os.environ["DUEL_LOCAL_PORT"], "9002")
                self.assertEqual(os.environ["DUEL_NPC_MODEL"], "file-model")
            finally:
                launcher.PROJECT_ROOT = original_root
                if original_port is None:
                    os.environ.pop("DUEL_LOCAL_PORT", None)
                else:
                    os.environ["DUEL_LOCAL_PORT"] = original_port
                if original_model is None:
                    os.environ.pop("DUEL_NPC_MODEL", None)
                else:
                    os.environ["DUEL_NPC_MODEL"] = original_model

    def test_pymahjong_build_metadata_is_platform_aware(self):
        setup_text = (
            PROJECT_ROOT / "third_party" / "pymahjonggb" / "setup.py"
        ).read_text(encoding="utf-8")
        pyproject = (
            PROJECT_ROOT / "third_party" / "pymahjonggb" / "pyproject.toml"
        ).read_text(encoding="utf-8")
        self.assertIn('os.name == "nt"', setup_text)
        self.assertIn('"/std:c++14"', setup_text)
        self.assertIn('"-std=c++11"', setup_text)
        self.assertIn('build-backend = "setuptools.build_meta"', pyproject)

    def test_windows_wheel_selection_covers_supported_cpython_x64(self):
        for minor in (10, 11, 12, 13):
            info = {
                "implementation": "cpython",
                "major": 3,
                "minor": minor,
                "platform": "win32",
                "machine": "AMD64",
                "pointer_bits": 64,
            }
            self.assertEqual(
                launcher.windows_wheel_filename(info),
                f"pymahjonggb-1.4.0-cp3{minor}-cp3{minor}-win_amd64.whl",
            )

        unsupported = [
            {"minor": 14},
            {"implementation": "pypy"},
            {"machine": "ARM64"},
            {"pointer_bits": 32},
            {"platform": "linux"},
        ]
        base = {
            "implementation": "cpython",
            "major": 3,
            "minor": 12,
            "platform": "win32",
            "machine": "AMD64",
            "pointer_bits": 64,
        }
        for changes in unsupported:
            self.assertIsNone(launcher.windows_wheel_filename({**base, **changes}))

    def test_windows_release_wheel_is_verified_before_pip_install(self):
        filename = "pymahjonggb-1.4.0-cp312-cp312-win_amd64.whl"
        wheel = b"a deterministic test wheel"
        digest = hashlib.sha256(wheel).hexdigest()

        def download(asset):
            if asset == "SHA256SUMS.txt":
                return f"{digest}  {filename}\n".encode("ascii")
            self.assertEqual(asset, filename)
            return wheel

        with patch.object(
            launcher, "_download_release_asset", side_effect=download
        ), patch.object(launcher.subprocess, "run") as run:
            installed, reason = launcher.install_windows_release_wheel(
                Path("python.exe"), filename
            )

        self.assertTrue(installed)
        self.assertEqual(reason, "")
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(
            command[:6],
            ["python.exe", "-m", "pip", "install", "--no-deps", "--force-reinstall"],
        )
        self.assertEqual(Path(command[-1]).name, filename)

    def test_windows_release_wheel_checksum_mismatch_never_runs_pip(self):
        filename = "pymahjonggb-1.4.0-cp312-cp312-win_amd64.whl"

        def download(asset):
            if asset == "SHA256SUMS.txt":
                return f"{'0' * 64}  {filename}\n".encode("ascii")
            return b"tampered wheel"

        with patch.object(
            launcher, "_download_release_asset", side_effect=download
        ), patch.object(launcher.subprocess, "run") as run, self.assertRaisesRegex(
            launcher.LocalSetupError, "SHA256 校验失败"
        ):
            launcher.install_windows_release_wheel(Path("python.exe"), filename)
        run.assert_not_called()

    def test_windows_wheel_unavailable_falls_back_with_clear_msvc_error(self):
        info = {
            "implementation": "cpython",
            "major": 3,
            "minor": 12,
            "platform": "win32",
            "machine": "AMD64",
            "pointer_bits": 64,
        }
        with patch.object(
            launcher, "pymahjong_importable", return_value=False
        ), patch.object(
            launcher, "target_python_info", return_value=info
        ), patch.object(
            launcher,
            "install_windows_release_wheel",
            return_value=(False, "Release 中没有匹配 wheel"),
        ), patch.object(
            launcher,
            "install_pymahjong_from_source",
            side_effect=subprocess.CalledProcessError(1, ["pip"]),
        ) as source, self.assertRaisesRegex(
            launcher.LocalSetupError,
            r"Microsoft C\+\+ Build Tools 14\+.*普通 Web/MCP 依赖已经独立安装",
        ):
            launcher.install_pymahjong_dependency(Path("python.exe"))
        source.assert_called_once_with(Path("python.exe"))

    def test_matching_windows_wheel_avoids_source_compiler(self):
        info = {
            "implementation": "cpython",
            "major": 3,
            "minor": 12,
            "platform": "win32",
            "machine": "AMD64",
            "pointer_bits": 64,
        }
        with tempfile.TemporaryDirectory() as temporary:
            stamp = Path(temporary) / "mahjong.stamp"
            with patch.object(
                launcher, "PYMAHJONG_STAMP_PATH", stamp
            ), patch.object(
                launcher, "pymahjong_requirements_fingerprint", return_value="mahjong-fingerprint"
            ), patch.object(
                launcher, "pymahjong_importable", side_effect=[False, True]
            ), patch.object(
                launcher, "target_python_info", return_value=info
            ), patch.object(
                launcher, "install_windows_release_wheel", return_value=(True, "")
            ) as wheel, patch.object(
                launcher, "install_pymahjong_from_source"
            ) as source, patch.object(
                launcher, "cleanup_pymahjong_build_artifacts"
            ):
                launcher.install_pymahjong_dependency(Path("python.exe"))

            wheel.assert_called_once_with(
                Path("python.exe"), "pymahjonggb-1.4.0-cp312-cp312-win_amd64.whl"
            )
            source.assert_not_called()
            self.assertEqual(stamp.read_text(encoding="ascii"), "mahjong-fingerprint\n")

    def test_core_dependencies_remain_installed_when_mahjong_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            core_stamp = Path(temporary) / "core.stamp"
            with patch.object(launcher, "CORE_STAMP_PATH", core_stamp), patch.object(
                launcher, "core_requirements_fingerprint", return_value="core-fingerprint"
            ), patch.object(
                launcher, "core_dependencies_importable", side_effect=[False, True]
            ), patch.object(launcher.subprocess, "run") as run, patch.object(
                launcher,
                "install_pymahjong_dependency",
                side_effect=launcher.LocalSetupError("麻将安装失败"),
            ), self.assertRaisesRegex(launcher.LocalSetupError, "麻将安装失败"):
                launcher.install_dependencies(Path("python"))

            self.assertEqual(core_stamp.read_text(encoding="ascii"), "core-fingerprint\n")
            run.assert_called_once()
            self.assertIn("requirements-local.txt", run.call_args.args[0][-1])

    def test_pymahjong_cleanup_removes_only_known_build_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "pymahjonggb"
            (source / "build" / "temp").mkdir(parents=True)
            (source / "build" / "temp" / "object.obj").touch()
            (source / "PyMahjongGB.egg-info").mkdir()
            (source / "PyMahjongGB.egg-info" / "PKG-INFO").touch()
            (source / "MahjongGB").mkdir()
            keep = source / "MahjongGB" / "mahjong.cpp"
            keep.touch()

            with patch.object(launcher, "PYMAHJONG_SOURCE_DIR", source):
                launcher.cleanup_pymahjong_build_artifacts()

            self.assertFalse((source / "build").exists())
            self.assertFalse((source / "PyMahjongGB.egg-info").exists())
            self.assertTrue(keep.exists())

    def test_source_build_uses_temporary_copy_and_cleans_original_residue(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "pymahjonggb"
            (source / "MahjongGB").mkdir(parents=True)
            (source / "setup.py").write_text("# test\n", encoding="utf-8")
            (source / "MahjongGB" / "mahjong.cpp").write_text("// test\n", encoding="utf-8")
            (source / "build").mkdir()
            (source / "PyMahjongGB.egg-info").mkdir()

            with patch.object(launcher, "PYMAHJONG_SOURCE_DIR", source), patch.object(
                launcher.subprocess, "run"
            ) as run:
                launcher.install_pymahjong_from_source(Path("python"))

            installed_from = Path(run.call_args.args[0][-1])
            self.assertNotEqual(installed_from, source)
            self.assertIn("cedarduet-pymahjong-source-", str(installed_from))
            self.assertFalse((source / "build").exists())
            self.assertFalse((source / "PyMahjongGB.egg-info").exists())

    def test_failed_source_build_cleans_original_residue(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "pymahjonggb"
            (source / "MahjongGB").mkdir(parents=True)
            (source / "setup.py").write_text("# test\n", encoding="utf-8")

            def fail_build(*_args, **_kwargs):
                (source / "build").mkdir()
                (source / "PyMahjongGB.egg-info").mkdir()
                raise subprocess.CalledProcessError(1, ["pip"])

            with patch.object(launcher, "PYMAHJONG_SOURCE_DIR", source), patch.object(
                launcher.subprocess, "run", side_effect=fail_build
            ), self.assertRaises(subprocess.CalledProcessError):
                launcher.install_pymahjong_from_source(Path("python"))

            self.assertFalse((source / "build").exists())
            self.assertFalse((source / "PyMahjongGB.egg-info").exists())

    def test_local_requirements_exclude_native_mahjong_build(self):
        local_requirements = (PROJECT_ROOT / "requirements-local.txt").read_text(
            encoding="utf-8"
        )
        common_requirements = (PROJECT_ROOT / "requirements-common.txt").read_text(
            encoding="utf-8"
        )
        requirements = local_requirements + common_requirements
        self.assertNotIn("requirements.txt", local_requirements)
        self.assertNotIn("third_party/pymahjonggb", local_requirements.lower())
        for dependency in ("fastapi", "httpx", "numpy", "rlcard", "uvicorn", "mcp"):
            self.assertIn(dependency, requirements)

    def test_production_and_local_share_the_tzdata_dependency(self):
        common_path = PROJECT_ROOT / "requirements-common.txt"
        common_requirements = common_path.read_text(encoding="utf-8")
        production_requirements = (PROJECT_ROOT / "requirements.txt").read_text(
            encoding="utf-8"
        )
        local_requirements = (PROJECT_ROOT / "requirements-local.txt").read_text(
            encoding="utf-8"
        )

        self.assertIn("tzdata>=", common_requirements)
        self.assertEqual(common_requirements.lower().count("tzdata"), 1)
        for requirements in (production_requirements, local_requirements):
            self.assertIn("-r requirements-common.txt", requirements)
            self.assertNotIn("tzdata", requirements.lower())
        self.assertIn(common_path, launcher.CORE_REQUIREMENT_INPUTS)
        self.assertIn("tzdata", launcher.CORE_IMPORTS)

    def test_installed_local_runtime_has_shanghai_timezone_data(self):
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import tzdata; from zoneinfo import ZoneInfo; import app.chips; "
                    "assert ZoneInfo('Asia/Shanghai').key == 'Asia/Shanghai'"
                ),
            ],
            cwd=PROJECT_ROOT,
            env={**os.environ, "PYTHONTZPATH": ""},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_wheel_workflow_builds_all_supported_windows_assets_and_manifest(self):
        workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "pymahjonggb-windows-wheels.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('python-version: ["3.10", "3.11", "3.12", "3.13"]', workflow)
        self.assertIn("architecture: x64", workflow)
        self.assertIn("SHA256SUMS.txt", workflow)
        self.assertIn(launcher.PYMAHJONG_RELEASE_TAG, workflow)
        self.assertIn("$wheel[0].Name -cne $expected", workflow)
        self.assertIn("import app.chips", workflow)
        self.assertIn("ZoneInfo('Asia/Shanghai')", workflow)

        cross_platform_workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "local-cross-platform.yml"
        ).read_text(encoding="utf-8")
        self.assertEqual(cross_platform_workflow.count("import app.chips"), 2)
        self.assertEqual(cross_platform_workflow.count("ZoneInfo('Asia/Shanghai')"), 2)


if __name__ == "__main__":
    unittest.main()
