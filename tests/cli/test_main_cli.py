"""Tests for penguiflow CLI main commands error handling."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    def test_accepts_with_rich_output_flag(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with patch("penguiflow.cli.new.run_new") as mock_run:
                mock_result = MagicMock()
                mock_result.success = True
                mock_run.return_value = mock_result
                result = runner.invoke(app, ["new", "test-agent", "--with-rich-output"])
                assert result.exit_code == 0
                assert mock_run.call_args.kwargs["with_rich_output"] is True


class TestGenerateCommand:
    def test_error_when_both_init_and_spec(self, tmp_path: Path) -> None:
        runner = CliRunner()
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text("agent:\n  name: test\n")
        result = runner.invoke(app, ["generate", "--init", "my-agent", "--spec", str(spec_file)])
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


class TestEvalCommand:
    def test_run_command_is_not_available(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["eval", "run", "--spec", "removed.spec.json"])
        assert result.exit_code != 0
        assert "No such command 'run'" in result.output

    def test_eval_help_lists_collect_and_evaluate_only(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["eval", "--help"])
        assert result.exit_code == 0
        assert "collect" in result.output
        assert "evaluate" in result.output
        assert "run" not in result.output

    def test_collect_nested_spec_autoloads_project_root_dotenv(self, tmp_path: Path, monkeypatch) -> None:
        runner = CliRunner()
        monkeypatch.chdir(tmp_path)
        env_key = "PENGUIFLOW_EVAL_COLLECT_CWD_BASE"
        (tmp_path / ".env").write_text(f"{env_key}=from_root\n", encoding="utf-8")

        spec_dir = tmp_path / "evals" / "native_avails_v1"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "collect.spec.json"
        spec_file.write_text(
            json.dumps(
                {
                    "project_root": ".",
                    "query_suite_path": "evals/native_avails_v1/query_suite.json",
                    "output_dir": "artifacts/eval/native_avails_v1/collect-local",
                    "session_id": "session-collect",
                    "dataset_tag": "dataset:demo",
                }
            ),
            encoding="utf-8",
        )

        os.environ.pop(env_key, None)
        with patch("penguiflow.evals.api.collect_and_export_traces") as mock_collect:
            mock_collect.return_value = {"trace_count": 1, "trace_path": "trace.jsonl"}
            result = runner.invoke(app, ["eval", "collect", "--spec", str(spec_file)])

        assert result.exit_code == 0
        assert os.environ.get(env_key) == "from_root"

    def test_evaluate_autoloads_project_root_dotenv(self, tmp_path: Path, monkeypatch) -> None:
        runner = CliRunner()
        monkeypatch.chdir(tmp_path)
        env_key = "PENGUIFLOW_EVAL_EVALUATE_CWD_BASE"
        (tmp_path / ".env").write_text(f"{env_key}=from_root\n", encoding="utf-8")

        spec_dir = tmp_path / "evals" / "native_avails_v1"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "evaluate.spec.json"
        spec_file.write_text(
            json.dumps(
                {
                    "project_root": ".",
                    "dataset_path": "artifacts/eval/native_avails_v1/run-local/bundle/dataset.jsonl",
                    "metric_spec": "demo.metric:metric",
                    "output_dir": "artifacts/eval/native_avails_v1/rerun",
                }
            ),
            encoding="utf-8",
        )

        os.environ.pop(env_key, None)
        with patch("penguiflow.evals.api.evaluate_dataset_from_spec_file") as mock_eval:
            mock_eval.return_value = {"mode": "baseline", "test_score": 0.9, "passed_threshold": True}
            result = runner.invoke(app, ["eval", "evaluate", "--spec", str(spec_file)])

        assert result.exit_code == 0
        assert os.environ.get(env_key) == "from_root"

    def test_eval_collect_rejects_env_file_option(self, tmp_path: Path) -> None:
        runner = CliRunner()
        spec_file = tmp_path / "collect.spec.json"
        spec_file.write_text("{}", encoding="utf-8")

        result = runner.invoke(app, ["eval", "collect", "--spec", str(spec_file), "--env-file", ".env"])

        assert result.exit_code != 0
        assert "No such option: --env-file" in result.output

    def test_eval_evaluate_rejects_env_file_option(self, tmp_path: Path) -> None:
        runner = CliRunner()
        spec_file = tmp_path / "evaluate.spec.json"
        spec_file.write_text("{}", encoding="utf-8")

        result = runner.invoke(app, ["eval", "evaluate", "--spec", str(spec_file), "--env-file", ".env"])

        assert result.exit_code != 0
        assert "No such option: --env-file" in result.output

    def test_evaluate_invokes_api_with_dataset(self, tmp_path: Path) -> None:
        runner = CliRunner()
        spec_file = tmp_path / "evaluate.spec.json"
        spec_file.write_text(
            """
{
  "dataset_path": "dataset.jsonl",
  "metric_spec": "demo.metric:metric",
  "output_dir": "artifacts/eval/rerun",
  "min_test_score": 0.8,
  "report_path": "reports/evaluate.json"
}
""".strip(),
            encoding="utf-8",
        )
        with patch("penguiflow.evals.api.evaluate_dataset_from_spec_file") as mock_eval:
            mock_eval.return_value = {"mode": "baseline", "test_score": 0.9, "passed_threshold": True}
            result = runner.invoke(app, ["eval", "evaluate", "--spec", str(spec_file)])

        assert result.exit_code == 0
        assert "mode: baseline" in result.output
        assert "test_score: 0.9" in result.output

    def test_collect_requires_spec_file(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["eval", "collect"])
        assert result.exit_code != 0

    def test_collect_uses_api_spec_loader_and_invokes_api(self, tmp_path: Path) -> None:
        runner = CliRunner()
        spec_file = tmp_path / "collect.spec.json"
        spec_file.write_text("{}", encoding="utf-8")
        with patch("penguiflow.evals.api.load_eval_collect_spec") as mock_load_spec:
            mock_spec = MagicMock()
            mock_spec.project_root = Path(".")
            mock_spec.query_suite_path = Path("query_suite.json")
            mock_spec.output_dir = Path("artifacts/eval/collect-local")
            mock_spec.session_id = "session-1"
            mock_spec.dataset_tag = "dataset:demo"
            mock_spec.agent_package = "demo_pkg"
            mock_spec.state_store_spec = None
            mock_load_spec.return_value = mock_spec
            with patch("penguiflow.evals.api.collect_and_export_traces") as mock_collect:
                mock_collect.return_value = {"trace_count": 1, "dataset_path": "dataset.jsonl"}
                result = runner.invoke(app, ["eval", "collect", "--spec", str(spec_file)])

        assert result.exit_code == 0
        assert "trace_count: 1" in result.output
        assert mock_load_spec.called
        assert mock_collect.call_args.kwargs["agent_package"] == "demo_pkg"
