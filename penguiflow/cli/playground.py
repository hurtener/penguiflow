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
from penguiflow.planner import PlannerEvent

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
    RichOutputValidationError = None  # type: ignore[assignment,misc]
    validate_interaction_result = None  # type: ignore[assignment]

from .playground_sse import EventBroker, SSESentinel, format_sse, stream_queue
from .playground_state import InMemoryStateStore, PlaygroundStateStore
from .playground_wrapper import (
    AgentWrapper,
    ChatResult,
    OrchestratorAgentWrapper,
    PlannerAgentWrapper,
)

_LOGGER = logging.getLogger(__name__)


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
                "name": "lighthouse",
                "enabled": spec.services.lighthouse.enabled,
                "url": spec.services.lighthouse.base_url,
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


def _event_frame(event: PlannerEvent, trace_id: str | None, session_id: str) -> bytes | None:
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
        payload.update(
            {
                "text": extra.get("text", ""),
                "done": extra.get("done", False),
                "phase": phase_llm,
                "channel": channel_llm,
            }
        )
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


def discover_agent(project_root: Path | None = None) -> DiscoveryResult:
    """Locate an agent entry point within the provided project directory."""

    base_dir = Path(project_root or Path.cwd()).resolve()
    _ensure_sys_path(base_dir)
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


def _instantiate_orchestrator(cls: type[Any], config: Any | None) -> Any:
    signature = inspect.signature(cls)
    params = [param for name, param in signature.parameters.items() if name != "self"]
    if not params:
        return cls()
    first = params[0]
    if config is None and first.default is inspect._empty:
        raise PlaygroundError(f"Orchestrator {cls.__name__} requires a config")
    try:
        return cls(config) if config is not None else cls()
    except TypeError as exc:
        raise PlaygroundError(f"Failed to instantiate orchestrator {cls.__name__}: {exc}") from exc


def _call_builder(
    builder: Callable[..., Any],
    config: Any | None,
) -> Any:
    kwargs: dict[str, Any] = {}
    try:
        signature = inspect.signature(builder)
        if "event_callback" in signature.parameters:
            kwargs["event_callback"] = None
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
) -> tuple[AgentWrapper, DiscoveryResult]:
    """Discover and wrap the first available agent entry point."""

    result = discover_agent(project_root)
    config = result.config_factory() if result.config_factory else None
    state_store = state_store or InMemoryStateStore()

    if result.kind == "orchestrator":
        orchestrator = _instantiate_orchestrator(result.target, config)
        wrapper: AgentWrapper = OrchestratorAgentWrapper(
            orchestrator,
            state_store=state_store,
        )
    else:
        builder_output = _call_builder(result.target, config)
        planner = _unwrap_planner(builder_output)
        wrapper = PlannerAgentWrapper(
            planner,
            state_store=state_store,
        )

    return wrapper, result


