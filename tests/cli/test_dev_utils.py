"""Tests for utility functions in penguiflow.cli.dev module."""

from __future__ import annotations

from pathlib import Path

import pytest

from penguiflow.cli.dev import CLIError, _ensure_ui_assets, _load_env_file


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
