import importlib.util
import json
import os
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
