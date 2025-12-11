from click.testing import CliRunner

from penguiflow.cli import app


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
