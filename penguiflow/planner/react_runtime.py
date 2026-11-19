"""Runtime helpers for the React planner loop."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import warnings
from collections.abc import (
    Coroutine,
    Iterable,
    Iterator,
    Mapping,
    MutableMapping,
    Sequence,
)
from contextlib import contextmanager
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ValidationError

from ..catalog import NodeSpec, ToolLoadingMode
from ..rich_output.tools import RICH_OUTPUT_RENDER_TOOL_NAMES
from ..skills.models import SkillQuery
from ..skills.provider import build_skill_capability_context
from ..steering import (
    SteeringCancelled,
    SteeringEvent,
    SteeringEventType,
    SteeringInbox,
)
from . import prompts
from .artifact_handling import _ArtifactCollector, _SourceCollector
from .artifact_registry import ArtifactRegistry
from .constraints import _ConstraintTracker, _CostTracker
from .error_recovery import ErrorRecoveryConfig, step_with_recovery
from .llm_context_hooks import LLMContextHook, LLMContextHookInput
from .models import (
    PlannerAction,
    PlannerEvent,
    PlannerFinish,
    PlannerPause,
    ReflectionCritique,
    ToolDirectoryConfig,
    ToolGroupConfig,
    ToolHintsConfig,
    ToolSearchConfig,
    ToolVisibilityPolicy,
)
from .react_utils import _serialize_validation_errors
from .streaming import _StreamingArgsExtractor
from .tool_aliasing import build_aliased_tool_catalog, rewrite_action_node
from .tool_calls import execute_tool_call
from .trajectory import Trajectory, TrajectoryStep, extract_background_results
from .validation_repair import (
    _autofill_missing_args,
    _clear_rich_output_schema_repair_state,
    _coerce_tool_context,
    _get_rich_output_schema_error,
    _mark_rich_output_schema_arg_fill_attempted,
    _rich_output_schema_arg_fill_attempted,
    _validate_llm_context,
)

if TYPE_CHECKING:
    from ..llm.types import ContentPart
    from .memory import MemoryKey
    from .react import ReactPlanner

logger = logging.getLogger("penguiflow.planner")

_TASK_SERVICE_KEY = "task_service"
_MULTI_ACTION_BLOCKED_NODES = frozenset(
    {"final_response", "tasks.spawn"} | set(RICH_OUTPUT_RENDER_TOOL_NAMES)
)
_MULTI_ACTION_READONLY_SIDE_EFFECTS = frozenset({"pure", "read"})
_ACTIVATED_TOOLS_METADATA_KEY = "activated_tools"
_SKILLS_CONTEXT_METADATA_KEY = "skills_context"
_SKILLS_DIRECTORY_METADATA_KEY = "skills_directory"
_TOOLS_DIRECTORY_METADATA_KEY = "tools_directory"
_TOOL_HINTS_METADATA_KEY = "tool_hints"


def _hook_name(hook: object) -> str:
    """Return a hook's ``name``, falling back to its class name."""
    name = getattr(hook, "name", None)
    if isinstance(name, str) and name.strip():
        return name
    return hook.__class__.__name__


def _emit_hook_failed(
    planner: ReactPlanner, hook_name: str, error_type: str
) -> None:
    """Emit the ``llm_context_hook_failed`` planner event."""
    planner.emit_event(
        PlannerEvent(
            event_type="llm_context_hook_failed",
            ts=planner.time_source(),
            trajectory_step=0,
            extra={"hook_name": hook_name, "error_type": error_type},
        )
    )


async def _run_context_hook(
    planner: ReactPlanner,
    hook: LLMContextHook,
    *,
    hook_name: str,
    hook_input: LLMContextHookInput,
) -> Mapping[str, Any] | None:
    """Run one hook; return its patch, or ``None`` if it gave none."""
    try:
        patch: object = await hook.before_run(hook_input)
    except Exception as exc:
        logger.warning(
            "llm_context_hook_failed",
            extra={
                "hook_name": hook_name,
                "error": str(exc),
                "error_type": exc.__class__.__name__,
            },
        )
        _emit_hook_failed(planner, hook_name, exc.__class__.__name__)
        return None
    if patch is None:
        return None
    if not isinstance(patch, Mapping):
        logger.warning(
            "llm_context_hook_invalid_patch",
            extra={
                "hook_name": hook_name,
                "patch_type": type(patch).__name__,
            },
        )
        _emit_hook_failed(planner, hook_name, "InvalidPatchType")
        return None
    return patch


def _merge_context_patch(
    planner: ReactPlanner,
    *,
    hook_name: str,
    merged: dict[str, Any],
    patch: Mapping[str, Any],
    overwrite: bool,
) -> dict[str, Any] | None:
    """Return ``merged`` updated with ``patch``.

    Returns ``None`` when the patch changes nothing or the merged context is
    not JSON-serialisable.
    """
    candidate = dict(merged)
    keys_added: list[str] = []
    for key, value in patch.items():
        if key in candidate and not overwrite:
            continue
        if key not in candidate:
            keys_added.append(str(key))
        candidate[key] = value
    if candidate == merged:
        return None

    try:
        payload = json.dumps(candidate, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "llm_context_hook_patch_not_json_serialisable",
            extra={
                "hook_name": hook_name,
                "error": str(exc),
                "error_type": exc.__class__.__name__,
            },
        )
        _emit_hook_failed(planner, hook_name, "NotJSONSerialisable")
        return None

    planner.emit_event(
        PlannerEvent(
            event_type="llm_context_hook_applied",
            ts=planner.time_source(),
            trajectory_step=0,
            extra={
                "hook_name": hook_name,
                "keys_added": keys_added,
                "bytes_est": len(payload),
            },
        )
    )
    return candidate


async def _apply_llm_context_hooks(
    planner: ReactPlanner,
    *,
    query: str,
    llm_context: dict[str, Any] | None,
    tool_context: Mapping[str, Any],
    memory_key: MemoryKey | None,
) -> dict[str, Any] | None:
    hooks = getattr(planner, "_llm_context_hooks", None)
    if not hooks:
        return llm_context

    merged: dict[str, Any] = dict(llm_context or {})
    for hook in hooks:
        hook_name = _hook_name(hook)
        patch = await _run_context_hook(
            planner,
            hook,
            hook_name=hook_name,
            hook_input=LLMContextHookInput(
                query=query,
                llm_context=merged,
                tool_context=tool_context,
                memory_key=memory_key,
            ),
        )
        if patch is None:
            continue
        updated = _merge_context_patch(
            planner,
            hook_name=hook_name,
            merged=merged,
            patch=patch,
            overwrite=bool(getattr(hook, "overwrite", False)),
        )
        if updated is not None:
            merged = updated
    return merged


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Outcome of looking for a deterministic (auto-seq) transition."""

    status: Literal["unique", "ambiguous", "none", "skipped"]
    selected_action: PlannerAction | None = None
    candidates: list[str] | None = None
    reason: str | None = None


def _auto_seq_eligible(spec: NodeSpec, *, read_only_only: bool) -> bool:
    """Whether a tool opted into auto-seq and may run without the LLM."""
    if spec.name in _MULTI_ACTION_BLOCKED_NODES:
        return False
    if spec.extra.get("auto_seq") is not True:
        return False
    return (
        not read_only_only
        or spec.side_effects in _MULTI_ACTION_READONLY_SIDE_EFFECTS
    )


def _validate_auto_seq_args(
    spec: NodeSpec, payload: Mapping[str, Any]
) -> tuple[NodeSpec, BaseModel] | None:
    """Validate the previous observation as ``spec``'s arguments."""
    try:
        return spec, spec.args_model.model_validate(payload)
    except ValidationError:
        return None


def _detect_deterministic_transition(
    planner: ReactPlanner, trajectory: Trajectory
) -> DetectionResult:
    if not trajectory.steps:
        return DetectionResult(status="skipped", reason="no_steps")

    last_step = trajectory.steps[-1]
    if last_step.action.next_node == "parallel":
        return DetectionResult(
            status="skipped", reason="previous_step_parallel"
        )

    payload = last_step.serialise_for_llm()
    if not isinstance(payload, Mapping):
        return DetectionResult(
            status="skipped", reason="non_structured_observation"
        )

    specs = list(getattr(planner, "specs", []) or [])
    read_only_only = bool(getattr(planner, "auto_seq_read_only_only", True))
    matches = [
        match
        for spec in specs
        if _auto_seq_eligible(spec, read_only_only=read_only_only)
        and (match := _validate_auto_seq_args(spec, payload)) is not None
    ]

    if not matches:
        return DetectionResult(status="none")

    if len(matches) > 1:
        return DetectionResult(
            status="ambiguous", candidates=[spec.name for spec, _ in matches]
        )

    spec, validated = matches[0]
    action = PlannerAction(
        next_node=spec.name,
        args=validated.model_dump(mode="json"),
    )
    return DetectionResult(status="unique", selected_action=action)


