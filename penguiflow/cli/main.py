"""PenguiFlow command-line interface."""

from __future__ import annotations

import sys

import click


@click.group()
@click.version_option()
def app() -> None:
    """PenguiFlow CLI - bootstrap and manage agent projects."""


@app.command()
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing files if they already exist.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show which files would be created without writing them.",
)
@click.option(
    "--no-launch",
    is_flag=True,
    help="Skip generating .vscode/launch.json.",
)
@click.option(
    "--no-tasks",
    is_flag=True,
    help="Skip generating .vscode/tasks.json.",
)
@click.option(
    "--no-settings",
    is_flag=True,
    help="Skip generating .vscode/settings.json.",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress output messages (useful for scripting).",
)
def init(
    force: bool,
    dry_run: bool,
    no_launch: bool,
    no_tasks: bool,
    no_settings: bool,
    quiet: bool,
) -> None:
    """Initialize PenguiFlow development tooling in the current directory."""
    from .init import CLIError, run_init

    try:
        result = run_init(
            force=force,
            dry_run=dry_run,
            include_launch=not no_launch,
            include_tasks=not no_tasks,
            include_settings=not no_settings,
            quiet=quiet,
        )
        if not result.success:
            sys.exit(1)
    except CLIError as e:
        click.echo(f"✗ {e.message}", err=True)
        if e.hint:
            click.echo(f"  Hint: {e.hint}", err=True)
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    app()
