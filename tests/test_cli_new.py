"""Tests for the penguiflow CLI new command and templates."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from penguiflow.cli import app
from penguiflow.cli.new import TemplateNotFoundError, run_new


def _project_paths(base: Path, name: str) -> tuple[Path, Path]:
    project_dir = base / name
    package_dir = project_dir / "src" / name.replace("-", "_")
    return project_dir, package_dir


@pytest.mark.parametrize(
    "template",
    ["minimal", "react", "parallel", "flow", "controller", "lighthouse", "wayfinder", "analyst", "enterprise"],
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
    ["minimal", "react", "parallel", "flow", "controller", "lighthouse", "wayfinder", "analyst", "enterprise"],
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
        ("minimal", {"with_streaming": True, "with_hitl": True}),
        ("react", {"with_streaming": True, "with_a2a": True}),
        ("parallel", {"no_memory": True}),
        ("flow", {"with_streaming": True, "with_a2a": True}),
        ("controller", {"with_streaming": True, "no_memory": True}),
        ("lighthouse", {"with_streaming": True}),
        ("wayfinder", {"with_hitl": True}),
        ("analyst", {"with_a2a": True}),
        ("enterprise", {"with_streaming": True, "with_hitl": True}),
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
