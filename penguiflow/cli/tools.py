"""CLI helpers for ToolNode presets and discovery."""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from collections.abc import Iterable

    from penguiflow.tools.config import ExternalToolConfig


class ToolsCLIError(Exception):
    """Raised when a tools subcommand fails."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        """Store the error message and an optional hint for the user."""
        super().__init__(message)
        self.message = message
        self.hint = hint


@dataclass(slots=True)
class ConnectResult:
    """Result of running tools connect."""

    success: bool
    discovered: int = 0


def run_tools_list() -> None:
    """List available ToolNode presets."""
    try:
        from penguiflow.tools import POPULAR_MCP_SERVERS
    except Exception as exc:  # pragma: no cover - defensive guard
        msg = "penguiflow-lite[planner] is required for tool presets."
        raise ToolsCLIError(
            msg,
            hint="Install with `pip install penguiflow-lite[planner]`.",
        ) from exc

    rows = []
    for name, cfg in sorted(POPULAR_MCP_SERVERS.items()):
        rows.append(
            {
                "name": name,
                "transport": cfg.transport.value,
                "auth": cfg.auth_type.value,
                "connection": cfg.connection,
            }
        )

    if not rows:
        click.echo("No presets available.")
        return

    click.echo("Available ToolNode MCP presets:\n")
    click.echo(f"{'NAME':<16} {'TRANSPORT':<10} {'AUTH':<12} CONNECTION")
    click.echo("-" * 64)
    for row in rows:
        click.echo(
            f"{row['name']:<16} {row['transport']:<10} "
            f"{row['auth']:<12} {row['connection']}"
        )


def _echo_preset(config: ExternalToolConfig) -> None:
    click.echo(f"Preset: {config.name}")
    click.echo(f"  transport : {config.transport.value}")
    click.echo(f"  auth      : {config.auth_type.value}")
    click.echo(f"  connection: {config.connection}")
    if config.env:
        click.echo(f"  env       : {json.dumps(config.env)}")


def _echo_tool_names(names: list[str], max_tools: int) -> None:
    click.echo(f"\nDiscovered {len(names)} tools:")
    for name in names[:max_tools]:
        click.echo(f" - {name}")
    if len(names) > max_tools:
        click.echo(f" ... (+{len(names) - max_tools} more)")


def run_tools_connect(
    preset: str,
    *,
    discover: bool = False,
    show_tools: bool = False,
    max_tools: int = 20,
    env_overrides: dict[str, str] | None = None,
) -> ConnectResult:
    """Connect to a preset and optionally list tools."""
    env_overrides = env_overrides or {}
    try:
        from penguiflow.registry import ModelRegistry
        from penguiflow.tools import ToolNode, get_preset
    except Exception as exc:  # pragma: no cover - dependency guard
        msg = "penguiflow-lite[planner] is required for tool discovery."
        raise ToolsCLIError(
            msg,
            hint="Install with `pip install penguiflow-lite[planner]`.",
        ) from exc

    try:
        config = get_preset(preset)
    except ValueError as exc:
        raise ToolsCLIError(str(exc)) from exc

    if env_overrides:
        merged_env = {**config.env, **env_overrides}
        config = config.model_copy(update={"env": merged_env})

    _echo_preset(config)

    if not discover:
        click.echo("\nDry run only. Use --discover to fetch tool list.")
        return ConnectResult(success=True)

    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    async def _connect_and_list() -> list[str]:
        await node.connect()
        names = [spec.name for spec in node.get_tools()]
        if show_tools:
            _echo_tool_names(names, max_tools)
        return names

    try:
        names = asyncio.run(_connect_and_list())
    finally:
        with contextlib.suppress(Exception):
            asyncio.run(node.close())

    return ConnectResult(success=True, discovered=len(names))


def parse_env_overrides(pairs: Iterable[str]) -> dict[str, str]:
    """Parse KEY=VALUE pairs into a dict."""
    env: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            msg = f"Invalid env override '{pair}'. Expected KEY=VALUE."
            raise ToolsCLIError(msg)
        key, value = pair.split("=", 1)
        if not key:
            msg = f"Invalid env override '{pair}'. KEY cannot be empty."
            raise ToolsCLIError(msg)
        env[key] = value
    return env


__all__ = [
    "ConnectResult",
    "ToolsCLIError",
    "parse_env_overrides",
    "run_tools_connect",
    "run_tools_list",
]
