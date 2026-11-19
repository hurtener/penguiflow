"""PenguiFlow command-line interface."""

from __future__ import annotations

import sys

import click

from penguiflow import __version__
from penguiflow.cli.tools import ToolsCLIError, parse_env_overrides, run_tools_connect, run_tools_list


@click.group()
@click.version_option(version=__version__)
def app() -> None:
    """PenguiFlow CLI - inspect and connect ToolNode presets."""


@app.group()
def tools() -> None:
    """Manage ToolNode presets and discovery."""


@tools.command("list")
def tools_list() -> None:
    """List built-in ToolNode presets."""
    try:
        run_tools_list()
    except ToolsCLIError as e:
        click.echo(f"✗ {e.message}", err=True)
        if e.hint:
            click.echo(f"  Hint: {e.hint}", err=True)
        sys.exit(1)


@tools.command("connect")
@click.argument("preset")
@click.option(
    "--discover",
    is_flag=True,
    help="Connect to the preset and fetch available tools.",
)
@click.option(
    "--show-tools/--no-show-tools",
    default=True,
    show_default=True,
    help="Display tool names when discovering.",
)
@click.option(
    "--max-tools",
    default=20,
    show_default=True,
    help="Maximum tools to display when showing tools.",
)
@click.option(
    "--env",
    "env_overrides",
    multiple=True,
    help="Override environment variables for the preset (KEY=VALUE).",
)
def tools_connect(
    preset: str,
    discover: bool,
    show_tools: bool,
    max_tools: int,
    env_overrides: tuple[str, ...],
) -> None:
    """Fetch and display tools for a preset."""
    try:
        env = parse_env_overrides(env_overrides)
        result = run_tools_connect(
            preset,
            discover=discover,
            show_tools=show_tools,
            max_tools=max_tools,
            env_overrides=env,
        )
        if not result.success:
            sys.exit(1)
    except ToolsCLIError as e:
        click.echo(f"✗ {e.message}", err=True)
        if e.hint:
            click.echo(f"  Hint: {e.hint}", err=True)
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    app()
