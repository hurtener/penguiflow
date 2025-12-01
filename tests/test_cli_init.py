"""Tests for the PenguiFlow CLI init command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from click.testing import CliRunner

from penguiflow.cli import app
from penguiflow.cli.init import (
    CLIError,
    DirectoryCreationError,
    InitResult,
    PermissionDeniedError,
    run_init,
)

# Expected template files that should be created
EXPECTED_TEMPLATES = {
    "launch.json",
    "tasks.json",
    "settings.json",
    "penguiflow.code-snippets",
}


class TestRunInitFunction:
    """Tests for the run_init() function directly."""

    def test_creates_vscode_directory(self, tmp_path: Path) -> None:
        """run_init creates .vscode directory."""
        result = run_init(output_dir=tmp_path, quiet=True)

        assert result.success
        assert (tmp_path / ".vscode").is_dir()

    def test_creates_all_template_files(self, tmp_path: Path) -> None:
        """run_init creates all expected template files."""
        result = run_init(output_dir=tmp_path, quiet=True)

        assert result.success
        vscode_dir = tmp_path / ".vscode"
        created_files = {p.name for p in vscode_dir.iterdir()}

        # Should have all expected templates
        assert created_files == EXPECTED_TEMPLATES

    def test_returns_created_files_list(self, tmp_path: Path) -> None:
        """run_init returns list of created files."""
        result = run_init(output_dir=tmp_path, quiet=True)

        assert result.success
        assert len(result.created) == len(EXPECTED_TEMPLATES)
        assert all(".vscode/" in f for f in result.created)

    def test_dry_run_does_not_create_files(self, tmp_path: Path) -> None:
        """--dry-run shows output but creates no files."""
        result = run_init(output_dir=tmp_path, dry_run=True, quiet=True)

        assert result.success
        assert not (tmp_path / ".vscode").exists()
        assert result.created == []
        assert result.skipped == []

    def test_skips_existing_files_without_force(self, tmp_path: Path) -> None:
        """Existing files are skipped without --force."""
        # Create .vscode with one existing file
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()
        existing_file = vscode_dir / "launch.json"
        existing_file.write_text('{"existing": true}')

        result = run_init(output_dir=tmp_path, quiet=True)

        assert result.success
        # launch.json should be skipped
        assert any("launch.json" in s for s in result.skipped)
        # Other files should be created
        assert len(result.created) == len(EXPECTED_TEMPLATES) - 1
        # Existing file should not be modified
        assert json.loads(existing_file.read_text()) == {"existing": True}

    def test_force_overwrites_existing_files(self, tmp_path: Path) -> None:
        """--force overwrites existing files."""
        # Create .vscode with one existing file
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()
        existing_file = vscode_dir / "launch.json"
        existing_file.write_text('{"existing": true}')

        result = run_init(output_dir=tmp_path, force=True, quiet=True)

        assert result.success
        assert len(result.skipped) == 0
        # All files should be created/overwritten
        assert len(result.created) == len(EXPECTED_TEMPLATES)
        # File should be overwritten with template content
        content = json.loads(existing_file.read_text())
        assert "existing" not in content

    def test_no_launch_skips_launch_json(self, tmp_path: Path) -> None:
        """--no-launch skips launch.json generation."""
        result = run_init(output_dir=tmp_path, include_launch=False, quiet=True)

        assert result.success
        assert not (tmp_path / ".vscode" / "launch.json").exists()
        assert len(result.created) == len(EXPECTED_TEMPLATES) - 1

    def test_no_tasks_skips_tasks_json(self, tmp_path: Path) -> None:
        """--no-tasks skips tasks.json generation."""
        result = run_init(output_dir=tmp_path, include_tasks=False, quiet=True)

        assert result.success
        assert not (tmp_path / ".vscode" / "tasks.json").exists()
        assert len(result.created) == len(EXPECTED_TEMPLATES) - 1

    def test_no_settings_skips_settings_json(self, tmp_path: Path) -> None:
        """--no-settings skips settings.json generation."""
        result = run_init(output_dir=tmp_path, include_settings=False, quiet=True)

        assert result.success
        assert not (tmp_path / ".vscode" / "settings.json").exists()
        assert len(result.created) == len(EXPECTED_TEMPLATES) - 1

    def test_combined_skip_flags(self, tmp_path: Path) -> None:
        """Multiple skip flags work together."""
        result = run_init(
            output_dir=tmp_path,
            include_launch=False,
            include_tasks=False,
            include_settings=False,
            quiet=True,
        )

        assert result.success
        vscode_dir = tmp_path / ".vscode"
        created_files = {p.name for p in vscode_dir.iterdir()} - {"__init__.py"}
        # Only snippets should be created
        assert created_files == {"penguiflow.code-snippets"}

    def test_quiet_suppresses_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--quiet suppresses all output."""
        run_init(output_dir=tmp_path, quiet=True)

        captured = capsys.readouterr()
        assert captured.out == ""

    def test_non_quiet_shows_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Without --quiet, output is shown."""
        run_init(output_dir=tmp_path, quiet=False)

        captured = capsys.readouterr()
        assert "Created" in captured.out
        assert "PenguiFlow development environment ready!" in captured.out

    def test_created_files_are_valid_json(self, tmp_path: Path) -> None:
        """All created JSON files are valid."""
        run_init(output_dir=tmp_path, quiet=True)

        vscode_dir = tmp_path / ".vscode"
        json_files = ["launch.json", "tasks.json", "settings.json"]

        for filename in json_files:
            filepath = vscode_dir / filename
            content = filepath.read_text()
            # Should not raise
            parsed = json.loads(content)
            assert isinstance(parsed, dict)

    def test_snippets_file_is_valid_json(self, tmp_path: Path) -> None:
        """Code snippets file is valid JSON."""
        run_init(output_dir=tmp_path, quiet=True)

        snippets_file = tmp_path / ".vscode" / "penguiflow.code-snippets"
        content = snippets_file.read_text()
        parsed = json.loads(content)

        # Should have snippet definitions
        assert isinstance(parsed, dict)
        assert len(parsed) > 0


class TestRunInitErrorHandling:
    """Tests for error handling in run_init()."""

    def test_permission_denied_on_directory_creation(self, tmp_path: Path) -> None:
        """PermissionDeniedError raised when directory creation fails."""
        with mock.patch("pathlib.Path.mkdir") as mock_mkdir:
            mock_mkdir.side_effect = PermissionError("Permission denied")

            with pytest.raises(PermissionDeniedError) as exc_info:
                run_init(output_dir=tmp_path, quiet=True)

            assert "Cannot create directory" in exc_info.value.message
            assert exc_info.value.hint is not None

    def test_oserror_on_directory_creation(self, tmp_path: Path) -> None:
        """DirectoryCreationError raised on other OS errors."""
        with mock.patch("pathlib.Path.mkdir") as mock_mkdir:
            mock_mkdir.side_effect = OSError("Disk full")

            with pytest.raises(DirectoryCreationError) as exc_info:
                run_init(output_dir=tmp_path, quiet=True)

            assert "Failed to create directory" in exc_info.value.message

    def test_permission_denied_on_file_copy(self, tmp_path: Path) -> None:
        """PermissionDeniedError raised when file copy fails."""
        # Create directory first
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()

        with mock.patch("shutil.copyfile") as mock_copy:
            mock_copy.side_effect = PermissionError("Permission denied")

            with pytest.raises(PermissionDeniedError) as exc_info:
                run_init(output_dir=tmp_path, quiet=True)

            assert "Cannot write file" in exc_info.value.message

    def test_cli_error_has_hint(self) -> None:
        """CLIError includes hint for user guidance."""
        error = CLIError("Test error", hint="Try this instead")

        assert error.message == "Test error"
        assert error.hint == "Try this instead"
        assert str(error) == "Test error"


class TestInitResultNamedTuple:
    """Tests for InitResult data structure."""

    def test_init_result_attributes(self) -> None:
        """InitResult has expected attributes."""
        result = InitResult(
            success=True,
            created=["file1", "file2"],
            skipped=["file3"],
            errors=[],
        )

        assert result.success is True
        assert result.created == ["file1", "file2"]
        assert result.skipped == ["file3"]
        assert result.errors == []

    def test_init_result_is_immutable(self) -> None:
        """InitResult is a NamedTuple (immutable)."""
        result = InitResult(success=True, created=[], skipped=[], errors=[])

        with pytest.raises(AttributeError):
            result.success = False  # type: ignore[misc]


class TestCliRunner:
    """Tests using Click's CliRunner for CLI integration."""

    def test_init_command_success(self, tmp_path: Path) -> None:
        """penguiflow init succeeds and creates files."""
        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(app, ["init"])

            assert result.exit_code == 0
            assert "Created" in result.output
            assert "PenguiFlow development environment ready!" in result.output
            assert Path(".vscode").is_dir()

    def test_init_dry_run(self, tmp_path: Path) -> None:
        """penguiflow init --dry-run shows but doesn't create."""
        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(app, ["init", "--dry-run"])

            assert result.exit_code == 0
            assert "dry run" in result.output.lower()
            assert "would create" in result.output
            assert not Path(".vscode").exists()

    def test_init_force_flag(self, tmp_path: Path) -> None:
        """penguiflow init --force overwrites existing files."""
        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            # First init
            runner.invoke(app, ["init"])
            # Modify a file
            Path(".vscode/launch.json").write_text('{"modified": true}')

            # Second init with --force
            result = runner.invoke(app, ["init", "--force"])

            assert result.exit_code == 0
            # File should be overwritten
            content = json.loads(Path(".vscode/launch.json").read_text())
            assert "modified" not in content

    def test_init_quiet_flag(self, tmp_path: Path) -> None:
        """penguiflow init --quiet suppresses output."""
        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(app, ["init", "--quiet"])

            assert result.exit_code == 0
            assert result.output == ""

    def test_init_quiet_short_flag(self, tmp_path: Path) -> None:
        """penguiflow init -q works as shorthand for --quiet."""
        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(app, ["init", "-q"])

            assert result.exit_code == 0
            assert result.output == ""

    def test_init_no_launch_flag(self, tmp_path: Path) -> None:
        """penguiflow init --no-launch skips launch.json."""
        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(app, ["init", "--no-launch"])

            assert result.exit_code == 0
            assert not Path(".vscode/launch.json").exists()
            assert Path(".vscode/tasks.json").exists()

    def test_init_no_tasks_flag(self, tmp_path: Path) -> None:
        """penguiflow init --no-tasks skips tasks.json."""
        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(app, ["init", "--no-tasks"])

            assert result.exit_code == 0
            assert not Path(".vscode/tasks.json").exists()
            assert Path(".vscode/launch.json").exists()

    def test_init_no_settings_flag(self, tmp_path: Path) -> None:
        """penguiflow init --no-settings skips settings.json."""
        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(app, ["init", "--no-settings"])

            assert result.exit_code == 0
            assert not Path(".vscode/settings.json").exists()
            assert Path(".vscode/launch.json").exists()

    def test_init_combined_flags(self, tmp_path: Path) -> None:
        """Multiple flags work together."""
        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(
                app, ["init", "--no-launch", "--no-tasks", "--quiet"]
            )

            assert result.exit_code == 0
            assert result.output == ""
            assert not Path(".vscode/launch.json").exists()
            assert not Path(".vscode/tasks.json").exists()
            assert Path(".vscode/settings.json").exists()

    def test_init_skips_existing_without_force(self, tmp_path: Path) -> None:
        """Existing files are skipped and warning shown."""
        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            # First init
            runner.invoke(app, ["init", "--quiet"])

            # Second init without --force
            result = runner.invoke(app, ["init"])

            assert result.exit_code == 0
            assert "Skipped" in result.output
            assert "use --force to overwrite" in result.output

    def test_init_help_option(self) -> None:
        """penguiflow init --help shows usage information."""
        runner = CliRunner()
        result = runner.invoke(app, ["init", "--help"])

        assert result.exit_code == 0
        assert "Initialize PenguiFlow" in result.output
        assert "--force" in result.output
        assert "--dry-run" in result.output
        assert "--quiet" in result.output
        assert "--no-launch" in result.output
        assert "--no-tasks" in result.output
        assert "--no-settings" in result.output


