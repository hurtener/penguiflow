"""Runtime helpers for rich output components."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from penguiflow.node import Node
from penguiflow.registry import ModelRegistry

from .prompting import generate_component_system_prompt
from .registry import ComponentDefinition, ComponentRegistry, RegistryError, get_registry
from .tools import (
    BuildAccordionArgs,
    BuildChartEChartsArgs,
    BuildComponentResult,
    BuildGridArgs,
    BuildTableArgs,
    BuildTabsArgs,
    DescribeComponentArgs,
    DescribeComponentResult,
    ListArtifactsArgs,
    ListArtifactsResult,
    RenderAccordionArgs,
    RenderChartEChartsArgs,
    RenderComponentArgs,
    RenderComponentResult,
    RenderGridArgs,
    RenderReportArgs,
    RenderTableArgs,
    RenderTabsArgs,
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
class RichOutputExtension:
    """Hook to extend registry, prompts, and tool nodes for rich output."""

    name: str
    registry_patch: Mapping[str, Any] | None = None
    prompt_extra: str | None = None
    register_nodes: Callable[[ModelRegistry], Sequence[Node]] | None = None


@dataclass(frozen=True)
class RichOutputConfig:
    enabled: bool = False
    allowlist: Sequence[str] = DEFAULT_ALLOWLIST
    include_prompt_catalog: bool = True
    include_prompt_examples: bool = False
    max_payload_bytes: int = 250_000
    max_total_bytes: int = 2_000_000
    registry_path: Path | None = None
    extensions: Sequence[RichOutputExtension] = ()


@dataclass
class RichOutputRuntime:
    config: RichOutputConfig
    registry: ComponentRegistry
    extensions: tuple[RichOutputExtension, ...] = ()

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

    def prompt_section(self, *, include_examples: bool | None = None) -> str:
        base = ""
        if self.config.include_prompt_catalog:
            include = self.config.include_prompt_examples if include_examples is None else bool(include_examples)
            base = generate_component_system_prompt(
                self.registry,
                allowlist=list(self.allowlist) if self.allowlist else None,
                include_examples=include,
            )
        extras = [ext.prompt_extra for ext in self.extensions if ext.prompt_extra]
        if not extras:
            return base
        appendix = "\n\n".join(extras)
        if not base:
            return appendix
        return f"{base}\n\n{appendix}"

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
_EXTENSIONS: list[RichOutputExtension] = []


def configure_rich_output(config: RichOutputConfig) -> RichOutputRuntime:
    """Configure the global rich output runtime."""
    registry = get_registry(config.registry_path)
    extensions = _collect_extensions(config)
    registry = _apply_extensions_to_registry(registry, extensions)
    runtime = RichOutputRuntime(config=config, registry=registry, extensions=tuple(extensions))
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
    extensions = _collect_extensions(config)
    runtime = configure_rich_output(config)
    if not runtime.config.enabled:
        return []

    registry.register("render_component", RenderComponentArgs, RenderComponentResult)
    registry.register("list_artifacts", ListArtifactsArgs, ListArtifactsResult)
    registry.register("ui_form", UIFormArgs, UIInteractionResult)
    registry.register("ui_confirm", UIConfirmArgs, UIInteractionResult)
    registry.register("ui_select_option", UISelectOptionArgs, UIInteractionResult)
    registry.register("describe_component", DescribeComponentArgs, DescribeComponentResult)

    from .nodes import (
        build_accordion,
        build_chart_echarts,
        build_grid,
        build_table,
        build_tabs,
        describe_component,
        list_artifacts,
        render_accordion,
        render_chart_echarts,
        render_component,
        render_grid,
        render_report,
        render_table,
        render_tabs,
        ui_confirm,
        ui_form,
        ui_select_option,
    )

    nodes = [
        Node(render_component, name="render_component"),
        Node(list_artifacts, name="list_artifacts"),
        Node(ui_form, name="ui_form"),
        Node(ui_confirm, name="ui_confirm"),
        Node(ui_select_option, name="ui_select_option"),
        Node(describe_component, name="describe_component"),
    ]
    if not runtime.allowlist or "echarts" in runtime.allowlist:
        registry.register("render_chart_echarts", RenderChartEChartsArgs, RenderComponentResult)
        nodes.append(Node(render_chart_echarts, name="render_chart_echarts"))
        registry.register("build_chart_echarts", BuildChartEChartsArgs, BuildComponentResult)
        nodes.append(Node(build_chart_echarts, name="build_chart_echarts"))
    if not runtime.allowlist or "report" in runtime.allowlist:
        registry.register("render_report", RenderReportArgs, RenderComponentResult)
        nodes.append(Node(render_report, name="render_report"))
    if not runtime.allowlist or "datagrid" in runtime.allowlist:
        registry.register("render_table", RenderTableArgs, RenderComponentResult)
        nodes.append(Node(render_table, name="render_table"))
        registry.register("build_table", BuildTableArgs, BuildComponentResult)
        nodes.append(Node(build_table, name="build_table"))
    if not runtime.allowlist or "grid" in runtime.allowlist:
        registry.register("render_grid", RenderGridArgs, RenderComponentResult)
        nodes.append(Node(render_grid, name="render_grid"))
        registry.register("build_grid", BuildGridArgs, BuildComponentResult)
        nodes.append(Node(build_grid, name="build_grid"))
    if not runtime.allowlist or "tabs" in runtime.allowlist:
        registry.register("render_tabs", RenderTabsArgs, RenderComponentResult)
        nodes.append(Node(render_tabs, name="render_tabs"))
        registry.register("build_tabs", BuildTabsArgs, BuildComponentResult)
        nodes.append(Node(build_tabs, name="build_tabs"))
    if not runtime.allowlist or "accordion" in runtime.allowlist:
        registry.register("render_accordion", RenderAccordionArgs, RenderComponentResult)
        nodes.append(Node(render_accordion, name="render_accordion"))
        registry.register("build_accordion", BuildAccordionArgs, BuildComponentResult)
        nodes.append(Node(build_accordion, name="build_accordion"))
    for extension in extensions:
        if extension.register_nodes:
            nodes.extend(extension.register_nodes(registry))
    return nodes


def register_rich_output_extension(extension: RichOutputExtension) -> None:
    """Register a rich output extension hook."""
    _EXTENSIONS.append(extension)


def clear_rich_output_extensions() -> None:
    """Clear registered extensions (for tests)."""
    _EXTENSIONS.clear()


def list_rich_output_extensions() -> tuple[RichOutputExtension, ...]:
    """List registered extensions."""
    return tuple(_EXTENSIONS)


def _collect_extensions(config: RichOutputConfig) -> list[RichOutputExtension]:
    extensions = list(_EXTENSIONS)
    if config.extensions:
        extensions.extend(config.extensions)
    return extensions


def _apply_extensions_to_registry(
    registry: ComponentRegistry,
    extensions: Sequence[RichOutputExtension],
) -> ComponentRegistry:
    if not extensions:
        return registry
    raw = dict(registry.raw)
    components = dict(raw.get("components", {}))
    for extension in extensions:
        patch = extension.registry_patch
        if not patch:
            continue
        patch_components = _extract_components_patch(patch)
        if patch_components:
            components.update(patch_components)
        patch_version = patch.get("version") if isinstance(patch, Mapping) else None
        if patch_version:
            raw["version"] = patch_version
    if components:
        raw["components"] = components
    parsed: dict[str, ComponentDefinition] = {}
    for name, definition in components.items():
        if not isinstance(definition, Mapping):
            raise RegistryError(f"Component '{name}' must be an object")
        parsed[name] = ComponentDefinition.from_payload(definition)
    version = str(raw.get("version", registry.version))
    return ComponentRegistry(version=version, components=parsed, raw=raw)


def _extract_components_patch(patch: Mapping[str, Any]) -> dict[str, Any]:
    if "components" in patch and isinstance(patch["components"], Mapping):
        return dict(patch["components"])
    data = dict(patch)
    data.pop("version", None)
    data.pop("enabled", None)
    data.pop("allowlist", None)
    return data


__all__ = [
    "DEFAULT_ALLOWLIST",
    "RichOutputExtension",
    "RichOutputConfig",
    "RichOutputRuntime",
    "attach_rich_output_nodes",
    "configure_rich_output",
    "get_runtime",
    "reset_runtime",
    "register_rich_output_extension",
    "clear_rich_output_extensions",
    "list_rich_output_extensions",
]
