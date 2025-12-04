"""Tests for penguiflow CLI main commands error handling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from penguiflow.cli import app
from penguiflow.cli.dev import CLIError as DevCLIError
from penguiflow.cli.init import CLIError


class TestInitCommand:
    def test_exits_on_cli_error(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with patch("penguiflow.cli.init.run_init") as mock_run:
                mock_run.side_effect = CLIError("Init failed", hint="Check permissions")
                result = runner.invoke(app, ["init"])
                assert result.exit_code == 1
                assert "Init failed" in result.output
                assert "Check permissions" in result.output

    def test_exits_on_cli_error_without_hint(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with patch("penguiflow.cli.init.run_init") as mock_run:
                mock_run.side_effect = CLIError("Init failed")
                result = runner.invoke(app, ["init"])
                assert result.exit_code == 1
                assert "Init failed" in result.output

    def test_exits_when_result_not_success(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with patch("penguiflow.cli.init.run_init") as mock_run:
                mock_result = MagicMock()
                mock_result.success = False
                mock_run.return_value = mock_result
                result = runner.invoke(app, ["init"])
                assert result.exit_code == 1


class TestDevCommand:
    def test_exits_on_dev_cli_error(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with patch("penguiflow.cli.main.run_dev") as mock_run:
            mock_run.side_effect = DevCLIError("Dev failed", hint="Install deps")
            result = runner.invoke(app, ["dev", "--project-root", str(tmp_path)])
            assert result.exit_code == 1
            assert "Dev failed" in result.output
            assert "Install deps" in result.output

    def test_exits_on_dev_cli_error_without_hint(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with patch("penguiflow.cli.main.run_dev") as mock_run:
            mock_run.side_effect = DevCLIError("Dev failed")
            result = runner.invoke(app, ["dev", "--project-root", str(tmp_path)])
            assert result.exit_code == 1
            assert "Dev failed" in result.output


class TestNewCommand:
    def test_exits_on_cli_error(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with patch("penguiflow.cli.new.run_new") as mock_run:
                mock_run.side_effect = CLIError("New failed", hint="Invalid name")
                result = runner.invoke(app, ["new", "test-agent"])
                assert result.exit_code == 1
                assert "New failed" in result.output
                assert "Invalid name" in result.output

    def test_exits_on_cli_error_without_hint(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with patch("penguiflow.cli.new.run_new") as mock_run:
                mock_run.side_effect = CLIError("New failed")
                result = runner.invoke(app, ["new", "test-agent"])
                assert result.exit_code == 1
                assert "New failed" in result.output

    def test_exits_when_result_not_success(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with patch("penguiflow.cli.new.run_new") as mock_run:
                mock_result = MagicMock()
                mock_result.success = False
                mock_run.return_value = mock_result
                result = runner.invoke(app, ["new", "test-agent"])
                assert result.exit_code == 1


class TestGenerateCommand:
    def test_error_when_both_init_and_spec(self, tmp_path: Path) -> None:
        runner = CliRunner()
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text("agent:\n  name: test\n")
        result = runner.invoke(
            app, ["generate", "--init", "my-agent", "--spec", str(spec_file)]
        )
        assert result.exit_code == 1
        assert "Cannot use --init and --spec together" in result.output

    def test_error_when_init_with_dry_run(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["generate", "--init", "my-agent", "--dry-run"])
        assert result.exit_code == 1
        assert "--dry-run is not supported with --init" in result.output

    def test_error_when_neither_init_nor_spec(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["generate"])
        assert result.exit_code == 1
        assert "Either --init or --spec is required" in result.output
        assert "Use --init to create a new spec workspace" in result.output

    def test_init_exits_on_cli_error(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with patch("penguiflow.cli.generate.run_init_spec") as mock_run:
                mock_run.side_effect = CLIError("Init spec failed", hint="Try again")
                result = runner.invoke(app, ["generate", "--init", "my-agent"])
                assert result.exit_code == 1
                assert "Init spec failed" in result.output
                assert "Try again" in result.output

    def test_init_exits_on_cli_error_without_hint(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with patch("penguiflow.cli.generate.run_init_spec") as mock_run:
                mock_run.side_effect = CLIError("Init spec failed")
                result = runner.invoke(app, ["generate", "--init", "my-agent"])
                assert result.exit_code == 1
                assert "Init spec failed" in result.output

    def test_init_exits_when_result_not_success(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with patch("penguiflow.cli.generate.run_init_spec") as mock_run:
                mock_result = MagicMock()
                mock_result.success = False
                mock_run.return_value = mock_result
                result = runner.invoke(app, ["generate", "--init", "my-agent"])
                assert result.exit_code == 1

    def test_generate_exits_when_result_not_success(self, tmp_path: Path) -> None:
        runner = CliRunner()
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text("agent:\n  name: test\n")
        with patch("penguiflow.cli.generate.run_generate") as mock_run:
            mock_result = MagicMock()
            mock_result.success = False
            mock_run.return_value = mock_result
            result = runner.invoke(app, ["generate", "--spec", str(spec_file)])
            assert result.exit_code == 1

    def test_generate_exits_on_cli_error(self, tmp_path: Path) -> None:
        runner = CliRunner()
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text("agent:\n  name: test\n")
        with patch("penguiflow.cli.generate.run_generate") as mock_run:
            mock_run.side_effect = CLIError("Generate failed", hint="Check spec")
            result = runner.invoke(app, ["generate", "--spec", str(spec_file)])
            assert result.exit_code == 1
            assert "Generate failed" in result.output
            assert "Check spec" in result.output

    def test_generate_exits_on_cli_error_without_hint(self, tmp_path: Path) -> None:
        runner = CliRunner()
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text("agent:\n  name: test\n")
        with patch("penguiflow.cli.generate.run_generate") as mock_run:
            mock_run.side_effect = CLIError("Generate failed")
            result = runner.invoke(app, ["generate", "--spec", str(spec_file)])
            assert result.exit_code == 1
            assert "Generate failed" in result.output
