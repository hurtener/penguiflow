"""Tests for penguiflow/cli/tools.py helper functions."""

import pytest

from penguiflow.cli.tools import (
    ConnectResult,
    ToolsCLIError,
    parse_env_overrides,
    run_tools_list,
)


def test_tools_cli_error_with_hint():
    """ToolsCLIError should store message and hint."""
    exc = ToolsCLIError("Something failed", hint="Try doing X")
    assert exc.message == "Something failed"
    assert exc.hint == "Try doing X"


def test_tools_cli_error_without_hint():
    """ToolsCLIError should work without hint."""
    exc = ToolsCLIError("Something failed")
    assert exc.message == "Something failed"
    assert exc.hint is None


def test_connect_result_defaults():
    """ConnectResult should have correct defaults."""
    result = ConnectResult(success=True)
    assert result.success is True
    assert result.discovered == 0


def test_connect_result_with_discovered():
    """ConnectResult should store discovered count."""
    result = ConnectResult(success=True, discovered=5)
    assert result.discovered == 5


def test_parse_env_overrides_valid():
    """parse_env_overrides should parse KEY=VALUE pairs."""
    result = parse_env_overrides(["FOO=bar", "BAZ=qux"])
    assert result == {"FOO": "bar", "BAZ": "qux"}


def test_parse_env_overrides_with_equals_in_value():
    """parse_env_overrides should handle = in value."""
    result = parse_env_overrides(["KEY=value=with=equals"])
    assert result == {"KEY": "value=with=equals"}


def test_parse_env_overrides_empty_value():
    """parse_env_overrides should handle empty values."""
    result = parse_env_overrides(["KEY="])
    assert result == {"KEY": ""}


def test_parse_env_overrides_empty_list():
    """parse_env_overrides should handle empty list."""
    result = parse_env_overrides([])
    assert result == {}


def test_parse_env_overrides_missing_equals():
    """parse_env_overrides should raise for missing =."""
    with pytest.raises(ToolsCLIError) as exc_info:
        parse_env_overrides(["INVALID"])
    assert "Invalid env override" in exc_info.value.message
    assert "Expected KEY=VALUE" in exc_info.value.message


def test_parse_env_overrides_empty_key():
    """parse_env_overrides should raise for empty key."""
    with pytest.raises(ToolsCLIError) as exc_info:
        parse_env_overrides(["=value"])
    assert "KEY cannot be empty" in exc_info.value.message


def test_run_tools_list_returns_presets(capsys):
    """run_tools_list should output available presets."""
    run_tools_list()
    captured = capsys.readouterr()
    assert "github" in captured.out
    assert "TRANSPORT" in captured.out or "transport" in captured.out.lower()
