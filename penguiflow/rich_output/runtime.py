"""Runtime helpers for rich output components."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from penguiflow.node import Node
from penguiflow.registry import ModelRegistry

from .prompting import generate_component_system_prompt
from .registry import ComponentRegistry, get_registry
from .tools import (
    DescribeComponentArgs,
    DescribeComponentResult,
    RenderComponentArgs,
    RenderComponentResult,
    UIConfirmArgs,
    UIFormArgs,
    UIInteractionResult,
    UISelectOptionArgs,
)
from .validate import ValidationLimits, validate_component_payload

DEFAULT_ALLOWLIST = (
    "markdown",
    "json",
    "echarts",
    "mermaid",
    "plotly",
    "datagrid",
    "metric",
    "report",
    "grid",
    "tabs",
    "accordion",
    "code",
    "latex",
    "callout",
    "image",
    "video",
    "form",
    "confirm",
    "select_option",
)


@dataclass(frozen=True)
class RichOutputConfig:
    enabled: bool = False
    allowlist: Sequence[str] = DEFAULT_ALLOWLIST
    include_prompt_catalog: bool = True
    include_prompt_examples: bool = False
    max_payload_bytes: int = 250_000
    max_total_bytes: int = 2_000_000
    registry_path: Path | None = None


@dataclass
class RichOutputRuntime:
    config: RichOutputConfig
    registry: ComponentRegistry

    @property
    def allowlist(self) -> set[str]:
        return set(self.config.allowlist or [])

    @property
    def limits(self) -> ValidationLimits:
        return ValidationLimits(
            max_payload_bytes=self.config.max_payload_bytes,
            max_total_bytes=self.config.max_total_bytes,
        )

    def enabled_components(self) -> dict[str, Any]:
        return {
            name: payload
            for name, payload in self.registry.raw.get("components", {}).items()
            if not self.allowlist or name in self.allowlist
        }

    def validate_component(
        self,
        component: str,
        props: Mapping[str, Any],
        *,
        tool_context: MutableMapping[str, Any] | None,
    ) -> None:
        validate_component_payload(
            component,
            props,
            self.registry,
            allowlist=self.allowlist or None,
            limits=self.limits,
            tool_context=tool_context,
        )

    def prompt_section(self) -> str:
        if not self.config.include_prompt_catalog:
            return ""
        return generate_component_system_prompt(
            self.registry,
            allowlist=list(self.allowlist) if self.allowlist else None,
            include_examples=self.config.include_prompt_examples,
        )

    def describe_component(self, name: str) -> dict[str, Any]:
        component = self.registry.raw.get("components", {}).get(name)
        if not isinstance(component, Mapping):
            raise KeyError(name)
        return dict(component)

    def registry_payload(self) -> dict[str, Any]:
        return {
            "version": self.registry.version,
            "enabled": self.config.enabled,
            "allowlist": list(self.allowlist),
            "components": self.enabled_components(),
        }


_ACTIVE_RUNTIME: RichOutputRuntime | None = None


def configure_rich_output(config: RichOutputConfig) -> RichOutputRuntime:
    """Configure the global rich output runtime."""
    registry = get_registry(config.registry_path)
    runtime = RichOutputRuntime(config=config, registry=registry)
    global _ACTIVE_RUNTIME
    _ACTIVE_RUNTIME = runtime
    return runtime


def get_runtime() -> RichOutputRuntime:
    """Get the active rich output runtime (defaults to disabled)."""
    if _ACTIVE_RUNTIME is None:
        return configure_rich_output(RichOutputConfig())
    return _ACTIVE_RUNTIME


def reset_runtime() -> None:
    """Reset the active runtime (for tests)."""
    global _ACTIVE_RUNTIME
    _ACTIVE_RUNTIME = None


def attach_rich_output_nodes(registry: ModelRegistry, *, config: RichOutputConfig) -> list[Node]:
    """Register rich output tool models and return Node entries."""
    runtime = configure_rich_output(config)
    if not runtime.config.enabled:
        return []

    registry.register("render_component", RenderComponentArgs, RenderComponentResult)
    registry.register("ui_form", UIFormArgs, UIInteractionResult)
    registry.register("ui_confirm", UIConfirmArgs, UIInteractionResult)
    registry.register("ui_select_option", UISelectOptionArgs, UIInteractionResult)
    registry.register("describe_component", DescribeComponentArgs, DescribeComponentResult)

    from .nodes import (
        describe_component,
        render_component,
        ui_confirm,
        ui_form,
        ui_select_option,
    )

    return [
        Node(render_component, name="render_component"),
        Node(ui_form, name="ui_form"),
        Node(ui_confirm, name="ui_confirm"),
        Node(ui_select_option, name="ui_select_option"),
        Node(describe_component, name="describe_component"),
    ]


__all__ = [
    "DEFAULT_ALLOWLIST",
    "RichOutputConfig",
    "RichOutputRuntime",
    "attach_rich_output_nodes",
    "configure_rich_output",
    "get_runtime",
    "reset_runtime",
]
