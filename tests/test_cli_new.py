"""Tests for the penguiflow CLI new command and templates."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from penguiflow.cli import app
from penguiflow.cli.init import CLIError
from penguiflow.cli.new import TemplateNotFoundError, run_new


def _project_paths(base: Path, name: str) -> tuple[Path, Path]:
    project_dir = base / name
    package_dir = project_dir / "src" / name.replace("-", "_")
    return project_dir, package_dir


@pytest.mark.parametrize(
    "template",
    ["minimal", "react", "parallel", "flow", "controller", "rag_server", "wayfinder", "analyst", "enterprise"],
)
def test_run_new_creates_expected_files(tmp_path: Path, template: str) -> None:
    name = f"{template}-agent"
    result = run_new(name=name, template=template, output_dir=tmp_path, quiet=True)
    assert result.success

    project_dir, package_dir = _project_paths(tmp_path, name)
    assert (project_dir / "pyproject.toml").exists()
    assert (project_dir / "README.md").exists()
    assert package_dir.exists()


def test_run_new_dry_run_does_not_touch_disk(tmp_path: Path) -> None:
    name = "dry-run-agent"
    result = run_new(
        name=name,
        template="minimal",
        output_dir=tmp_path,
        dry_run=True,
        quiet=True,
    )
    assert result.success
    assert not (tmp_path / name).exists()


def test_unknown_template_raises(tmp_path: Path) -> None:
    with pytest.raises(TemplateNotFoundError):
        run_new(name="unknown", template="nope", output_dir=tmp_path, quiet=True)


@pytest.mark.parametrize("template", ["flow", "controller"])
def test_mlflow_requires_planner_template(tmp_path: Path, template: str) -> None:
    with pytest.raises(CLIError, match="planner-backed"):
        run_new(
            name="unsupported",
            template=template,
            output_dir=tmp_path,
            quiet=True,
            with_mlflow=True,
        )


def test_cli_new_command_creates_project(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["new", "cli-agent", "--template", "minimal", "--output-dir", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert (tmp_path / "cli-agent" / "pyproject.toml").exists()


@pytest.mark.parametrize(
    "template",
    ["minimal", "react", "parallel", "rag_server", "wayfinder", "analyst", "enterprise"],
)
def test_run_new_adds_mlflow_only_when_enabled(tmp_path: Path, template: str) -> None:
    enabled = run_new(
        name=f"{template}-mlflow",
        template=template,
        output_dir=tmp_path,
        quiet=True,
        with_mlflow=True,
    )
    disabled = run_new(
        name=f"{template}-plain",
        template=template,
        output_dir=tmp_path,
        quiet=True,
    )
    assert enabled.success and disabled.success

    enabled_project, enabled_package = _project_paths(tmp_path, f"{template}-mlflow")
    disabled_project, disabled_package = _project_paths(tmp_path, f"{template}-plain")
    assert "mlflow-tracing>=3.8,<4" in (enabled_project / "pyproject.toml").read_text()
    assert "penguiflow:mlflow:dependency" not in (enabled_project / "pyproject.toml").read_text()
    assert "trace_agent_run" in (enabled_package / "telemetry.py").read_text()
    assert "trace_agent_run" in (enabled_package / "orchestrator.py").read_text()
    enabled_env = (enabled_project / ".env.example").read_text()
    assert "MLFLOW_TRACKING_URI=http://localhost:5000" in enabled_env
    assert "# MLFLOW_TRACKING_URI=databricks://<profile>" in enabled_env
    assert "mlflow" not in (disabled_project / "pyproject.toml").read_text().lower()
    assert "mlflow" not in (disabled_package / "telemetry.py").read_text().lower()
    assert "MLFLOW_TRACKING_URI" not in (disabled_project / ".env.example").read_text()


@pytest.mark.parametrize(
    "template",
    ["minimal", "react", "parallel", "flow", "controller", "rag_server", "wayfinder", "analyst", "enterprise"],
)
def test_generated_project_tests_pass(tmp_path: Path, template: str) -> None:
    name = f"{template}-proj"
    project_dir, _ = _project_paths(tmp_path, name)
    run_new(name=name, template=template, output_dir=tmp_path, quiet=True, force=True)

    env = os.environ.copy()
    repo_root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo_root), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=project_dir,
        env=env,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:  # pragma: no cover - captured for debugging
        print(completed.stdout)
        print(completed.stderr, file=sys.stderr)
    assert completed.returncode == 0


@pytest.mark.parametrize(
    ("template", "flags"),
    [
        ("minimal", {"with_streaming": True, "with_hitl": True, "with_mlflow": True}),
        ("react", {"with_streaming": True, "with_a2a": True, "with_rich_output": True, "with_mlflow": True}),
        ("parallel", {"no_memory": True, "with_mlflow": True}),
        ("flow", {"with_streaming": True, "with_a2a": True}),
        ("controller", {"with_streaming": True, "no_memory": True}),
        ("rag_server", {"with_streaming": True, "with_mlflow": True}),
        ("wayfinder", {"with_hitl": True, "with_mlflow": True}),
        ("analyst", {"with_a2a": True, "with_mlflow": True}),
        ("enterprise", {"with_streaming": True, "with_hitl": True, "with_mlflow": True}),
    ],
)
def test_generated_project_tests_pass_with_flags(
    tmp_path: Path,
    template: str,
    flags: dict[str, bool],
) -> None:
    name = f"{template}-flags-proj"
    project_dir, _ = _project_paths(tmp_path, name)
    result = run_new(
        name=name,
        template=template,
        output_dir=tmp_path,
        quiet=True,
        force=True,
        **flags,
    )

    assert result.success

    env = os.environ.copy()
    repo_root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo_root), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=project_dir,
        env=env,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:  # pragma: no cover - captured for debugging
        print(completed.stdout)
        print(completed.stderr, file=sys.stderr)
    assert completed.returncode == 0
