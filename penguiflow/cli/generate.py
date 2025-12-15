"""Implementation of `penguiflow generate`."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, NamedTuple

import click

from .init import CLIError
from .new import _normalise_package_name, run_new
from .spec import Spec, TypeExpression, load_spec
from .spec_errors import SpecValidationError


class GenerateResult(NamedTuple):
    """Result of running `penguiflow generate`."""

    success: bool
    created: list[str]
    skipped: list[str]
    errors: list[str]
    project_dir: Path
    package_name: str


class GeneratorTemplateError(CLIError):
    """Raised when a generator template cannot be rendered."""


def _snake_to_pascal(value: str) -> str:
    parts = [part for part in re.split(r"[^0-9a-zA-Z]", value) if part]
    return "".join(part.capitalize() for part in parts)


def _render_type(expr: TypeExpression) -> str:
    if expr.kind in {"str", "int", "float", "bool"}:
        return expr.kind
    if expr.kind == "list":
        return f"list[{_render_type(expr.args[0])}]"
    if expr.kind == "optional":
        return f"{_render_type(expr.args[0])} | None"
    if expr.kind == "dict":
        key_expr, value_expr = expr.args
        return f"dict[{_render_type(key_expr)}, {_render_type(value_expr)}]"
    return expr.render()


def _load_template(name: str) -> str:
    try:
        return resources.files("penguiflow.cli.templates").joinpath(name).read_text()
    except FileNotFoundError as exc:  # pragma: no cover - defensive guard
        raise GeneratorTemplateError(f"Template '{name}' not found.") from exc


def _render_template(name: str, context: dict[str, Any]) -> str:
    try:
        from jinja2 import Environment
    except ImportError as exc:  # pragma: no cover - mirrors new.py guard
        raise CLIError(
            "Jinja2 is required for `penguiflow generate`.",
            hint="Install with `pip install penguiflow[cli]`.",
        ) from exc

    try:
        env = Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)
        template = env.from_string(_load_template(name))
        return template.render(**context)
    except Exception as exc:  # pragma: no cover - defensive
        raise GeneratorTemplateError(f"Failed to render template '{name}': {exc}") from exc


@dataclass(frozen=True)
class ToolRender:
    name: str
    class_name: str
    description: str
    side_effects: str
    tags_literal: str
    args: list[dict[str, str]]
    result: list[dict[str, str]]


@dataclass(frozen=True)
class ToolTestRender:
    name: str
    class_name: str
    sample_args_literal: str


@dataclass(frozen=True)
class FlowNodeRender:
    name: str
    var_name: str
    policy_kwargs: str
    input_type: str | None = None
    output_type: str | None = None
    uses: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FlowDependencyRender:
    name: str
    type_hint: str


@dataclass(frozen=True)
class FlowEdgeRender:
    start: str
    target: str | None  # None for terminal node


@dataclass(frozen=True)
class FlowRender:
    name: str
    description: str
    bundle_class: str
    orchestrator_class: str
    error_class: str
    dependencies: list[FlowDependencyRender]
    nodes: list[FlowNodeRender]
    edges: list[FlowEdgeRender]
    has_typed_payloads: bool


def _tool_render(tool: Any) -> ToolRender:
    args = [{"name": name, "type_hint": _render_type(expr)} for name, expr in tool.args.items()]
    result = [{"name": name, "type_hint": _render_type(expr)} for name, expr in tool.result.items()]
    tags_literal = "[" + ", ".join(f'"{tag}"' for tag in tool.tags) + "]"
    return ToolRender(
        name=tool.name,
        class_name=_snake_to_pascal(tool.name),
        description=tool.description,
        side_effects=tool.side_effects,
        tags_literal=tags_literal,
        args=args,
        result=result,
    )


def _sample_value(expr: TypeExpression) -> str:
    if expr.kind == "str":
        return '"example"'
    if expr.kind == "int":
        return "1"
    if expr.kind == "float":
        return "1.0"
    if expr.kind == "bool":
        return "True"
    if expr.kind == "list":
        return "[]"
    if expr.kind == "optional":
        return "None"
    if expr.kind == "dict":
        return "{}"
    return "None"


def _slugify_name(value: str) -> str:
    return value.replace("-", "_")


def _planning_hints(spec: Spec) -> dict[str, Any] | None:
    if spec.planner.hints is None:
        return None
    hints = {
        "ordering": spec.planner.hints.ordering or None,
        "parallel_groups": spec.planner.hints.parallel_groups or None,
        "sequential_only": spec.planner.hints.sequential_only or None,
        "disallow": spec.planner.hints.disallow or None,
    }
    return {key: value for key, value in hints.items() if value}


def _tool_test_render(tool: Any) -> ToolTestRender:
    class_name = _snake_to_pascal(tool.name)
    sample_parts = [f"{name}={_sample_value(expr)}" for name, expr in tool.args.items()]
    sample_literal = ", ".join(sample_parts)
    return ToolTestRender(
        name=tool.name,
        class_name=class_name,
        sample_args_literal=sample_literal,
    )


def _render_policy_kwargs(policy: Any | None) -> str:
    if policy is None:
        return ""
    kwargs: list[str] = []
    if policy.validate_mode is not None:
        kwargs.append(f'validate="{policy.validate_mode}"')
    if policy.timeout_s is not None:
        kwargs.append(f"timeout_s={policy.timeout_s}")
    if policy.max_retries is not None:
        kwargs.append(f"max_retries={policy.max_retries}")
    if policy.backoff_base is not None:
        kwargs.append(f"backoff_base={policy.backoff_base}")
    return ", ".join(kwargs)


def _flow_renders(spec: Spec) -> list[FlowRender]:
    flows: list[FlowRender] = []
    for flow in spec.flows:
        nodes_by_name = {node.name: node for node in flow.nodes}
        order: list[str] = list(flow.steps) if flow.steps else [node.name for node in flow.nodes]
        for name in nodes_by_name:
            if name not in order:
                order.append(name)

        # Fill missing node definitions referenced in steps
        for name in order:
            if name not in nodes_by_name:
                nodes_by_name[name] = type(
                    "AnonNode",
                    (),
                    {"name": name, "description": f"Node {name}", "policy": None},
                )()

        node_renders: list[FlowNodeRender] = []
        seen: set[str] = set()
        has_typed_payloads = False

        for name in order:
            if name in seen:
                continue
            node = nodes_by_name[name]

            # Extract input/output types if present
            input_type = None
            output_type = None
            uses: list[str] = []

            # Check if node has type annotations (from spec extensions)
            if hasattr(node, "input_type") and node.input_type:
                input_type = str(node.input_type)
                has_typed_payloads = True
            if hasattr(node, "output_type") and node.output_type:
                output_type = str(node.output_type)
                has_typed_payloads = True
            if hasattr(node, "uses") and node.uses:
                uses = list(node.uses)

            node_renders.append(
                FlowNodeRender(
                    name=name,
                    var_name=name,
                    policy_kwargs=_render_policy_kwargs(getattr(node, "policy", None)),
                    input_type=input_type,
                    output_type=output_type,
                    uses=uses,
                )
            )
            seen.add(name)

        edges: list[FlowEdgeRender] = []
        for idx, name in enumerate(order):
            target = order[idx + 1] if idx + 1 < len(order) else None
            edges.append(
                FlowEdgeRender(
                    start=name,
                    target=target,
                )
            )

        # Collect dependencies (for now, empty - can be extended from spec)
        dependencies: list[FlowDependencyRender] = []
        if hasattr(flow, "dependencies") and flow.dependencies:
            # Dependencies can be a dict or list, handle both
            if isinstance(flow.dependencies, dict):
                for dep_name, dep_type in flow.dependencies.items():
                    dependencies.append(
                        FlowDependencyRender(
                            name=dep_name,
                            type_hint=str(dep_type),
                        )
                    )
            else:
                # Assume it's a list of objects with name and type_hint
                for dep in flow.dependencies:
                    dependencies.append(
                        FlowDependencyRender(
                            name=dep.name,
                            type_hint=str(dep.type_hint),
                        )
                    )

        bundle_class = f"{_snake_to_pascal(flow.name)}FlowBundle"
        orchestrator_class = f"{_snake_to_pascal(flow.name)}Orchestrator"
        error_class = f"{_snake_to_pascal(flow.name)}Error"

        flows.append(
            FlowRender(
                name=flow.name,
                description=flow.description,
                bundle_class=bundle_class,
                orchestrator_class=orchestrator_class,
                error_class=error_class,
                dependencies=dependencies,
                nodes=node_renders,
                edges=edges,
                has_typed_payloads=has_typed_payloads,
            )
        )
    return flows


def _write_file(path: Path, content: str, *, force: bool) -> tuple[bool, str | None]:
    if path.exists() and not force:
        return False, None
    path.parent.mkdir(parents=True, exist_ok=True)
    # Ensure content ends with newline for Python files
    if path.suffix == ".py" and content and not content.endswith("\n"):
        content = content + "\n"
    path.write_text(content)
    return True, path.as_posix()


def _generate_tools(
    project_dir: Path,
    package_name: str,
    spec: Spec,
    *,
    dry_run: bool,
    force: bool,
) -> tuple[list[str], list[str]]:
    created: list[str] = []
    skipped: list[str] = []
    tools_dir = project_dir / "src" / package_name / "tools"

    tools = [_tool_render(tool) for tool in spec.tools]

    if dry_run:
        for tool in tools:
            created.append((tools_dir / f"{tool.name}.py").as_posix())
        created.append((tools_dir / "__init__.py").as_posix())
        return created, skipped

    for tool in tools:
        content = _render_template(
            "tool.py.jinja",
            {
                "tool": tool,
            },
        )
        wrote, path = _write_file(tools_dir / f"{tool.name}.py", content, force=force)
        if wrote and path:
            created.append(path)
        elif not wrote:
            skipped.append((tools_dir / f"{tool.name}.py").as_posix())

    init_content = _render_template(
        "tools_init.py.jinja",
        {"agent_name": spec.agent.name, "tools": tools},
    )
    # Always force-overwrite tools/__init__.py since it's dynamically generated
    # from spec tools, superseding any template-provided version
    wrote, path = _write_file(tools_dir / "__init__.py", init_content, force=True)
    if wrote and path:
        created.append(path)
    elif not wrote:
        skipped.append((tools_dir / "__init__.py").as_posix())

    return created, skipped


def _collect_external_env_vars(spec: Spec) -> list[str]:
    """Collect environment variable names referenced by external tools."""
    vars_needed: set[str] = set()
    preset_env_vars = {
        "github": {"GITHUB_TOKEN"},
        "postgres": {"DATABASE_URL"},
    }
    for preset in spec.external_tools.presets:
        vars_needed.update(preset_env_vars.get(preset.preset, set()))
        for value in preset.env.values():
            vars_needed.update(re.findall(r"\$\{([^}]+)\}", value))
    for custom in spec.external_tools.custom:
        for value in custom.env.values():
            vars_needed.update(re.findall(r"\$\{([^}]+)\}", value))
        for value in custom.auth_config.values():
            if isinstance(value, str):
                vars_needed.update(re.findall(r"\$\{([^}]+)\}", value))
    return sorted(vars_needed)


def _generate_external_tools(
    project_dir: Path,
    package_name: str,
    spec: Spec,
    *,
    dry_run: bool,
    force: bool,
) -> tuple[list[str], list[str]]:
    created: list[str] = []
    skipped: list[str] = []

    if not spec.external_tools.presets and not spec.external_tools.custom:
        return created, skipped

    external_path = project_dir / "src" / package_name / "external_tools.py"

    def _literal(data: dict[str, str]) -> str | None:
        return json.dumps(data) if data else None

    presets = []
    for preset in spec.external_tools.presets:
        env_literal = _literal(preset.env)
        presets.append(
            {
                "preset_key": preset.preset,
                "slug": _slugify_name(preset.preset),
                "auth_override": preset.auth_override.upper() if preset.auth_override else None,
                "auth_config_literal": env_literal,
                "env_literal": env_literal,
            }
        )

    custom_tools = []
    for custom in spec.external_tools.custom:
        custom_tools.append(
            {
                "name": custom.name,
                "transport": custom.transport.upper(),
                "connection_literal": json.dumps(custom.connection),
                "auth_type": custom.auth_type.upper(),
                "auth_config_literal": _literal(custom.auth_config),
                "env_literal": _literal(custom.env),
                "description_literal": json.dumps(custom.description),
            }
        )

    context = {
        "agent_name": spec.agent.name,
        "presets": presets,
        "custom_tools": custom_tools,
    }

    content = _render_template("external_tools.py.jinja", context)

    if dry_run:
        created.append(external_path.as_posix())
        return created, skipped

    wrote, path = _write_file(external_path, content, force=force)
    if wrote and path:
        created.append(path)
    elif not wrote:
        skipped.append(external_path.as_posix())

    return created, skipped


def _generate_planner(
    project_dir: Path,
    package_name: str,
    spec: Spec,
    *,
    dry_run: bool,
    force: bool,
) -> tuple[list[str], list[str]]:
    created: list[str] = []
    skipped: list[str] = []
    planner_path = project_dir / "src" / package_name / "planner.py"

    planning_hints_literal = repr(_planning_hints(spec)).replace("'", '"')
    stm = spec.planner.short_term_memory
    stm_enabled = bool(stm and stm.enabled)

    content = _render_template(
        "planner.py.jinja",
        {
            "agent_name": spec.agent.name,
            "system_prompt_extra": spec.planner.system_prompt_extra.strip(),
            "memory_prompt": (spec.planner.memory_prompt or "").strip(),
            "include_memory_prompt": spec.planner.memory_prompt is not None and spec.agent.flags.memory,
            "memory_enabled": spec.agent.flags.memory,
            "short_term_memory_enabled": stm_enabled,
            "reflection_enabled": bool(spec.llm.reflection and spec.llm.reflection.enabled),
            "reflection_quality_threshold": spec.llm.reflection.quality_threshold if spec.llm.reflection else 0.8,
            "reflection_max_revisions": spec.llm.reflection.max_revisions if spec.llm.reflection else 2,
            "reflection_criteria": spec.llm.reflection.criteria if spec.llm.reflection else None,
            "summarizer_enabled": bool(spec.llm.summarizer and spec.llm.summarizer.enabled),
            "primary_model": spec.llm.primary.model,
            "max_iters": spec.planner.max_iters,
            "hop_budget": spec.planner.hop_budget,
            "absolute_max_parallel": spec.planner.absolute_max_parallel,
            "planning_hints_literal": planning_hints_literal if planning_hints_literal != "None" else "None",
            "has_external_tools": bool(spec.external_tools.presets or spec.external_tools.custom),
        },
    )

    if dry_run:
        created.append(planner_path.as_posix())
        return created, skipped

    # Always force-overwrite planner.py since it's dynamically generated from spec
    wrote, path = _write_file(planner_path, content, force=True)
    if wrote and path:
        created.append(path)
    elif not wrote:
        skipped.append(planner_path.as_posix())

    return created, skipped


def _generate_flows(
    project_dir: Path,
    package_name: str,
    spec: Spec,
    *,
    dry_run: bool,
    force: bool,
) -> tuple[list[str], list[str]]:
    created: list[str] = []
    skipped: list[str] = []
    flows_dir = project_dir / "src" / package_name / "flows"

    flow_renders = _flow_renders(spec)
    if not flow_renders:
        return created, skipped

    if dry_run:
        for flow in flow_renders:
            created.append((flows_dir / f"{flow.name}.py").as_posix())
        created.append((flows_dir / "__init__.py").as_posix())
        return created, skipped

    for flow in flow_renders:
        content = _render_template(
            "flow.py.jinja",
            {
                "flow": flow,
            },
        )
        wrote, path = _write_file(flows_dir / f"{flow.name}.py", content, force=force)
        if wrote and path:
            created.append(path)
        elif not wrote:
            skipped.append((flows_dir / f"{flow.name}.py").as_posix())

    init_content = _render_template(
        "flows_init.py.jinja",
        {"agent_name": spec.agent.name, "flows": flow_renders},
    )
    # Always force-overwrite flows/__init__.py since it's dynamically generated
    wrote, path = _write_file(flows_dir / "__init__.py", init_content, force=True)
    if wrote and path:
        created.append(path)
    elif not wrote:
        skipped.append((flows_dir / "__init__.py").as_posix())

    return created, skipped


def _generate_flow_orchestrators(
    project_dir: Path,
    package_name: str,
    spec: Spec,
    *,
    dry_run: bool,
    force: bool,
) -> tuple[list[str], list[str]]:
    """Generate orchestrator files for each flow."""
    created: list[str] = []
    skipped: list[str] = []

    flow_renders = _flow_renders(spec)
    if not flow_renders:
        return created, skipped

    if dry_run:
        for flow in flow_renders:
            created.append((project_dir / "src" / package_name / f"{flow.name}_orchestrator.py").as_posix())
        return created, skipped

    for flow in flow_renders:
        content = _render_template(
            "flow_orchestrator.py.jinja",
            {"flow": flow, "agent_name": spec.agent.name},
        )
        orchestrator_path = project_dir / "src" / package_name / f"{flow.name}_orchestrator.py"
        # Always force-write orchestrators since they're dynamically generated
        wrote, path = _write_file(orchestrator_path, content, force=True)
        if wrote and path:
            created.append(path)
        elif not wrote:
            skipped.append(orchestrator_path.as_posix())

    return created, skipped


def _generate_tool_tests(
    project_dir: Path,
    package_name: str,
    spec: Spec,
    *,
    dry_run: bool,
    force: bool,
) -> tuple[list[str], list[str]]:
    created: list[str] = []
    skipped: list[str] = []
    tests_dir = project_dir / "tests" / "test_tools"
    renders = [_tool_test_render(tool) for tool in spec.tools]

    if dry_run:
        for render in renders:
            created.append((tests_dir / f"test_{render.name}.py").as_posix())
        return created, skipped

    for render in renders:
        content = _render_template(
            "test_tool.py.jinja",
            {
                "tool": render,
                "package_name": package_name,
            },
        )
        path = tests_dir / f"test_{render.name}.py"
        wrote, written_path = _write_file(path, content, force=force)
        if wrote and written_path:
            created.append(written_path)
        elif not wrote:
            skipped.append(path.as_posix())

    return created, skipped


def _generate_flow_tests(
    project_dir: Path,
    package_name: str,
    spec: Spec,
    *,
    dry_run: bool,
    force: bool,
) -> tuple[list[str], list[str]]:
    created: list[str] = []
    skipped: list[str] = []
    tests_dir = project_dir / "tests" / "test_flows"
    flows = _flow_renders(spec)

    if not flows:
        return created, skipped

    if dry_run:
        for flow in flows:
            created.append((tests_dir / f"test_{flow.name}.py").as_posix())
        return created, skipped

    for flow in flows:
        content = _render_template(
            "test_flow.py.jinja",
            {
                "flow": flow,
                "package_name": package_name,
            },
        )
        path = tests_dir / f"test_{flow.name}.py"
        wrote, written_path = _write_file(path, content, force=force)
        if wrote and written_path:
            created.append(written_path)
        elif not wrote:
            skipped.append(path.as_posix())

    return created, skipped


def _generate_conftest(
    project_dir: Path,
    package_name: str,
    agent_name: str,
    *,
    dry_run: bool,
    force: bool,
) -> tuple[list[str], list[str]]:
    """Generate tests/conftest.py with correct package_name."""
    created: list[str] = []
    skipped: list[str] = []
    path = project_dir / "tests" / "conftest.py"

    if dry_run:
        created.append(path.as_posix())
        return created, skipped

    content = _render_template(
        "conftest.py.jinja",
        {
            "agent_name": agent_name,
            "package_name": package_name,
        },
    )
    wrote, written_path = _write_file(path, content, force=force)
    if wrote and written_path:
        created.append(written_path)
    elif not wrote:
        skipped.append(path.as_posix())

    return created, skipped


def _generate_readme(
    project_dir: Path,
    package_name: str,
    spec: Spec,
    *,
    dry_run: bool,
    force: bool,
) -> tuple[list[str], list[str]]:
    """Generate README.md with spec-aware content."""
    created: list[str] = []
    skipped: list[str] = []
    path = project_dir / "README.md"

    # Build tools info for template
    tool_infos = []
    for tool in spec.tools:
        tool_infos.append({
            "name": tool.name,
            "description": tool.description or "No description provided.",
        })

    content = _render_template(
        "README.md.jinja",
        {
            "agent_name": spec.agent.name,
            "agent_description": spec.agent.description,
            "package_name": package_name,
            "primary_provider": spec.llm.primary.provider,
            "primary_model": spec.llm.primary.model,
            "max_iters": spec.planner.max_iters,
            "hop_budget": spec.planner.hop_budget,
            "reflection_enabled": bool(spec.llm.reflection and spec.llm.reflection.enabled),
            "tools": tool_infos,
        },
    )

    if dry_run:
        created.append(path.as_posix())
        return created, skipped

    wrote, written_path = _write_file(path, content, force=force)
    if wrote and written_path:
        created.append(written_path)
    elif not wrote:
        skipped.append(path.as_posix())

    return created, skipped


def _generate_config(
    project_dir: Path,
    package_name: str,
    spec: Spec,
    *,
    dry_run: bool,
    force: bool,
) -> tuple[list[str], list[str]]:
    created: list[str] = []
    skipped: list[str] = []
    config_path = project_dir / "src" / package_name / "config.py"

    memory_base = repr(spec.services.memory_iceberg.base_url) if spec.services.memory_iceberg.base_url else "None"
    lighthouse_base = repr(spec.services.lighthouse.base_url) if spec.services.lighthouse.base_url else "None"
    wayfinder_base = repr(spec.services.wayfinder.base_url) if spec.services.wayfinder.base_url else "None"
    stm = spec.planner.short_term_memory
    stm_budget = stm.budget if stm else None
    stm_isolation = stm.isolation if stm else None

    content = _render_template(
        "config.py.jinja",
        {
            "agent_name": spec.agent.name,
            "primary_model": spec.llm.primary.model,
            "memory_enabled": spec.agent.flags.memory,
            "summarizer_enabled": bool(spec.llm.summarizer and spec.llm.summarizer.enabled),
            "reflection_enabled": bool(spec.llm.reflection and spec.llm.reflection.enabled),
            "reflection_quality_threshold": spec.llm.reflection.quality_threshold if spec.llm.reflection else 0.8,
            "reflection_max_revisions": spec.llm.reflection.max_revisions if spec.llm.reflection else 2,
            "memory_base_url": memory_base,
            "lighthouse_base_url": lighthouse_base,
            "wayfinder_base_url": wayfinder_base,
            "planner_max_iters": spec.planner.max_iters,
            "planner_hop_budget": spec.planner.hop_budget,
            "planner_absolute_max_parallel": spec.planner.absolute_max_parallel,
            "planner_stream_final_response": spec.planner.stream_final_response,
            "short_term_memory_enabled": bool(stm and stm.enabled),
            "short_term_memory_strategy": repr(stm.strategy if stm else "none"),
            "short_term_memory_full_zone_turns": stm_budget.full_zone_turns if stm_budget else 5,
            "short_term_memory_summary_max_tokens": stm_budget.summary_max_tokens if stm_budget else 1000,
            "short_term_memory_total_max_tokens": stm_budget.total_max_tokens if stm_budget else 10000,
            "short_term_memory_overflow_policy": repr(stm_budget.overflow_policy if stm_budget else "truncate_oldest"),
            "short_term_memory_tenant_key": repr(stm_isolation.tenant_key if stm_isolation else "tenant_id"),
            "short_term_memory_user_key": repr(stm_isolation.user_key if stm_isolation else "user_id"),
            "short_term_memory_session_key": repr(stm_isolation.session_key if stm_isolation else "session_id"),
            "short_term_memory_require_explicit_key": bool(
                stm_isolation.require_explicit_key if stm_isolation else True
            ),
            "short_term_memory_include_trajectory_digest": bool(stm.include_trajectory_digest if stm else True),
            "short_term_memory_summarizer_model": (
                repr(stm.summarizer_model) if stm and stm.summarizer_model is not None else "None"
            ),
            "short_term_memory_recovery_backlog_limit": stm.recovery_backlog_limit if stm else 20,
            "short_term_memory_retry_attempts": stm.retry_attempts if stm else 3,
            "short_term_memory_retry_backoff_base_s": stm.retry_backoff_base_s if stm else 2.0,
            "short_term_memory_degraded_retry_interval_s": stm.degraded_retry_interval_s if stm else 30.0,
        },
    )

    if dry_run:
        created.append(config_path.as_posix())
        return created, skipped

    wrote, path = _write_file(config_path, content, force=force)
    if wrote and path:
        created.append(path)
    elif not wrote:
        skipped.append(config_path.as_posix())

    return created, skipped


def _generate_env_example(
    project_dir: Path,
    spec: Spec,
    *,
    dry_run: bool,
    force: bool,
) -> tuple[list[str], list[str]]:
    created: list[str] = []
    skipped: list[str] = []
    env_path = project_dir / ".env.example"

    external_env_vars = _collect_external_env_vars(spec)
    stm = spec.planner.short_term_memory
    stm_budget = stm.budget if stm else None

    content = _render_template(
        "env.example.jinja",
        {
            "primary_model": spec.llm.primary.model,
            "primary_provider": spec.llm.primary.provider,
            "memory_enabled": str(spec.agent.flags.memory).lower(),
            "summarizer_enabled": str(bool(spec.llm.summarizer and spec.llm.summarizer.enabled)).lower(),
            "reflection_enabled": str(bool(spec.llm.reflection and spec.llm.reflection.enabled)).lower(),
            "memory_base_url": spec.services.memory_iceberg.base_url or "http://localhost:8000",
            "lighthouse_base_url": spec.services.lighthouse.base_url or "http://localhost:8081",
            "wayfinder_base_url": spec.services.wayfinder.base_url or "http://localhost:8082",
            "planner_max_iters": spec.planner.max_iters,
            "planner_hop_budget": spec.planner.hop_budget,
            "planner_absolute_max_parallel": spec.planner.absolute_max_parallel,
            "planner_stream_final_response": spec.planner.stream_final_response,
            "short_term_memory_enabled": str(bool(stm and stm.enabled)).lower(),
            "short_term_memory_strategy": stm.strategy if stm else "none",
            "short_term_memory_full_zone_turns": stm_budget.full_zone_turns if stm_budget else 5,
            "short_term_memory_summary_max_tokens": stm_budget.summary_max_tokens if stm_budget else 1000,
            "short_term_memory_total_max_tokens": stm_budget.total_max_tokens if stm_budget else 10000,
            "short_term_memory_overflow_policy": stm_budget.overflow_policy if stm_budget else "truncate_oldest",
            "short_term_memory_include_trajectory_digest": str(
                bool(stm.include_trajectory_digest if stm else True)
            ).lower(),
            "short_term_memory_recovery_backlog_limit": stm.recovery_backlog_limit if stm else 20,
            "short_term_memory_retry_attempts": stm.retry_attempts if stm else 3,
            "short_term_memory_retry_backoff_base_s": stm.retry_backoff_base_s if stm else 2.0,
            "short_term_memory_degraded_retry_interval_s": stm.degraded_retry_interval_s if stm else 30.0,
            "external_env_vars": external_env_vars,
        },
    )

    if dry_run:
        created.append(env_path.as_posix())
        return created, skipped

    wrote, path = _write_file(env_path, content, force=force)
    if wrote and path:
        created.append(path)
    elif not wrote:
        skipped.append(env_path.as_posix())

    return created, skipped


def _generate_env_setup_docs(
    project_dir: Path,
    spec: Spec,
    *,
    dry_run: bool,
    force: bool,
) -> tuple[list[str], list[str]]:
    """Generate ENV_SETUP.md with provider-specific API key documentation."""
    created: list[str] = []
    skipped: list[str] = []
    docs_path = project_dir / "ENV_SETUP.md"
    package_name = _normalise_package_name(spec.agent.name)
    stm = spec.planner.short_term_memory

    content = _render_template(
        "ENV_SETUP.md.jinja",
        {
            "agent_name": spec.agent.name,
            "package_name": package_name,
            "primary_model": spec.llm.primary.model,
            "primary_provider": spec.llm.primary.provider,
            "memory_enabled": str(spec.agent.flags.memory).lower(),
            "summarizer_enabled": str(bool(spec.llm.summarizer and spec.llm.summarizer.enabled)).lower(),
            "reflection_enabled": str(bool(spec.llm.reflection and spec.llm.reflection.enabled)).lower(),
            "short_term_memory_enabled": str(bool(stm and stm.enabled)).lower(),
            "planner_max_iters": spec.planner.max_iters,
            "planner_hop_budget": spec.planner.hop_budget,
            "planner_absolute_max_parallel": spec.planner.absolute_max_parallel,
            "planner_stream_final_response": spec.planner.stream_final_response,
        },
    )

    if dry_run:
        created.append(docs_path.as_posix())
        return created, skipped

    wrote, path = _write_file(docs_path, content, force=force)
    if wrote and path:
        created.append(path)
    elif not wrote:
        skipped.append(docs_path.as_posix())

    return created, skipped


def _scaffold_project(
    spec: Spec,
    *,
    output_dir: Path | None,
    dry_run: bool,
    force: bool,
    quiet: bool,
) -> tuple[Path, list[str], list[str], list[str]]:
    flags = spec.agent.flags
    result = run_new(
        name=spec.agent.name,
        template=spec.agent.template,
        force=force,
        dry_run=dry_run,
        output_dir=output_dir,
        quiet=quiet,
        with_streaming=flags.streaming,
        with_hitl=flags.hitl,
        with_a2a=flags.a2a,
        no_memory=not flags.memory,
    )
    project_dir = (output_dir or Path.cwd()) / spec.agent.name
    return project_dir, list(result.created), list(result.skipped), list(result.errors)


def run_generate(
    *,
    spec_path: Path,
    output_dir: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
    quiet: bool = False,
    verbose: bool = False,
) -> GenerateResult:
    """Generate tools and planner from an agent spec."""

    if verbose:
        click.echo(f"Loading spec from {spec_path}...")

    try:
        spec = load_spec(spec_path)
    except SpecValidationError:
        raise
    except Exception as exc:  # pragma: no cover - defensive guard
        raise CLIError(f"Failed to load spec: {exc}") from exc

    if verbose:
        click.echo(f"  Agent: {spec.agent.name}")
        click.echo(f"  Template: {spec.agent.template}")
        click.echo(f"  Tools: {len(spec.tools)}")
        click.echo(f"  External tools: {len(spec.external_tools.presets) + len(spec.external_tools.custom)}")
        click.echo(f"  Flows: {len(spec.flows)}")
        click.echo(f"  LLM: {spec.llm.primary.model}")

    created: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    if verbose:
        click.echo("\nScaffolding project structure...")

    project_dir, created_new, skipped_new, errors_new = _scaffold_project(
        spec,
        output_dir=output_dir,
        dry_run=dry_run,
        force=force,
        quiet=quiet,
    )
    created.extend(created_new)
    skipped.extend(skipped_new)
    errors.extend(errors_new)

    package_name = _normalise_package_name(spec.agent.name)

    if verbose:
        click.echo(f"  Package name: {package_name}")

    try:
        if verbose:
            click.echo("\nGenerating tools...")
        tool_created, tool_skipped = _generate_tools(project_dir, package_name, spec, dry_run=dry_run, force=force)
        created.extend(tool_created)
        skipped.extend(tool_skipped)
        if verbose:
            click.echo(f"  {len(tool_created)} tools generated")

        if verbose:
            click.echo("Generating external tools...")
        ext_created, ext_skipped = _generate_external_tools(
            project_dir, package_name, spec, dry_run=dry_run, force=force
        )
        created.extend(ext_created)
        skipped.extend(ext_skipped)
        if verbose and ext_created:
            click.echo(f"  {len(ext_created)} external tool files generated")

        if verbose:
            click.echo("Generating planner...")
        planner_created, planner_skipped = _generate_planner(
            project_dir, package_name, spec, dry_run=dry_run, force=force
        )
        created.extend(planner_created)
        skipped.extend(planner_skipped)

        if verbose:
            click.echo("Generating flows...")
        flow_created, flow_skipped = _generate_flows(project_dir, package_name, spec, dry_run=dry_run, force=force)
        created.extend(flow_created)
        skipped.extend(flow_skipped)
        if verbose and flow_created:
            click.echo(f"  {len(flow_created)} flows generated")

        if verbose:
            click.echo("Generating flow orchestrators...")
        orchestrator_created, orchestrator_skipped = _generate_flow_orchestrators(
            project_dir, package_name, spec, dry_run=dry_run, force=force
        )
        created.extend(orchestrator_created)
        skipped.extend(orchestrator_skipped)
        if verbose and orchestrator_created:
            click.echo(f"  {len(orchestrator_created)} orchestrators generated")

        if verbose:
            click.echo("Generating tool tests...")
        tool_test_created, tool_test_skipped = _generate_tool_tests(
            project_dir, package_name, spec, dry_run=dry_run, force=force
        )
        created.extend(tool_test_created)
        skipped.extend(tool_test_skipped)

        if verbose:
            click.echo("Generating flow tests...")
        flow_test_created, flow_test_skipped = _generate_flow_tests(
            project_dir, package_name, spec, dry_run=dry_run, force=force
        )
        created.extend(flow_test_created)
        skipped.extend(flow_test_skipped)

        # Generate conftest.py with correct package_name (always force to ensure
        # the DummyToolContext fixture imports from the correct package)
        if verbose:
            click.echo("Generating tests/conftest.py...")
        conftest_created, conftest_skipped = _generate_conftest(
            project_dir, package_name, spec.agent.name, dry_run=dry_run, force=True
        )
        created.extend(conftest_created)
        skipped.extend(conftest_skipped)

        # Config files MUST reflect spec values, so always force-overwrite them
        # even if they were created by scaffold (which uses generic defaults like "stub-llm").
        # This ensures spec values like llm.primary.model are correctly propagated.
        if verbose:
            click.echo("Generating config...")
        config_created, config_skipped = _generate_config(project_dir, package_name, spec, dry_run=dry_run, force=True)
        created.extend(config_created)
        skipped.extend(config_skipped)

        if verbose:
            click.echo("Generating .env.example...")
        env_created, env_skipped = _generate_env_example(project_dir, spec, dry_run=dry_run, force=True)
        created.extend(env_created)
        skipped.extend(env_skipped)

        if verbose:
            click.echo("Generating ENV_SETUP.md...")
        env_docs_created, env_docs_skipped = _generate_env_setup_docs(project_dir, spec, dry_run=dry_run, force=True)
        created.extend(env_docs_created)
        skipped.extend(env_docs_skipped)

        # Generate README with spec-aware content (always force to include agent description and tools)
        if verbose:
            click.echo("Generating README.md...")
        readme_created, readme_skipped = _generate_readme(project_dir, package_name, spec, dry_run=dry_run, force=True)
        created.extend(readme_created)
        skipped.extend(readme_skipped)

        # Persist spec as agent.yaml so playground can discover it
        if verbose:
            click.echo("Saving agent.yaml...")
        agent_yaml_path = project_dir / "agent.yaml"
        if not agent_yaml_path.exists() or force:
            if not dry_run:
                agent_yaml_path.write_text(spec_path.read_text(encoding="utf-8"), encoding="utf-8")
            created.append(agent_yaml_path.as_posix())
        else:
            skipped.append(agent_yaml_path.as_posix())
    except (GeneratorTemplateError, CLIError, SpecValidationError):
        raise
    except Exception as exc:  # pragma: no cover - defensive guard
        errors.append(str(exc))

    success = len(errors) == 0

    if verbose:
        click.echo(f"\nSummary: {len(created)} created, {len(skipped)} skipped, {len(errors)} errors")

    if not quiet:
        for path in created:
            click.echo(f"✓ Created {path}")
        for path in skipped:
            click.echo(f"⚠ Skipped {path} (exists, use --force to overwrite)")
        for path in errors:
            click.echo(f"✗ Error: {path}", err=True)

    return GenerateResult(
        success=success,
        created=created,
        skipped=skipped,
        errors=errors,
        project_dir=project_dir,
        package_name=package_name,
    )


class InitResult(NamedTuple):
    """Result of running `penguiflow generate --init`."""

    success: bool
    created: list[str]
    spec_path: Path
    project_dir: Path


def _load_init_template(name: str) -> str:
    """Load a template from the init/ subdirectory."""
    try:
        return resources.files("penguiflow.cli.templates").joinpath("init").joinpath(name).read_text()
    except FileNotFoundError as exc:  # pragma: no cover - defensive guard
        raise GeneratorTemplateError(f"Init template '{name}' not found.") from exc


def _render_init_template(name: str, context: dict[str, Any]) -> str:
    """Render a template from the init/ subdirectory."""
    try:
        from jinja2 import Environment
    except ImportError as exc:  # pragma: no cover
        raise CLIError(
            "Jinja2 is required for `penguiflow generate --init`.",
            hint="Install with `pip install penguiflow[cli]`.",
        ) from exc

    try:
        env = Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)
        template = env.from_string(_load_init_template(name))
        return template.render(**context)
    except Exception as exc:  # pragma: no cover - defensive
        raise GeneratorTemplateError(f"Failed to render init template '{name}': {exc}") from exc


def run_init_spec(
    agent_name: str,
    *,
    output_dir: Path | None = None,
    force: bool = False,
    quiet: bool = False,
) -> InitResult:
    """Initialize a spec workspace with sample spec and documentation.

    Creates:
    - {agent_name}.yaml - Sample spec file
    - PENGUIFLOW.md - Development guide
    - AGENTS.md - AI assistant instructions
    """
    # Normalize agent name
    agent_name = agent_name.lower().replace(" ", "-").replace("_", "-")
    package_name = _normalise_package_name(agent_name)

    # Determine output directory
    if output_dir is None:
        project_dir = Path.cwd() / agent_name
    else:
        project_dir = output_dir / agent_name

    # Create directory
    project_dir.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    context = {
        "agent_name": agent_name,
        "package_name": package_name,
    }

    # Generate spec file
    spec_path = project_dir / f"{agent_name}.yaml"
    if spec_path.exists() and not force:
        if not quiet:
            click.echo(f"⚠ Skipped {spec_path} (exists, use --force to overwrite)")
    else:
        spec_content = _render_init_template("sample_spec.yaml.jinja", context)
        spec_path.write_text(spec_content)
        created.append(spec_path.as_posix())
        if not quiet:
            click.echo(f"✓ Created {spec_path}")

    # Generate PENGUIFLOW.md
    guide_path = project_dir / "PENGUIFLOW.md"
    if guide_path.exists() and not force:
        if not quiet:
            click.echo(f"⚠ Skipped {guide_path} (exists, use --force to overwrite)")
    else:
        guide_content = _render_init_template("PENGUIFLOW.md.jinja", context)
        guide_path.write_text(guide_content)
        created.append(guide_path.as_posix())
        if not quiet:
            click.echo(f"✓ Created {guide_path}")

    # Generate AGENTS.md
    agents_path = project_dir / "AGENTS.md"
    if agents_path.exists() and not force:
        if not quiet:
            click.echo(f"⚠ Skipped {agents_path} (exists, use --force to overwrite)")
    else:
        agents_content = _render_init_template("AGENTS.md.jinja", context)
        agents_path.write_text(agents_content)
        created.append(agents_path.as_posix())
        if not quiet:
            click.echo(f"✓ Created {agents_path}")

    if not quiet:
        click.echo()
        click.echo(f"Spec workspace initialized in {project_dir}/")
        click.echo()
        click.echo("Next steps:")
        click.echo(f"  1. Edit {spec_path.name} to define your agent")
        click.echo(f"  2. Run: penguiflow generate --spec {spec_path}")
        click.echo()

    return InitResult(
        success=True,
        created=created,
        spec_path=spec_path,
        project_dir=project_dir,
    )


__all__ = ["run_generate", "run_init_spec", "GenerateResult", "InitResult", "GeneratorTemplateError"]
