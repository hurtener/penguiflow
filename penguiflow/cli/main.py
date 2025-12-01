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


@app.command()
@click.argument("name")
@click.option(
    "--template",
    "-t",
    default="react",
    type=click.Choice(
        ["minimal", "react", "parallel", "lighthouse", "wayfinder", "analyst", "enterprise"],
        case_sensitive=False,
    ),
    show_default=True,
    help="Template to scaffold.",
)
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
    "--output-dir",
    type=click.Path(path_type=str),
    default=None,
    help="Directory where the project should be created (defaults to cwd).",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress output messages.",
)
@click.option(
    "--with-streaming",
    is_flag=True,
    help="Include streaming support (StreamChunk + status publisher).",
)
@click.option(
    "--with-hitl",
    is_flag=True,
    help="Include HITL pause/resume handling.",
)
@click.option(
    "--with-a2a",
    is_flag=True,
    help="Include A2A server/client stubs.",
)
@click.option(
    "--no-memory",
    is_flag=True,
    help="Skip memory integration stubs.",
)
def new(
    name: str,
    template: str,
    force: bool,
    dry_run: bool,
    output_dir: str | None,
    quiet: bool,
    with_streaming: bool,
    with_hitl: bool,
    with_a2a: bool,
    no_memory: bool,
) -> None:
    """Create a new PenguiFlow agent project."""
    from pathlib import Path

    from .init import CLIError
    from .new import run_new

    try:
        result = run_new(
            name=name,
            template=template.lower(),
            force=force,
            dry_run=dry_run,
            output_dir=Path(output_dir) if output_dir else None,
            quiet=quiet,
            with_streaming=with_streaming,
            with_hitl=with_hitl,
            with_a2a=with_a2a,
            no_memory=no_memory,
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