def create_playground_app(
    project_root: Path | None = None,
    *,
    agent: AgentWrapper | None = None,
    state_store: PlaygroundStateStore | None = None,
) -> FastAPI:
    """Create the FastAPI playground app."""

    discovery: DiscoveryResult | None = None
    agent_wrapper = agent
    store = state_store
    broker = EventBroker()
    ui_dir = Path(__file__).resolve().parent / "playground_ui" / "dist"
    spec_payload, parsed_spec = _load_spec_payload(Path(project_root or ".").resolve())
    meta_payload = _meta_from_spec(parsed_spec)

    if agent_wrapper is None:
        store = state_store or InMemoryStateStore()
        agent_wrapper, discovery = load_agent(project_root, state_store=store)
    else:
        if store is None:
            store = getattr(agent_wrapper, "_state_store", None) or InMemoryStateStore()

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

    def _discover_planner() -> Any | None:
        """Discover the underlying planner instance from the agent wrapper."""
        planner = getattr(agent_wrapper, "_planner", None)
        if planner is not None:
            return planner
        orchestrator = getattr(agent_wrapper, "_orchestrator", None)
        if orchestrator is not None:
            planner = getattr(orchestrator, "_planner", None)
            if planner is not None:
                return planner
        return None

    def _discover_artifact_store() -> Any | None:
        """Discover the artifact store from the running agent (no injection).

        Returns None if the agent has no artifact store configured or is using NoOp.
        """
        from penguiflow.artifacts import ArtifactStore, NoOpArtifactStore

        planner = _discover_planner()
        if planner is None:
            return None

        store = getattr(planner, "artifact_store", None)
        if store is None:
            store = getattr(planner, "_artifact_store", None)
        if store is None:
            return None
        if isinstance(store, NoOpArtifactStore):
            return None
        if not isinstance(store, ArtifactStore):
            return None
        return store

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

        def _event_consumer(event: PlannerEvent, trace_id: str | None) -> None:
            tid = trace_id or trace_holder["id"]
            if tid is None:
                return
            trace_holder["id"] = tid
            frame = _event_frame(event, tid, session_id)
            if frame:
                broker.publish(tid, frame)

        try:
            llm_context = _merge_contexts(dict(request.llm_context or {}), request.context)
            result: ChatResult = await agent_wrapper.chat(
                query=request.query,
                session_id=session_id,
                llm_context=llm_context,
                tool_context=dict(request.tool_context or {}),
                event_consumer=_event_consumer,
                trace_id_hint=trace_holder["id"],
            )
        except Exception as exc:
            _LOGGER.exception("playground_chat_failed", exc_info=exc)
            raise HTTPException(status_code=500, detail=f"Chat failed: {exc}") from exc

        trace_holder["id"] = result.trace_id
        broker.publish(result.trace_id, _done_frame(result, session_id))

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

        def _event_consumer(event: PlannerEvent, trace_id: str | None) -> None:
            tid = trace_id or trace_holder["id"]
            if tid is None:
                return
            trace_holder["id"] = tid
            frame = _event_frame(event, tid, session_value)
            if frame:
                try:
                    queue.put_nowait(frame)
                except asyncio.QueueFull:
                    pass
                broker.publish(tid, frame)

        async def _run_chat() -> None:
            try:
                result: ChatResult = await agent_wrapper.chat(
                    query=query,
                    session_id=session_value,
                    llm_context=llm_payload,
                    tool_context=tool_payload,
                    event_consumer=_event_consumer,
                    trace_id_hint=trace_holder["id"],
                )
                trace_holder["id"] = result.trace_id
                frame = _done_frame(result, session_value)
                broker.publish(result.trace_id, frame)
                await queue.put(frame)
            except Exception as exc:  # pragma: no cover - defensive
                await queue.put(_error_frame(str(exc), trace_id=trace_holder["id"], session_id=session_value))
            finally:
                await queue.put(SSESentinel)

        asyncio.create_task(_run_chat())
        return StreamingResponse(
            stream_queue(queue),
            media_type="text/event-stream",
        )

    if PenguiFlowAdapter is not None and create_agui_endpoint is not None and RunAgentInput is not None:
        agui_adapter = PenguiFlowAdapter(agent_wrapper)

        @app.post("/agui/agent")
        async def agui_agent(input: RunAgentInput, request: Request) -> StreamingResponse:
            return await create_agui_endpoint(agui_adapter.run)(input, request)  # type: ignore[misc]

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

        stored_events = await store.get_events(trace_id)
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

    # ─── Artifact Endpoints ───────────────────────────────────────────────────

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
    async def list_resources(namespace: str) -> Mapping[str, Any]:
        """List available MCP resources for a ToolNode namespace."""
        # Get tool node from agent wrapper
        tool_nodes = getattr(agent_wrapper, "_tool_nodes", None)
        if tool_nodes is None:
            # Try to find tool nodes from planner
            planner = getattr(agent_wrapper, "_planner", None)
            if planner is not None:
                tool_nodes = getattr(planner, "_tool_nodes", None)

        if tool_nodes is None:
            return {"resources": [], "templates": [], "error": "No tool nodes available"}

        # Find the tool node with matching namespace
        tool_node = None
        if isinstance(tool_nodes, dict):
            tool_node = tool_nodes.get(namespace)
        elif isinstance(tool_nodes, list):
            for tn in tool_nodes:
                if getattr(tn, "config", None) and getattr(tn.config, "name", None) == namespace:
                    tool_node = tn
                    break

        if tool_node is None:
            raise HTTPException(status_code=404, detail=f"Tool node '{namespace}' not found")

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
        x_session_id: str | None = Header(None, alias="X-Session-ID"),
    ) -> Mapping[str, Any]:
        """Read a resource by URI from an MCP server.

        The resource content is cached and stored as an artifact.
        """
        # Get tool node
        tool_nodes = getattr(agent_wrapper, "_tool_nodes", None)
        if tool_nodes is None:
            planner = getattr(agent_wrapper, "_planner", None)
            if planner is not None:
                tool_nodes = getattr(planner, "_tool_nodes", None)

        if tool_nodes is None:
            raise HTTPException(status_code=500, detail="No tool nodes available")

        # Find tool node
        tool_node = None
        if isinstance(tool_nodes, dict):
            tool_node = tool_nodes.get(namespace)
        elif isinstance(tool_nodes, list):
            for tn in tool_nodes:
                if getattr(tn, "config", None) and getattr(tn.config, "name", None) == namespace:
                    tool_node = tn
                    break

        if tool_node is None:
            raise HTTPException(status_code=404, detail=f"Tool node '{namespace}' not found")

        if not getattr(tool_node, "resources_supported", False):
            raise HTTPException(
                status_code=400,
                detail=f"Tool node '{namespace}' does not support resources",
            )

        # Create a minimal context for resource reading
        resolved_session = session_id or x_session_id or "default"
        artifact_store = _discover_artifact_store()
        scoped_store: Any
        if artifact_store is None:
            scoped_store = _DisabledArtifactStore()
        else:
            from penguiflow.artifacts import ArtifactScope

            scoped_store = _ScopedArtifactStore(
                artifact_store,
                ArtifactScope(session_id=resolved_session),
            )

        # Create a context-like object for the read operation
        class MinimalCtx:
            def __init__(self, artifacts: Any):
                self._artifacts = artifacts

            @property
            def artifacts(self) -> Any:
                return self._artifacts

        ctx = MinimalCtx(scoped_store)

        try:
            result = await tool_node.read_resource(uri, ctx)
            return result
        except Exception as exc:
            _LOGGER.warning(f"Resource read failed for {uri}: {exc}")
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
    "create_playground_app",
    "discover_agent",
    "load_agent",
]
