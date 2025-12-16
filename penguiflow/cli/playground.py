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

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from penguiflow.cli.generate import run_generate
from penguiflow.cli.spec import Spec, load_spec
from penguiflow.cli.spec_errors import SpecValidationError
from penguiflow.planner import PlannerEvent

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


def _call_builder(builder: Callable[..., Any], config: Any | None) -> Any:
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
        try:
            yield
        finally:  # pragma: no cover - exercised in integration
            try:
                await broker.close()
            finally:
                await agent_wrapper.shutdown()

    app = FastAPI(title="PenguiFlow Playground", version="0.1.0", lifespan=_lifespan)

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