def _payload_fingerprint(payload: Mapping[str, Any]) -> str:
    type_name = type(payload).__name__
    keys = sorted(str(key) for key in payload)
    raw = json.dumps(
        {"type": type_name, "keys": keys}, ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _emit_auto_seq_detection_event(
    planner: ReactPlanner, trajectory: Trajectory, result: DetectionResult
) -> None:
    event_type_map = {
        "unique": "auto_seq_detected_unique",
        "ambiguous": "auto_seq_detected_ambiguous",
        "none": "auto_seq_detected_none",
        "skipped": "auto_seq_skipped",
    }
    event_type = event_type_map.get(result.status, "auto_seq_detected_none")
    extra: dict[str, Any] = {}
    payload: Any | None = None
    if trajectory.steps:
        payload = trajectory.steps[-1].serialise_for_llm()
    if isinstance(payload, Mapping):
        extra["payload_fingerprint"] = _payload_fingerprint(payload)
        extra["payload_keys_count"] = len(payload)
        extra["payload_type"] = type(payload).__name__
    else:
        extra["payload_type"] = (
            type(payload).__name__ if payload is not None else "none"
        )

    if result.status == "unique" and result.selected_action is not None:
        extra["tool_name"] = result.selected_action.next_node
    elif result.status == "ambiguous" and result.candidates:
        extra["candidate_count"] = len(result.candidates)
        extra["candidates"] = list(result.candidates)
    elif result.status == "skipped" and result.reason:
        extra["reason"] = result.reason

    planner.emit_event(
        PlannerEvent(
            event_type=event_type,
            ts=planner.time_source(),
            trajectory_step=len(trajectory.steps),
            extra=extra,
        )
    )


def _tool_search_config(planner: ReactPlanner) -> ToolSearchConfig | None:
    config = getattr(planner, "tool_search_config", None)
    if isinstance(config, ToolSearchConfig) and config.enabled:
        return config
    return None


async def _prepare_skills_context(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    allowed_names: set[str],
    all_tool_names: Iterable[str],
) -> None:
    config = getattr(planner, "skills_config", None)
    provider = getattr(planner, "skills_provider", None)
    if (
        config is None
        or not getattr(config, "enabled", False)
        or provider is None
    ):
        return
    tool_context = trajectory.tool_context or {}
    capability_context = build_skill_capability_context(
        execution_specs=getattr(planner, "execution_spec_by_name", {}),
        all_tool_names=all_tool_names,
        allowed_tool_names=allowed_names,
    )
    if _SKILLS_CONTEXT_METADATA_KEY not in trajectory.metadata:
        query = SkillQuery(task=trajectory.query, top_k=config.top_k)
        response = await provider.get_relevant(
            query,
            tool_context=tool_context,
            capability_context=capability_context,
        )
        if response.formatted_context:
            trajectory.metadata[_SKILLS_CONTEXT_METADATA_KEY] = (
                response.formatted_context
            )
        planner.emit_event(
            PlannerEvent(
                event_type="skills_retrieved",
                ts=planner.time_source(),
                trajectory_step=len(trajectory.steps),
                extra={
                    "skill_count": len(response.skills),
                    "top_k_used": response.top_k,
                    "raw_tokens_est": response.raw_tokens_est,
                    "final_tokens_est": response.final_tokens_est,
                    "was_summarized": response.was_summarized,
                },
            )
        )
    directory_cfg = getattr(config, "directory", None)
    if (
        directory_cfg is not None
        and getattr(directory_cfg, "enabled", False)
        and _SKILLS_DIRECTORY_METADATA_KEY not in trajectory.metadata
    ):
        entries = await provider.directory(
            directory_cfg,
            tool_context=tool_context,
            capability_context=capability_context,
        )
        if entries:
            rendered = prompts.render_skill_directory(
                [entry.model_dump(mode="json") for entry in entries],
                include_fields=directory_cfg.include_fields,
            )
            if rendered:
                trajectory.metadata[_SKILLS_DIRECTORY_METADATA_KEY] = rendered
                planner.emit_event(
                    PlannerEvent(
                        event_type="skill_directory_rendered",
                        ts=planner.time_source(),
                        trajectory_step=len(trajectory.steps),
                        extra={"count": len(entries)},
                    )
                )


def _current_turn_text(trajectory: Trajectory) -> str:
    value = getattr(trajectory, "resume_user_input", None)
    if isinstance(value, str) and value.strip():
        return value
    return trajectory.query


def _namespace_from_tool_name(name: str) -> str:
    if "." in name:
        return name.split(".", 1)[0]
    return "core"


def _match_group(spec: NodeSpec, group: ToolGroupConfig) -> bool:
    if group.tool_names and spec.name in set(group.tool_names):
        return True
    if group.match_name_patterns and any(
        fnmatch(spec.name, pattern) for pattern in group.match_name_patterns
    ):
        return True
    if group.match_namespaces:
        namespace = _namespace_from_tool_name(spec.name)
        if namespace in set(group.match_namespaces):
            return True
    if group.match_tags:
        tags = set(spec.tags)
        if set(group.match_tags) & tags:
            return True
    return False


def _build_tool_directory(
    specs: Sequence[NodeSpec],
    *,
    config: ToolDirectoryConfig,
) -> list[dict[str, Any]]:
    if not specs:
        return []

    remaining = {spec.name for spec in specs}
    spec_by_name = {spec.name: spec for spec in specs}
    groups: list[dict[str, Any]] = []

    # Explicit groups first
    for group in config.groups:
        matched = [
            spec.name
            for spec in specs
            if spec.name in remaining and _match_group(spec, group)
        ]
        if not matched:
            continue
        for name in matched:
            remaining.discard(name)
        matched_sorted = sorted(matched, key=lambda item: (len(item), item))
        preview = matched_sorted[: max(int(config.max_tools_per_group), 0)]
        groups.append(
            {
                "name": group.name,
                "title": group.title,
                "trigger": group.trigger,
                "task_type": group.task_type,
                "tool_count": len(matched)
                if config.include_tool_counts
                else None,
                "tools": preview,
            }
        )

    if config.include_default_groups and remaining:
        by_ns: dict[str, list[str]] = {}
        for name in sorted(remaining):
            ns = _namespace_from_tool_name(name)
            by_ns.setdefault(ns, []).append(name)
        for ns, names in sorted(
            by_ns.items(), key=lambda item: (item[0] != "core", item[0])
        ):
            preview = names[: max(int(config.max_tools_per_group), 0)]
            trigger = None
            if ns == "core":
                trigger = "Built-in tools"
            else:
                # Best-effort hint; avoid expensive inference.
                sample = spec_by_name.get(names[0])
                if sample is not None and "mcp" in set(sample.tags):
                    trigger = f"{ns} MCP tools"
                else:
                    trigger = f"{ns} tools"
            groups.append(
                {
                    "name": ns,
                    "title": None,
                    "trigger": trigger,
                    "task_type": None,
                    "tool_count": len(names)
                    if config.include_tool_counts
                    else None,
                    "tools": preview,
                }
            )

    # Cap after assembly, keeping explicit groups first.
    max_groups = max(int(config.max_groups), 1)
    return groups[:max_groups]


async def _prepare_tool_discovery_context(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    allowed_names: set[str],
) -> None:
    config = _tool_search_config(planner)
    if config is None:
        return
    cache = getattr(planner, "tool_search_cache", None)
    if cache is None:
        return

    turn_text = _current_turn_text(trajectory)

    hints_cfg = getattr(config, "hints", None)
    if (
        isinstance(hints_cfg, ToolHintsConfig)
        and hints_cfg.enabled
        and _TOOL_HINTS_METADATA_KEY not in trajectory.metadata
    ):
        results, effective = cache.search(
            turn_text,
            search_type=hints_cfg.search_type,
            limit=hints_cfg.top_k,
            include_always_loaded=bool(hints_cfg.include_always_loaded),
            allowed_names=set(allowed_names),
        )
        rendered = prompts.render_tool_hints(results, query=turn_text)
        if rendered:
            trajectory.metadata[_TOOL_HINTS_METADATA_KEY] = rendered
        planner.emit_event(
            PlannerEvent(
                event_type="tool_hints_generated",
                ts=planner.time_source(),
                trajectory_step=len(trajectory.steps),
                extra={
                    "query": turn_text,
                    "requested_search_type": hints_cfg.search_type,
                    "effective_search_type": effective,
                    "count": len(results),
                    "top_k": hints_cfg.top_k,
                },
            )
        )

    directory_cfg = getattr(config, "directory", None)
    if (
        isinstance(directory_cfg, ToolDirectoryConfig)
        and directory_cfg.enabled
        and _TOOLS_DIRECTORY_METADATA_KEY not in trajectory.metadata
    ):
        base_specs: Sequence[NodeSpec] = list(
            getattr(planner, "execution_specs", None) or []
        )
        allowed_specs = [
            spec for spec in base_specs if spec.name in allowed_names
        ]
        groups = _build_tool_directory(allowed_specs, config=directory_cfg)
        rendered = prompts.render_tool_directory(groups)
        if rendered:
            trajectory.metadata[_TOOLS_DIRECTORY_METADATA_KEY] = rendered
            planner.emit_event(
                PlannerEvent(
                    event_type="tool_directory_rendered",
                    ts=planner.time_source(),
                    trajectory_step=len(trajectory.steps),
                    extra={"group_count": len(groups)},
                )
            )


def _allowed_names_from_policy(
    base_specs: Sequence[NodeSpec],
    tool_visibility: ToolVisibilityPolicy | None,
    tool_context: Mapping[str, Any],
) -> set[str]:
    if tool_visibility is None:
        return {spec.name for spec in base_specs}

    base_by_name = {spec.name: spec for spec in base_specs}
    visible = tool_visibility.visible_tools(base_specs, tool_context)
    return {spec.name for spec in visible} & set(base_by_name)


def _is_always_loaded(spec: NodeSpec, config: ToolSearchConfig | None) -> bool:
    if spec.loading_mode == ToolLoadingMode.ALWAYS:
        return True
    if not config:
        return True
    return any(
        fnmatch(spec.name, pattern)
        for pattern in config.always_loaded_patterns
    )


def _compute_visible_specs(
    base_specs: Sequence[NodeSpec],
    *,
    allowed_names: set[str],
    activated_names: set[str],
    config: ToolSearchConfig | None,
) -> list[NodeSpec]:
    if config is None:
        return [spec for spec in base_specs if spec.name in allowed_names]
    visible_specs: list[NodeSpec] = []
    for spec in base_specs:
        if spec.name not in allowed_names:
            continue
        if _is_always_loaded(spec, config) or spec.name in activated_names:
            visible_specs.append(spec)
    return visible_specs


def _apply_visible_catalog(
    planner: ReactPlanner, visible_specs: Sequence[NodeSpec]
) -> None:
    planner.specs = list(visible_specs)
    spec_by_name, catalog_records, alias_to_real = build_aliased_tool_catalog(
        planner.specs
    )
    planner.spec_by_name = spec_by_name
    planner.catalog_records = catalog_records
    planner.tool_aliases = alias_to_real

    planning_hints = getattr(planner, "planning_hints", None)
    hints_payload = None
    if planning_hints is not None and not getattr(
        planning_hints, "empty", True
    ):
        hints_payload = planning_hints.to_prompt_payload()
    planner.system_prompt = prompts.build_system_prompt(
        planner.catalog_records,
        extra=getattr(planner, "system_prompt_extra", None),
        planning_hints=hints_payload,
        tool_examples=getattr(planner, "tool_examples_config", None),
        structured_final_schema=getattr(
            planner, "final_response_schema", None
        ),
        tool_call_mode=getattr(planner, "tool_call_mode", "prompted"),
    )

    guardrail_context = getattr(planner, "guardrail_context", None)
    if guardrail_context is not None:
        tool_ctx = getattr(guardrail_context, "tool_context", None)
        if isinstance(tool_ctx, dict):
            tool_ctx["available_tools"] = [spec.name for spec in planner.specs]

    register_cb = getattr(planner, "register_resource_callbacks", None)
    if callable(register_cb):
        register_cb()


def _refresh_visible_catalog(
    planner: ReactPlanner,
    *,
    allowed_names: set[str],
) -> None:
    base_specs: Sequence[NodeSpec] = list(
        getattr(planner, "execution_specs", None)
        or getattr(planner, "specs", None)
        or []
    )
    activated = getattr(planner, "active_tool_names", None)
    if activated is None:
        activated = set()
        planner.active_tool_names = activated
    config = _tool_search_config(planner)
    visible_specs = _compute_visible_specs(
        base_specs,
        allowed_names=allowed_names,
        activated_names=activated,
        config=config,
    )
    _apply_visible_catalog(planner, visible_specs)


def _guardrail_tools(guardrail_context: object) -> dict[str, Any] | None:
    """Return the guardrail context's ``tool_context`` dict, if any."""
    tool_ctx = getattr(guardrail_context, "tool_context", None)
    return tool_ctx if isinstance(tool_ctx, dict) else None


@dataclass(frozen=True, slots=True)
class _CatalogSnapshot:
    """The planner's visible catalog, saved to restore after a run."""

    specs: list[NodeSpec] | None
    spec_by_name: dict[str, NodeSpec] | None
    catalog_records: list[dict[str, Any]] | None
    tool_aliases: dict[str, str] | None
    system_prompt: str | None
    allowed_names: set[str] | None
    guardrail_context: object
    guardrail_available: list[str] | None

    @classmethod
    def capture(cls, planner: ReactPlanner) -> _CatalogSnapshot:
        """Save the catalog attributes the visibility scope overrides."""
        guardrail_context = getattr(planner, "guardrail_context", None)
        tool_ctx = _guardrail_tools(guardrail_context)
        available = (
            tool_ctx.get("available_tools") if tool_ctx is not None else None
        )
        return cls(
            specs=getattr(planner, "specs", None),
            spec_by_name=getattr(planner, "spec_by_name", None),
            catalog_records=getattr(planner, "catalog_records", None),
            tool_aliases=getattr(planner, "tool_aliases", None),
            system_prompt=getattr(planner, "system_prompt", None),
            allowed_names=getattr(
                planner, "tool_visibility_allowed_names", None
            ),
            guardrail_context=guardrail_context,
            guardrail_available=list(available)
            if isinstance(available, list)
            else None,
        )

    def restore(self, planner: ReactPlanner) -> None:
        """Put the saved catalog attributes back on the planner."""
        if self.specs is not None:
            planner.specs = self.specs
        if self.spec_by_name is not None:
            planner.spec_by_name = self.spec_by_name
        if self.catalog_records is not None:
            planner.catalog_records = self.catalog_records
        if self.tool_aliases is not None:
            planner.tool_aliases = self.tool_aliases
        if self.system_prompt is not None:
            planner.system_prompt = self.system_prompt
        planner.tool_visibility_allowed_names = self.allowed_names
        if self.guardrail_available is None:
            return
        tool_ctx = _guardrail_tools(self.guardrail_context)
        if tool_ctx is not None:
            tool_ctx["available_tools"] = self.guardrail_available


@contextmanager
def _tool_visibility_scope(
    planner: ReactPlanner,
    *,
    tool_visibility: ToolVisibilityPolicy | None,
    tool_context: Mapping[str, Any],
) -> Iterator[None]:
    base_specs: Sequence[NodeSpec] = list(
        getattr(planner, "execution_specs", None)
        or getattr(planner, "specs", None)
        or []
    )
    allowed_names = _allowed_names_from_policy(
        base_specs, tool_visibility, tool_context
    )
    snapshot = _CatalogSnapshot.capture(planner)
    try:
        planner.tool_visibility_allowed_names = allowed_names
        _refresh_visible_catalog(planner, allowed_names=allowed_names)

        yield
    finally:
        snapshot.restore(planner)


def _alternate_candidates(action: PlannerAction) -> list[PlannerAction]:
    """Convert action.alternate_actions into PlannerAction instances."""
    raw = action.alternate_actions
    if not raw:
        return []
    candidates: list[PlannerAction] = []
    for item in raw:
        next_node = item.get("next_node")
        args = item.get("args")
        if not isinstance(next_node, str) or not next_node.strip():
            continue
        if not isinstance(args, dict):
            args = {}
        candidates.append(PlannerAction(next_node=next_node, args=dict(args)))
    return candidates


def _candidate_is_executable_tool(
    planner: ReactPlanner, candidate: PlannerAction
) -> bool:
    if candidate.next_node in _MULTI_ACTION_BLOCKED_NODES:
        return False
    if not candidate.is_tool_call():
        return False
    spec = getattr(planner, "spec_by_name", {}).get(candidate.next_node)
    return spec is not None


def _maybe_select_alternate_action(
    planner: ReactPlanner,
    action: PlannerAction,
    *,
    prefer_same_tool: bool,
    require_read_only: bool,
) -> tuple[PlannerAction | None, str | None]:
    """Pick an alternate candidate action that can be executed."""
    candidates = _alternate_candidates(action)
    if not candidates:
        return None, None

    # Prefer candidates that target the same tool (likely args correction).
    ordered: list[PlannerAction] = []
    if prefer_same_tool and action.next_node:
        ordered.extend(
            [c for c in candidates if c.next_node == action.next_node]
        )
    ordered.extend([c for c in candidates if c.next_node != action.next_node])

    for candidate in ordered:
        if not _is_runnable_candidate(
            planner, candidate, read_only_only=require_read_only
        ):
            continue
        spec = planner.spec_by_name[candidate.next_node]
        if _args_validate(spec, candidate.args):
            return candidate, spec.name
    return None, None


def _args_validate(spec: NodeSpec, args: Mapping[str, Any]) -> bool:
    """Whether ``args`` validate against the tool's arguments model."""
    try:
        spec.args_model.model_validate(args)
    except ValidationError:
        return False
    return True


def _apply_steering(planner: ReactPlanner, trajectory: Trajectory) -> None:
    steering: SteeringInbox | None = getattr(planner, "steering", None)
    if steering is None:
        logger.info("_apply_steering: no steering inbox connected to planner")
        return

    if steering.cancelled:
        raise SteeringCancelled(steering.cancel_reason)

    has_pending = steering.has_event()
    logger.info(
        "steering_check",
        extra={"has_pending": has_pending, "step": len(trajectory.steps)},
    )
    events = steering.drain()
    if not events:
        return

    for event in events:
        _apply_steering_event(planner, trajectory, event=event)

        _record_steering_history(trajectory, event=event)


def _apply_steering_event(
    planner: ReactPlanner, trajectory: Trajectory, *, event: SteeringEvent
) -> None:
    """Apply one steering event to the trajectory."""
    planner.emit_event(
        PlannerEvent(
            event_type="steering_received",
            ts=planner.time_source(),
            trajectory_step=len(trajectory.steps),
            extra={
                "event_id": event.event_id,
                "event_type": event.event_type.value,
                "source": event.source,
            },
        )
    )
    if event.event_type == SteeringEventType.CANCEL:
        raise SteeringCancelled(
            str(event.payload.get("reason") or "cancelled")
        )
    if event.event_type in {
        SteeringEventType.INJECT_CONTEXT,
        SteeringEventType.REDIRECT,
    }:
        if event.event_type == SteeringEventType.REDIRECT:
            new_goal = (
                event.payload.get("instruction")
                or event.payload.get("goal")
                or event.payload.get("query")
            )
            if isinstance(new_goal, str) and new_goal.strip():
                trajectory.query = new_goal.strip()
        trajectory.steering_inputs.append(event.to_injection())
    elif event.event_type == SteeringEventType.USER_MESSAGE:
        # Rich injection with task context and interpretation guidance
        user_text = event.payload.get("text", "")
        active_tasks = event.payload.get("active_tasks", [])
        injection = {
            "steering": {
                "event_id": event.event_id,
                "event_type": "USER_MESSAGE",
                "user_text": user_text,
                "active_background_tasks": active_tasks,
                "instructions": (
                    "The user sent this message while you are working. "
                    "Interpret their intent: Are they providing "
                    "clarification, "
                    "changing direction, asking about status, or "
                    "controlling "
                    "a background task? If referencing a background task "
                    "ambiguously, "
                    "use select_option to let them choose which one. "
                    "Acknowledge naturally and act accordingly."
                ),
            }
        }
        trajectory.steering_inputs.append(
            json.dumps(injection, ensure_ascii=False)
        )


def _record_steering_history(
    trajectory: Trajectory, *, event: SteeringEvent
) -> None:
    """Record the event for observability without re-injecting it."""
    # Record steering history for observability/debugging (but do not
    # re-inject indefinitely).
    if isinstance(trajectory.metadata, MutableMapping):
        history = trajectory.metadata.get("steering_history")
        if not isinstance(history, list):
            history = []
            trajectory.metadata["steering_history"] = history
        history.append(
            {
                "event_id": event.event_id,
                "event_type": event.event_type.value,
                "source": event.source,
                "payload": dict(event.payload or {}),
                "trajectory_step": len(trajectory.steps),
            }
        )
        if len(history) > 50:
            del history[: len(history) - 50]


def _init_tool_activation_state(
    planner: ReactPlanner,
    trajectory: Trajectory,
    tool_context: Mapping[str, Any],
    *,
    resume: bool,
) -> set[str] | None:
    config = _tool_search_config(planner)
    if config is None:
        planner.active_tool_names = None
        return None

    if config.activation_scope == "session":
        session_id = tool_context.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            msg = (
                "activation_scope='session' requires "
                "tool_context['session_id']"
            )
            raise ValueError(msg)
        session_activated = planner.session_tool_activations.setdefault(
            session_id, set()
        )
        planner.active_tool_names = session_activated
        return session_activated

    activated: set[str]
    if resume:
        raw = trajectory.metadata.get(_ACTIVATED_TOOLS_METADATA_KEY, [])
        if isinstance(raw, list):
            activated = {str(item) for item in raw if isinstance(item, str)}
        else:
            activated = set()
    else:
        activated = set()
    trajectory.metadata[_ACTIVATED_TOOLS_METADATA_KEY] = sorted(activated)
    planner.active_tool_names = activated
    return activated


def _maybe_activate_tool(
    planner: ReactPlanner,
    tool_name: str,
    trajectory: Trajectory,
) -> bool:
    config = _tool_search_config(planner)
    if config is None:
        return False

    execution_specs: dict[str, NodeSpec] = getattr(
        planner, "execution_spec_by_name", {}
    )
    spec = execution_specs.get(tool_name)
    if spec is None:
        return False

    allowed_names = getattr(planner, "tool_visibility_allowed_names", None)
    if allowed_names is not None and tool_name not in allowed_names:
        planner.emit_event(
            PlannerEvent(
                event_type="tool_activation_denied",
                ts=planner.time_source(),
                trajectory_step=len(trajectory.steps),
                extra={"reason": "visibility_policy"},
            )
        )
        return False

    if _is_always_loaded(spec, config):
        return False
    if spec.loading_mode != ToolLoadingMode.DEFERRED:
        return False

    activated = getattr(planner, "active_tool_names", None)
    if activated is None:
        activated = set()
        planner.active_tool_names = activated

    if tool_name not in activated:
        activated.add(tool_name)
        if config.activation_scope == "run":
            trajectory.metadata[_ACTIVATED_TOOLS_METADATA_KEY] = sorted(
                activated
            )
        _refresh_visible_catalog(
            planner, allowed_names=allowed_names or set(execution_specs)
        )
        planner.emit_event(
            PlannerEvent(
                event_type="tool_activated",
                ts=planner.time_source(),
                trajectory_step=len(trajectory.steps),
                extra={
                    "tool_name": tool_name,
                    "activation_scope": config.activation_scope,
                    "source": "tool_call",
                    "reason": "first_use",
                },
            )
        )

    return True


# Strong references to fire-and-forget persistence tasks, so they are not
# garbage collected before they finish.
_PERSISTENCE_TASKS: set[asyncio.Task[None]] = set()


def _spawn_persistence(
    loop: asyncio.AbstractEventLoop,
    coro: Coroutine[Any, Any, None],
    *,
    name: str,
) -> None:
    """Run ``coro`` in the background, keeping a reference until it ends."""
    task = loop.create_task(coro, name=name)
    _PERSISTENCE_TASKS.add(task)
    task.add_done_callback(_PERSISTENCE_TASKS.discard)


async def _persist_trajectory(
    planner: ReactPlanner, trajectory: Trajectory
) -> None:
    """Save the trajectory to the StateStore, if any.

    Designed to run as a background task.
    """
    try:
        store = getattr(planner, "state_store", None)
        if store is None:
            return
        saver = getattr(store, "save_trajectory", None)
        if saver is None:
            return
        ctx = trajectory.tool_context or {}
        trace_id = ctx.get("trace_id")
        session_id = ctx.get("session_id")
        if not trace_id or not session_id:
            return
        await saver(trace_id, session_id, trajectory)
    except Exception:
        logger.warning(
            "Background trajectory persistence failed", exc_info=True
        )


async def _persist_events(
    events: list[PlannerEvent], planner: ReactPlanner, trace_id: str
) -> None:
    """Flush planner events to the StateStore, as a background task.

    Receives an already-snapshot list of events (buffer was cleared by the
    caller before spawning this task) so there is no shared-state concern.
    """
    try:
        store = getattr(planner, "state_store", None)
        if store is None:
            return
        saver = getattr(store, "save_planner_event", None)
        if saver is None:
            return
        for event in events:
            await saver(trace_id, event)
    except Exception:
        logger.warning(
            "Background planner-event persistence failed", exc_info=True
        )


def _fire_persistence_tasks(
    planner: ReactPlanner, trajectory: Trajectory, result: object | None
) -> None:
    """Spawn background tasks persisting the trajectory and the events.

    This is synchronous and returns immediately -- no awaiting.
    """
    loop = asyncio.get_running_loop()
    ctx = trajectory.tool_context or {}
    trace_id = ctx.get("trace_id")
    session_id = ctx.get("session_id")

    # Trajectory -- on PlannerFinish and PlannerPause
    if (
        isinstance(result, (PlannerFinish, PlannerPause))
        and trace_id
        and session_id
    ):
        _spawn_persistence(
            loop,
            _persist_trajectory(planner, trajectory),
            name="penguiflow-persist-trajectory",
        )

    # Events -- on any exit path (finish/pause/error/cancel)
    buf = getattr(planner, "event_buffer", None)
    if buf and trace_id:
        events = list(buf)  # snapshot
        buf.clear()  # clear immediately so planner can reuse
        _spawn_persistence(
            loop,
            _persist_events(events, planner, trace_id),
            name="penguiflow-persist-events",
        )
    elif buf:
        # no trace_id -> discard, but still clear to avoid unbounded growth
        buf.clear()


async def run(
    planner: ReactPlanner,
    query: str,
    *,
    input_parts: Sequence[ContentPart] | None = None,
    llm_context: Mapping[str, Any] | None = None,
    context_meta: Mapping[str, Any] | None = None,
    tool_context: Mapping[str, Any] | None = None,
    memory_key: MemoryKey | None = None,
    tool_visibility: ToolVisibilityPolicy | None = None,
) -> PlannerFinish | PlannerPause:
    """Run the planner on ``query`` and return its finish or pause."""
    # Handle backward compatibility
    if context_meta is not None:
        warnings.warn(
            "context_meta parameter is deprecated. Use llm_context instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        if llm_context is None:
            llm_context = context_meta

    logger.debug("planner_run_start", extra={"query": query})
    normalised_tool_context = _coerce_tool_context(tool_context)
    normalised_llm_context = _validate_llm_context(llm_context)
    resolved_key = planner.resolve_memory_key(
        memory_key, normalised_tool_context
    )
    normalised_llm_context = await planner.apply_memory_context(
        normalised_llm_context, resolved_key
    )
    normalised_llm_context = await _apply_llm_context_hooks(
        planner,
        query=query,
        llm_context=normalised_llm_context,
        tool_context=normalised_tool_context or {},
        memory_key=resolved_key,
    )
    cleaned_llm_context, extracted_results = extract_background_results(
        normalised_llm_context
    )
    planner.cost_tracker = _CostTracker()
    trajectory = Trajectory(
        query=query,
        llm_context=cleaned_llm_context,
        tool_context=normalised_tool_context,
        input_parts=tuple(input_parts or ()),
    )
    if extracted_results:
        trajectory.background_results.update(extracted_results)
    planner.artifact_registry = ArtifactRegistry.from_snapshot(
        trajectory.metadata.get("artifact_registry")
    )
    planner.artifact_registry.write_snapshot(trajectory.metadata)
    error_recovery_cfg = getattr(planner, "error_recovery_config", None)
    previous_active = getattr(planner, "active_tool_names", None)
    _init_tool_activation_state(
        planner, trajectory, normalised_tool_context or {}, resume=False
    )
    result: PlannerFinish | PlannerPause | None = None
    try:
        base_specs: Sequence[NodeSpec] = list(
            getattr(planner, "execution_specs", None)
            or getattr(planner, "specs", None)
            or []
        )
        allowed_names = _allowed_names_from_policy(
            base_specs, tool_visibility, normalised_tool_context or {}
        )
        all_tool_names = {spec.name for spec in base_specs}
        await _prepare_tool_discovery_context(
            planner,
            trajectory,
            allowed_names=allowed_names,
        )
        await _prepare_skills_context(
            planner,
            trajectory,
            allowed_names=allowed_names,
            all_tool_names=all_tool_names,
        )
        with _tool_visibility_scope(
            planner,
            tool_visibility=tool_visibility,
            tool_context=normalised_tool_context or {},
        ):
            result = await run_loop(
                planner,
                trajectory,
                tracker=None,
                error_recovery_config=error_recovery_cfg,
            )
    finally:
        planner.active_tool_names = previous_active
        _fire_persistence_tasks(planner, trajectory, result)
    assert result is not None
    await planner.maybe_record_memory_turn(
        query, result, trajectory, resolved_key
    )
    return result


async def resume(
    planner: ReactPlanner,
    token: str,
    user_input: str | None = None,
    *,
    tool_context: Mapping[str, Any] | None = None,
    memory_key: MemoryKey | None = None,
    tool_visibility: ToolVisibilityPolicy | None = None,
) -> PlannerFinish | PlannerPause:
    """Resume a paused run from its resume token."""
    logger.info("planner_resume", extra={"token": token[:8] + "..."})
    provided_tool_context = (
        _coerce_tool_context(tool_context)
        if tool_context is not None
        else None
    )
    record = await planner.load_pause_record(token)
    trajectory = record.trajectory
    trajectory.llm_context = (
        _validate_llm_context(trajectory.llm_context) or {}
    )
    cleaned_llm_context, extracted_results = extract_background_results(
        trajectory.llm_context
    )
    trajectory.llm_context = cleaned_llm_context or {}
    if extracted_results:
        trajectory.background_results.update(extracted_results)
    if provided_tool_context is not None:
        trajectory.tool_context = provided_tool_context
    elif record.tool_context is not None:
        trajectory.tool_context = dict(record.tool_context)
    else:
        trajectory.tool_context = trajectory.tool_context or {}
    if user_input is not None:
        trajectory.resume_user_input = user_input

    planner.artifact_registry = ArtifactRegistry.from_snapshot(
        trajectory.metadata.get("artifact_registry")
    )
    planner.artifact_registry.write_snapshot(trajectory.metadata)

    resolved_key = planner.resolve_memory_key(
        memory_key, trajectory.tool_context or {}
    )
    merged_llm_context = await planner.apply_memory_context(
        dict(trajectory.llm_context or {}),
        resolved_key,
    )
    cleaned_llm_context, extracted_results = extract_background_results(
        merged_llm_context
    )
    trajectory.llm_context = cleaned_llm_context or {}
    if extracted_results:
        trajectory.background_results.update(extracted_results)
    tracker: _ConstraintTracker | None = None
    if record.constraints is not None:
        tracker = _ConstraintTracker.from_snapshot(
            record.constraints,
            time_source=planner.time_source,
        )

    # Emit resume event
    planner.emit_event(
        PlannerEvent(
            event_type="resume",
            ts=planner.time_source(),
            trajectory_step=len(trajectory.steps),
            extra={"user_input": user_input} if user_input else {},
        )
    )

    error_recovery_cfg = getattr(planner, "error_recovery_config", None)
    previous_active = getattr(planner, "active_tool_names", None)
    _init_tool_activation_state(
        planner, trajectory, trajectory.tool_context or {}, resume=True
    )
    result: PlannerFinish | PlannerPause | None = None
    try:
        base_specs: Sequence[NodeSpec] = list(
            getattr(planner, "execution_specs", None)
            or getattr(planner, "specs", None)
            or []
        )
        allowed_names = _allowed_names_from_policy(
            base_specs, tool_visibility, trajectory.tool_context or {}
        )
        all_tool_names = {spec.name for spec in base_specs}
        await _prepare_tool_discovery_context(
            planner,
            trajectory,
            allowed_names=allowed_names,
        )
        await _prepare_skills_context(
            planner,
            trajectory,
            allowed_names=allowed_names,
            all_tool_names=all_tool_names,
        )
        with _tool_visibility_scope(
            planner,
            tool_visibility=tool_visibility,
            tool_context=trajectory.tool_context or {},
        ):
            result = await run_loop(
                planner,
                trajectory,
                tracker=tracker,
                error_recovery_config=error_recovery_cfg,
            )
    finally:
        planner.active_tool_names = previous_active
        _fire_persistence_tasks(planner, trajectory, result)
    assert result is not None
    await planner.maybe_record_memory_turn(
        trajectory.query, result, trajectory, resolved_key
    )
    return result


def _check_deadline(
    planner: ReactPlanner,
    trajectory: Trajectory,
    tracker: _ConstraintTracker,
    artifact_collector: _ArtifactCollector,
    source_collector: _SourceCollector,
    last_observation: object | None,
) -> PlannerFinish | None:
    deadline_message = tracker.check_deadline()
    if deadline_message is None:
        return None

    logger.warning(
        "deadline_exhausted",
        extra={"step": len(trajectory.steps)},
    )
    trajectory.artifacts = artifact_collector.snapshot()
    trajectory.sources = source_collector.snapshot()
    return planner.finish(
        trajectory,
        reason="budget_exhausted",
        payload=last_observation,
        thought=deadline_message,
        constraints=tracker,
    )


def _emit_step_start(
    planner: ReactPlanner, trajectory: Trajectory
) -> tuple[float, int]:
    step_start_ts = planner.time_source()
    planner.action_seq += 1
    current_action_seq = planner.action_seq
    planner.emit_event(
        PlannerEvent(
            event_type="step_start",
            ts=step_start_ts,
            trajectory_step=len(trajectory.steps),
            extra={"action_seq": current_action_seq},
        )
    )
    return step_start_ts, current_action_seq


def _log_action_received(
    action: PlannerAction, trajectory: Trajectory
) -> None:
    action_extra: dict[str, Any] = {
        "step": len(trajectory.steps),
        "thought": action.thought,
        "next_node": action.next_node,
        "has_plan": action.next_node == "parallel",
    }
    # For finish actions, log the args to help debug answer extraction issues
    if action.next_node == "final_response":
        args_preview = str(action.args)
        if len(args_preview) > 500:
            args_preview = args_preview[:500] + "..."
        action_extra["args_preview"] = args_preview
        action_extra["args_keys"] = list(action.args.keys())
        action_extra["has_raw_answer"] = "raw_answer" in action.args
        action_extra["has_answer"] = "answer" in action.args
    logger.debug("planner_action", extra=action_extra)


async def _handle_parallel_plan(
    planner: ReactPlanner,
    action: PlannerAction,
    trajectory: Trajectory,
    tracker: _ConstraintTracker,
    artifact_collector: _ArtifactCollector,
    source_collector: _SourceCollector,
    *,
    action_seq: int,
) -> tuple[Any | None, PlannerPause | None]:
    parallel_observation, pause = await planner.execute_parallel_plan(
        action,
        trajectory,
        tracker,
        artifact_collector,
        source_collector,
        action_seq=action_seq,
    )
    if pause is not None:
        return None, pause
    trajectory.summary = None
    last_observation = parallel_observation
    trajectory.artifacts = artifact_collector.snapshot()
    trajectory.sources = source_collector.snapshot()
    trajectory.resume_user_input = None
    return last_observation, None


async def _handle_finish_action(
    planner: ReactPlanner,
    action: PlannerAction,
    trajectory: Trajectory,
    tracker: _ConstraintTracker,
    last_observation: object | None,
    artifact_collector: _ArtifactCollector,
    source_collector: _SourceCollector,
    action_seq: int,
) -> PlannerFinish:
    # Check if raw_answer is missing and attempt finish repair
    answer = action.answer_text()
    has_answer = (
        answer is not None
        and answer not in {"", "<auto>"}
        and answer.strip() != ""
    )

    if not has_answer and not trajectory.metadata.get(
        "finish_repair_attempted"
    ):
        filled_answer = await _repair_missing_answer(
            planner,
            trajectory,
            action=action,
            action_seq=action_seq,
            answer=answer,
        )

        if filled_answer is not None:
            # Success! Update action args with both canonical + legacy keys.
            action.args["answer"] = filled_answer
            action.args["raw_answer"] = filled_answer
            logger.info(
                "finish_repair_success",
                extra={
                    "answer_len": len(filled_answer),
                    "args_keys": list(action.args.keys()),
                },
            )
        else:
            logger.warning(
                "finish_repair_failed",
                extra={"thought": action.thought},
            )

    # Structured final answers (final_response_model): validate
    # args["structured"] with a bounded corrective-turn repair; strips the
    # field on exhaustion so payload.structured never carries unvalidated data.
    if getattr(planner, "final_response_model", None) is not None:
        await planner.ensure_structured_final(
            trajectory, action, action_seq=action_seq
        )

    candidate_answer = action.args if action.args else last_observation
    _discard_invalid_answer_fields(
        planner,
        trajectory,
        action=action,
        action_seq=action_seq,
        candidate_answer=candidate_answer,
    )
    # Trace: Log candidate_answer state (helps debug answer loss issues)
    _ca_raw = (
        candidate_answer.get("raw_answer")
        if isinstance(candidate_answer, dict)
        else None
    )
    logger.debug(
        "finish_candidate_answer",
        extra={
            "has_raw_answer": _ca_raw is not None,
            "raw_answer_len": len(_ca_raw)
            if isinstance(_ca_raw, str)
            else None,
        },
    )
    candidate_answer, metadata_reflection = await _reflect_on_answer(
        planner,
        trajectory,
        action=action,
        candidate_answer=candidate_answer,
        tracker=tracker,
    )

    metadata_extra: dict[str, Any] | None = None
    if metadata_reflection is not None:
        metadata_extra = {"reflection": metadata_reflection}

    trajectory.artifacts = artifact_collector.snapshot()
    trajectory.sources = source_collector.snapshot()

    # Trace: Verify answer state before final payload
    _pre_raw = (
        candidate_answer.get("raw_answer")
        if isinstance(candidate_answer, dict)
        else None
    )
    logger.debug(
        "finish_pre_payload",
        extra={
            "has_raw_answer": _pre_raw is not None,
            "raw_answer_len": len(_pre_raw)
            if isinstance(_pre_raw, str)
            else None,
        },
    )

    final_payload = planner.build_final_payload(
        candidate_answer,
        last_observation,
        trajectory.artifacts,
        trajectory.sources,
    )
    logger.debug(
        "finish_payload_built",
        extra={
            "raw_answer_len": len(final_payload.raw_answer)
            if final_payload.raw_answer
            else 0
        },
    )
    # Note: Real-time streaming of args content happens during LLM call
    # via _StreamingArgsExtractor in step(). No post-hoc chunking needed.

    return planner.finish(
        trajectory,
        reason="answer_complete",
        payload=final_payload.model_dump(mode="json"),
        thought=action.thought,
        constraints=tracker,
        metadata_extra=metadata_extra,
    )


async def _repair_missing_answer(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action: PlannerAction,
    action_seq: int,
    answer: str | None,
) -> str | None:
    """Ask the LLM to fill in a missing final answer (once per run)."""
    # Model tried to finish without an answer - attempt repair Log full
    # args for debugging what the model actually returned
    args_dump = str(action.args)
    if len(args_dump) > 1000:
        args_dump = args_dump[:1000] + "..."
    # Include raw LLM response for exact model output debugging
    raw_response = action.raw_llm_response
    if raw_response and len(raw_response) > 2000:
        raw_response = raw_response[:2000] + "..."
    logger.info(
        "finish_repair_attempt",
        extra={
            "args_keys": list(action.args.keys()),
            "args_dump": args_dump,
            "thought": action.thought,
            "answer_text_result": repr(
                answer
            ),  # Show what answer_text() returned
            "raw_llm_response": raw_response,  # Exact model output
        },
    )

    return await planner.attempt_finish_repair(
        trajectory,
        action,
        action_seq=action_seq,
    )


def _discard_invalid_answer_fields(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action: PlannerAction,
    action_seq: int,
    candidate_answer: object,
) -> None:
    """Drop non-string answer fields, keeping a placeholder answer."""
    if isinstance(candidate_answer, MutableMapping):
        invalid_answer_fields: dict[str, str] = {}
        for answer_key in ("answer", "raw_answer"):
            if answer_key in candidate_answer and not isinstance(
                candidate_answer.get(answer_key), str
            ):
                value = candidate_answer.get(answer_key)
                invalid_answer_fields[answer_key] = type(value).__name__
                candidate_answer.pop(answer_key, None)
        if invalid_answer_fields:
            if (
                "answer" not in candidate_answer
                and "raw_answer" not in candidate_answer
            ):
                candidate_answer["raw_answer"] = "No answer produced."
            planner.emit_event(
                PlannerEvent(
                    event_type="finish_invalid_answer_type_discarded",
                    ts=planner.time_source(),
                    trajectory_step=len(trajectory.steps),
                    thought=action.thought,
                    extra={
                        "fields": invalid_answer_fields,
                        "action_seq": action_seq,
                    },
                )
            )
            logger.warning(
                "finish_invalid_answer_type_discarded",
                extra={
                    "fields": invalid_answer_fields,
                    "action_seq": action_seq,
                },
            )


async def _reflect_on_answer(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action: PlannerAction,
    candidate_answer: object,
    tracker: _ConstraintTracker,
) -> tuple[object, dict[str, Any] | None]:
    """Critique the answer and revise it until it passes or budgets run out."""
    metadata_reflection: dict[str, Any] | None = None
    revision_idx = 0

    config = planner.reflection_config
    if candidate_answer is not None and config is not None and config.enabled:
        critique: ReflectionCritique | None = None
        metadata_reflection = {}
        for revision_idx in range(config.max_revisions + 1):
            critique = await planner.critique_answer(
                trajectory, candidate_answer
            )

            planner.emit_event(
                PlannerEvent(
                    event_type="reflection_critique",
                    ts=planner.time_source(),
                    trajectory_step=len(trajectory.steps),
                    thought=action.thought,
                    extra={
                        "score": critique.score,
                        "passed": critique.passed,
                        "revision": revision_idx,
                        "feedback": critique.feedback[:200],
                    },
                )
            )

            if critique.passed or critique.score >= config.quality_threshold:
                logger.info(
                    "reflection_passed",
                    extra={
                        "score": critique.score,
                        "revisions": revision_idx,
                    },
                )
                break

            if revision_idx >= config.max_revisions:
                candidate_answer = await _answer_after_last_revision(
                    planner,
                    trajectory,
                    threshold=config.quality_threshold,
                    candidate_answer=candidate_answer,
                    critique=critique,
                    revision_idx=revision_idx,
                )

                break

            if not tracker.has_budget_for_next_tool():
                snapshot = tracker.snapshot()
                logger.warning(
                    "reflection_budget_exhausted",
                    extra={
                        "score": critique.score,
                        "hops_used": snapshot.get("hops_used"),
                    },
                )
                break

            candidate_answer = await _revise_answer(
                planner,
                trajectory,
                critique=critique,
                revision_idx=revision_idx,
            )

        if critique is not None:
            metadata_reflection = {
                "score": critique.score,
                "revisions": min(
                    revision_idx,
                    config.max_revisions,
                ),
                "passed": critique.passed,
            }
            if critique.feedback:
                metadata_reflection["feedback"] = critique.feedback
    return candidate_answer, metadata_reflection


async def _answer_after_last_revision(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    candidate_answer: object,
    critique: ReflectionCritique,
    revision_idx: int,
    threshold: float,
) -> object:
    """Return the answer to keep once no revisions are left."""
    # Check if quality is still below threshold
    if critique.score < threshold:
        # Quality remains poor - transform into honest
        # clarification
        logger.warning(
            "reflection_honest_failure",
            extra={
                "score": critique.score,
                "threshold": threshold,
                "revisions": revision_idx,
            },
        )

        # Generate clarification instead of returning low-quality
        # answer
        clarification_text = await planner.generate_clarification(
            trajectory=trajectory,
            failed_answer=candidate_answer,
            critique=critique,
            revision_attempts=revision_idx,
        )

        # Replace candidate answer with clarification Ensure proper
        # structure for downstream consumers (like FinalAnswer
        # model)
        if isinstance(candidate_answer, dict):
            # Update existing dict with clarification
            candidate_answer["raw_answer"] = clarification_text
            candidate_answer["text"] = clarification_text

            # Ensure required fields are present
            if "route" not in candidate_answer:
                # Extract route from first step observation if
                # available
                route = "unknown"
                if trajectory.steps and trajectory.steps[0].observation:
                    obs = trajectory.steps[0].observation
                    # Handle both dict and Pydantic model
                    # observations
                    if isinstance(obs, dict):
                        route = obs.get("route", "unknown")
                    else:
                        route = getattr(obs, "route", "unknown")
                candidate_answer["route"] = route
            if "artifacts" not in candidate_answer:
                candidate_answer["artifacts"] = {}
            if "metadata" not in candidate_answer:
                candidate_answer["metadata"] = {}

            # Mark as unsatisfied in metadata
            candidate_answer["metadata"]["confidence"] = "unsatisfied"
            candidate_answer["metadata"]["reflection_score"] = critique.score
            candidate_answer["metadata"]["revision_attempts"] = revision_idx
        else:
            # Create structured answer from scratch
            route = "unknown"
            if trajectory.steps and trajectory.steps[0].observation:
                obs = trajectory.steps[0].observation
                # Handle both dict and Pydantic model observations
                if isinstance(obs, dict):
                    route = obs.get("route", "unknown")
                else:
                    route = getattr(obs, "route", "unknown")

            candidate_answer = {
                "raw_answer": clarification_text,
                "text": clarification_text,
                "route": route,
                "artifacts": {},
                "metadata": {
                    "confidence": "unsatisfied",
                    "reflection_score": critique.score,
                    "revision_attempts": revision_idx,
                },
            }

        # Emit telemetry event
        planner.emit_event(
            PlannerEvent(
                event_type="reflection_clarification_generated",
                ts=planner.time_source(),
                trajectory_step=len(trajectory.steps),
                thought=("Generated clarification for unsatisfiable query"),
                extra={
                    "original_score": critique.score,
                    "threshold": threshold,
                    "revisions": revision_idx,
                },
            )
        )
    else:
        # Quality improved enough, just log warning
        logger.warning(
            "reflection_max_revisions",
            extra={
                "score": critique.score,
                "threshold": threshold,
            },
        )
    return candidate_answer


async def _revise_answer(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    critique: ReflectionCritique,
    revision_idx: int,
) -> object:
    """Ask the LLM to revise the answer from the critique; record the step."""
    logger.debug(
        "reflection_requesting_revision",
        extra={
            "revision": revision_idx + 1,
            "score": critique.score,
        },
    )

    # Build streaming callback for revision (reuse extractor pattern)
    revision_extractor = _StreamingArgsExtractor()

    def _emit_revision_chunk(
        text: str,
        done: bool,
        *,
        _extractor: _StreamingArgsExtractor = revision_extractor,
        _revision_idx: int = revision_idx,
    ) -> None:
        if planner.event_callback is None:
            return

        args_chars = _extractor.feed(text)

        if args_chars:
            args_text = "".join(args_chars)
            planner.emit_event(
                PlannerEvent(
                    event_type="llm_stream_chunk",
                    ts=planner.time_source(),
                    trajectory_step=len(trajectory.steps),
                    extra={
                        "text": args_text,
                        "done": False,
                        "phase": "revision",
                        "revision_idx": _revision_idx + 1,
                    },
                )
            )

        if done and _extractor.is_finish_action:
            planner.emit_event(
                PlannerEvent(
                    event_type="llm_stream_chunk",
                    ts=planner.time_source(),
                    trajectory_step=len(trajectory.steps),
                    extra={
                        "text": "",
                        "done": True,
                        "phase": "revision",
                        "revision_idx": _revision_idx + 1,
                    },
                )
            )

    revision_action = await planner.request_revision(
        trajectory,
        critique,
        on_stream_chunk=_emit_revision_chunk
        if planner.stream_final_response
        else None,
    )
    candidate_answer = revision_action.args or revision_action.model_dump()
    trajectory.steps.append(
        TrajectoryStep(
            action=revision_action,
            observation={"status": "revision_requested"},
        )
    )
    trajectory.summary = None
    return candidate_answer


@dataclass(frozen=True, slots=True)
class _ArgsRepaired:
    """Arg fill produced valid arguments; the tool runs with them."""

    parsed_args: BaseModel


def _pop_queued_action(trajectory: Trajectory) -> PlannerAction | None:
    """Pop a tool action queued from the previous LLM response.

    This supports executing additional tool actions that were emitted in the
    same LLM response as the previous step (multi-JSON outputs).
    """
    pending = trajectory.metadata.get("pending_actions")
    if not isinstance(pending, list) or not pending:
        return None
    item = pending.pop(0)
    if not isinstance(item, dict):
        return None
    next_node = item.get("next_node")
    if not isinstance(next_node, str):
        return None
    args = item.get("args")
    trajectory.metadata["auto_seq_skip_next"] = True
    return PlannerAction(
        next_node=next_node,
        args=dict(args) if isinstance(args, dict) else {},
    )


def _detect_auto_seq(
    planner: ReactPlanner, trajectory: Trajectory
) -> DetectionResult | None:
    """Detect a deterministic transition when auto-seq is enabled."""
    if not (getattr(planner, "auto_seq_enabled", False) and trajectory.steps):
        return None
    if trajectory.metadata.pop("auto_seq_skip_next", False):
        detection = DetectionResult(
            status="skipped", reason="pending_actions_present"
        )
    else:
        detection = _detect_deterministic_transition(planner, trajectory)
    _emit_auto_seq_detection_event(planner, trajectory, detection)
    return detection


def _auto_seq_action(
    planner: ReactPlanner,
    trajectory: Trajectory,
    detection: DetectionResult | None,
) -> PlannerAction | None:
    """Return a unique auto-seq transition that may run without the LLM.

    Both the planner (``auto_seq_execute``) and the tool
    (``extra["auto_seq_execute"]``) must allow executing it.
    """
    if detection is None or detection.status != "unique":
        return None
    selected = detection.selected_action
    if selected is None or not getattr(planner, "auto_seq_execute", False):
        return None
    spec = planner.spec_by_name.get(selected.next_node)
    if spec is None or spec.extra.get("auto_seq_execute") is not True:
        return None
    planner.emit_event(
        PlannerEvent(
            event_type="auto_seq_executed",
            ts=planner.time_source(),
            trajectory_step=len(trajectory.steps),
            extra={"tool_name": selected.next_node},
        )
    )
    return selected


def _is_runnable_candidate(
    planner: ReactPlanner, candidate: PlannerAction, *, read_only_only: bool
) -> bool:
    """Whether an alternate candidate names a runnable catalog tool."""
    if candidate.next_node in _MULTI_ACTION_BLOCKED_NODES:
        return False
    if not candidate.is_tool_call():
        return False
    spec = planner.spec_by_name.get(candidate.next_node)
    if spec is None:
        return False
    return (
        not read_only_only
        or getattr(spec, "side_effects", None)
        in _MULTI_ACTION_READONLY_SIDE_EFFECTS
    )


def _enqueue_alternate_actions(
    planner: ReactPlanner, trajectory: Trajectory, action: PlannerAction
) -> None:
    """Queue eligible follow-up tool calls from a mixed-output response.

    The queued calls run sequentially, without another LLM call.
    """
    if not (
        getattr(planner, "multi_action_sequential", False)
        and action.alternate_actions
    ):
        return
    pending = trajectory.metadata.get("pending_actions")
    if not isinstance(pending, list):
        pending = []
        trajectory.metadata["pending_actions"] = pending
    max_tools = int(getattr(planner, "multi_action_max_tools", 0) or 0)
    read_only_only = bool(
        getattr(planner, "multi_action_read_only_only", True)
    )
    added = 0
    for candidate in _alternate_candidates(action):
        if not _is_runnable_candidate(
            planner, candidate, read_only_only=read_only_only
        ):
            continue
        pending.append(
            {
                "next_node": candidate.next_node,
                "args": dict(candidate.args or {}),
            }
        )
        added += 1
        if max_tools > 0 and added >= max_tools:
            break
    if added:
        planner.emit_event(
            PlannerEvent(
                event_type="multi_action_enqueued",
                ts=planner.time_source(),
                trajectory_step=len(trajectory.steps),
                extra={"count": added},
            )
        )


async def _select_action(
    planner: ReactPlanner, trajectory: Trajectory, state: _LoopState
) -> PlannerAction:
    """Pick the step's action: queued, auto-seq or from the LLM."""
    queued = _pop_queued_action(trajectory)
    if queued is not None:
        return queued
    detection = _detect_auto_seq(planner, trajectory)
    action = _auto_seq_action(planner, trajectory, detection)
    if action is None:
        action = await step_with_recovery(
            planner, trajectory, config=state.error_recovery_config
        )
    _enqueue_alternate_actions(planner, trajectory, action)
    return action


def _adopt_alternate(action: PlannerAction, alt: PlannerAction) -> None:
    """Switch ``action`` to an alternate candidate and drop it from the list.

    Removing the used candidate from the alternates avoids reusing it.
    """
    action.next_node = alt.next_node
    action.args = dict(alt.args or {})
    if isinstance(action.alternate_actions, list):
        action.alternate_actions = [
            item
            for item in action.alternate_actions
            if not (
                isinstance(item, dict)
                and item.get("next_node") == alt.next_node
                and isinstance(item.get("args"), dict)
                and dict(item.get("args") or {}) == dict(alt.args or {})
            )
        ]


def _resolve_tool_spec(
    planner: ReactPlanner, trajectory: Trajectory, action: PlannerAction
) -> NodeSpec | None:
    """Find the action's tool, activating it or switching to an alternate.

    Returns ``None`` after recording an invalid-node error step.
    """
    spec = planner.spec_by_name.get(action.next_node)
    if spec is not None:
        return spec
    if _maybe_activate_tool(planner, action.next_node, trajectory):
        spec = planner.spec_by_name.get(action.next_node)
        if spec is not None:
            return spec
    # Try alternate candidates (multi-action outputs) before requiring
    # another LLM step.
    alt, _ = _maybe_select_alternate_action(
        planner,
        action,
        prefer_same_tool=False,
        require_read_only=False,
    )
    if alt is not None:
        previous = action.next_node
        _adopt_alternate(action, alt)
        planner.emit_event(
            PlannerEvent(
                event_type="multi_action_fallback_used",
                ts=planner.time_source(),
                trajectory_step=len(trajectory.steps),
                extra={
                    "from": previous,
                    "to": action.next_node,
                    "reason": "unknown_tool",
                },
            )
        )
        spec = planner.spec_by_name.get(action.next_node)
        if spec is not None:
            return spec
    error = prompts.render_invalid_node(
        action.next_node,
        list(planner.spec_by_name.keys()),
    )
    trajectory.steps.append(TrajectoryStep(action=action, error=error))
    trajectory.summary = None
    return None


@dataclass(frozen=True, slots=True)
class _ParsedArgs:
    """Validated tool arguments and the fields autofill supplied."""

    parsed_args: BaseModel
    autofilled_fields: tuple[str, ...] = ()


def _record_arg_error(
    trajectory: Trajectory,
    *,
    action: PlannerAction,
    spec: NodeSpec,
    exc: ValidationError,
) -> None:
    """Record a step whose arguments failed validation."""
    error = prompts.render_validation_error(
        spec.name, _serialize_validation_errors(exc)
    )
    trajectory.steps.append(TrajectoryStep(action=action, error=error))
    trajectory.summary = None


def _use_same_tool_alternate(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action: PlannerAction,
    spec: NodeSpec,
) -> bool:
    """Swap in a same-tool alternative args payload, if the model gave one."""
    alt, _ = _maybe_select_alternate_action(
        planner,
        action,
        prefer_same_tool=True,
        require_read_only=False,
    )
    if alt is None or alt.next_node != action.next_node:
        return False
    _adopt_alternate(action, alt)
    planner.emit_event(
        PlannerEvent(
            event_type="multi_action_fallback_used",
            ts=planner.time_source(),
            trajectory_step=len(trajectory.steps),
            extra={
                "tool": spec.name,
                "reason": "args_validation",
            },
        )
    )
    return True


def _parse_tool_args(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action: PlannerAction,
    spec: NodeSpec,
) -> _ParsedArgs | None:
    """Validate the action's arguments, falling back to repairs.

    When validation fails, a same-tool alternative payload from the model is
    tried first, then autofilling the missing fields. Returns ``None`` after
    recording the validation error as a step.
    """
    try:
        parsed_args = spec.args_model.model_validate(action.args)
    except ValidationError as exc:
        return _repair_tool_args(
            planner, trajectory, action=action, spec=spec, exc=exc
        )
    return _ParsedArgs(parsed_args)


def _repair_tool_args(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action: PlannerAction,
    spec: NodeSpec,
    exc: ValidationError,
) -> _ParsedArgs | None:
    """Recover from invalid tool arguments; see :func:`_parse_tool_args`."""
    if _use_same_tool_alternate(planner, trajectory, action=action, spec=spec):
        return _ParsedArgs(spec.args_model.model_validate(action.args))
    autofilled = _autofill_missing_args(spec, action.args)
    if autofilled is None:
        _record_arg_error(trajectory, action=action, spec=spec, exc=exc)
        return None
    autofilled_args, filled_fields = autofilled
    try:
        parsed_args = spec.args_model.model_validate(autofilled_args)
    except ValidationError as autofill_exc:
        _record_arg_error(
            trajectory, action=action, spec=spec, exc=autofill_exc
        )
        return None
    action.args = autofilled_args
    logger.info(
        "planner_autofill_args",
        extra={
            "tool": spec.name,
            "filled": list(filled_fields),
        },
    )
    return _ParsedArgs(parsed_args, filled_fields)


@dataclass(slots=True)
class _LoopState:
    """State carried across the steps of one planner run."""

    tracker: _ConstraintTracker
    artifact_collector: _ArtifactCollector
    source_collector: _SourceCollector
    error_recovery_config: ErrorRecoveryConfig | None
    last_observation: object = None


def _finish_out_of_steps(
    planner: ReactPlanner, trajectory: Trajectory, state: _LoopState
) -> PlannerFinish:
    """Finish a run that hit its budget or iteration limit."""
    tracker = state.tracker
    trajectory.artifacts = state.artifact_collector.snapshot()
    trajectory.sources = state.source_collector.snapshot()
    if tracker.deadline_triggered or tracker.hop_exhausted:
        thought = (
            prompts.render_deadline_exhausted()
            if tracker.deadline_triggered
            else prompts.render_hop_budget_violation(planner.hop_budget or 0)
        )
        return planner.finish(
            trajectory,
            reason="budget_exhausted",
            payload=state.last_observation,
            thought=thought,
            constraints=tracker,
        )
    return planner.finish(
        trajectory,
        reason="no_path",
        payload=state.last_observation,
        thought="iteration limit reached",
        constraints=tracker,
    )


async def run_loop(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    tracker: _ConstraintTracker | None,
    error_recovery_config: ErrorRecoveryConfig | None = None,
) -> PlannerFinish | PlannerPause:
    """Run the planner loop until it finishes, pauses or runs out of steps."""
    planner.active_trajectory = trajectory
    if tracker is None:
        tracker = _ConstraintTracker(
            deadline_s=planner.deadline_s,
            hop_budget=planner.hop_budget,
            time_source=planner.time_source,
        )
    planner.active_tracker = tracker
    state = _LoopState(
        tracker=tracker,
        artifact_collector=_ArtifactCollector(trajectory.artifacts),
        source_collector=_SourceCollector(trajectory.sources),
        error_recovery_config=error_recovery_config,
    )
    try:
        while len(trajectory.steps) < planner.max_iters:
            result = await _run_step(planner, trajectory, state)
            if result is not None:
                return result
        return _finish_out_of_steps(planner, trajectory, state)
    finally:
        planner.active_trajectory = None
        planner.active_tracker = None


async def _run_step(
    planner: ReactPlanner, trajectory: Trajectory, state: _LoopState
) -> PlannerFinish | PlannerPause | None:
    """Run one planner step; return the run's result once it ends."""
    steering: SteeringInbox | None = getattr(planner, "steering", None)
    if steering is not None:
        await steering.wait_if_paused()
    _apply_steering(planner, trajectory)
    finish = _check_deadline(
        planner,
        trajectory,
        state.tracker,
        state.artifact_collector,
        state.source_collector,
        state.last_observation,
    )
    if finish is not None:
        return finish

    # Emit step start event and bump action sequence
    step_start_ts, current_action_seq = _emit_step_start(planner, trajectory)

    action = await _select_action(planner, trajectory, state)

    # Normalize aliased tool names to their real names (RFC alignment)
    tool_aliases = getattr(planner, "tool_aliases", {})
    if tool_aliases:
        action.next_node = rewrite_action_node(
            action.next_node, alias_to_real=tool_aliases
        )

    # Log the action received from LLM
    _log_action_received(action, trajectory)

    # Check constraints BEFORE executing parallel plan or any action
    constraint_error = planner.check_action_constraints(
        action, trajectory, state.tracker
    )
    if constraint_error is not None:
        trajectory.steps.append(
            TrajectoryStep(action=action, error=constraint_error)
        )
        trajectory.summary = None
        return None

    if action.next_node == "parallel":
        return await _handle_parallel_action(
            planner,
            trajectory,
            action=action,
            current_action_seq=current_action_seq,
            state=state,
        )

    if action.next_node == "final_response":
        return await _handle_final_response_action(
            planner,
            trajectory,
            action=action,
            current_action_seq=current_action_seq,
            state=state,
        )

    if not action.is_tool_call():
        error = prompts.render_invalid_node(
            action.next_node,
            list(planner.spec_by_name.keys()),
        )
        trajectory.steps.append(TrajectoryStep(action=action, error=error))
        trajectory.summary = None
        return None

    return await _run_tool_action(
        planner,
        trajectory,
        action=action,
        current_action_seq=current_action_seq,
        state=state,
        steering=steering,
        step_start_ts=step_start_ts,
    )


async def _run_tool_action(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action: PlannerAction,
    current_action_seq: int,
    state: _LoopState,
    steering: SteeringInbox | None,
    step_start_ts: float,
) -> PlannerFinish | PlannerPause | None:
    """Validate the arguments of a tool action, then execute it."""
    spec = _resolve_tool_spec(planner, trajectory, action)
    if spec is None:
        return None

    parsed = _parse_tool_args(planner, trajectory, action=action, spec=spec)
    if parsed is None:
        return None
    parsed_args = parsed.parsed_args
    autofilled_fields = parsed.autofilled_fields

    arg_validation_error = planner.apply_arg_validation(
        trajectory,
        spec=spec,
        action=action,
        parsed_args=parsed_args,
        autofilled_fields=autofilled_fields,
    )
    if arg_validation_error is not None:
        handled = await _handle_arg_validation_error(
            planner,
            trajectory,
            action=action,
            arg_validation_error=arg_validation_error,
            autofilled_fields=autofilled_fields,
            spec=spec,
            state=state,
        )
        if not isinstance(handled, _ArgsRepaired):
            return handled
        parsed_args = handled.parsed_args

    return await _execute_tool_step(
        planner,
        trajectory,
        action=action,
        current_action_seq=current_action_seq,
        parsed_args=parsed_args,
        spec=spec,
        state=state,
        steering=steering,
        step_start_ts=step_start_ts,
    )


async def _handle_parallel_action(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action: PlannerAction,
    current_action_seq: int,
    state: _LoopState,
) -> PlannerFinish | PlannerPause | None:
    """Run the action's parallel plan and record its results."""
    state.last_observation, pause = await _handle_parallel_plan(
        planner,
        action,
        trajectory,
        state.tracker,
        state.artifact_collector,
        state.source_collector,
        action_seq=current_action_seq,
    )
    if pause is not None:
        return pause
    return None


async def _handle_final_response_action(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action: PlannerAction,
    current_action_seq: int,
    state: _LoopState,
) -> PlannerFinish | PlannerPause | None:
    """Finish the run, or repair an invalid final response."""
    # Before finishing, check if there are pending steering events
    # that arrived while the LLM was generating this finish action.
    # If so, process them and continue instead of finishing.
    steering_inbox: SteeringInbox | None = getattr(planner, "steering", None)
    if steering_inbox is not None and steering_inbox.has_event():
        _apply_steering(planner, trajectory)
        if trajectory.steering_inputs:
            # User sent steering while LLM was responding - defer
            # finish
            logger.info(
                "finish_deferred_for_steering",
                extra={
                    "pending_inputs": len(trajectory.steering_inputs),
                    "step": len(trajectory.steps),
                },
            )
            # Record the attempted finish as a step so LLM knows it
            # tried
            trajectory.steps.append(
                TrajectoryStep(
                    action=action,
                    observation=(
                        "Finish deferred: new steering input "
                        "received from user."
                    ),
                )
            )
            trajectory.summary = None
            return None  # Process steering in next iteration

    return await _handle_finish_action(
        planner,
        action,
        trajectory,
        state.tracker,
        state.last_observation,
        state.artifact_collector,
        state.source_collector,
        action_seq=current_action_seq,
    )


async def _handle_arg_validation_error(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action: PlannerAction,
    arg_validation_error: str,
    autofilled_fields: tuple[str, ...],
    spec: NodeSpec,
    state: _LoopState,
) -> PlannerFinish | PlannerPause | _ArgsRepaired | None:
    """Repair, retry or finish after the tool arguments failed validation."""
    # Use per-tool counters to avoid one tool's failures affecting
    # another tool's chances
    autofill_rejection_key = f"autofill_rejection_count_{spec.name}"
    autofill_rejection_count = int(
        trajectory.metadata.get(autofill_rejection_key, 0)
    )
    consecutive_failures_key = f"consecutive_arg_failures_{spec.name}"
    consecutive_failures = int(
        trajectory.metadata.get(consecutive_failures_key, 0)
    )

    # Force finish conditions:
    # 1. Second autofill rejection for THIS tool (gave model one
    #    chance with explicit field names)
    # 2. Consecutive failures threshold reached for THIS tool
    force_finish = (
        autofilled_fields and autofill_rejection_count >= 2
    ) or consecutive_failures >= planner.max_consecutive_arg_failures

    if force_finish:
        return await _finish_after_arg_failures(
            planner,
            trajectory,
            action=action,
            arg_validation_error=arg_validation_error,
            autofill_rejection_count=autofill_rejection_count,
            autofilled_fields=autofilled_fields,
            consecutive_failures=consecutive_failures,
            spec=spec,
            state=state,
        )

    # Try arg-fill if eligible:
    # - Default: only for autofilled required args (missing fields
    #   at Pydantic layer)
    # - Special case: rich-output render tools may be
    #   "Pydantic-valid" but still invalid vs the component
    #   registry schemas. In that case,
    # trigger arg-fill for
    #   the tool's top-level payload field (`props`, `sections`,
    # `items`, etc.) to
    #   avoid deterministic tool failures/loops.
    missing_fields_for_arg_fill: list[str] | None = None
    if autofilled_fields:
        missing_fields_for_arg_fill = list(autofilled_fields)
    else:
        schema_error = _get_rich_output_schema_error(trajectory)
        repair_field = (
            schema_error.get("repair_field")
            if isinstance(schema_error, Mapping)
            else None
        )
        if (
            isinstance(schema_error, Mapping)
            and schema_error.get("tool_name") == spec.name
            and schema_error.get("code") == "schema_invalid"
            and isinstance(repair_field, str)
        ):
            missing_fields_for_arg_fill = [repair_field]

    if missing_fields_for_arg_fill and planner.is_arg_fill_eligible(
        spec, missing_fields_for_arg_fill, trajectory
    ):
        return await _try_arg_fill(
            planner,
            trajectory,
            action=action,
            arg_validation_error=arg_validation_error,
            autofilled_fields=autofilled_fields,
            missing_fields_for_arg_fill=missing_fields_for_arg_fill,
            spec=spec,
            state=state,
        )
    # Arg-fill not eligible or not enabled, use standard repair
    return _request_arg_repair(
        planner,
        trajectory,
        action=action,
        arg_validation_error=arg_validation_error,
        autofilled_fields=autofilled_fields,
        spec=spec,
    )


async def _finish_after_arg_failures(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action: PlannerAction,
    arg_validation_error: str,
    autofill_rejection_count: int,
    autofilled_fields: tuple[str, ...],
    consecutive_failures: int,
    spec: NodeSpec,
    state: _LoopState,
) -> PlannerFinish:
    """Finish the run once a tool's arguments keep failing validation."""
    failure_reason = (
        "autofill_rejection"
        if autofilled_fields and autofill_rejection_count >= 2
        else "consecutive_arg_failures"
    )
    logger.warning(
        "planner_arg_failure_threshold",
        extra={
            "tool": spec.name,
            "consecutive_failures": consecutive_failures,
            "autofill_rejection_count": autofill_rejection_count,
            "threshold": planner.max_consecutive_arg_failures,
            "last_error": arg_validation_error,
            "failure_reason": failure_reason,
        },
    )
    trajectory.steps.append(
        TrajectoryStep(action=action, error=arg_validation_error)
    )
    trajectory.artifacts = state.artifact_collector.snapshot()
    trajectory.sources = state.source_collector.snapshot()

    # Attempt graceful failure: ask model to generate
    # user-friendly response instead of returning a technical
    # error
    graceful_answer = await planner.attempt_graceful_failure(
        trajectory,
        action_seq=planner.action_seq,
    )

    if graceful_answer is not None:
        # Success - return with a proper answer instead of
        # no_path
        return planner.finish(
            trajectory,
            reason="answer_complete",
            payload={
                "raw_answer": graceful_answer,
                "graceful_failure": True,
                "failure_reason": failure_reason,
                "tool": spec.name,
            },
            thought=(
                f"Gracefully handled {failure_reason} for tool '{spec.name}'"
            ),
            constraints=state.tracker,
        )

    # Graceful failure didn't work - fall back to no_path
    return planner.finish(
        trajectory,
        reason="no_path",
        payload={
            "requires_followup": True,
            "failure_reason": failure_reason,
            "tool": spec.name,
            "last_error": arg_validation_error,
            "missing_fields": list(autofilled_fields)
            if autofilled_fields
            else None,
        },
        thought=(
            f"Cannot proceed: {failure_reason} for "
            f"tool '{spec.name}'. "
            f"Last error: {arg_validation_error}"
        ),
        constraints=state.tracker,
        metadata_extra={"requires_followup": True},
    )


async def _try_arg_fill(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action: PlannerAction,
    arg_validation_error: str,
    autofilled_fields: tuple[str, ...],
    missing_fields_for_arg_fill: list[str],
    spec: NodeSpec,
    state: _LoopState,
) -> PlannerFinish | PlannerPause | _ArgsRepaired | None:
    """Fill the missing arguments with the LLM, or ask the user for them."""
    filled_args = await planner.attempt_arg_fill(
        trajectory,
        spec,
        missing_fields_for_arg_fill,
    )

    if filled_args is None:
        return _ask_for_missing_args(
            planner,
            trajectory,
            action=action,
            arg_validation_error=arg_validation_error,
            autofilled_fields=autofilled_fields,
            spec=spec,
            state=state,
        )

    # Merge filled args into action
    merged_args = dict(action.args or {})
    merged_args.update(filled_args)

    # Re-validate with merged args
    try:
        outcome = await _merge_filled_args(
            planner,
            trajectory,
            action=action,
            filled_args=filled_args,
            merged_args=merged_args,
            spec=spec,
        )

    except ValidationError as merge_exc:
        # Merge failed validation
        logger.warning(
            "arg_fill_merge_validation_failed",
            extra={
                "tool": spec.name,
                "filled_fields": list(filled_args.keys()),
                "error": str(merge_exc),
            },
        )
        # Fall through to repair message
        repair_msg = prompts.render_arg_repair_message(
            spec.name,
            _serialize_validation_errors(merge_exc),
            tool_call_mode=getattr(planner, "tool_call_mode", "prompted"),
        )
        if isinstance(trajectory.metadata, MutableMapping):
            trajectory.metadata["arg_repair_message"] = repair_msg
        error = prompts.render_validation_error(
            spec.name,
            _serialize_validation_errors(merge_exc),
        )
        trajectory.steps.append(TrajectoryStep(action=action, error=error))
        trajectory.summary = None
        return None
    return outcome


async def _merge_filled_args(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action: PlannerAction,
    filled_args: dict[str, Any],
    merged_args: dict[str, Any],
    spec: NodeSpec,
) -> _ArgsRepaired | None:
    """Validate the merged arguments, retrying rich-output payload fills."""
    parsed_args = spec.args_model.model_validate(merged_args)
    action.args = merged_args

    # Re-run arg validation (placeholders, custom
    # validators)
    revalidation_error = planner.apply_arg_validation(
        trajectory,
        spec=spec,
        action=action,
        parsed_args=parsed_args,
        autofilled_fields=(),  # No longer autofilled
    )

    if revalidation_error is None:
        # Success! Reset ALL failure counters for this
        # tool This is critical:
        # autofill_rejection_count must reset so the
        # tool gets fresh chances if it needs to retry
        # later (e.g., tool fails for other reasons and
        # model retries with autofill again)
        trajectory.metadata["consecutive_arg_failures"] = 0
        trajectory.metadata[f"consecutive_arg_failures_{spec.name}"] = 0
        trajectory.metadata[f"autofill_rejection_count_{spec.name}"] = 0
        trajectory.metadata[f"arg_fill_attempted_{spec.name}"] = False
        _clear_rich_output_schema_repair_state(trajectory)

        logger.info(
            "arg_fill_merged_success",
            extra={
                "tool": spec.name,
                "filled_fields": list(filled_args.keys()),
            },
        )

        # Jump to tool execution (parsed_args is now
        # valid) We need to NOT continue the loop, but
        # proceed with execution below This is done by
        # not entering the repair flow
        pass  # Fall through to tool execution
    else:
        # Arg-fill succeeded but validation still
        # failed
        logger.warning(
            "arg_fill_revalidation_failed",
            extra={
                "tool": spec.name,
                "filled_fields": list(filled_args.keys()),
                "error": revalidation_error,
            },
        )
        # Special-case: allow a second arg-fill for
        # rich-output payload fields if the first
        # arg-fill solved another field but left the
        # schema-invalid payload untouched.
        schema_error = _get_rich_output_schema_error(trajectory)
        repair_field = (
            schema_error.get("repair_field")
            if isinstance(schema_error, Mapping)
            else None
        )
        if (
            isinstance(schema_error, Mapping)
            and schema_error.get("tool_name") == spec.name
            and isinstance(repair_field, str)
            and repair_field not in filled_args
            and not _rich_output_schema_arg_fill_attempted(trajectory)
        ):
            _mark_rich_output_schema_arg_fill_attempted(trajectory)
            trajectory.metadata[f"arg_fill_attempted_{spec.name}"] = False
            if planner.is_arg_fill_eligible(spec, [repair_field], trajectory):
                props_fill = await planner.attempt_arg_fill(
                    trajectory,
                    spec,
                    [repair_field],
                )
                if props_fill is not None and repair_field in props_fill:
                    merged_again = dict(action.args or {})
                    merged_again.update(props_fill)
                    try:
                        parsed_args = spec.args_model.model_validate(
                            merged_again
                        )
                    except ValidationError as merge_exc:
                        revalidation_error = _serialize_validation_errors(
                            merge_exc
                        )
                    else:
                        action.args = merged_again
                        revalidation_error = planner.apply_arg_validation(
                            trajectory,
                            spec=spec,
                            action=action,
                            parsed_args=parsed_args,
                            autofilled_fields=(),
                        )
                        if revalidation_error is None:
                            trajectory.metadata["consecutive_arg_failures"] = 0
                            trajectory.metadata[
                                f"consecutive_arg_failures_{spec.name}"
                            ] = 0
                            trajectory.metadata[
                                f"autofill_rejection_count_{spec.name}"
                            ] = 0
                            trajectory.metadata[
                                f"arg_fill_attempted_{spec.name}"
                            ] = False
                            _clear_rich_output_schema_repair_state(trajectory)
                            logger.info(
                                "arg_fill_merged_success",
                                extra={
                                    "tool": spec.name,
                                    "filled_fields": list(props_fill.keys()),
                                },
                            )
                            pass
                        else:
                            logger.warning(
                                "arg_fill_revalidation_failed",
                                extra={
                                    "tool": spec.name,
                                    "filled_fields": list(props_fill.keys()),
                                    "error": revalidation_error,
                                },
                            )

        if revalidation_error is not None:
            # Fall through to repair message
            repair_msg = prompts.render_arg_repair_message(
                spec.name,
                revalidation_error,
                tool_call_mode=getattr(
                    planner,
                    "tool_call_mode",
                    "prompted",
                ),
            )
            if isinstance(trajectory.metadata, MutableMapping):
                trajectory.metadata["arg_repair_message"] = repair_msg
            error = prompts.render_validation_error(
                spec.name, revalidation_error
            )
            trajectory.steps.append(TrajectoryStep(action=action, error=error))
            trajectory.summary = None
            return None
    # Arg fill repaired the arguments: execute the tool with them.
    return _ArgsRepaired(parsed_args)


def _ask_for_missing_args(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action: PlannerAction,
    arg_validation_error: str,
    autofilled_fields: tuple[str, ...],
    spec: NodeSpec,
    state: _LoopState,
) -> PlannerFinish:
    """Finish asking the user for the arguments the LLM could not fill."""
    # Arg-fill failed, generate user-friendly clarification
    field_descriptions = planner.extract_field_descriptions(spec)
    clarification = prompts.render_arg_fill_clarification(
        spec.name,
        list(autofilled_fields),
        field_descriptions,
    )

    # Use clarification as the failure message instead of
    # diagnostic dump
    trajectory.steps.append(
        TrajectoryStep(action=action, error=arg_validation_error)
    )
    trajectory.artifacts = state.artifact_collector.snapshot()
    trajectory.sources = state.source_collector.snapshot()
    return planner.finish(
        trajectory,
        reason="no_path",
        payload={
            "requires_followup": True,
            "failure_reason": "arg_fill_failed",
            "tool": spec.name,
            "clarification": clarification,
            "missing_fields": list(autofilled_fields),
        },
        thought=clarification,
        constraints=state.tracker,
        metadata_extra={"requires_followup": True},
    )


def _request_arg_repair(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action: PlannerAction,
    arg_validation_error: str,
    autofilled_fields: tuple[str, ...],
    spec: NodeSpec,
) -> PlannerFinish | PlannerPause | _ArgsRepaired | None:
    """Ask the model to resend the tool call with corrected arguments."""
    # flow Choose repair message based on whether this was an
    # autofill rejection
    if autofilled_fields:
        # First autofill rejection: tell model exactly which
        # fields it forgot
        repair_msg = prompts.render_missing_args_message(
            spec.name,
            list(autofilled_fields),
            user_query=(trajectory.resume_user_input or trajectory.query),
        )
    else:
        # Regular arg validation failure
        repair_msg = prompts.render_arg_repair_message(
            spec.name,
            arg_validation_error,
            tool_call_mode=getattr(planner, "tool_call_mode", "prompted"),
        )

    if isinstance(trajectory.metadata, MutableMapping):
        trajectory.metadata["arg_repair_message"] = repair_msg
    error = prompts.render_validation_error(spec.name, arg_validation_error)
    trajectory.steps.append(TrajectoryStep(action=action, error=error))
    trajectory.summary = None
    return None


async def _execute_tool_step(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action: PlannerAction,
    current_action_seq: int,
    parsed_args: BaseModel,
    spec: NodeSpec,
    state: _LoopState,
    steering: SteeringInbox | None,
    step_start_ts: float,
) -> PlannerFinish | PlannerPause | None:
    """Execute the validated tool call and record its observation."""
    tool_call_id = f"call_{current_action_seq}_{len(trajectory.steps)}"
    step_index = len(trajectory.steps)
    outcome = await execute_tool_call(
        planner,
        trajectory=trajectory,
        spec=spec,
        parsed_args=parsed_args,
        tool_call_id=tool_call_id,
        action_seq=current_action_seq,
        step_index=step_index,
        artifact_collector=state.artifact_collector,
        source_collector=state.source_collector,
        steering=steering,
    )

    if outcome.pause is not None:
        state.tracker.record_hop()
        trajectory.steps.append(
            TrajectoryStep(
                action=action,
                observation={
                    "pause": outcome.pause.reason,
                    "payload": outcome.pause.payload,
                },
                streams=outcome.streams or None,
            )
        )
        trajectory.summary = None
        await planner.record_pause(outcome.pause, trajectory, state.tracker)
        return outcome.pause

    if outcome.error is not None:
        guardrail_payload = None
        if isinstance(outcome.failure, Mapping):
            guardrail_payload = outcome.failure.get("guardrail")
        if (
            isinstance(guardrail_payload, Mapping)
            and guardrail_payload.get("action") == "STOP"
        ):
            stop_message = None
            stop_info = guardrail_payload.get("stop")
            if isinstance(stop_info, Mapping):
                stop_message = stop_info.get("user_message")
            return planner.finish(
                trajectory,
                reason="no_path",
                payload={
                    "raw_answer": stop_message
                    or "Unable to complete the request.",
                    "guardrail": dict(guardrail_payload),
                },
                thought=str(
                    guardrail_payload.get("reason") or "guardrail_stop"
                ),
                constraints=state.tracker,
                error=outcome.error,
                metadata_extra={"guardrail": dict(guardrail_payload)},
            )
        trajectory.steps.append(
            TrajectoryStep(
                action=action,
                error=outcome.error,
                failure=outcome.failure,
                streams=outcome.streams or None,
            )
        )
        state.tracker.record_hop()
        trajectory.summary = None
        state.last_observation = None
        return None

    observation_json = outcome.observation or {}
    llm_obs = outcome.llm_observation or observation_json
    trajectory.steps.append(
        TrajectoryStep(
            action=action,
            observation=observation_json,
            llm_observation=llm_obs,
            streams=outcome.streams or None,
        )
    )
    state.tracker.record_hop()
    trajectory.summary = None
    state.last_observation = observation_json
    trajectory.resume_user_input = None

    # Preserve legacy behavior: background-spawned tools did not emit
    # step_complete.
    if not outcome.background_spawned:
        trajectory.artifacts = state.artifact_collector.snapshot()
        trajectory.sources = state.source_collector.snapshot()

        # Emit step complete event
        step_latency = (planner.time_source() - step_start_ts) * 1000  # ms
        planner.emit_event(
            PlannerEvent(
                event_type="step_complete",
                ts=planner.time_source(),
                trajectory_step=len(trajectory.steps) - 1,
                thought=action.thought,
                node_name=spec.name,
                latency_ms=step_latency,
            )
        )

    # Reset consecutive arg failure counters on successful tool
    # execution
    if trajectory.metadata.get("consecutive_arg_failures"):
        trajectory.metadata["consecutive_arg_failures"] = 0
    per_tool_key = f"consecutive_arg_failures_{spec.name}"
    if trajectory.metadata.get(per_tool_key):
        trajectory.metadata[per_tool_key] = 0
    return None
