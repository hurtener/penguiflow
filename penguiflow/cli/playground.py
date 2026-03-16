"""FastAPI playground backend with agent discovery and wrapping."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import secrets
import sys
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

try:
    from ag_ui.core import RunAgentInput
    from ag_ui.encoder import EventEncoder
except Exception:  # pragma: no cover - optional dependency
    RunAgentInput = None  # type: ignore[assignment,misc]
    EventEncoder = None  # type: ignore[assignment,misc]

from penguiflow.cli.generate import run_generate
from penguiflow.cli.spec import Spec, load_spec
from penguiflow.cli.spec_errors import SpecValidationError
from penguiflow.evals.api import TraceSelector as EvalTraceSelector
from penguiflow.evals.api import ensure_project_on_sys_path
from penguiflow.evals.api import export_dataset as export_eval_dataset
from penguiflow.evals.api import resolve_callable, wrap_metric
from penguiflow.planner import PlannerEvent, Trajectory
from penguiflow.sessions import (
    MergeStrategy,
    PlannerTaskPipeline,
    SessionLimits,
    SessionManager,
    StateUpdate,
    TaskContextSnapshot,
    TaskStateModel,
    TaskStatus,
    TaskType,
    UpdateType,
)
from penguiflow.sessions.projections import PlannerEventProjector
from penguiflow.steering import (
    SteeringEvent,
    SteeringEventType,
    SteeringInbox,
    SteeringValidationError,
    sanitize_steering_event,
    validate_steering_event,
)

try:
    from penguiflow.agui_adapter import PenguiFlowAdapter, create_agui_endpoint
except Exception:  # pragma: no cover - optional dependency
    PenguiFlowAdapter = None  # type: ignore[assignment,misc]
    create_agui_endpoint = None  # type: ignore[assignment]
try:
    from penguiflow.rich_output import DEFAULT_ALLOWLIST, RichOutputConfig, configure_rich_output
    from penguiflow.rich_output.validate import RichOutputValidationError, validate_interaction_result
except Exception:  # pragma: no cover - optional dependency
    DEFAULT_ALLOWLIST = ()  # type: ignore[assignment]
    RichOutputConfig = None  # type: ignore[assignment,misc]
    configure_rich_output = None  # type: ignore[assignment]
    RichOutputValidationError = Exception  # type: ignore[assignment,misc]
    validate_interaction_result = None  # type: ignore[assignment]

from .playground_sse import EventBroker, SSESentinel, format_sse, stream_queue
from .playground_state import InMemoryStateStore, PlaygroundStateStore
from .playground_wrapper import (
    AgentWrapper,
    ChatResult,
    OrchestratorAgentWrapper,
    PlannerAgentWrapper,
    _normalise_answer,
)

_LOGGER = logging.getLogger(__name__)
EVAL_RUN_HARD_MAX_CASES = 200


class PlaygroundError(RuntimeError):
    """Raised when the playground cannot start or bind to an agent."""


@dataclass
class DiscoveryResult:
    """Metadata about a discovered agent entry point."""

    kind: Literal["orchestrator", "planner"]
    target: Any
    package: str
    module: str
    config_factory: Callable[[], Any] | None


class ChatRequest(BaseModel):
    """Request payload for the /chat endpoint."""

    model_config = ConfigDict(extra="ignore")

    query: str = Field(..., description="User query to send to the agent.")
    session_id: str | None = Field(
        default=None,
        description="Session identifier; generated automatically if omitted.",
    )
    llm_context: dict[str, Any] = Field(default_factory=dict, description="Optional LLM-visible context.")
    tool_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional runtime context (not LLM-visible).",
    )

    # Backward-compatible alias for older UI clients.
    context: dict[str, Any] | None = Field(default=None, description="Deprecated alias for llm_context.")


class ChatResponse(BaseModel):
    """Response payload for the /chat endpoint."""

    trace_id: str
    session_id: str
    answer: str | None = None
    metadata: dict[str, Any] | None = None
    pause: dict[str, Any] | None = None


class SteerRequest(BaseModel):
    session_id: str
    task_id: str
    event_type: SteeringEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    source: str = "user"
    event_id: str | None = None


class SteerResponse(BaseModel):
    accepted: bool


class TaskSpawnRequest(BaseModel):
    session_id: str
    query: str | None = None
    task_type: Literal["foreground", "background"] = "background"
    priority: int = 0
    llm_context: dict[str, Any] = Field(default_factory=dict)
    tool_context: dict[str, Any] = Field(default_factory=dict)
    spawn_reason: str | None = None
    description: str | None = None
    wait: bool = False
    merge_strategy: MergeStrategy | None = None
    parent_task_id: str | None = None
    spawned_from_event_id: str | None = None


class TaskSpawnResponse(BaseModel):
    task_id: str
    session_id: str
    status: TaskStatus
    trace_id: str | None = None
    result: dict[str, Any] | None = None


class SessionInfo(BaseModel):
    session_id: str
    task_count: int
    active_tasks: int
    pending_patches: int
    context_version: int
    context_hash: str | None = None


class TaskStateResponse(BaseModel):
    """Response for task state query."""

    foreground_task_id: str | None
    foreground_status: str | None
    background_tasks: list[dict[str, Any]]


class SessionContextUpdate(BaseModel):
    llm_context: dict[str, Any] | None = None
    tool_context: dict[str, Any] | None = None
    merge: bool = False


class ApplyContextPatchRequest(BaseModel):
    patch_id: str
    strategy: MergeStrategy | None = None
    action: Literal["apply", "reject"] = "apply"


class SpecPayload(BaseModel):
    content: str
    valid: bool
    errors: list[dict[str, Any]]
    path: str | None = None


class MetaPayload(BaseModel):
    agent: dict[str, Any]
    planner: dict[str, Any]
    services: list[dict[str, Any]]
    tools: list[dict[str, Any]]


class ComponentRegistryPayload(BaseModel):
    version: str
    enabled: bool
    allowlist: list[str]
    components: dict[str, Any]


class TraceSummaryPayload(BaseModel):
    trace_id: str
    session_id: str
    tags: list[str] = Field(default_factory=list)
    query_preview: str | None = None
    turn_index: int | None = None


class TraceTagsRequest(BaseModel):
    session_id: str
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


class EvalDatasetSelectorPayload(BaseModel):
    include_tags: list[str] = Field(default_factory=list)
    exclude_tags: list[str] = Field(default_factory=list)
    limit: int = 0


class EvalDatasetExportRequest(BaseModel):
    selector: EvalDatasetSelectorPayload | None = None
    output_dir: str
    redaction_profile: str = "internal_safe"


class EvalDatasetExportResponse(BaseModel):
    trace_count: int
    dataset_path: str
    manifest_path: str


class EvalDatasetLoadRequest(BaseModel):
    dataset_path: str


class EvalDatasetExamplePayload(BaseModel):
    example_id: str
    split: str
    question: str


class EvalDatasetLoadResponse(BaseModel):
    dataset_path: str
    manifest_path: str | None = None
    counts: dict[str, Any]
    examples: list[EvalDatasetExamplePayload]


class EvalDatasetBrowseEntry(BaseModel):
    path: str
    label: str
    is_default: bool = False


class EvalMetricBrowseEntry(BaseModel):
    metric_spec: str
    label: str
    source_spec_path: str


class EvalRunRequest(BaseModel):
    dataset_path: str
    metric_spec: str
    min_test_score: float | None = None
    max_cases: int | None = Field(default=None, ge=1)


class EvalRunCasePayload(BaseModel):
    example_id: str
    split: str
    score: float
    feedback: str | None = None
    pred_trace_id: str
    pred_session_id: str
    question: str


class EvalRunResponse(BaseModel):
    run_id: str
    counts: dict[str, int]
    min_test_score: float | None = None
    passed_threshold: bool
    cases: list[EvalRunCasePayload]


class EvalCaseComparisonRequest(BaseModel):
    dataset_path: str
    example_id: str
    pred_trace_id: str
    pred_session_id: str


class EvalCaseComparisonResponse(BaseModel):
    example_id: str
    pred_trace_id: str
    pred_session_id: str
    gold_trace_id: str | None = None
    gold_trajectory: dict[str, Any] | None = None
    pred_trajectory: dict[str, Any] | None = None


class AguiResumeRequest(BaseModel):
    resume_token: str
    thread_id: str
    run_id: str
    tool_name: str | None = None
    component: str | None = None
    result: Any | None = None
    tool_context: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")


def _parse_context_arg(raw: str | None) -> dict[str, Any]:
    if raw is None:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _merge_contexts(primary: dict[str, Any], secondary: dict[str, Any] | None) -> dict[str, Any]:
    if not secondary:
        return primary
    merged = dict(primary)
    merged.update(secondary)
    return merged


def _format_resume_input(input: AguiResumeRequest) -> str | None:
    payload: dict[str, Any] = {}
    if input.tool_name:
        payload["tool"] = input.tool_name
    if input.component:
        payload["component"] = input.component
    if input.result is not None:
        payload["result"] = input.result
    if not payload:
        return None
    try:
        return json.dumps(payload, ensure_ascii=False)
    except TypeError:
        return str(payload)


def _discover_spec_path(project_root: Path) -> Path | None:
    candidates = [
        project_root / "agent.yaml",
        project_root / "agent.yml",
        project_root / "spec.yaml",
        project_root / "spec.yml",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _load_spec_payload(project_root: Path) -> tuple[SpecPayload | None, Spec | None]:
    spec_path = _discover_spec_path(project_root)
    if spec_path is None:
        return None, None
    try:
        spec = load_spec(spec_path)
        return (
            SpecPayload(
                content=spec_path.read_text(encoding="utf-8"),
                valid=True,
                errors=[],
                path=spec_path.as_posix(),
            ),
            spec,
        )
    except SpecValidationError as exc:
        return (
            SpecPayload(
                content=spec_path.read_text(encoding="utf-8"),
                valid=False,
                errors=[
                    {
                        "message": err.message,
                        "path": list(err.path),
                        "line": err.line,
                        "suggestion": err.suggestion,
                    }
                    for err in exc.errors
                ],
                path=spec_path.as_posix(),
            ),
            None,
        )
    except Exception:
        return None, None


def _meta_from_spec(spec: Spec | None) -> MetaPayload:
    agent = {
        "name": spec.agent.name if spec else "unknown_agent",
        "description": spec.agent.description if spec else "",
        "template": spec.agent.template if spec else "unknown",
        "flags": list(spec.agent.flags.model_dump()) if spec else [],
        "flows": len(spec.flows) if spec else 0,
    }
    planner = {
        "max_iters": spec.planner.max_iters if spec else None,
        "hop_budget": spec.planner.hop_budget if spec else None,
        "absolute_max_parallel": spec.planner.absolute_max_parallel if spec else None,
        "reflection": spec.planner.memory_prompt is not None if spec else False,
        "rich_output_enabled": spec.planner.rich_output.enabled if spec else None,
        "rich_output_allowlist": (
            ", ".join(spec.planner.rich_output.allowlist) if spec and spec.planner.rich_output.allowlist else None
        ),
    }
    services = []
    if spec:
        services = [
            {
                "name": "memory_iceberg",
                "enabled": spec.services.memory_iceberg.enabled,
                "url": spec.services.memory_iceberg.base_url,
            },
            {
                "name": "rag_server",
                "enabled": spec.services.rag_server.enabled,
                "url": spec.services.rag_server.base_url,
            },
            {
                "name": "wayfinder",
                "enabled": spec.services.wayfinder.enabled,
                "url": spec.services.wayfinder.base_url,
            },
        ]
    tools = []
    if spec:
        for tool in spec.tools:
            tools.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "side_effects": tool.side_effects,
                    "tags": tool.tags,
                }
            )
    return MetaPayload(agent=agent, planner=planner, services=services, tools=tools)


def _event_frame(
    event: PlannerEvent,
    trace_id: str | None,
    session_id: str,
    *,
    default_message_id: str | None = None,
) -> bytes | None:
    """Convert a planner event into an SSE frame."""
    if trace_id is None:
        return None
    payload: dict[str, Any] = {
        "trace_id": trace_id,
        "session_id": session_id,
        "ts": event.ts,
        "step": event.trajectory_step,
    }
    extra = dict(event.extra or {})
    message_id: str | None = None
    extra_message_id = extra.get("message_id")
    if isinstance(extra_message_id, str) and extra_message_id.strip():
        message_id = extra_message_id
    elif isinstance(default_message_id, str) and default_message_id.strip():
        message_id = default_message_id
    if event.event_type == "stream_chunk":
        phase = "observation"
        meta = extra.get("meta")
        if isinstance(meta, Mapping):
            meta_phase = meta.get("phase")
            if isinstance(meta_phase, str) and meta_phase.strip():
                phase = meta_phase.strip()
        channel_raw: str | None = None
        channel_val_chunk = extra.get("channel")
        if isinstance(channel_val_chunk, str):
            channel_raw = channel_val_chunk
        elif isinstance(meta, Mapping):
            meta_channel = meta.get("channel")
            channel_raw = meta_channel if isinstance(meta_channel, str) else None
        channel: str = channel_raw or "thinking"
        payload.update(
            {
                "stream_id": extra.get("stream_id"),
                "seq": extra.get("seq"),
                "text": extra.get("text"),
                "done": extra.get("done", False),
                "meta": extra.get("meta", {}),
                "phase": phase,
                "channel": channel,
            }
        )
        if message_id is not None:
            payload["message_id"] = message_id
        return format_sse("chunk", payload)

    if event.event_type == "artifact_chunk":
        payload.update(
            {
                "stream_id": extra.get("stream_id"),
                "seq": extra.get("seq"),
                "chunk": extra.get("chunk"),
                "done": extra.get("done", False),
                "artifact_type": extra.get("artifact_type"),
                "meta": extra.get("meta", {}),
                "event": "artifact_chunk",
            }
        )
        return format_sse("artifact_chunk", payload)

    if event.event_type == "artifact_stored":
        # Emit when a binary artifact is stored (e.g., from MCP tool output)
        # Note: Use artifact_filename in extra to avoid LogRecord reserved key conflict
        payload.update(
            {
                "artifact_id": extra.get("artifact_id"),
                "mime_type": extra.get("mime_type"),
                "size_bytes": extra.get("size_bytes"),
                "filename": extra.get("artifact_filename") or extra.get("filename"),
                "source": extra.get("source"),
                "event": "artifact_stored",
            }
        )
        return format_sse("artifact_stored", payload)

    if event.event_type == "resource_updated":
        # Emit when an MCP resource is updated (cache invalidation)
        payload.update(
            {
                "uri": extra.get("uri"),
                "namespace": extra.get("namespace"),
                "event": "resource_updated",
            }
        )
        return format_sse("resource_updated", payload)

    if event.event_type == "llm_stream_chunk":
        phase_val_llm = extra.get("phase")
        phase_llm: str | None = phase_val_llm if isinstance(phase_val_llm, str) else None
        channel_llm_val = extra.get("channel")
        if isinstance(channel_llm_val, str):
            channel_llm: str = channel_llm_val
        elif phase_llm == "answer":
            channel_llm = "answer"
        elif phase_llm == "revision":
            channel_llm = "revision"
        else:
            channel_llm = "thinking"
        action_seq = extra.get("action_seq")
        payload.update(
            {
                "text": extra.get("text", ""),
                "done": extra.get("done", False),
                "phase": phase_llm,
                "channel": channel_llm,
                "action_seq": action_seq,
            }
        )
        if message_id is not None:
            payload["message_id"] = message_id
        return format_sse("llm_stream_chunk", payload)

    if event.node_name:
        payload["node"] = event.node_name
    if event.latency_ms is not None:
        payload["latency_ms"] = event.latency_ms
    if event.token_estimate is not None:
        payload["token_estimate"] = event.token_estimate
    if event.thought:
        payload["thought"] = event.thought
    if extra:
        payload.update(extra)

    if event.event_type in {"step_start", "step_complete"}:
        payload["event"] = event.event_type
        return format_sse("step", payload)

    # Emit dedicated SSE event types for tool calls (enables streaming pattern)
    if event.event_type == "tool_call_start":
        tool_call_id = extra.get("tool_call_id")
        tool_name = extra.get("tool_name")
        args_json = extra.get("args_json", "")
        # Emit tool_call_start first
        frames = format_sse(
            "tool_call_start",
            {
                **payload,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                **({"message_id": message_id} if message_id is not None else {}),
            },
        )
        # Emit args as a single delta chunk for streaming compatibility
        if args_json:
            frames += format_sse(
                "tool_call_args",
                {
                    "tool_call_id": tool_call_id,
                    "delta": args_json,
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "ts": event.ts,
                    **({"message_id": message_id} if message_id is not None else {}),
                },
            )
        return frames

    if event.event_type == "tool_call_end":
        return format_sse(
            "tool_call_end",
            {
                **payload,
                "tool_call_id": extra.get("tool_call_id"),
                "tool_name": extra.get("tool_name"),
                **({"message_id": message_id} if message_id is not None else {}),
            },
        )

    payload["event"] = event.event_type
    return format_sse("event", payload)


def _done_frame(result: ChatResult, session_id: str) -> bytes:
    return format_sse(
        "done",
        {
            "trace_id": result.trace_id,
            "session_id": session_id,
            "answer": result.answer,
            "metadata": result.metadata,
            "pause": result.pause,
            "answer_action_seq": (
                result.metadata.get("answer_action_seq") if isinstance(result.metadata, Mapping) else None
            ),
        },
    )


def _state_update_frame(update: StateUpdate) -> bytes:
    payload = update.model_dump(mode="json")
    return format_sse("state_update", payload)


def _error_frame(message: str, *, trace_id: str | None = None, session_id: str | None = None) -> bytes:
    payload: dict[str, Any] = {"error": message}
    if trace_id:
        payload["trace_id"] = trace_id
    if session_id:
        payload["session_id"] = session_id
    return format_sse("error", payload)


def _ensure_sys_path(base_dir: Path) -> None:
    src_dir = base_dir / "src"
    candidate = src_dir if src_dir.exists() else base_dir
    path_str = str(candidate)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


def _candidate_packages(base_dir: Path) -> list[str]:
    src_dir = base_dir / "src"
    search_dir = src_dir if src_dir.exists() else base_dir
    packages: list[str] = []
    for entry in search_dir.iterdir():
        if entry.is_dir() and (entry / "__init__.py").exists():
            packages.append(entry.name)
    return sorted(packages)


def _import_modules(package: str) -> tuple[list[Any], list[str]]:
    modules: list[Any] = []
    errors: list[str] = []
    for name in ("orchestrator", "planner", "__main__", "__init__"):
        module_name = f"{package}.{name}"
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(f"{module_name}: {exc}")
            continue
        modules.append(module)
    return modules, errors


def _config_factory(package: str) -> Callable[[], Any] | None:
    try:
        cfg_module = importlib.import_module(f"{package}.config")
    except ModuleNotFoundError:
        return None
    except Exception as exc:  # pragma: no cover - defensive
        _LOGGER.debug("playground_config_import_failed", exc_info=exc)
        return None

    config_cls = getattr(cfg_module, "Config", None)
    if config_cls is None:
        return None
    from_env = getattr(config_cls, "from_env", None)
    if callable(from_env):
        return from_env
    try:
        return lambda: config_cls()
    except Exception as exc:  # pragma: no cover - defensive
        _LOGGER.debug("playground_config_default_failed", exc_info=exc)
        return None


def _find_orchestrators(module: Any) -> list[type[Any]]:
    candidates: list[type[Any]] = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if not obj.__name__.endswith("Orchestrator"):
            continue
        execute = getattr(obj, "execute", None)
        if execute and inspect.iscoroutinefunction(execute):
            candidates.append(obj)
    return candidates


def _find_builders(module: Any) -> list[Callable[..., Any]]:
    builder = getattr(module, "build_planner", None)
    if builder and inspect.isfunction(builder):
        return [builder]
    return []


def _discover_agent_with_package(*, base_dir: Path, agent_package: str) -> DiscoveryResult:
    modules, import_errors = _import_modules(agent_package)
    cfg_factory = _config_factory(agent_package)

    orchestrator_matches: list[DiscoveryResult] = []
    planner_matches: list[DiscoveryResult] = []
    for module in modules:
        for orchestrator in _find_orchestrators(module):
            orchestrator_matches.append(
                DiscoveryResult(
                    kind="orchestrator",
                    target=orchestrator,
                    package=agent_package,
                    module=module.__name__,
                    config_factory=cfg_factory,
                )
            )
        for builder in _find_builders(module):
            planner_matches.append(
                DiscoveryResult(
                    kind="planner",
                    target=builder,
                    package=agent_package,
                    module=module.__name__,
                    config_factory=cfg_factory,
                )
            )

    if orchestrator_matches:
        return orchestrator_matches[0]
    if planner_matches:
        return planner_matches[0]

    hint = "; ".join(import_errors) if import_errors else "no orchestrator or planner entry points found"
    raise PlaygroundError(f"Could not discover agent package {agent_package!r} in {base_dir}: {hint}")


def discover_agent(project_root: Path | None = None, *, agent_package: str | None = None) -> DiscoveryResult:
    """Locate an agent entry point within the provided project directory."""

    base_dir = Path(project_root or Path.cwd()).resolve()
    _ensure_sys_path(base_dir)
    if agent_package:
        return _discover_agent_with_package(base_dir=base_dir, agent_package=agent_package)

    packages = _candidate_packages(base_dir)
    errors: list[str] = []
    orchestrators: list[DiscoveryResult] = []
    planners: list[DiscoveryResult] = []

    for package in packages:
        modules, import_errors = _import_modules(package)
        errors.extend(import_errors)
        cfg_factory = _config_factory(package)

        for module in modules:
            for orchestrator in _find_orchestrators(module):
                orchestrators.append(
                    DiscoveryResult(
                        kind="orchestrator",
                        target=orchestrator,
                        package=package,
                        module=module.__name__,
                        config_factory=cfg_factory,
                    )
                )
            for builder in _find_builders(module):
                planners.append(
                    DiscoveryResult(
                        kind="planner",
                        target=builder,
                        package=package,
                        module=module.__name__,
                        config_factory=cfg_factory,
                    )
                )

    if orchestrators:
        return orchestrators[0]
    if planners:
        return planners[0]

    hint = "; ".join(errors) if errors else "no orchestrator or planner entry points found"
    raise PlaygroundError(f"Could not discover agent in {base_dir}: {hint}")


def _instantiate_orchestrator(
    cls: type[Any],
    config: Any | None,
    *,
    session_manager: Any | None = None,
    state_store: Any | None = None,
) -> Any:
    signature = inspect.signature(cls)
    params = [param for name, param in signature.parameters.items() if name != "self"]
    if not params:
        return cls()
    first = params[0]
    if config is None and first.default is inspect._empty:
        raise PlaygroundError(f"Orchestrator {cls.__name__} requires a config")

    # Build kwargs for optional parameters the orchestrator may accept
    kwargs: dict[str, Any] = {}
    if session_manager is not None and "session_manager" in signature.parameters:
        kwargs["session_manager"] = session_manager
    if state_store is not None and "state_store" in signature.parameters:
        kwargs["state_store"] = state_store

    try:
        if config is not None:
            return cls(config, **kwargs)
        return cls(**kwargs) if kwargs else cls()
    except TypeError as exc:
        raise PlaygroundError(f"Failed to instantiate orchestrator {cls.__name__}: {exc}") from exc


def _call_builder(
    builder: Callable[..., Any],
    config: Any | None,
    *,
    state_store: Any | None = None,
) -> Any:
    kwargs: dict[str, Any] = {}
    try:
        signature = inspect.signature(builder)
        if "event_callback" in signature.parameters:
            kwargs["event_callback"] = None
        if state_store is not None and (
            "state_store" in signature.parameters
            or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
        ):
            kwargs["state_store"] = state_store
        params = list(signature.parameters.values())
        if not params:
            return builder(**kwargs)
        first = params[0]
        if config is None and first.default is inspect._empty:
            raise PlaygroundError("build_planner requires a config but none was found")
        if config is None:
            return builder(**kwargs)
        return builder(config, **kwargs)
    except TypeError as exc:
        raise PlaygroundError(f"Failed to invoke build_planner: {exc}") from exc


def _unwrap_planner(builder_output: Any) -> Any:
    if hasattr(builder_output, "planner"):
        return builder_output.planner
    return builder_output


def load_agent(
    project_root: Path | None = None,
    *,
    state_store: PlaygroundStateStore | None = None,
    session_manager: Any | None = None,
    agent_package: str | None = None,
) -> tuple[AgentWrapper, DiscoveryResult]:
    """Discover and wrap the first available agent entry point.

    Args:
        project_root: Path to the project root directory.
        state_store: State store for agent wrapper.
        session_manager: SessionManager instance to share with orchestrator for
            background task visibility. If provided and the orchestrator accepts it,
            the same instance will be used for both UI endpoints and orchestrator.
    """

    result = discover_agent(project_root, agent_package=agent_package)
    config = result.config_factory() if result.config_factory else None
    state_store = state_store or InMemoryStateStore()

    if result.kind == "orchestrator":
        orchestrator = _instantiate_orchestrator(
            result.target,
            config,
            session_manager=session_manager,
            state_store=state_store,
        )
        wrapper: AgentWrapper = OrchestratorAgentWrapper(
            orchestrator,
        )
    else:
        builder_output = _call_builder(result.target, config, state_store=state_store)
        planner = _unwrap_planner(builder_output)
        wrapper = PlannerAgentWrapper(
            planner,
        )

    return wrapper, result


def _build_planner_factory(
    result: DiscoveryResult | None,
    *,
    state_store: Any | None = None,
) -> Callable[[], Any] | None:
    if result is None or result.kind != "planner":
        return None

    def _factory() -> Any:
        config = result.config_factory() if result.config_factory else None
        builder_output = _call_builder(result.target, config, state_store=state_store)
        return _unwrap_planner(builder_output)

    return _factory


def create_playground_app(
    project_root: Path | None = None,
    *,
    agent: AgentWrapper | None = None,
    state_store: PlaygroundStateStore | None = None,
    agent_package: str | None = None,
) -> FastAPI:
    """Create the FastAPI playground app."""

    discovery: DiscoveryResult | None = None
    agent_wrapper = agent
    store = state_store
    broker = EventBroker()
    session_limits = SessionLimits()
    planner_factory: Callable[[], Any] | None = None
    # Embedded MCP Apps keep calling back after the planner turn ends, so the
    # backend needs a session-scoped handle to the live ToolNode/MCP client.
    sticky_tool_nodes: dict[str, dict[str, Any]] = {}
    ui_dir = Path(__file__).resolve().parent / "playground_ui" / "dist"
    resolved_project_root = Path(project_root or ".").resolve()
    spec_payload, parsed_spec = _load_spec_payload(resolved_project_root)
    meta_payload = _meta_from_spec(parsed_spec)

    # Determine session_store first so we can create SessionManager before load_agent.
    # This allows the orchestrator to share the same SessionManager for background task visibility.
    if agent_wrapper is None:
        store = state_store or InMemoryStateStore()
    else:
        if store is None:
            store = getattr(agent_wrapper, "_state_store", None) or InMemoryStateStore()

    # Share the same store with the SessionManager when it supports task persistence.
    # Otherwise, keep session/task state in-memory (the Playground can still store trajectories/events).
    session_store: Any | None = None
    if store is not None and (
        hasattr(store, "save_task") or (hasattr(store, "save_event") and hasattr(store, "load_history"))
    ):
        session_store = store
    session_manager = SessionManager(limits=session_limits, state_store=session_store)

    # Now load the agent, passing the shared SessionManager
    if agent_wrapper is None:
        agent_wrapper, discovery = load_agent(
            project_root,
            state_store=store,
            session_manager=session_manager,
            agent_package=agent_package,
        )
        planner_factory = _build_planner_factory(discovery, state_store=store)
    else:
        planner_factory = None
    try:
        supports_steering_chat = "steering" in inspect.signature(agent_wrapper.chat).parameters
    except (TypeError, ValueError):
        supports_steering_chat = False

    _LOGGER.info(
        "playground_steering_support",
        extra={"supports_steering_chat": supports_steering_chat},
    )

    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        # Eagerly initialize the agent wrapper (connects external tools, sets up planner)
        # This ensures event callbacks can be attached before the first request
        try:
            await agent_wrapper.initialize()
        except Exception as exc:
            _LOGGER.warning(f"Agent initialization failed during startup: {exc}")
            # Continue anyway - lazy init will retry on first request
        try:
            yield
        finally:  # pragma: no cover - exercised in integration
            try:
                await broker.close()
            finally:
                await agent_wrapper.shutdown()

    app = FastAPI(title="PenguiFlow Playground", version="0.1.0", lifespan=_lifespan)
    proactive_setup: Callable[[Any], None] | None = None

    # Optional: enable platform task-management meta-tools when a planner factory is available.
    # This is a Playground convenience to make background tasks discoverable without requiring
    # downstream project code changes.
    if planner_factory is not None:
        try:
            from penguiflow.planner import ReactPlanner
            from penguiflow.planner import prompts as planner_prompts
            from penguiflow.planner.catalog_extension import extend_tool_catalog
            from penguiflow.planner.models import BackgroundTasksConfig
            from penguiflow.sessions.task_service import InProcessTaskService
            from penguiflow.sessions.task_tools import SUBAGENT_FLAG_KEY, TASK_SERVICE_KEY, build_task_tool_specs
            from penguiflow.sessions.tool_jobs import build_tool_job_pipeline

            planner = None
            try:
                planner = getattr(agent_wrapper, "_planner", None)
            except Exception:
                planner = None
            tool_job_factory = None
            background_cfg: BackgroundTasksConfig | None = None
            if isinstance(planner, ReactPlanner):
                spec_by_name = getattr(planner, "_spec_by_name", {}) or {}
                artifact_store = getattr(planner, "artifact_store", None)

                def _tool_job_factory(tool_name: str, tool_args: Any):
                    spec = spec_by_name.get(tool_name)
                    if spec is None:
                        raise RuntimeError(f"tool_not_found:{tool_name}")
                    return build_tool_job_pipeline(
                        spec=spec,
                        args_payload=dict(tool_args or {}),
                        artifacts=artifact_store,
                    )

                tool_job_factory = _tool_job_factory
            if isinstance(planner, ReactPlanner):
                existing_extra = getattr(planner, "_system_prompt_extra", None)
                planner._system_prompt_extra = planner_prompts.merge_prompt_extras(
                    existing_extra,
                    planner_prompts.render_background_task_guidance(),
                )
                existing_cfg = getattr(planner, "_background_tasks", None)
                if isinstance(existing_cfg, BackgroundTasksConfig):
                    planner._background_tasks = existing_cfg.model_copy(
                        update={"enabled": True, "allow_tool_background": True}
                    )
                    background_cfg = planner._background_tasks
                    if background_cfg.enabled and background_cfg.proactive_report_enabled:
                        from penguiflow.sessions.proactive import setup_proactive_reporting

                        def _setup(session: Any) -> None:
                            setup_proactive_reporting(
                                session,
                                planner_factory,
                                enabled=True,
                                strategies=list(background_cfg.proactive_report_strategies),
                                max_queued=background_cfg.proactive_report_max_queued,
                                timeout_s=background_cfg.proactive_report_timeout_s,
                                max_hops=background_cfg.proactive_report_max_hops,
                                fallback_notification=background_cfg.proactive_report_fallback_notification,
                            )

                        proactive_setup = _setup
                extend_tool_catalog(planner, build_task_tool_specs())
            task_service = InProcessTaskService(
                sessions=session_manager,
                planner_factory=planner_factory,
                tool_job_factory=tool_job_factory,
                background_config=background_cfg,
            )
            # Inject TaskService into PlannerAgentWrapper tool_context defaults if available.
            defaults = getattr(agent_wrapper, "_tool_context_defaults", None)
            if isinstance(defaults, dict):
                defaults.setdefault(TASK_SERVICE_KEY, task_service)
                defaults.setdefault(SUBAGENT_FLAG_KEY, False)
        except Exception as exc:  # pragma: no cover - optional wiring
            _LOGGER.debug("task_tools_unavailable", extra={"error": str(exc)})

    async def _get_session(session_id: str) -> Any:
        session = await session_manager.get_or_create(session_id)
        _sticky_tool_nodes_for_session(session_id)
        config = getattr(session, "_proactive_config", None)
        already_enabled = isinstance(config, dict) and bool(config.get("enabled"))
        if proactive_setup is not None and not already_enabled:
            proactive_setup(session)
            return session

        # If proactive_setup wasn't configured (common for orchestrator-based agents),
        # try enabling proactive reporting dynamically based on the discovered planner.
        if proactive_setup is None and not already_enabled:
            try:
                from penguiflow.planner import ReactPlanner
                from penguiflow.planner.models import BackgroundTasksConfig
                from penguiflow.sessions.proactive import setup_proactive_reporting

                planner = _discover_planner()
                if isinstance(planner, ReactPlanner):
                    background_cfg = getattr(planner, "_background_tasks", None)
                    if isinstance(background_cfg, BackgroundTasksConfig):
                        if background_cfg.enabled and background_cfg.proactive_report_enabled:
                            # Prefer the discovered builder planner_factory, otherwise fork the live planner.
                            effective_planner_factory = planner_factory
                            if effective_planner_factory is None:

                                def _fork_factory() -> Any:
                                    return planner.fork()

                                effective_planner_factory = _fork_factory
                            setup_proactive_reporting(
                                session,
                                effective_planner_factory,
                                enabled=True,
                                strategies=list(background_cfg.proactive_report_strategies),
                                max_queued=background_cfg.proactive_report_max_queued,
                                timeout_s=background_cfg.proactive_report_timeout_s,
                                max_hops=background_cfg.proactive_report_max_hops,
                                fallback_notification=background_cfg.proactive_report_fallback_notification,
                            )
            except Exception:
                # Optional wiring; avoid breaking session creation.
                pass
        return session

    def _discover_planner() -> Any | None:
        """Discover the underlying planner instance from the agent wrapper."""
        wrapper_dict = getattr(agent_wrapper, "__dict__", {})
        planner = wrapper_dict.get("_planner")
        if planner is not None:
            return planner
        orchestrator = wrapper_dict.get("_orchestrator")
        if orchestrator is not None:
            planner = getattr(orchestrator, "__dict__", {}).get("_planner")
            if planner is not None:
                return planner
        return None

    def _discover_artifact_store() -> Any | None:
        """Discover the artifact store from the running agent (no injection).

        Returns None if the agent has no artifact store configured or is using NoOp.
        """
        from penguiflow.artifacts import ArtifactStore, NoOpArtifactStore

        planner = _discover_planner()
        if planner is not None:
            found = getattr(planner, "artifact_store", None)
            if found is None:
                found = getattr(planner, "_artifact_store", None)
            if found is not None and isinstance(found, ArtifactStore) and not isinstance(found, NoOpArtifactStore):
                return found

        # Fallback: check the playground state store directly
        if store is not None:
            found = getattr(store, "artifact_store", None)
            if found is not None and isinstance(found, ArtifactStore) and not isinstance(found, NoOpArtifactStore):
                return found

        return None

    def _namespace_from_tool_node(tool_node: Any) -> str | None:
        config = getattr(tool_node, "config", None)
        raw_name = getattr(config, "name", None)
        if isinstance(raw_name, str) and raw_name.strip():
            return raw_name.strip()
        return None

    def _iter_tool_nodes(tool_nodes: Any) -> list[tuple[str, Any]]:
        resolved: list[tuple[str, Any]] = []
        if isinstance(tool_nodes, dict):
            for key, tool_node in tool_nodes.items():
                namespace = str(key).strip()
                if namespace and tool_node is not None:
                    resolved.append((namespace, tool_node))
            return resolved
        if isinstance(tool_nodes, list):
            for tool_node in tool_nodes:
                tool_namespace = _namespace_from_tool_node(tool_node)
                if tool_namespace is not None:
                    resolved.append((tool_namespace, tool_node))
        return resolved

    def _tool_nodes_from_planner_specs(planner: Any | None) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        if planner is None:
            return resolved
        specs = getattr(planner, "_specs", None)
        if not isinstance(specs, list):
            return resolved

        for spec in specs:
            extra = getattr(spec, "extra", None)
            if not isinstance(extra, Mapping):
                continue
            tool_node = extra.get("tool_node")
            if tool_node is None:
                continue
            raw_namespace = extra.get("namespace")
            if isinstance(raw_namespace, str) and raw_namespace.strip():
                resolved[raw_namespace.strip()] = tool_node
                continue
            namespace = _namespace_from_tool_node(tool_node)
            if namespace is not None:
                resolved[namespace] = tool_node

        return resolved

    def _discover_live_tool_nodes() -> dict[str, Any]:
        resolved: dict[str, Any] = {}

        for namespace, tool_node in _iter_tool_nodes(getattr(agent_wrapper, "__dict__", {}).get("_tool_nodes")):
            resolved[namespace] = tool_node

        planner = _discover_planner()
        if planner is not None:
            for namespace, tool_node in _iter_tool_nodes(getattr(planner, "__dict__", {}).get("_tool_nodes")):
                resolved[namespace] = tool_node
            resolved.update(_tool_nodes_from_planner_specs(planner))

        return resolved

    def _sticky_tool_nodes_for_session(session_id: str | None) -> dict[str, Any]:
        if not isinstance(session_id, str) or not session_id.strip():
            return {}
        normalized_session_id = session_id.strip()
        sticky_for_session = sticky_tool_nodes.setdefault(normalized_session_id, {})
        live_tool_nodes = _discover_live_tool_nodes()
        if live_tool_nodes:
            sticky_for_session.update(live_tool_nodes)
        return sticky_for_session

    def _rich_output_config_from_spec(spec: Spec | None) -> Any | None:
        if configure_rich_output is None or RichOutputConfig is None:
            return None
        if spec is None:
            return RichOutputConfig(enabled=False)
        rich = spec.planner.rich_output
        allowlist = rich.allowlist if rich.allowlist else list(DEFAULT_ALLOWLIST)
        return RichOutputConfig(
            enabled=rich.enabled,
            allowlist=allowlist,
            include_prompt_catalog=rich.include_prompt_catalog,
            include_prompt_examples=rich.include_prompt_examples,
            max_payload_bytes=rich.max_payload_bytes,
            max_total_bytes=rich.max_total_bytes,
        )

    class _ScopedArtifactStore:
        """ArtifactStore wrapper that injects a default scope when missing."""

        def __init__(self, store: Any, scope: Any) -> None:
            self._store = store
            self._scope = scope

        async def put_bytes(
            self,
            data: bytes,
            *,
            mime_type: str | None = None,
            filename: str | None = None,
            namespace: str | None = None,
            scope: Any | None = None,
            meta: dict[str, Any] | None = None,
        ) -> Any:
            return await self._store.put_bytes(
                data,
                mime_type=mime_type,
                filename=filename,
                namespace=namespace,
                scope=scope or self._scope,
                meta=meta,
            )

        async def put_text(
            self,
            text: str,
            *,
            mime_type: str = "text/plain",
            filename: str | None = None,
            namespace: str | None = None,
            scope: Any | None = None,
            meta: dict[str, Any] | None = None,
        ) -> Any:
            return await self._store.put_text(
                text,
                mime_type=mime_type,
                filename=filename,
                namespace=namespace,
                scope=scope or self._scope,
                meta=meta,
            )

        async def get(self, artifact_id: str):
            return await self._store.get(artifact_id)

        async def get_ref(self, artifact_id: str):
            return await self._store.get_ref(artifact_id)

        async def delete(self, artifact_id: str):
            return await self._store.delete(artifact_id)

        async def exists(self, artifact_id: str):
            return await self._store.exists(artifact_id)

        async def list(self, *, scope: Any | None = None) -> Any:
            return await self._store.list(scope=scope or self._scope)

    class _DisabledArtifactStore:
        """ArtifactStore shim used when artifact storage is not enabled."""

        async def put_bytes(self, *_args, **_kwargs):
            raise RuntimeError("Artifact storage is not enabled for this agent")

        async def put_text(self, *_args, **_kwargs):
            raise RuntimeError("Artifact storage is not enabled for this agent")

        async def get(self, _artifact_id: str):
            return None

        async def get_ref(self, _artifact_id: str):
            return None

        async def delete(self, _artifact_id: str):
            return False

        async def exists(self, _artifact_id: str):
            return False

        async def list(self, *, scope: Any | None = None) -> list[Any]:
            return []

    @app.on_event("shutdown")
    async def _shutdown_events() -> None:  # pragma: no cover - exercised at runtime
        await broker.close()

    if ui_dir.exists():
        # Mount assets directory for JS/CSS
        assets_dir = ui_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/", include_in_schema=False)
        async def root_ui() -> FileResponse:
            return FileResponse(ui_dir / "index.html")

    @app.get("/health")
    async def health() -> Mapping[str, str]:
        return {"status": "ok"}

    def _jsonify_for_api(value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json", exclude_none=True)
        if hasattr(value, "model_dump") and callable(value.model_dump):
            return value.model_dump(mode="json", exclude_none=True)
        if isinstance(value, Mapping):
            return {str(k): _jsonify_for_api(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonify_for_api(v) for v in value]
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    def _resolve_tool_nodes(session_id: str | None = None) -> dict[str, Any]:
        normalized_session_id = session_id.strip() if isinstance(session_id, str) and session_id.strip() else None
        resolved: dict[str, Any] = {}
        if normalized_session_id:
            resolved.update(_sticky_tool_nodes_for_session(normalized_session_id))
        live_tool_nodes = _discover_live_tool_nodes()
        if live_tool_nodes:
            resolved.update(live_tool_nodes)
            if normalized_session_id:
                sticky_tool_nodes.setdefault(normalized_session_id, {}).update(live_tool_nodes)
        return resolved

    def _resolve_tool_node(namespace: str, *, session_id: str | None = None) -> Any:
        tool_nodes = _resolve_tool_nodes(session_id=session_id)
        tool_node = tool_nodes.get(namespace)
        if tool_node is not None:
            return tool_node
        if tool_nodes:
            raise HTTPException(status_code=404, detail=f"Tool node '{namespace}' not found")
        raise HTTPException(status_code=500, detail="No tool nodes available")

    def _build_scoped_store(
        session_id: str | None,
        tenant_id: str | None,
        user_id: str | None,
    ) -> tuple[str, Any]:
        resolved_session = session_id or "default"
        artifact_store = _discover_artifact_store()
        if artifact_store is None:
            return resolved_session, _DisabledArtifactStore()

        from penguiflow.artifacts import ArtifactScope

        return resolved_session, _ScopedArtifactStore(
            artifact_store,
            ArtifactScope(
                session_id=resolved_session,
                tenant_id=tenant_id,
                user_id=user_id,
            ),
        )

    def _build_minimal_ctx(
        artifacts: Any,
        *,
        session_id: str,
        tenant_id: str | None,
        user_id: str | None,
    ) -> Any:
        class MinimalToolCtx:
            def __init__(self) -> None:
                self._artifacts_store = artifacts
                self._tool_context = {
                    "session_id": session_id,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                }
                self._llm_context: dict[str, Any] = {}
                self._meta: dict[str, Any] = {}

            @property
            def tool_context(self) -> dict[str, Any]:
                return self._tool_context

            @property
            def llm_context(self) -> dict[str, Any]:
                return self._llm_context

            @property
            def meta(self) -> dict[str, Any]:
                return self._meta

            @property
            def _artifacts(self) -> Any:
                return self._artifacts_store

        return MinimalToolCtx()

    async def _ensure_live_mcp_client(tool_node: Any) -> Any | None:
        current_loop = asyncio.get_running_loop()
        connected_loop = getattr(tool_node, "_connected_loop", None)
        if not getattr(tool_node, "_connected", False) or (
            connected_loop is not None and connected_loop is not current_loop
        ):
            force_reconnect = getattr(tool_node, "_force_reconnect", None)
            if callable(force_reconnect):
                maybe_awaitable = force_reconnect()
                if inspect.isawaitable(maybe_awaitable):
                    await maybe_awaitable
        return getattr(tool_node, "_mcp_client", None)

    def _resolve_original_tool_name(namespace: str, tool_name: str) -> str:
        if "." in tool_name:
            if not tool_name.startswith(f"{namespace}."):
                raise HTTPException(status_code=400, detail="Tool name namespace mismatch")
            return tool_name.removeprefix(f"{namespace}.")
        return tool_name

    @app.get("/ui/spec", response_model=SpecPayload | None)
    async def ui_spec() -> SpecPayload | None:
        return spec_payload

    @app.post("/ui/validate", response_model=SpecPayload)
    async def ui_validate(payload: dict[str, Any]) -> SpecPayload:
        spec_text = payload.get("spec_text", "")
        temp_path = Path(project_root or ".").resolve() / ".tmp_spec.yaml"
        temp_path.write_text(spec_text, encoding="utf-8")
        try:
            load_spec(temp_path)
            return SpecPayload(content=spec_text, valid=True, errors=[], path=str(temp_path))
        except SpecValidationError as exc:
            return SpecPayload(
                content=spec_text,
                valid=False,
                errors=[
                    {
                        "message": err.message,
                        "path": list(err.path),
                        "line": err.line,
                        "suggestion": err.suggestion,
                    }
                    for err in exc.errors
                ],
                path=str(temp_path),
            )
        finally:
            temp_path.unlink(missing_ok=True)

    @app.get("/ui/meta", response_model=MetaPayload)
    async def ui_meta() -> MetaPayload:
        return meta_payload

    @app.get("/ui/components", response_model=ComponentRegistryPayload)
    async def ui_components() -> ComponentRegistryPayload:
        config = _rich_output_config_from_spec(parsed_spec)
        if configure_rich_output is None or config is None:
            raise HTTPException(status_code=501, detail="Rich output support requires jsonschema dependency.")
        runtime = configure_rich_output(config)
        payload = runtime.registry_payload()
        return ComponentRegistryPayload(**payload)

    @app.post("/ui/generate")
    async def ui_generate(payload: dict[str, Any]) -> Mapping[str, Any]:
        spec_text = payload.get("spec_text")
        if not isinstance(spec_text, str):
            raise HTTPException(status_code=400, detail="spec_text is required")
        temp_spec = Path(project_root or ".").resolve() / ".ui_spec.yaml"
        temp_spec.write_text(spec_text, encoding="utf-8")
        try:
            result = run_generate(
                spec_path=temp_spec,
                output_dir=Path(project_root or "."),
                dry_run=True,
                force=True,
                quiet=True,
            )
            return {
                "success": result.success,
                "created": result.created,
                "skipped": result.skipped,
                "errors": result.errors,
            }
        except SpecValidationError as exc:
            detail = [
                {
                    "message": err.message,
                    "path": list(err.path),
                    "line": err.line,
                    "suggestion": err.suggestion,
                }
                for err in exc.errors
            ]
            raise HTTPException(status_code=400, detail=detail) from exc
        except Exception as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            temp_spec.unlink(missing_ok=True)

    @app.post("/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        session_id = request.session_id or secrets.token_hex(8)
        trace_holder: dict[str, str | None] = {"id": request.session_id}
        session = await _get_session(session_id)
        try:
            await session.ensure_capacity(TaskType.FOREGROUND)
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        task_id = secrets.token_hex(8)
        snapshot = TaskContextSnapshot(
            session_id=session_id,
            task_id=task_id,
            query=request.query,
            llm_context=dict(request.llm_context or {}),
            tool_context=dict(request.tool_context or {}),
            spawn_reason="foreground_chat",
        )
        task_state = await session.registry.create_task(
            session_id=session_id,
            task_type=TaskType.FOREGROUND,
            priority=0,
            context_snapshot=snapshot,
            description=request.query,
            trace_id=trace_holder["id"],
            task_id=task_id,
        )
        session._emit_status_change(task_state, reason="created")
        updated_state = await session.registry.update_status(task_id, TaskStatus.RUNNING)
        session._emit_status_change(updated_state or task_state, reason="running")
        steering = SteeringInbox()
        session._steering_inboxes[task_id] = steering

        def _event_consumer(event: PlannerEvent, trace_id: str | None) -> None:
            tid = trace_id or trace_holder["id"]
            if tid is None:
                return
            trace_holder["id"] = tid
            frame = _event_frame(event, tid, session_id)
            if frame:
                broker.publish(tid, frame)
            for update in PlannerEventProjector(
                session_id=session_id,
                task_id=task_id,
                trace_id=tid,
            ).project(event):
                session._publish(update)

        try:
            llm_context = _merge_contexts(dict(request.llm_context or {}), request.context)
            session.update_context(
                llm_context=dict(llm_context or {}),
                tool_context=dict(request.tool_context or {}),
            )
            chat_kwargs: dict[str, Any] = {
                "query": request.query,
                "session_id": session_id,
                "llm_context": llm_context,
                "tool_context": {
                    **dict(request.tool_context or {}),
                    "task_id": task_id,
                    "is_subagent": False,
                },
                "event_consumer": _event_consumer,
                "trace_id_hint": trace_holder["id"],
            }
            if supports_steering_chat:
                chat_kwargs["steering"] = steering
            result: ChatResult = await agent_wrapper.chat(**chat_kwargs)
        except Exception as exc:
            _LOGGER.exception("playground_chat_failed", exc_info=exc)
            updated_state = await session.registry.update_task(task_id, status=TaskStatus.FAILED, error=str(exc))
            session._emit_status_change(updated_state or task_state, reason="failed")
            raise HTTPException(status_code=500, detail=f"Chat failed: {exc}") from exc
        finally:
            session._steering_inboxes.pop(task_id, None)

        trace_holder["id"] = result.trace_id
        broker.publish(result.trace_id, _done_frame(result, session_id))
        if result.pause:
            updated_state = await session.registry.update_task(
                task_id,
                status=TaskStatus.PAUSED,
                trace_id=result.trace_id,
            )
            session._emit_status_change(updated_state or task_state, reason="paused")
            session._publish(
                StateUpdate(
                    session_id=session_id,
                    task_id=task_id,
                    trace_id=result.trace_id,
                    update_type=UpdateType.CHECKPOINT,
                    content={
                        "kind": "approval_required",
                        "resume_token": result.pause.get("resume_token"),
                        "prompt": result.pause.get("payload", {}).get("prompt", "Awaiting input"),
                        "options": ["approve", "reject"],
                    },
                )
            )
        else:
            updated_state = await session.registry.update_task(
                task_id,
                status=TaskStatus.COMPLETE,
                result=result.answer,
                trace_id=result.trace_id,
            )
            session._emit_status_change(updated_state or task_state, reason="complete")

        return ChatResponse(
            trace_id=result.trace_id,
            session_id=result.session_id,
            answer=result.answer,
            metadata=result.metadata,
            pause=result.pause,
        )

    @app.get("/chat/stream")
    async def chat_stream(
        query: str,
        session_id: str | None = None,
        llm_context: str | None = None,
        tool_context: str | None = None,
        context: str | None = None,
    ) -> StreamingResponse:
        session_value = session_id or secrets.token_hex(8)
        llm_payload = _merge_contexts(_parse_context_arg(llm_context), _parse_context_arg(context) or None)
        tool_payload = _parse_context_arg(tool_context)
        queue: asyncio.Queue[bytes | object] = asyncio.Queue()
        trace_holder: dict[str, str | None] = {"id": secrets.token_hex(8)}
        stream_message_id = f"msg_{secrets.token_hex(8)}"
        session = await _get_session(session_value)
        try:
            await session.ensure_capacity(TaskType.FOREGROUND)
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        session.update_context(
            llm_context=dict(llm_payload or {}),
            tool_context=dict(tool_payload or {}),
        )
        task_id = secrets.token_hex(8)
        snapshot = TaskContextSnapshot(
            session_id=session_value,
            task_id=task_id,
            query=query,
            llm_context=dict(llm_payload or {}),
            tool_context=dict(tool_payload or {}),
            spawn_reason="foreground_chat",
        )
        await session.registry.create_task(
            session_id=session_value,
            task_type=TaskType.FOREGROUND,
            priority=0,
            context_snapshot=snapshot,
            description=query,
            trace_id=trace_holder["id"],
            task_id=task_id,
        )
        steering = SteeringInbox()
        session._steering_inboxes[task_id] = steering

        def _emit_state_update(update: StateUpdate) -> None:
            session._publish(update)
            try:
                queue.put_nowait(_state_update_frame(update))
            except asyncio.QueueFull:
                pass

        _emit_state_update(
            StateUpdate(
                session_id=session_value,
                task_id=task_id,
                trace_id=trace_holder["id"],
                update_type=UpdateType.STATUS_CHANGE,
                content={
                    "status": TaskStatus.PENDING.value,
                    "reason": "created",
                    "task_type": TaskType.FOREGROUND.value,
                },
            )
        )
        await session.registry.update_status(task_id, TaskStatus.RUNNING)
        _emit_state_update(
            StateUpdate(
                session_id=session_value,
                task_id=task_id,
                trace_id=trace_holder["id"],
                update_type=UpdateType.STATUS_CHANGE,
                content={
                    "status": TaskStatus.RUNNING.value,
                    "reason": "running",
                    "task_type": TaskType.FOREGROUND.value,
                },
            )
        )

        def _event_consumer(event: PlannerEvent, trace_id: str | None) -> None:
            tid = trace_id or trace_holder["id"]
            if tid is None:
                return
            trace_holder["id"] = tid
            frame = _event_frame(event, tid, session_value, default_message_id=stream_message_id)
            if frame:
                try:
                    queue.put_nowait(frame)
                except asyncio.QueueFull:
                    pass
                broker.publish(tid, frame)
            for update in PlannerEventProjector(
                session_id=session_value,
                task_id=task_id,
                trace_id=tid,
            ).project(event):
                _emit_state_update(update)

        async def _run_chat() -> None:
            try:
                chat_kwargs: dict[str, Any] = {
                    "query": query,
                    "session_id": session_value,
                    "llm_context": llm_payload,
                    "tool_context": {
                        **dict(tool_payload or {}),
                        "task_id": task_id,
                        "is_subagent": False,
                    },
                    "event_consumer": _event_consumer,
                    "trace_id_hint": trace_holder["id"],
                }
                if supports_steering_chat:
                    chat_kwargs["steering"] = steering
                result: ChatResult = await agent_wrapper.chat(**chat_kwargs)
                trace_holder["id"] = result.trace_id
                frame = _done_frame(result, session_value)
                broker.publish(result.trace_id, frame)
                await queue.put(frame)
                if result.pause:
                    await session.registry.update_task(
                        task_id,
                        status=TaskStatus.PAUSED,
                        trace_id=result.trace_id,
                    )
                    _emit_state_update(
                        StateUpdate(
                            session_id=session_value,
                            task_id=task_id,
                            trace_id=result.trace_id,
                            update_type=UpdateType.STATUS_CHANGE,
                            content={
                                "status": TaskStatus.PAUSED.value,
                                "reason": "paused",
                                "task_type": TaskType.FOREGROUND.value,
                            },
                        )
                    )
                    _emit_state_update(
                        StateUpdate(
                            session_id=session_value,
                            task_id=task_id,
                            trace_id=result.trace_id,
                            update_type=UpdateType.CHECKPOINT,
                            content={
                                "kind": "approval_required",
                                "resume_token": result.pause.get("resume_token"),
                                "prompt": result.pause.get("payload", {}).get("prompt", "Awaiting input"),
                                "options": ["approve", "reject"],
                            },
                        )
                    )
                else:
                    await session.registry.update_task(
                        task_id,
                        status=TaskStatus.COMPLETE,
                        result=result.answer,
                        trace_id=result.trace_id,
                    )
                    _emit_state_update(
                        StateUpdate(
                            session_id=session_value,
                            task_id=task_id,
                            trace_id=result.trace_id,
                            update_type=UpdateType.STATUS_CHANGE,
                            content={
                                "status": TaskStatus.COMPLETE.value,
                                "reason": "complete",
                                "task_type": TaskType.FOREGROUND.value,
                            },
                        )
                    )
            except Exception as exc:  # pragma: no cover - defensive
                await session.registry.update_task(task_id, status=TaskStatus.FAILED, error=str(exc))
                _emit_state_update(
                    StateUpdate(
                        session_id=session_value,
                        task_id=task_id,
                        trace_id=trace_holder["id"],
                        update_type=UpdateType.STATUS_CHANGE,
                        content={
                            "status": TaskStatus.FAILED.value,
                            "reason": "failed",
                            "task_type": TaskType.FOREGROUND.value,
                        },
                    )
                )
                await queue.put(_error_frame(str(exc), trace_id=trace_holder["id"], session_id=session_value))
            finally:
                session._steering_inboxes.pop(task_id, None)
                await queue.put(SSESentinel)

        asyncio.create_task(_run_chat())
        return StreamingResponse(
            stream_queue(queue),
            media_type="text/event-stream",
        )

    @app.get("/session/stream")
    async def session_stream(
        session_id: str,
        since_id: str | None = None,
        task_ids: list[str] | None = None,
        update_types: list[UpdateType] | None = None,
    ) -> StreamingResponse:
        session = await _get_session(session_id)
        updates_iter = await session.subscribe(
            since_id=since_id,
            task_ids=task_ids,
            update_types=update_types,
        )

        async def _event_stream() -> AsyncIterator[bytes]:
            try:
                yield format_sse("state_update", {"event": "connected", "session_id": session_id})
                async for update in updates_iter:
                    yield _state_update_frame(update)
            except asyncio.CancelledError:
                pass

        return StreamingResponse(
            _event_stream(),
            media_type="text/event-stream",
        )

    @app.get("/sessions/{session_id}", response_model=SessionInfo)
    async def session_info(session_id: str) -> SessionInfo:
        session = await _get_session(session_id)
        tasks = await session.list_tasks()
        active = len(
            [task for task in tasks if task.status in {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.PAUSED}]
        )
        return SessionInfo(
            session_id=session_id,
            task_count=len(tasks),
            active_tasks=active,
            pending_patches=len(session.pending_patches),
            context_version=session.context_version,
            context_hash=session.context_hash,
        )

    @app.get("/session/{session_id}/task-state", response_model=TaskStateResponse)
    async def get_task_state(session_id: str) -> TaskStateResponse:
        """Get current foreground/background task state for steering decisions."""
        session = await _get_session(session_id)

        foreground_id = session._foreground_task_id
        foreground_status = None
        if foreground_id:
            fg_state = await session.registry.get_task(foreground_id)
            foreground_status = fg_state.status.value if fg_state else None

        # Get active background tasks
        all_tasks = await session.registry.list_tasks(session_id)
        active_statuses = {TaskStatus.RUNNING, TaskStatus.PENDING, TaskStatus.PAUSED}
        background_tasks = [
            {
                "task_id": t.task_id,
                "description": t.description,
                "status": t.status.value,
                "task_type": t.task_type.value,
                "priority": t.priority,
            }
            for t in all_tasks
            if t.task_type == TaskType.BACKGROUND and t.status in active_statuses
        ]

        return TaskStateResponse(
            foreground_task_id=foreground_id if foreground_status == "RUNNING" else None,
            foreground_status=foreground_status,
            background_tasks=background_tasks,
        )

    @app.delete("/sessions/{session_id}")
    async def session_delete(session_id: str) -> Mapping[str, Any]:
        await session_manager.drop(session_id)
        sticky_tool_nodes.pop(session_id, None)
        return {"deleted": True, "session_id": session_id}

    @app.patch("/sessions/{session_id}/context")
    async def session_update_context(session_id: str, payload: SessionContextUpdate) -> Mapping[str, Any]:
        session = await _get_session(session_id)
        if payload.merge:
            llm_context, tool_context = session.get_context()
            if payload.llm_context:
                llm_context.update(payload.llm_context)
            if payload.tool_context:
                tool_context.update(payload.tool_context)
            session.update_context(llm_context=llm_context, tool_context=tool_context)
        else:
            session.update_context(
                llm_context=payload.llm_context,
                tool_context=payload.tool_context,
            )
        return {"ok": True, "context_version": session.context_version}

    @app.post("/sessions/{session_id}/apply-context-patch")
    async def session_apply_context_patch(
        session_id: str,
        payload: ApplyContextPatchRequest,
    ) -> Mapping[str, Any]:
        session = await _get_session(session_id)
        if payload.action == "reject":
            await session.steer(
                SteeringEvent(
                    session_id=session_id,
                    task_id="context_patch",
                    event_type=SteeringEventType.REJECT,
                    payload={"patch_id": payload.patch_id},
                    source="user",
                )
            )
            return {"ok": True, "action": "rejected"}
        applied = await session.apply_pending_patch(
            patch_id=payload.patch_id,
            strategy=payload.strategy,
        )
        if not applied:
            raise HTTPException(status_code=404, detail="Patch not found")
        return {"ok": True, "action": "applied"}

    @app.post("/steer", response_model=SteerResponse)
    async def steer(request: SteerRequest) -> SteerResponse:
        session = await _get_session(request.session_id)
        event = SteeringEvent(
            session_id=request.session_id,
            task_id=request.task_id,
            event_id=request.event_id or secrets.token_hex(8),
            event_type=request.event_type,
            payload=dict(request.payload or {}),
            trace_id=request.trace_id,
            source=request.source or "user",
        )
        event = sanitize_steering_event(event)
        try:
            validate_steering_event(event)
        except SteeringValidationError as exc:
            raise HTTPException(status_code=422, detail={"errors": exc.errors}) from exc
        accepted = await session.steer(event)
        return SteerResponse(accepted=accepted)

    @app.get("/tasks", response_model=list[TaskStateModel])
    async def list_tasks(
        session_id: str,
        status: TaskStatus | None = None,
    ) -> list[TaskStateModel]:
        session = await _get_session(session_id)
        tasks = await session.list_tasks(status=status)
        return [TaskStateModel.from_state(task) for task in tasks]

    @app.get("/tasks/{task_id}", response_model=TaskStateModel)
    async def get_task(task_id: str, session_id: str) -> TaskStateModel:
        session = await _get_session(session_id)
        task = await session.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return TaskStateModel.from_state(task)

    @app.delete("/tasks/{task_id}")
    async def delete_task(task_id: str, session_id: str) -> Mapping[str, Any]:
        session = await _get_session(session_id)
        accepted = await session.cancel_task(task_id, reason="api_cancel")
        if not accepted:
            raise HTTPException(status_code=404, detail="Task not found")
        return {"ok": True, "task_id": task_id}

    @app.post("/tasks", response_model=TaskSpawnResponse)
    async def spawn_task(request: TaskSpawnRequest) -> TaskSpawnResponse:
        if planner_factory is None:
            raise HTTPException(status_code=501, detail="Background tasks require a planner factory")
        session = await _get_session(request.session_id)
        session.update_context(
            llm_context=dict(request.llm_context or {}),
            tool_context=dict(request.tool_context or {}),
        )
        task_id = secrets.token_hex(8)
        snapshot = TaskContextSnapshot(
            session_id=request.session_id,
            task_id=task_id,
            query=request.query,
            spawn_reason=request.spawn_reason,
            llm_context=dict(request.llm_context or {}),
            tool_context=dict(request.tool_context or {}),
        )
        task_type = TaskType.BACKGROUND if request.task_type == "background" else TaskType.FOREGROUND
        pipeline = PlannerTaskPipeline(planner_factory=planner_factory)
        merge_strategy = request.merge_strategy or (
            MergeStrategy.HUMAN_GATED if task_type == TaskType.BACKGROUND else MergeStrategy.APPEND
        )
        if request.wait or task_type == TaskType.FOREGROUND:
            try:
                result = await session.run_task(
                    pipeline,
                    task_type=task_type,
                    priority=request.priority,
                    context_snapshot=snapshot,
                    description=request.description,
                    query=request.query,
                    task_id=task_id,
                    merge_strategy=merge_strategy,
                    parent_task_id=request.parent_task_id,
                    spawned_from_event_id=request.spawned_from_event_id,
                )
            except RuntimeError as exc:
                raise HTTPException(status_code=429, detail=str(exc)) from exc
            task_state = await session.get_task(task_id)
            response_payload = {
                "answer": _normalise_answer(result.payload),
                "metadata": result.metadata,
            }
            return TaskSpawnResponse(
                task_id=task_id,
                session_id=request.session_id,
                status=TaskStatus.COMPLETE,
                trace_id=task_state.trace_id if task_state is not None else snapshot.trace_id,
                result=response_payload,
            )
        try:
            task_id = await session.spawn_task(
                pipeline,
                task_type=task_type,
                priority=request.priority,
                context_snapshot=snapshot,
                description=request.description,
                query=request.query,
                task_id=task_id,
                merge_strategy=merge_strategy,
                parent_task_id=request.parent_task_id,
                spawned_from_event_id=request.spawned_from_event_id,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return TaskSpawnResponse(
            task_id=task_id,
            session_id=request.session_id,
            status=TaskStatus.PENDING,
            trace_id=snapshot.trace_id,
        )

    if PenguiFlowAdapter is not None and create_agui_endpoint is not None and RunAgentInput is not None:
        agui_adapter = PenguiFlowAdapter(agent_wrapper, session_manager=session_manager)

        @app.post("/agui/agent")
        async def agui_agent(input: dict[str, Any], request: Request) -> StreamingResponse:
            if RunAgentInput is None:
                raise HTTPException(status_code=501, detail="AG-UI support requires ag-ui-protocol.")
            parsed = RunAgentInput(**dict(input))
            return await create_agui_endpoint(agui_adapter.run)(parsed, request)  # type: ignore[misc]

        @app.post("/agui/resume")
        async def agui_resume(input: AguiResumeRequest, request: Request) -> StreamingResponse:
            if EventEncoder is None:
                raise HTTPException(status_code=501, detail="AG-UI support requires ag-ui-protocol.")
            if not input.resume_token:
                raise HTTPException(status_code=400, detail="resume_token is required")

            user_input = _format_resume_input(input)
            if validate_interaction_result is not None and input.component:
                try:
                    validate_interaction_result(input.component, input.result)
                except RichOutputValidationError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc

            encoder = EventEncoder(accept=request.headers.get("accept", "text/event-stream"))

            async def stream():
                async for event in agui_adapter.resume(
                    resume_token=input.resume_token,
                    thread_id=input.thread_id,
                    run_id=input.run_id,
                    user_input=user_input,
                    tool_context=input.tool_context,
                ):
                    yield encoder.encode(event)

            return StreamingResponse(
                stream(),
                media_type=encoder.get_content_type(),
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

    else:  # pragma: no cover - optional dependency guard

        @app.post("/agui/agent")
        async def agui_agent_unavailable() -> None:
            raise HTTPException(
                status_code=501,
                detail="AG-UI support requires ag-ui-protocol; install penguiflow[cli].",
            )

    @app.get("/events")
    async def events(
        trace_id: str,
        session_id: str | None = None,
        follow: bool = False,
    ) -> StreamingResponse:
        if store is None:
            raise HTTPException(status_code=500, detail="State store is not configured")
        if session_id is not None:
            trajectory = await store.get_trajectory(trace_id, session_id)
            if trajectory is None:
                raise HTTPException(status_code=404, detail="Trace not found for session")

        queue: asyncio.Queue[bytes | object] | None = None
        unsubscribe: Callable[[], Any] | None = None
        if follow:
            queue, unsubscribe = await broker.subscribe(trace_id)

        stored_events = await store.list_planner_events(trace_id)
        session_payload = session_id or ""
        stored_frames: list[bytes] = []
        for event in stored_events:
            frame = _event_frame(event, trace_id, session_payload)
            if frame:
                stored_frames.append(frame)

        async def _event_stream() -> AsyncIterator[bytes]:
            try:
                yield format_sse(
                    "event",
                    {"event": "connected", "trace_id": trace_id, "session_id": session_payload},
                )
                for frame in stored_frames:
                    yield frame

                if not follow or queue is None:
                    return

                while True:
                    try:
                        # Use timeout to allow checking for cancellation periodically
                        item = await asyncio.wait_for(queue.get(), timeout=1.0)
                        if item is SSESentinel:
                            break
                        if isinstance(item, bytes):
                            yield item
                    except TimeoutError:
                        # Continue waiting - this allows cancellation to be processed
                        continue
            except asyncio.CancelledError:
                # Graceful shutdown - don't re-raise
                pass
            finally:
                if unsubscribe:
                    await unsubscribe()

        return StreamingResponse(
            _event_stream(),
            media_type="text/event-stream",
        )

    @app.get("/trajectory/{trace_id}")
    async def trajectory(trace_id: str, session_id: str) -> Mapping[str, Any]:
        if store is None:
            raise HTTPException(status_code=500, detail="State store is not configured")
        trajectory_record = await store.get_trajectory(trace_id, session_id)
        if trajectory_record is None:
            raise HTTPException(status_code=404, detail="Trajectory not found")
        payload = trajectory_record.serialise()
        payload["trace_id"] = trace_id
        payload["session_id"] = session_id
        return payload

    def _query_preview(value: str, *, limit: int = 160) -> str:
        """Build concise single-line query preview for trace tables."""

        collapsed = " ".join(value.split())
        if len(collapsed) <= limit:
            return collapsed
        return f"{collapsed[: limit - 1].rstrip()}…"

    @app.get("/traces", response_model=list[TraceSummaryPayload], response_model_exclude_none=True)
    async def list_traces(limit: int = 50) -> list[TraceSummaryPayload]:
        if store is None:
            raise HTTPException(status_code=500, detail="State store is not configured")
        list_trace_refs = getattr(store, "list_trace_refs", None)
        if list_trace_refs is None:
            raise HTTPException(status_code=501, detail="Trace listing is not supported")

        refs = await list_trace_refs(limit=limit)
        get_trajectory = getattr(store, "get_trajectory", None)

        session_turn_counts: dict[str, int] = {}
        turn_index_by_key: dict[tuple[str, str], int] = {}
        for ref in reversed(refs):
            trace_id = str(ref.get("trace_id") or "")
            session_id = str(ref.get("session_id") or "")
            if not trace_id or not session_id:
                continue
            current = session_turn_counts.get(session_id, 0) + 1
            session_turn_counts[session_id] = current
            turn_index_by_key[(trace_id, session_id)] = current

        payloads: list[TraceSummaryPayload] = []
        for ref in refs:
            trace_id = str(ref.get("trace_id") or "")
            session_id = str(ref.get("session_id") or "")
            if not trace_id or not session_id:
                continue

            tags: list[str] = []
            query_preview: str | None = None
            if get_trajectory is not None:
                trajectory = await get_trajectory(trace_id, session_id)
                if trajectory is not None:
                    tags_raw = trajectory.metadata.get("tags", [])
                    if isinstance(tags_raw, list):
                        tags = sorted({str(tag).strip() for tag in tags_raw if str(tag).strip()})
                    query_preview = _query_preview(trajectory.query)

            payloads.append(
                TraceSummaryPayload(
                    trace_id=trace_id,
                    session_id=session_id,
                    tags=tags,
                    query_preview=query_preview,
                    turn_index=turn_index_by_key.get((trace_id, session_id)),
                )
            )
        return payloads

    @app.post("/traces/{trace_id}/tags", response_model=TraceSummaryPayload, response_model_exclude_none=True)
    async def set_trace_tags(trace_id: str, request: TraceTagsRequest) -> TraceSummaryPayload:
        if store is None:
            raise HTTPException(status_code=500, detail="State store is not configured")
        list_trace_refs = getattr(store, "list_trace_refs", None)
        if list_trace_refs is None:
            raise HTTPException(status_code=501, detail="Trace listing is not supported")

        refs = await list_trace_refs(limit=0)
        trace_ref = next((ref for ref in refs if ref.get("trace_id") == trace_id), None)
        if trace_ref is None:
            raise HTTPException(status_code=404, detail="Trace not found for tagging")

        trace_session_id = str(trace_ref.get("session_id") or "")
        if trace_session_id != request.session_id:
            raise HTTPException(status_code=404, detail="Trace not found for tagging")

        get_trajectory = getattr(store, "get_trajectory", None)
        save_trajectory = getattr(store, "save_trajectory", None)
        if get_trajectory is None or save_trajectory is None:
            raise HTTPException(status_code=501, detail="Trajectory tagging is not supported")

        trajectory = await get_trajectory(trace_id, request.session_id)
        if trajectory is None:
            raise HTTPException(status_code=404, detail="Trajectory not found for tagging")

        existing_raw = trajectory.metadata.get("tags", [])
        tags = (
            {str(tag).strip() for tag in existing_raw if str(tag).strip()} if isinstance(existing_raw, list) else set()
        )
        tags.update(str(tag).strip() for tag in request.add if str(tag).strip())
        tags.difference_update(str(tag).strip() for tag in request.remove if str(tag).strip())
        normalized_tags = sorted(tags)

        trajectory.metadata["tags"] = normalized_tags
        await save_trajectory(trace_id, request.session_id, trajectory)
        return TraceSummaryPayload(
            trace_id=trace_id,
            session_id=request.session_id,
            tags=normalized_tags,
            query_preview=_query_preview(trajectory.query),
        )

    @app.get("/eval/datasets/browse", response_model=list[EvalDatasetBrowseEntry])
    async def browse_eval_datasets() -> list[EvalDatasetBrowseEntry]:
        evals_root = (
            (resolved_project_root / Path(agent_package) / "evals").resolve()
            if agent_package
            else (resolved_project_root / "evals").resolve()
        )
        if evals_root != resolved_project_root and resolved_project_root not in evals_root.parents:
            return []
        if not evals_root.exists() or not evals_root.is_dir():
            return []

        entries: list[EvalDatasetBrowseEntry] = []
        for dataset_path in sorted(evals_root.rglob("*.jsonl")):
            if not dataset_path.is_file():
                continue
            try:
                rel_project = dataset_path.relative_to(resolved_project_root)
                rel_label = dataset_path.relative_to(evals_root)
            except ValueError:
                continue
            entries.append(
                EvalDatasetBrowseEntry(
                    path=rel_project.as_posix(),
                    label=rel_label.as_posix(),
                    is_default=dataset_path.name == "dataset.jsonl",
                )
            )
        entries.sort(key=lambda entry: (entry.label, entry.path))
        return entries

    @app.get("/eval/metrics/browse", response_model=list[EvalMetricBrowseEntry])
    async def browse_eval_metrics() -> list[EvalMetricBrowseEntry]:
        evals_root = (
            (resolved_project_root / Path(agent_package) / "evals").resolve()
            if agent_package
            else (resolved_project_root / "evals").resolve()
        )
        if evals_root != resolved_project_root and resolved_project_root not in evals_root.parents:
            return []
        if not evals_root.exists() or not evals_root.is_dir():
            return []

        entries_by_metric: dict[str, EvalMetricBrowseEntry] = {}
        for spec_path in sorted(evals_root.rglob("evaluate.spec.json")):
            if not spec_path.is_file():
                continue
            try:
                raw = json.loads(spec_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            metric_spec = raw.get("metric_spec")
            if not isinstance(metric_spec, str) or ":" not in metric_spec:
                continue
            try:
                rel_spec = spec_path.relative_to(resolved_project_root)
            except ValueError:
                continue
            metric_label = metric_spec.split(":")[-1]
            if metric_spec in entries_by_metric:
                continue
            entries_by_metric[metric_spec] = EvalMetricBrowseEntry(
                metric_spec=metric_spec,
                label=metric_label,
                source_spec_path=rel_spec.as_posix(),
            )

        return sorted(entries_by_metric.values(), key=lambda entry: entry.label)

    @app.post("/eval/datasets/export", response_model=EvalDatasetExportResponse)
    async def export_eval_dataset_bundle(request: EvalDatasetExportRequest) -> EvalDatasetExportResponse:
        if store is None:
            raise HTTPException(status_code=500, detail="State store is not configured")

        output_dir = Path(request.output_dir)
        if not output_dir.is_absolute():
            output_dir = (resolved_project_root / output_dir).resolve()
        else:
            output_dir = output_dir.resolve()

        if output_dir != resolved_project_root and resolved_project_root not in output_dir.parents:
            raise HTTPException(status_code=400, detail="output_dir must be under project root")

        selector = request.selector or EvalDatasetSelectorPayload()
        export_result = await export_eval_dataset(
            state_store=store,
            output_dir=output_dir,
            selector=EvalTraceSelector(
                include_tags=tuple(selector.include_tags),
                exclude_tags=tuple(selector.exclude_tags),
                limit=selector.limit,
            ),
            redaction_profile=request.redaction_profile,
        )
        return EvalDatasetExportResponse(
            trace_count=int(export_result["trace_count"]),
            dataset_path=str(export_result["dataset_path"]),
            manifest_path=str(export_result["manifest_path"]),
        )

    @app.post("/eval/datasets/load", response_model=EvalDatasetLoadResponse)
    async def load_eval_dataset(request: EvalDatasetLoadRequest) -> EvalDatasetLoadResponse:
        raw_path = Path(request.dataset_path)
        if not raw_path.is_absolute():
            raw_path = (resolved_project_root / raw_path).resolve()
        else:
            raw_path = raw_path.resolve()

        if raw_path != resolved_project_root and resolved_project_root not in raw_path.parents:
            raise HTTPException(status_code=400, detail="dataset_path must be under project root")

        if raw_path.is_dir():
            dataset_path = raw_path / "dataset.jsonl"
            manifest_path = raw_path / "manifest.json"
        else:
            dataset_path = raw_path
            manifest_path = raw_path.parent / "manifest.json"

        if not dataset_path.exists() or not dataset_path.is_file():
            raise HTTPException(status_code=404, detail="dataset.jsonl not found")

        rows: list[dict[str, Any]] = []
        counts_by_split: dict[str, int] = {}
        with dataset_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                payload = json.loads(stripped)
                if not isinstance(payload, dict):
                    continue
                rows.append(payload)
                split = str(payload.get("split") or "unknown")
                counts_by_split[split] = counts_by_split.get(split, 0) + 1

        examples = [
            EvalDatasetExamplePayload(
                example_id=str(row.get("example_id") or ""),
                split=str(row.get("split") or "unknown"),
                question=str(row.get("question") or ""),
            )
            for row in rows
        ]

        resolved_manifest: str | None = str(manifest_path) if manifest_path.exists() else None
        return EvalDatasetLoadResponse(
            dataset_path=str(dataset_path),
            manifest_path=resolved_manifest,
            counts={"total": len(rows), "by_split": counts_by_split},
            examples=examples,
        )

    @app.post("/eval/run", response_model=EvalRunResponse)
    async def run_eval(request: EvalRunRequest) -> EvalRunResponse:
        raw_path = Path(request.dataset_path)
        if not raw_path.is_absolute():
            raw_path = (resolved_project_root / raw_path).resolve()
        else:
            raw_path = raw_path.resolve()

        if raw_path != resolved_project_root and resolved_project_root not in raw_path.parents:
            raise HTTPException(status_code=400, detail="dataset_path must be under project root")

        dataset_path = (raw_path / "dataset.jsonl") if raw_path.is_dir() else raw_path
        if not dataset_path.exists() or not dataset_path.is_file():
            raise HTTPException(status_code=404, detail="dataset.jsonl not found")

        ensure_project_on_sys_path(resolved_project_root)
        metric = wrap_metric(resolve_callable(request.metric_spec))

        rows: list[dict[str, Any]] = []
        for line in dataset_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            if isinstance(payload, dict):
                rows.append(payload)

        effective_max_cases = EVAL_RUN_HARD_MAX_CASES
        if request.max_cases is not None:
            effective_max_cases = min(request.max_cases, EVAL_RUN_HARD_MAX_CASES)
        rows = rows[:effective_max_cases]

        run_id = secrets.token_hex(8)
        pred_session_id = f"eval:{run_id}"
        counts = {"total": len(rows), "val": 0, "test": 0}
        test_scores: list[float] = []
        cases: list[EvalRunCasePayload] = []

        save_trajectory = getattr(store, "save_trajectory", None) if store is not None else None
        get_trajectory = getattr(store, "get_trajectory", None) if store is not None else None
        wait_for_trace_persistence = getattr(agent_wrapper, "wait_for_trace_persistence", None)

        for row in rows:
            split = str(row.get("split") or "unknown")
            if split == "val":
                counts["val"] += 1
            elif split == "test":
                counts["test"] += 1

            question = str(row.get("question") or "")
            gold_trace = row.get("gold_trace")
            inputs = gold_trace.get("inputs") if isinstance(gold_trace, Mapping) else {}
            llm_context = inputs.get("llm_context") if isinstance(inputs, Mapping) else {}
            tool_context = inputs.get("tool_context") if isinstance(inputs, Mapping) else {}
            llm_context_dict = dict(llm_context) if isinstance(llm_context, Mapping) else {}
            tool_context_dict = dict(tool_context) if isinstance(tool_context, Mapping) else {}

            chat_result = await agent_wrapper.chat(
                question,
                session_id=pred_session_id,
                llm_context=llm_context_dict,
                tool_context=tool_context_dict,
            )

            if callable(wait_for_trace_persistence):
                try:
                    maybe_wait = wait_for_trace_persistence(
                        chat_result.trace_id,
                        pred_session_id,
                        timeout_s=1.0,
                    )
                    if inspect.isawaitable(maybe_wait):
                        await maybe_wait
                except TimeoutError:
                    _LOGGER.info(
                        "eval_run_trace_persistence_wait_timeout",
                        extra={
                            "trace_id": chat_result.trace_id,
                            "session_id": pred_session_id,
                        },
                    )

            pred_trace_payload: dict[str, Any] | None = None
            pred_record = (
                await get_trajectory(chat_result.trace_id, pred_session_id) if get_trajectory is not None else None
            )
            if pred_record is None and save_trajectory is not None:
                trajectory = Trajectory(query=question, llm_context=llm_context_dict, tool_context=tool_context_dict)
                trajectory.metadata["answer"] = chat_result.answer
                await save_trajectory(chat_result.trace_id, pred_session_id, trajectory)
                pred_record = trajectory
            if pred_record is not None and hasattr(pred_record, "serialise"):
                pred_trace_payload = dict(pred_record.serialise())
                pred_trace_payload["trace_id"] = chat_result.trace_id
                pred_trace_payload["session_id"] = pred_session_id
            pred_steps = pred_trace_payload.get("steps") if isinstance(pred_trace_payload, Mapping) else None
            pred_step_count = len(pred_steps) if isinstance(pred_steps, list) else 0

            _LOGGER.info(
                "eval_run_metric_debug",
                extra={
                    "example_id": str(row.get("example_id") or ""),
                    "metric_spec": request.metric_spec,
                    "pred_trace_id": chat_result.trace_id,
                    "pred_session_id": pred_session_id,
                    "pred_trace_has_steps": bool(pred_step_count > 0),
                    "pred_trace_step_count": pred_step_count,
                },
            )
            _LOGGER.info(
                "eval_run_metric_debug_details example_id=%s metric_spec=%s pred_trace_id=%s pred_session_id=%s pred_trace_step_count=%d",
                str(row.get("example_id") or ""),
                request.metric_spec,
                chat_result.trace_id,
                pred_session_id,
                pred_step_count,
            )

            raw_score = metric(
                row,
                chat_result.answer,
                gold_trace,
                "baseline",
                pred_trace_payload,
            )
            score = 0.0
            feedback: str | None = None
            if isinstance(raw_score, dict):
                score_value = raw_score.get("score", 0.0)
                if isinstance(score_value, (int, float)) and not isinstance(score_value, bool):
                    score = float(score_value)
                elif isinstance(score_value, bool):
                    score = 1.0 if score_value else 0.0
                feedback_raw = raw_score.get("feedback")
                feedback = str(feedback_raw) if feedback_raw is not None else None
            elif isinstance(raw_score, bool):
                score = 1.0 if raw_score else 0.0
            elif isinstance(raw_score, (int, float)):
                score = float(raw_score)

            if split == "test":
                test_scores.append(score)

            cases.append(
                EvalRunCasePayload(
                    example_id=str(row.get("example_id") or ""),
                    split=split,
                    score=score,
                    feedback=feedback,
                    pred_trace_id=chat_result.trace_id,
                    pred_session_id=pred_session_id,
                    question=question,
                )
            )

        cases.sort(key=lambda item: item.score)

        test_score = (sum(test_scores) / len(test_scores)) if test_scores else None
        passed_threshold = True
        if request.min_test_score is not None and test_score is not None:
            passed_threshold = test_score >= request.min_test_score

        return EvalRunResponse(
            run_id=run_id,
            counts=counts,
            min_test_score=request.min_test_score,
            passed_threshold=passed_threshold,
            cases=cases,
        )

    @app.post("/eval/cases/compare", response_model=EvalCaseComparisonResponse)
    async def compare_eval_case(request: EvalCaseComparisonRequest) -> EvalCaseComparisonResponse:
        raw_path = Path(request.dataset_path)
        if not raw_path.is_absolute():
            raw_path = (resolved_project_root / raw_path).resolve()
        else:
            raw_path = raw_path.resolve()

        if raw_path != resolved_project_root and resolved_project_root not in raw_path.parents:
            raise HTTPException(status_code=400, detail="dataset_path must be under project root")

        dataset_path = (raw_path / "dataset.jsonl") if raw_path.is_dir() else raw_path
        if not dataset_path.exists() or not dataset_path.is_file():
            raise HTTPException(status_code=404, detail="dataset.jsonl not found")

        selected_row: dict[str, Any] | None = None
        for line in dataset_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            if not isinstance(payload, dict):
                continue
            if str(payload.get("example_id") or "") == request.example_id:
                selected_row = payload
                break

        if selected_row is None:
            raise HTTPException(status_code=404, detail="Eval example not found")

        gold_trace_raw = selected_row.get("gold_trace") if isinstance(selected_row.get("gold_trace"), Mapping) else {}
        gold_trace = dict(gold_trace_raw) if isinstance(gold_trace_raw, Mapping) else {}
        gold_trajectory_raw = (
            gold_trace.get("trajectory_full") if isinstance(gold_trace.get("trajectory_full"), Mapping) else None
        )
        gold_trajectory = dict(gold_trajectory_raw) if isinstance(gold_trajectory_raw, Mapping) else None
        gold_trace_id = str(gold_trace.get("trace_id") or "") or None

        if store is None:
            raise HTTPException(status_code=500, detail="State store is not configured")
        get_trajectory = getattr(store, "get_trajectory", None)
        if get_trajectory is None:
            raise HTTPException(status_code=501, detail="Trajectory retrieval is not supported")

        pred_record = await get_trajectory(request.pred_trace_id, request.pred_session_id)
        pred_trajectory: dict[str, Any] | None = None
        if pred_record is not None and hasattr(pred_record, "serialise"):
            pred_trajectory = dict(pred_record.serialise())
            pred_trajectory["trace_id"] = request.pred_trace_id
            pred_trajectory["session_id"] = request.pred_session_id

        return EvalCaseComparisonResponse(
            example_id=request.example_id,
            pred_trace_id=request.pred_trace_id,
            pred_session_id=request.pred_session_id,
            gold_trace_id=gold_trace_id,
            gold_trajectory=gold_trajectory,
            pred_trajectory=pred_trajectory,
        )

    # ─── Artifact Endpoints ───────────────────────────────────────────────────

    @app.get("/artifacts")
    async def list_artifacts(
        session_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        x_session_id: str | None = Header(None, alias="X-Session-ID"),
    ) -> list[Mapping[str, Any]]:
        """List artifacts for a session (best-effort hydration)."""
        artifact_store = _discover_artifact_store()
        if artifact_store is None:
            return []

        resolved_session = session_id or x_session_id
        if resolved_session is None:
            return []

        from penguiflow.artifacts import ArtifactScope

        scope = ArtifactScope(
            session_id=resolved_session,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        try:
            refs = await artifact_store.list(scope=scope)
        except Exception:
            _LOGGER.warning("Failed to list artifacts for session %s", resolved_session, exc_info=True)
            return []
        return [ref.model_dump(exclude={"scope"}) for ref in refs]

    @app.get("/artifacts/{artifact_id}")
    async def get_artifact(
        artifact_id: str,
        session_id: str | None = None,
        x_session_id: str | None = Header(None, alias="X-Session-ID"),
    ) -> Response:
        """Download artifact binary content.

        Session ID can be provided as query param or X-Session-ID header.
        If no session ID provided, returns artifact without session validation.
        """
        artifact_store = _discover_artifact_store()
        if artifact_store is None:
            raise HTTPException(status_code=501, detail="Artifact storage not enabled for this agent")

        # Resolve session ID from query param or header
        resolved_session = session_id or x_session_id

        # Get artifact with session validation if session provided
        if resolved_session is not None:
            # Use session-aware retrieval for access control
            if hasattr(artifact_store, "get_with_session_check"):
                data = await artifact_store.get_with_session_check(artifact_id, resolved_session)
                if data is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Artifact not found or access denied",
                    )
            else:
                ref = await artifact_store.get_ref(artifact_id) if hasattr(artifact_store, "get_ref") else None
                if ref is None:
                    raise HTTPException(status_code=404, detail="Artifact not found")
                stored_session = getattr(getattr(ref, "scope", None), "session_id", None)
                if stored_session is not None and stored_session != resolved_session:
                    raise HTTPException(status_code=404, detail="Artifact not found or access denied")
                data = await artifact_store.get(artifact_id)
        else:
            # No session validation - allow access (for backward compatibility)
            data = await artifact_store.get(artifact_id)

        if data is None:
            raise HTTPException(status_code=404, detail="Artifact not found")

        # Get metadata for content-type
        ref = None
        if hasattr(artifact_store, "get_ref"):
            ref = await artifact_store.get_ref(artifact_id)

        mime_type = ref.mime_type if ref and ref.mime_type else "application/octet-stream"
        filename = ref.filename if ref and ref.filename else artifact_id

        return Response(
            content=data,
            media_type=mime_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(data)),
            },
        )

    @app.get("/artifacts/{artifact_id}/meta")
    async def get_artifact_meta(
        artifact_id: str,
        session_id: str | None = None,
        x_session_id: str | None = Header(None, alias="X-Session-ID"),
    ) -> Mapping[str, Any]:
        """Get artifact metadata without downloading content."""
        artifact_store = _discover_artifact_store()
        if artifact_store is None:
            raise HTTPException(status_code=501, detail="Artifact storage not enabled for this agent")

        # Resolve session ID
        resolved_session = session_id or x_session_id

        # Check existence with session validation if provided
        if resolved_session is not None and hasattr(artifact_store, "get_with_session_check"):
            data = await artifact_store.get_with_session_check(artifact_id, resolved_session)
            if data is None:
                raise HTTPException(
                    status_code=404,
                    detail="Artifact not found or access denied",
                )
        elif resolved_session is not None:
            ref = await artifact_store.get_ref(artifact_id) if hasattr(artifact_store, "get_ref") else None
            if ref is None:
                raise HTTPException(status_code=404, detail="Artifact not found")
            stored_session = getattr(getattr(ref, "scope", None), "session_id", None)
            if stored_session is not None and stored_session != resolved_session:
                raise HTTPException(status_code=404, detail="Artifact not found or access denied")

        # Get metadata
        if not hasattr(artifact_store, "get_ref"):
            raise HTTPException(
                status_code=500,
                detail="Artifact store does not support metadata retrieval",
            )

        ref = await artifact_store.get_ref(artifact_id)
        if ref is None:
            raise HTTPException(status_code=404, detail="Artifact not found")

        return ref.model_dump()

    # ─── Resource Endpoints ───────────────────────────────────────────────────

    @app.get("/resources/{namespace}")
    async def list_resources(
        namespace: str,
        session_id: str | None = None,
        x_session_id: str | None = Header(None, alias="X-Session-ID"),
    ) -> Mapping[str, Any]:
        """List available MCP resources for a ToolNode namespace."""
        try:
            tool_node = _resolve_tool_node(namespace, session_id=session_id or x_session_id)
        except HTTPException as exc:
            if exc.status_code == 500:
                return {"resources": [], "templates": [], "error": exc.detail}
            raise

        if not getattr(tool_node, "resources_supported", False):
            return {
                "resources": [],
                "templates": [],
                "supported": False,
            }

        resources = getattr(tool_node, "resources", [])
        templates = getattr(tool_node, "resource_templates", [])

        return {
            "resources": [r.model_dump() if hasattr(r, "model_dump") else r for r in resources],
            "templates": [t.model_dump() if hasattr(t, "model_dump") else t for t in templates],
            "supported": True,
        }

    @app.get("/resources/{namespace}/{uri:path}")
    async def read_resource(
        namespace: str,
        uri: str,
        session_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        x_session_id: str | None = Header(None, alias="X-Session-ID"),
    ) -> Mapping[str, Any]:
        """Read a resource by URI from an MCP server.

        The resource content is cached and stored as an artifact.
        """
        resolved_session_id = session_id or x_session_id
        tool_node = _resolve_tool_node(namespace, session_id=resolved_session_id)

        if not getattr(tool_node, "resources_supported", False):
            raise HTTPException(
                status_code=400,
                detail=f"Tool node '{namespace}' does not support resources",
            )

        resolved_session, scoped_store = _build_scoped_store(
            resolved_session_id,
            tenant_id,
            user_id,
        )
        ctx = _build_minimal_ctx(
            scoped_store,
            session_id=resolved_session,
            tenant_id=tenant_id,
            user_id=user_id,
        )

        try:
            result = await tool_node.read_resource(uri, ctx)
            return result
        except Exception as exc:
            _LOGGER.warning(f"Resource read failed for {uri}: {exc}")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # ── MCP Apps: raw MCP proxy endpoints ─────────────────────────────────────

    @app.post("/apps/{namespace}/call-tool")
    async def app_call_tool(
        namespace: str,
        request: Request,
        session_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        x_session_id: str | None = Header(None, alias="X-Session-ID"),
    ) -> Mapping[str, Any]:
        """Proxy raw MCP tools/call for embedded MCP Apps."""
        body = await request.json()
        tool_name = body.get("name")
        tool_args = body.get("arguments", {})

        if not tool_name:
            raise HTTPException(status_code=400, detail="Missing 'name' in request body")
        resolved_session_id = session_id or x_session_id
        tool_node = _resolve_tool_node(namespace, session_id=resolved_session_id)
        original_name = _resolve_original_tool_name(namespace, str(tool_name))

        try:
            mcp_client = await _ensure_live_mcp_client(tool_node)
            call_tool_mcp = getattr(mcp_client, "call_tool_mcp", None)
            if callable(call_tool_mcp) and inspect.iscoroutinefunction(call_tool_mcp):
                result = await call_tool_mcp(original_name, dict(tool_args))
                return _jsonify_for_api(result)

            resolved_session, scoped_store = _build_scoped_store(
                resolved_session_id,
                tenant_id,
                user_id,
            )
            ctx = _build_minimal_ctx(
                scoped_store,
                session_id=resolved_session,
                tenant_id=tenant_id,
                user_id=user_id,
            )
            result = await tool_node.call(f"{namespace}.{original_name}", dict(tool_args), ctx)
            if isinstance(result, Mapping):
                return result
            return {"result": result}
        except Exception as exc:
            _LOGGER.warning(f"App tool call failed for {namespace}.{original_name}: {exc}")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/apps/{namespace}/tools")
    async def app_list_tools(
        namespace: str,
        session_id: str | None = None,
        x_session_id: str | None = Header(None, alias="X-Session-ID"),
    ) -> Mapping[str, Any]:
        """Proxy raw MCP tools/list for embedded MCP Apps."""
        tool_node = _resolve_tool_node(namespace, session_id=session_id or x_session_id)

        try:
            mcp_client = await _ensure_live_mcp_client(tool_node)
            list_tools_fn = getattr(mcp_client, "list_tools", None)
            if not callable(list_tools_fn) or not inspect.iscoroutinefunction(list_tools_fn):
                raise HTTPException(status_code=400, detail=f"Tool node '{namespace}' does not support MCP tools")
            tools = await list_tools_fn()
            return {"tools": _jsonify_for_api(tools)}
        except HTTPException:
            raise
        except Exception as exc:
            _LOGGER.warning(f"App tools/list failed for {namespace}: {exc}")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/apps/{namespace}/resources")
    async def app_list_resources(
        namespace: str,
        session_id: str | None = None,
        x_session_id: str | None = Header(None, alias="X-Session-ID"),
    ) -> Mapping[str, Any]:
        """Proxy raw MCP resources/list and resources/templates/list for embedded MCP Apps."""
        tool_node = _resolve_tool_node(namespace, session_id=session_id or x_session_id)

        if not getattr(tool_node, "resources_supported", False):
            return {"resources": [], "resourceTemplates": []}

        try:
            mcp_client = await _ensure_live_mcp_client(tool_node)
            if mcp_client is None:
                raise HTTPException(status_code=400, detail=f"Tool node '{namespace}' is not connected")
            list_resources_fn = getattr(mcp_client, "list_resources", None)
            list_templates_fn = getattr(mcp_client, "list_resource_templates", None)
            resources = (
                await list_resources_fn()
                if callable(list_resources_fn) and inspect.iscoroutinefunction(list_resources_fn)
                else []
            )
            templates = (
                await list_templates_fn()
                if callable(list_templates_fn) and inspect.iscoroutinefunction(list_templates_fn)
                else []
            )
            return {
                "resources": _jsonify_for_api(resources),
                "resourceTemplates": _jsonify_for_api(templates),
            }
        except HTTPException:
            raise
        except Exception as exc:
            _LOGGER.warning(f"App resources/list failed for {namespace}: {exc}")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/apps/{namespace}/read-resource")
    async def app_read_resource(
        namespace: str,
        request: Request,
        session_id: str | None = None,
        x_session_id: str | None = Header(None, alias="X-Session-ID"),
    ) -> Mapping[str, Any]:
        """Proxy raw MCP resources/read for embedded MCP Apps."""
        body = await request.json()
        uri = body.get("uri")
        if not uri:
            raise HTTPException(status_code=400, detail="Missing 'uri' in request body")

        tool_node = _resolve_tool_node(namespace, session_id=session_id or x_session_id)
        if not getattr(tool_node, "resources_supported", False):
            raise HTTPException(status_code=400, detail=f"Tool node '{namespace}' does not support resources")

        try:
            mcp_client = await _ensure_live_mcp_client(tool_node)
            read_resource_fn = getattr(mcp_client, "read_resource", None)
            if not callable(read_resource_fn) or not inspect.iscoroutinefunction(read_resource_fn):
                raise HTTPException(status_code=400, detail=f"Tool node '{namespace}' is not connected")
            contents = await read_resource_fn(str(uri))
            payload = _jsonify_for_api(contents)
            if isinstance(payload, Mapping) and "contents" in payload:
                return payload
            return {"contents": payload}
        except HTTPException:
            raise
        except Exception as exc:
            _LOGGER.warning(f"App resources/read failed for {namespace} {uri}: {exc}")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    if discovery:
        app.state.discovery = discovery
    app.state.agent_wrapper = agent_wrapper
    app.state.state_store = store
    app.state.broker = broker
    return app


__all__ = [
    "ChatRequest",
    "ChatResponse",
    "DiscoveryResult",
    "InMemoryStateStore",
    "PlaygroundError",
    "PlaygroundStateStore",
    "TaskStateResponse",
    "create_playground_app",
    "discover_agent",
    "load_agent",
]
