"""Implementation of `penguiflow init`."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import NamedTuple

import click


class InitResult(NamedTuple):
    """Result of running `penguiflow init`."""

    success: bool
    created: list[str]
    skipped: list[str]
    errors: list[str]


class CLIError(Exception):
    """Base exception for CLI errors with user-friendly messages."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class PermissionDeniedError(CLIError):
    """Raised when file/directory operations fail due to permissions."""


class DirectoryCreationError(CLIError):
    """Raised when directory creation fails."""


class FileCopyError(CLIError):
    """Raised when file copy operation fails."""


def _iter_templates(
    include_launch: bool, include_tasks: bool, include_settings: bool
) -> Iterator[tuple[str, Traversable]]:
    """Yield template names and resource paths honoring skip flags."""
    base = resources.files("penguiflow.templates.vscode")
    for entry in base.iterdir():
        # Skip Python package files
        if entry.name.startswith("__"):
            continue
        if entry.name == "launch.json" and not include_launch:
            continue
        if entry.name == "tasks.json" and not include_tasks:
            continue
        if entry.name == "settings.json" and not include_settings:
            continue
        if entry.is_file():
            yield entry.name, entry


def _create_vscode_directory(vscode_dir: Path) -> None:
    """Create the .vscode directory with proper error handling."""
    try:
        vscode_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        raise PermissionDeniedError(
            f"Cannot create directory: {vscode_dir}",
            hint="Check that you have write permissions to this location.",
        ) from e
    except OSError as e:
        raise DirectoryCreationError(
            f"Failed to create directory: {vscode_dir}",
            hint=f"System error: {e}",
        ) from e


def _copy_template(source: Traversable, target: Path) -> None:
    """Copy a template file with proper error handling."""
    try:
        with resources.as_file(source) as source_path:
            shutil.copyfile(source_path, target)
    except PermissionError as e:
        raise PermissionDeniedError(
            f"Cannot write file: {target}",
            hint="Check that you have write permissions to the .vscode directory.",
        ) from e
    except OSError as e:
        raise FileCopyError(
            f"Failed to copy template to: {target}",
            hint=f"System error: {e}",
        ) from e


def run_init(
    *,
    force: bool = False,
    dry_run: bool = False,
    include_launch: bool = True,
    include_tasks: bool = True,
    include_settings: bool = True,
    output_dir: Path | None = None,
    quiet: bool = False,
) -> InitResult:
    """Create .vscode files for PenguiFlow development.

    Args:
        force: Overwrite existing files if True.
        dry_run: Only show what would be created, don't write files.
        include_launch: Include launch.json in output.
        include_tasks: Include tasks.json in output.
        include_settings: Include settings.json in output.
        output_dir: Base directory for .vscode (defaults to cwd).
        quiet: Suppress output messages if True.

    Returns:
        InitResult with success status and lists of created/skipped/error files.

    Raises:
        CLIError: On permission or I/O errors (with user-friendly messages).
    """
    vscode_dir = (output_dir or Path.cwd()) / ".vscode"
    templates = list(_iter_templates(include_launch, include_tasks, include_settings))

    created: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    if dry_run:
        if not quiet:
            click.echo("PenguiFlow init (dry run)")
            for name, _ in templates:
                click.echo(f"  would create: .vscode/{name}")
        return InitResult(success=True, created=[], skipped=[], errors=[])

    # Create .vscode directory
    _create_vscode_directory(vscode_dir)

    # Copy each template
    for name, path in templates:
        target = vscode_dir / name
        if target.exists() and not force:
            skipped.append(target.as_posix())
            continue

        try:
            _copy_template(path, target)
            created.append(target.as_posix())
        except CLIError:
            # Re-raise CLI errors to be handled by caller
            raise
        except Exception as e:
            # Catch unexpected errors and continue with other files
            errors.append(f"{target.as_posix()}: {e}")

    # Output results
    if not quiet:
        for item in created:
            click.echo(f"✓ Created {item}")
        for item in skipped:
            click.echo(f"⚠ Skipped {item} (exists, use --force to overwrite)")
        for item in errors:
            click.echo(f"✗ Error: {item}", err=True)

        if created or skipped:
            click.echo("\nPenguiFlow development environment ready!")
            click.echo("Open this folder in VS Code for snippets and debugging support.")

    success = len(errors) == 0
    return InitResult(success=success, created=created, skipped=skipped, errors=errors)
