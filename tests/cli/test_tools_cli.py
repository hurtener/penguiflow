from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from penguiflow.cli import app
from penguiflow.cli.tools import (
    ConnectResult,
    ToolsCLIError,
    parse_env_overrides,
    run_tools_connect,
    run_tools_list,
)


def test_tools_list_outputs_presets():
    runner = CliRunner()
    result = runner.invoke(app, ["tools", "list"])
    assert result.exit_code == 0
    assert "github" in result.output
    assert "transport" in result.output.lower()


def test_tools_connect_dry_run():
    runner = CliRunner()
    result = runner.invoke(app, ["tools", "connect", "github"])
    assert result.exit_code == 0
    assert "Dry run only" in result.output


def test_tools_connect_env_override_validation():
    runner = CliRunner()
    result = runner.invoke(app, ["tools", "connect", "github", "--env", "INVALID"])
    assert result.exit_code == 1
    assert "Invalid env override" in result.output


# --- Direct function tests for better coverage ---


def test_run_tools_connect_invalid_preset():
    """Test that invalid preset name raises ToolsCLIError."""
    with pytest.raises(ToolsCLIError) as exc_info:
        run_tools_connect("nonexistent_preset_xyz")
    assert "nonexistent_preset_xyz" in str(exc_info.value.message).lower() or "not found" in str(
        exc_info.value.message
    ).lower()


def test_run_tools_connect_with_env_overrides():
    """Test that env overrides are merged into config."""
    result = run_tools_connect(
        "github",
        discover=False,
        env_overrides={"MY_VAR": "my_value"},
    )
    assert result.success is True


def test_run_tools_connect_with_discover_mocked():
    """Test discover mode with mocked ToolNode."""
    mock_spec = MagicMock()
    mock_spec.name = "test_tool"

    mock_node = MagicMock()
    mock_node.connect = AsyncMock()
    mock_node.get_tools = MagicMock(return_value=[mock_spec])
    mock_node.close = AsyncMock()

    with patch("penguiflow.tools.ToolNode", return_value=mock_node):
        with patch("penguiflow.registry.ModelRegistry"):
            result = run_tools_connect(
                "github",
                discover=True,
                show_tools=False,
            )

    assert result.success is True
    assert result.discovered == 1
    mock_node.connect.assert_called_once()
    mock_node.close.assert_called_once()


def test_run_tools_connect_with_discover_and_show_tools():
    """Test discover mode with show_tools enabled."""
    mock_specs = [MagicMock(name=f"tool_{i}") for i in range(5)]
    for i, spec in enumerate(mock_specs):
        spec.name = f"tool_{i}"

    mock_node = MagicMock()
    mock_node.connect = AsyncMock()
    mock_node.get_tools = MagicMock(return_value=mock_specs)
    mock_node.close = AsyncMock()

    with patch("penguiflow.tools.ToolNode", return_value=mock_node):
        with patch("penguiflow.registry.ModelRegistry"):
            result = run_tools_connect(
                "github",
                discover=True,
                show_tools=True,
                max_tools=3,
            )

    assert result.success is True
    assert result.discovered == 5


def test_run_tools_connect_discover_with_many_tools_truncation():
    """Test that tool list is truncated when exceeding max_tools."""
    mock_specs = [MagicMock() for i in range(25)]
    for i, spec in enumerate(mock_specs):
        spec.name = f"tool_{i}"

    mock_node = MagicMock()
    mock_node.connect = AsyncMock()
    mock_node.get_tools = MagicMock(return_value=mock_specs)
    mock_node.close = AsyncMock()

    with patch("penguiflow.tools.ToolNode", return_value=mock_node):
        with patch("penguiflow.registry.ModelRegistry"):
            result = run_tools_connect(
                "github",
                discover=True,
                show_tools=True,
                max_tools=20,
            )

    assert result.success is True
    assert result.discovered == 25


# --- parse_env_overrides tests ---


def test_parse_env_overrides_valid():
    """Test parsing valid KEY=VALUE pairs."""
    result = parse_env_overrides(["FOO=bar", "BAZ=qux"])
    assert result == {"FOO": "bar", "BAZ": "qux"}


def test_parse_env_overrides_value_with_equals():
    """Test that values containing = are handled correctly."""
    result = parse_env_overrides(["CONNECTION=postgres://user:pass=123@host"])
    assert result == {"CONNECTION": "postgres://user:pass=123@host"}


def test_parse_env_overrides_empty_value():
    """Test that empty values are allowed."""
    result = parse_env_overrides(["EMPTY="])
    assert result == {"EMPTY": ""}


def test_parse_env_overrides_missing_equals():
    """Test that missing = raises error."""
    with pytest.raises(ToolsCLIError) as exc_info:
        parse_env_overrides(["INVALID"])
    assert "Invalid env override" in exc_info.value.message


def test_parse_env_overrides_empty_key():
    """Test that empty key raises error."""
    with pytest.raises(ToolsCLIError) as exc_info:
        parse_env_overrides(["=value"])
    assert "KEY cannot be empty" in exc_info.value.message


def test_tools_connect_invalid_preset_via_cli():
    """Test invalid preset via CLI."""
    runner = CliRunner()
    result = runner.invoke(app, ["tools", "connect", "nonexistent_xyz"])
    assert result.exit_code == 1


def test_tools_connect_valid_env_override_via_cli():
    """Test valid env override via CLI."""
    runner = CliRunner()
    result = runner.invoke(app, ["tools", "connect", "github", "--env", "MY_VAR=value"])
    assert result.exit_code == 0
    assert "Dry run only" in result.output
