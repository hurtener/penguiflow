"""Tests for utility functions in penguiflow.cli.dev module."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from penguiflow.cli.dev import (
    _MEMORY_BASE_URL_ALIASES,
    CLIError,
    _ensure_ui_assets,
    _load_env_file,
    _load_project_state_store,
    _memory_base_url_compat_env,
)


class TestLoadEnvFile:
    def test_returns_empty_dict_for_missing_file(self, tmp_path: Path) -> None:
        result = _load_env_file(tmp_path / "nonexistent.env")
        assert result == {}

    def test_parses_simple_key_value(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value\n")
        result = _load_env_file(env_file)
        assert result == {"KEY": "value"}

    def test_parses_multiple_values(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("KEY1=value1\nKEY2=value2\n")
        result = _load_env_file(env_file)
        assert result == {"KEY1": "value1", "KEY2": "value2"}

    def test_ignores_comments(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("# This is a comment\nKEY=value\n# Another comment\n")
        result = _load_env_file(env_file)
        assert result == {"KEY": "value"}

    def test_ignores_empty_lines(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("\n\nKEY=value\n\n")
        result = _load_env_file(env_file)
        assert result == {"KEY": "value"}

    def test_ignores_lines_without_equals(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("INVALID_LINE\nKEY=value\n")
        result = _load_env_file(env_file)
        assert result == {"KEY": "value"}

    def test_strips_double_quotes(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text('KEY="quoted value"\n')
        result = _load_env_file(env_file)
        assert result == {"KEY": "quoted value"}

    def test_strips_single_quotes(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("KEY='quoted value'\n")
        result = _load_env_file(env_file)
        assert result == {"KEY": "quoted value"}

    def test_handles_value_with_equals(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value=with=equals\n")
        result = _load_env_file(env_file)
        assert result == {"KEY": "value=with=equals"}

    def test_strips_whitespace(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("  KEY  =  value  \n")
        result = _load_env_file(env_file)
        assert result == {"KEY": "value"}


class TestCLIError:
    def test_stores_message(self) -> None:
        error = CLIError("Something went wrong")
        assert error.message == "Something went wrong"
        assert str(error) == "Something went wrong"

    def test_stores_hint(self) -> None:
        error = CLIError("Error", hint="Try this instead")
        assert error.hint == "Try this instead"

    def test_hint_defaults_to_none(self) -> None:
        error = CLIError("Error")
        assert error.hint is None


class TestEnsureUiAssets:
    def test_raises_when_dist_missing(self, tmp_path: Path) -> None:
        # Create playground_ui but no dist
        (tmp_path / "playground_ui").mkdir()
        with pytest.raises(CLIError) as exc_info:
            _ensure_ui_assets(tmp_path)
        assert "UI assets not found" in exc_info.value.message
        assert exc_info.value.hint is not None

    def test_passes_when_dist_exists(self, tmp_path: Path) -> None:
        # Create playground_ui/dist
        (tmp_path / "playground_ui" / "dist").mkdir(parents=True)
        # Should not raise
        _ensure_ui_assets(tmp_path)


class TestMemoryBaseUrlCompatibility:
    def test_temporarily_maps_memory_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MEMORY_BASE_URL", "http://localhost:6004")
        for key in _MEMORY_BASE_URL_ALIASES:
            monkeypatch.delenv(key, raising=False)
        with _memory_base_url_compat_env():
            for key in _MEMORY_BASE_URL_ALIASES:
                assert os.environ[key] == "http://localhost:6004"
        for key in _MEMORY_BASE_URL_ALIASES:
            assert key not in os.environ

    def test_preserves_existing_legacy_alias(self, monkeypatch: pytest.MonkeyPatch) -> None:
        alias_key = _MEMORY_BASE_URL_ALIASES[0]
        monkeypatch.setenv(alias_key, "http://platform:7000")
        monkeypatch.setenv("MEMORY_BASE_URL", "http://memory:6004")
        with _memory_base_url_compat_env():
            assert os.environ[alias_key] == "http://platform:7000"
        assert os.environ[alias_key] == "http://platform:7000"


class TestProjectStateStoreLoader:
    def test_loads_state_store_builder_from_project_src(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        agentiv_dir = tmp_path / "src" / "agentiv"
        agentiv_dir.mkdir(parents=True)
        (agentiv_dir / "__init__.py").write_text("", encoding="utf-8")
        (agentiv_dir / "state_store.py").write_text(
            "def build_agentiv_state_store_from_env():\n"
            "    return {'kind': 'state_store'}\n",
            encoding="utf-8",
        )
        sys.modules.pop("agentiv", None)
        sys.modules.pop("agentiv.state_store", None)
        sys.modules.pop("agentiv.state_store_enhanced", None)
        store = _load_project_state_store(tmp_path)
        assert store == {"kind": "state_store"}

    def test_builder_can_use_memory_base_url_only(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        alias_key = _MEMORY_BASE_URL_ALIASES[0]
        agentiv_dir = tmp_path / "src" / "agentiv"
        agentiv_dir.mkdir(parents=True)
        (agentiv_dir / "__init__.py").write_text("", encoding="utf-8")
        (agentiv_dir / "state_store.py").write_text(
            "import os\n"
            "def build_agentiv_state_store_from_env():\n"
            f"    val = os.getenv({alias_key!r})\n"
            "    if not val:\n"
            f"        raise ValueError('missing {alias_key}')\n"
            "    return {'kind': 'state_store', 'url': val}\n",
            encoding="utf-8",
        )
        monkeypatch.delenv(alias_key, raising=False)
        monkeypatch.setenv("MEMORY_BASE_URL", "http://memory-only:6004")
        sys.modules.pop("agentiv", None)
        sys.modules.pop("agentiv.state_store", None)
        sys.modules.pop("agentiv.state_store_enhanced", None)

        store = _load_project_state_store(tmp_path)
        assert store == {"kind": "state_store", "url": "http://memory-only:6004"}

    def test_builder_helper_ignoring_env_still_uses_memory_base_url(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        alias_key = _MEMORY_BASE_URL_ALIASES[0]
        agentiv_dir = tmp_path / "src" / "agentiv"
        agentiv_dir.mkdir(parents=True)
        (agentiv_dir / "__init__.py").write_text("", encoding="utf-8")
        (agentiv_dir / "state_store.py").write_text(
            "def from_env_or_dotenv(env_var_name: str, default: str) -> str:\n"
            "    # Simulate legacy helper that ignores os.environ locally.\n"
            "    return default\n"
            "def build_agentiv_state_store_from_env():\n"
            f"    val = from_env_or_dotenv({alias_key!r}, '')\n"
            "    if not val:\n"
            "        raise ValueError('missing alias url')\n"
            "    return {'kind': 'state_store', 'url': val}\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("MEMORY_BASE_URL", "http://memory-helper:6004")
        monkeypatch.delenv(alias_key, raising=False)
        sys.modules.pop("agentiv", None)
        sys.modules.pop("agentiv.state_store", None)
        sys.modules.pop("agentiv.state_store_enhanced", None)

        store = _load_project_state_store(tmp_path)
        assert store == {"kind": "state_store", "url": "http://memory-helper:6004"}

    def test_builder_helper_cwd_dotenv_value_does_not_override_memory_base_url(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        alias_key = _MEMORY_BASE_URL_ALIASES[0]
        agentiv_dir = tmp_path / "src" / "agentiv"
        agentiv_dir.mkdir(parents=True)
        (agentiv_dir / "__init__.py").write_text("", encoding="utf-8")
        (agentiv_dir / "state_store.py").write_text(
            "def from_env_or_dotenv(env_var_name: str, default: str) -> str:\n"
            "    if env_var_name == 'PLATFORM_URL':\n"
            "        return 'http://localhost:8000'\n"
            "    return default\n"
            "def build_agentiv_state_store_from_env():\n"
            "    val = from_env_or_dotenv('PLATFORM_URL', '')\n"
            "    if not val:\n"
            "        raise ValueError('missing alias url')\n"
            "    return {'kind': 'state_store', 'url': val}\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("MEMORY_BASE_URL", "http://memory-final:6004")
        monkeypatch.delenv(alias_key, raising=False)
        sys.modules.pop("agentiv", None)
        sys.modules.pop("agentiv.state_store", None)
        sys.modules.pop("agentiv.state_store_enhanced", None)

        store = _load_project_state_store(tmp_path)
        assert store == {"kind": "state_store", "url": "http://memory-final:6004"}
