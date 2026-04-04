# tests/test_packaging.py
"""包结构和入口点的测试。"""

import importlib
import subprocess
import sys

import pytest


class TestPackageImports:
    """验证所有子包能正常导入。"""

    @pytest.mark.parametrize("module", [
        "ultrabot",
        "ultrabot.agent",
        "ultrabot.agent.auxiliary",
        "ultrabot.agent.context_compressor",
        "ultrabot.agent.delegate",
        "ultrabot.agent.title_generator",
        "ultrabot.chunking",
        "ultrabot.chunking.chunker",
        "ultrabot.config.doctor",
        "ultrabot.config.migrations",
        "ultrabot.cli.themes",
        "ultrabot.providers.prompt_cache",
        "ultrabot.providers.auth_rotation",
        "ultrabot.security.injection_detector",
        "ultrabot.security.redact",
        "ultrabot.usage.tracker",
        "ultrabot.channels.group_activation",
        "ultrabot.channels.pairing",
        "ultrabot.skills.manager",
    ])
    def test_import(self, module: str):
        """每个模块应能无错误导入。"""
        importlib.import_module(module)


class TestVersion:
    def test_version_exists(self):
        from ultrabot import __version__
        assert __version__
        # 应该是类似 semver 的字符串
        parts = __version__.split(".")
        assert len(parts) >= 2

    def test_version_matches_pyproject(self):
        from ultrabot import __version__
        # 从 pyproject.toml 读取版本
        import tomllib
        from pathlib import Path
        toml_path = Path(__file__).parent.parent / "pyproject.toml"
        if toml_path.exists():
            with open(toml_path, "rb") as f:
                data = tomllib.load(f)
            assert __version__ == data["project"]["version"]


class TestEntryPoint:
    def test_ultrabot_help(self):
        """`ultrabot --help` 命令应该可以正常工作。"""
        result = subprocess.run(
            [sys.executable, "-m", "ultrabot", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "ultrabot" in result.stdout.lower()

    def test_ultrabot_version(self):
        """`ultrabot --version` 命令应该输出版本号。"""
        result = subprocess.run(
            [sys.executable, "-m", "ultrabot", "--version"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "0.1.0" in result.stdout


class TestPackageStructure:
    def test_all_init_files_exist(self):
        """每个子目录都应该有一个 __init__.py。"""
        from pathlib import Path
        root = Path(__file__).parent.parent / "ultrabot"
        for subdir in root.iterdir():
            if subdir.is_dir() and not subdir.name.startswith(("_", ".")):
                init_file = subdir / "__init__.py"
                assert init_file.exists(), f"Missing __init__.py in {subdir}"