class TestCliMain:
    """Tests for the main CLI app structure."""

    def test_app_help(self) -> None:
        """penguiflow --help shows app help."""
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        assert "PenguiFlow CLI" in result.output
        assert "init" in result.output

    def test_app_version(self) -> None:
        """penguiflow --version shows version."""
        runner = CliRunner()
        result = runner.invoke(app, ["--version"])

        assert result.exit_code == 0
        # Version should be displayed (format may vary)
        assert "version" in result.output.lower() or "." in result.output

    def test_unknown_command(self) -> None:
        """Unknown command shows error."""
        runner = CliRunner()
        result = runner.invoke(app, ["unknown-command"])

        assert result.exit_code != 0
        assert "No such command" in result.output or "Error" in result.output


class TestCliErrorHandling:
    """Tests for CLI error handling and exit codes."""

    def test_permission_error_shows_message(self, tmp_path: Path) -> None:
        """Permission errors show user-friendly message."""
        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            with mock.patch("pathlib.Path.mkdir") as mock_mkdir:
                mock_mkdir.side_effect = PermissionError("Permission denied")

                result = runner.invoke(app, ["init"])

                assert result.exit_code == 1
                assert "Cannot create directory" in result.output

    def test_permission_error_shows_hint(self, tmp_path: Path) -> None:
        """Permission errors include helpful hint."""
        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            with mock.patch("pathlib.Path.mkdir") as mock_mkdir:
                mock_mkdir.side_effect = PermissionError("Permission denied")

                result = runner.invoke(app, ["init"])

                assert "Hint:" in result.output
