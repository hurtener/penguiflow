"""Public API helpers for import-path driven eval execution."""

from __future__ import annotations

import importlib
import inspect
import json
import secrets
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from penguiflow.planner import PlannerFinish, PlannerPause, Trajectory
from penguiflow.state import InMemoryStateStore

from .export import collect_trace_rows
from .inputs import load_query_suite

MetricFn = Callable[[object, object, object | None, str | None, object | None], float | dict[str, object]]
RunOneFn = Callable[[dict[str, Any], dict[str, Any] | None], Any]


@dataclass(frozen=True, slots=True)
class EvalDatasetSpec:
    """Declarative config for evaluation runs over an existing dataset bundle."""

    dataset_path: Path
    candidates_path: Path | None
    metric_spec: str
    output_dir: Path
    report_path: Path | None = None
    min_test_score: float | None = None
    project_root: Path | None = None
    env_files: tuple[Path, ...] = ()
    agent_package: str | None = None
    run_one_spec: str | None = None


@dataclass(frozen=True, slots=True)
class EvalCollectSpec:
    """Declarative config for collect->export workflows without evaluation.

    Collect-only runs are useful for metric development and debugging, where the
    primary goal is inspecting exported traces and contexts before defining a
    scoring function.
    """

    project_root: Path
    query_suite_path: Path
    output_dir: Path
    session_id: str
    dataset_tag: str
    env_files: tuple[Path, ...] = ()
    agent_package: str | None = None
    state_store_spec: str | None = None


@dataclass(frozen=True, slots=True)
class TraceSelector:
    """Selector for exporting traces from a StateStore.

    Why: API users should not manage trace-id lists manually. A selector keeps
    export intent compact (all traces by default, optional tag narrowing).
    """

    include_tags: tuple[str, ...] = ()
    exclude_tags: tuple[str, ...] = ()
    limit: int = 0


@dataclass(frozen=True, slots=True)
class QueryCase:
    """Single collection input used for trace generation.

    Why: query list collection should be deterministic and explicit without
    forcing users to write wrapper glue for each project.
    """

    query: str
    split: str | None = None
    tags: tuple[str, ...] = ()
    llm_context: Mapping[str, Any] | None = None
    tool_context: Mapping[str, Any] | None = None


@dataclass(slots=True)
class ChatResult:
    """Normalized chat response for eval-local runners.

    Why: eval APIs are stable library surface and cannot depend on CLI wrapper
    internals. This keeps the minimal response contract local to evals.
    """

    answer: str | None
    trace_id: str
    session_id: str
    metadata: dict[str, Any] | None = None
    pause: dict[str, Any] | None = None


def ensure_project_on_sys_path(project_root: str | Path) -> None:
    """Ensure project root is importable for consumer-owned metric/run hooks."""

    base = Path(project_root).resolve()
    candidate = base / "src"
    import_path = candidate if candidate.exists() else base
    path_str = str(import_path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


def resolve_callable(spec: str) -> Callable[..., Any]:
    """Resolve a callable from ``module:callable`` import path format."""

    module_name, _, attr_name = spec.partition(":")
    if not module_name or not attr_name:
        raise ValueError("import path must be in module:callable format")
    module = importlib.import_module(module_name)
    try:
        target = getattr(module, attr_name)
    except AttributeError as exc:
        raise ValueError(f"{spec!r} does not resolve to a callable") from exc
    if not callable(target):
        raise TypeError(f"{spec!r} resolved to {type(target)!r}, not a callable")
    return target


def wrap_metric(metric: Callable[..., Any]) -> MetricFn:
    """Adapt user metric callables to the harness metric signature."""

    signature = inspect.signature(metric)
    params = signature.parameters
    has_var_args = any(param.kind is inspect.Parameter.VAR_POSITIONAL for param in params.values())
    has_var_kwargs = any(param.kind is inspect.Parameter.VAR_KEYWORD for param in params.values())

    def _wrapped(
        gold: object,
        pred: object,
        trace: object | None = None,
        pred_name: str | None = None,
        pred_trace: object | None = None,
    ) -> float | dict[str, object]:
        values: dict[str, object | None] = {
            "gold": gold,
            "pred": pred,
            "trace": trace,
            "pred_name": pred_name,
            "pred_trace": pred_trace,
        }
        if has_var_args:
            raw = metric(gold, pred, trace, pred_name, pred_trace)
            return raw

        positional: list[object | None] = []
        kwargs: dict[str, object | None] = {}
        for param in params.values():
            if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
                if param.name in values:
                    positional.append(values[param.name])
                    continue
                if param.default is inspect.Parameter.empty:
                    raise TypeError(f"metric has unsupported required parameter {param.name!r}")
            elif param.kind is inspect.Parameter.KEYWORD_ONLY:
                if param.name in values:
                    kwargs[param.name] = values[param.name]
                    continue
                if param.default is inspect.Parameter.empty:
                    raise TypeError(f"metric has unsupported required parameter {param.name!r}")

        if has_var_kwargs:
            for key, value in values.items():
                kwargs.setdefault(key, value)

        raw = metric(*positional, **kwargs)
        return raw

    return _wrapped


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _selector_from_user(selector: TraceSelector | None) -> TraceSelector:
    if selector is None:
        return TraceSelector()
    return selector


def _matches_selector(tags: list[str], selector: TraceSelector) -> bool:
    # ALL-of include semantics keep selection deterministic and minimal.
    if selector.include_tags and not all(tag in tags for tag in selector.include_tags):
        return False
    if selector.exclude_tags and any(tag in tags for tag in selector.exclude_tags):
        return False
    return True


async def resolve_trace_refs(
    *,
    state_store: Any,
    selector: TraceSelector | None = None,
) -> list[dict[str, str]]:
    """Resolve trace refs globally, optionally narrowed by tags.

    Why: datasets often span multiple sessions, so selection defaults to global
    trace scope when the store supports ``list_trace_refs``.
    """

    selected = _selector_from_user(selector)
    list_trace_refs = getattr(state_store, "list_trace_refs", None)
    if list_trace_refs is None:
        raise TypeError("StateStore missing list_trace_refs required for global dataset export")

    refs = await _maybe_await(list_trace_refs(limit=selected.limit))
    if not isinstance(refs, list):
        raise TypeError("list_trace_refs must return a list")

    if not selected.include_tags and not selected.exclude_tags:
        return [dict(ref) for ref in refs if isinstance(ref, dict) and ref.get("trace_id")]

    filtered: list[dict[str, str]] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        trace_id = str(ref.get("trace_id") or "")
        session_id = str(ref.get("session_id") or "")
        if not trace_id or not session_id:
            continue
        trajectory = None
        get_trajectory = getattr(state_store, "get_trajectory", None)
        if get_trajectory is not None:
            trajectory = await _maybe_await(get_trajectory(trace_id, session_id))
        tags_raw = []
        if trajectory is not None and isinstance(getattr(trajectory, "metadata", None), dict):
            tags_raw = trajectory.metadata.get("tags", [])
        tags = [str(tag) for tag in tags_raw] if isinstance(tags_raw, list) else []
        if _matches_selector(tags, selected):
            filtered.append({"trace_id": trace_id, "session_id": session_id})
    return filtered


async def export_dataset(
    *,
    state_store: Any,
    output_dir: str | Path,
    selector: TraceSelector | None = None,
    redaction_profile: str = "internal_safe",
    workload: str | None = None,
) -> dict[str, Any]:
    """Export a portable dataset bundle from stored traces.

    Why: bundle export decouples capture from eval runs so users can run
    optimization repeatedly without keeping the original runtime session alive.
    """

    refs = await resolve_trace_refs(state_store=state_store, selector=selector)
    if not refs:
        raise ValueError("trace selector resolved no traces")

    collected = await collect_trace_rows(
        state_store=state_store,
        trace_ids=[str(ref["trace_id"]) for ref in refs],
        trace_refs=refs,
        redaction_profile=redaction_profile,
        workload=workload,
    )
    rows = list(collected["rows"])
    dataset_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        dataset_rows.append(
            {
                "example_id": str(row.get("trace_id") or f"trace-{idx + 1}"),
                "split": str(row.get("trajectory", {}).get("split") or "unknown"),
                "question": str(row.get("query") or ""),
                "gold_trace": row,
            }
        )
    dataset_path = Path(output_dir) / "dataset.jsonl"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    with dataset_path.open("w", encoding="utf-8") as handle:
        for row in dataset_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    manifest_path = Path(output_dir) / "manifest.json"
    manifest_path.write_text(
        json.dumps(collected["manifest"], ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    return {
        "trace_count": int(collected["trace_count"]),
        "dataset_path": str(dataset_path),
        "manifest_path": str(manifest_path),
    }


def _get_attr(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _normalize_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _extract_from_dict(data: Mapping[str, Any]) -> str | None:
    for key in ("raw_answer", "answer", "text", "content", "message", "greeting", "response", "result"):
        if key in data:
            value = data[key]
            return str(value) if value is not None else None
    return None


def _normalise_answer(payload: Any) -> str | None:
    if payload is None:
        return None
    if isinstance(payload, str):
        return payload
    if isinstance(payload, Mapping):
        if "branches" in payload and isinstance(payload["branches"], list):
            for branch in payload["branches"]:
                if isinstance(branch, Mapping) and "observation" in branch:
                    observation = branch["observation"]
                    if isinstance(observation, Mapping):
                        result = _extract_from_dict(observation)
                        if result is not None:
                            return result
                    elif observation is not None:
                        return str(observation)
        result = _extract_from_dict(payload)
        if result is not None:
            return result
    for attr in ("raw_answer", "answer", "text", "content", "message", "greeting", "response", "result"):
        if hasattr(payload, attr):
            value = getattr(payload, attr)
            return str(value) if value is not None else None
    return str(payload)


def _build_trajectory(
    query: str,
    session_id: str,
    trace_id: str,
    metadata: Mapping[str, Any] | None,
    llm_context: Mapping[str, Any] | None,
    tool_context: Mapping[str, Any] | None = None,
) -> Trajectory | None:
    if metadata is None:
        return None
    steps = metadata.get("steps")
    if not isinstance(steps, list):
        return None

    actual_llm_context = metadata.get("llm_context") or llm_context or {}
    actual_tool_context = metadata.get("tool_context") or tool_context or {}
    payload: dict[str, Any] = {
        "query": query,
        "llm_context": dict(actual_llm_context),
        "tool_context": {
            **(dict(actual_tool_context)),
            "session_id": session_id,
            "trace_id": trace_id,
        },
        "steps": steps,
        "hint_state": {},
    }
    trajectory_meta = metadata.get("trajectory_metadata")
    if isinstance(trajectory_meta, Mapping):
        payload["metadata"] = dict(trajectory_meta)
    if "artifacts" in metadata:
        payload["artifacts"] = metadata["artifacts"]
    if "sources" in metadata:
        payload["sources"] = metadata["sources"]
    if "summary" in metadata and metadata["summary"] is not None:
        payload["summary"] = metadata["summary"]
    return Trajectory.from_serialised(payload)


def _collect_tags(case: QueryCase) -> list[str]:
    tags = [str(tag) for tag in case.tags]
    if case.split:
        split_tag = f"split:{case.split}"
        if split_tag not in tags:
            tags.append(split_tag)
    return tags


def _merge_tag_lists(existing: list[str], incoming: list[str]) -> list[str]:
    merged: list[str] = []
    for tag in [*existing, *incoming]:
        if tag not in merged:
            merged.append(tag)
    return merged


def _instantiate_orchestrator(target: type[Any], config: Any | None, *, state_store: Any | None = None) -> Any:
    signature = inspect.signature(target)
    kwargs: dict[str, Any] = {}
    if state_store is not None and "state_store" in signature.parameters:
        kwargs["state_store"] = state_store
    params = [param for name, param in signature.parameters.items() if name != "self"]
    if not params:
        return target(**kwargs)
    first = params[0]
    if config is None and first.default is inspect._empty:
        raise TypeError(f"Orchestrator {target.__name__} requires config")
    if config is None:
        return target(**kwargs)
    return target(config, **kwargs)


def _call_builder(builder: Callable[..., Any], config: Any | None, *, state_store: Any | None = None) -> Any:
    signature = inspect.signature(builder)
    kwargs: dict[str, Any] = {}
    if state_store is not None and (
        "state_store" in signature.parameters
        or any(param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
    ):
        kwargs["state_store"] = state_store
    params = list(signature.parameters.values())
    if not params:
        return builder(**kwargs)
    first = params[0]
    if config is None and first.default is inspect._empty:
        raise TypeError("build_planner requires config")
    if config is None:
        return builder(**kwargs)
    return builder(config, **kwargs)


def _flatten_planner_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    planner = metadata.get("planner")
    if not isinstance(planner, Mapping):
        return metadata
    flattened = dict(metadata)
    for key in ("steps", "llm_context", "tool_context", "trajectory_metadata", "summary", "artifacts", "sources"):
        if key not in flattened and key in planner:
            flattened[key] = planner[key]
    return flattened


class _EvalOrchestratorWrapper:
    """Eval-local orchestrator wrapper to avoid touching Playground APIs.

    Why: Playground wrapper internals are moving; keeping eval-specific overrides
    in this module reduces merge conflicts while preserving compatibility.
    """

    def __init__(self, orchestrator: Any, *, state_store: Any | None = None) -> None:
        self._orchestrator = orchestrator
        self._state_store = state_store

    async def initialize(self) -> None:
        ensure_init = getattr(self._orchestrator, "_ensure_initialized", None)
        if callable(ensure_init):
            if inspect.iscoroutinefunction(ensure_init):
                await ensure_init()
            else:
                ensure_init()

    async def wait_for_trace_persistence(
        self,
        trace_id: str,
        session_id: str,
        *,
        timeout_s: float = 1.0,
    ) -> None:
        planner = getattr(self._orchestrator, "_planner", None)
        if planner is None:
            return
        await _await_trace_persistence(planner, trace_id, session_id, timeout_s=timeout_s)

    async def chat(
        self,
        query: str,
        *,
        session_id: str,
        llm_context: Mapping[str, Any] | None = None,
        tool_context: Mapping[str, Any] | None = None,
    ) -> ChatResult:
        execute = self._orchestrator.execute
        signature = inspect.signature(execute)
        kwargs: dict[str, Any] = {"query": query}
        eval_trace_id = secrets.token_hex(8)
        tool_ctx = {
            **dict(tool_context or {}),
            "session_id": session_id,
            "trace_id": eval_trace_id,
        }
        if "tenant_id" in signature.parameters:
            kwargs["tenant_id"] = str(tool_ctx.get("tenant_id") or "default")
        if "user_id" in signature.parameters:
            kwargs["user_id"] = str(tool_ctx.get("user_id") or "eval-user")
        if "session_id" in signature.parameters:
            kwargs["session_id"] = session_id
        if "tool_context" in signature.parameters:
            kwargs["tool_context"] = tool_ctx
        raw_memories = (llm_context or {}).get("memories") if isinstance(llm_context, Mapping) else None
        if "memories" in signature.parameters and isinstance(raw_memories, list):
            kwargs["memories"] = raw_memories

        response = await execute(**kwargs)
        metadata = _flatten_planner_metadata(_normalize_metadata(_get_attr(response, "metadata")))
        trace_id = str(metadata.get("trace_id") or _get_attr(response, "trace_id") or eval_trace_id)
        answer = _normalise_answer(_get_attr(response, "answer"))
        if answer is None:
            answer = _normalise_answer(_get_attr(response, "text"))

        planner = getattr(self._orchestrator, "_planner", None)
        if not _supports_trace_persistence_wait(planner):
            trajectory = _build_trajectory(query, session_id, trace_id, metadata, llm_context or {}, tool_ctx)
            if (
                trajectory is not None
                and self._state_store is not None
                and hasattr(self._state_store, "save_trajectory")
            ):
                await self._state_store.save_trajectory(trace_id, session_id, trajectory)

        return ChatResult(
            answer=answer,
            trace_id=trace_id,
            session_id=session_id,
            metadata=metadata,
        )


class _EvalPlannerWrapper:
    """Adapter for bare planners returned by build_planner()."""

    def __init__(self, planner: Any, *, state_store: Any | None = None) -> None:
        self._planner = planner
        self._state_store = state_store

    async def initialize(self) -> None:
        return None

    async def wait_for_trace_persistence(
        self,
        trace_id: str,
        session_id: str,
        *,
        timeout_s: float = 1.0,
    ) -> None:
        await _await_trace_persistence(self._planner, trace_id, session_id, timeout_s=timeout_s)

    async def chat(
        self,
        query: str,
        *,
        session_id: str,
        llm_context: Mapping[str, Any] | None = None,
        tool_context: Mapping[str, Any] | None = None,
    ) -> ChatResult:
        llm_context = dict(llm_context or {})
        trace_id = secrets.token_hex(8)
        merged_tool_context = {
            **dict(tool_context or {}),
            "session_id": session_id,
            "trace_id": trace_id,
        }
        result = await self._planner.run(
            query=query,
            llm_context=llm_context,
            tool_context=merged_tool_context,
        )

        if isinstance(result, PlannerPause):
            pause_payload = {
                "reason": result.reason,
                "payload": result.payload,
                "resume_token": result.resume_token,
            }
            return ChatResult(
                answer=None,
                trace_id=trace_id,
                session_id=session_id,
                metadata={"pause": pause_payload},
                pause=pause_payload,
            )

        payload = result.payload if isinstance(result, PlannerFinish) else result
        metadata = _normalize_metadata(getattr(result, "metadata", None))
        if not _supports_trace_persistence_wait(self._planner):
            trajectory = _build_trajectory(query, session_id, trace_id, metadata, llm_context, merged_tool_context)
            if (
                trajectory is not None
                and self._state_store is not None
                and hasattr(self._state_store, "save_trajectory")
            ):
                await self._state_store.save_trajectory(trace_id, session_id, trajectory)

        answer = _normalise_answer(payload)
        if answer is None and metadata:
            maybe_thought = metadata.get("thought")
            answer = str(maybe_thought) if maybe_thought is not None else None

        return ChatResult(
            answer=answer,
            trace_id=trace_id,
            session_id=session_id,
            metadata=metadata,
        )


async def _await_trace_persistence(
    planner: Any,
    trace_id: str,
    session_id: str,
    *,
    timeout_s: float = 1.0,
) -> None:
    waiter = getattr(planner, "wait_for_trace_persistence", None)
    if not callable(waiter):
        return
    wait_result = waiter(trace_id, session_id=session_id, timeout_s=timeout_s)
    if inspect.isawaitable(wait_result):
        await wait_result


def _supports_trace_persistence_wait(planner: Any) -> bool:
    return callable(getattr(planner, "wait_for_trace_persistence", None))


def _candidate_packages(project_root: str | Path) -> list[str]:
    base = Path(project_root).resolve()
    roots = [base / "src", base]
    packages: list[str] = []
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda item: item.name):
            if child.is_dir() and (child / "__init__.py").exists() and child.name not in packages:
                packages.append(child.name)
    return packages


def _discover_agent(project_root: str | Path) -> Any:
    ensure_project_on_sys_path(project_root)
    packages = _candidate_packages(project_root)
    orchestrator_match: Any | None = None
    planner_match: Any | None = None

    for package in packages:
        discovered = _discover_agent_with_package(project_root, package, _allow_fallback=False)
        if discovered is None:
            continue
        if discovered.kind == "orchestrator" and orchestrator_match is None:
            orchestrator_match = discovered
        if discovered.kind == "planner" and planner_match is None:
            planner_match = discovered
        if orchestrator_match is not None:
            return orchestrator_match

    if planner_match is not None:
        return planner_match
    raise ValueError(f"Could not discover agent in {Path(project_root).resolve()}")


def _coerce_query_case(value: str | Mapping[str, Any]) -> QueryCase:
    if isinstance(value, str):
        return QueryCase(query=value)
    query = str(value.get("query") or value.get("text") or "")
    tags_raw = value.get("tags", [])
    tags = tuple(str(tag) for tag in tags_raw) if isinstance(tags_raw, list) else ()
    split_value = value.get("split")
    split = str(split_value) if split_value is not None else None
    llm_context = value.get("llm_context")
    tool_context = value.get("tool_context")
    return QueryCase(
        query=query,
        split=split,
        tags=tags,
        llm_context=llm_context if isinstance(llm_context, Mapping) else None,
        tool_context=tool_context if isinstance(tool_context, Mapping) else None,
    )


async def _build_project_runner(*, project_root: str | Path, state_store: Any, agent_package: str | None = None) -> Any:
    """Build a discovery-backed runner without mutating Playground modules.

    Why: eval workflows need the same discovery ergonomics as Playground but we
    keep overrides local to evals to avoid conflicts with ongoing UI work.
    """

    ensure_project_on_sys_path(project_root)
    discovery = _discover_agent_with_package(project_root, agent_package)
    config = discovery.config_factory() if discovery.config_factory else None

    runner: _EvalPlannerWrapper | _EvalOrchestratorWrapper
    if discovery.kind == "planner":
        planner_output = _call_builder(discovery.target, config, state_store=state_store)
        planner = planner_output.planner if hasattr(planner_output, "planner") else planner_output
        runner = _EvalPlannerWrapper(planner, state_store=state_store)
    else:
        orchestrator = _instantiate_orchestrator(discovery.target, config, state_store=state_store)
        runner = _EvalOrchestratorWrapper(orchestrator, state_store=state_store)

    await _maybe_await(runner.initialize())
    return runner


def _discover_agent_with_package(
    project_root: str | Path,
    agent_package: str | None,
    *,
    _allow_fallback: bool = True,
) -> Any:
    if not agent_package:
        if _allow_fallback:
            return _discover_agent(project_root)
        return None

    ensure_project_on_sys_path(project_root)
    modules: list[Any] = []
    for name in ("orchestrator", "planner", "__main__", "__init__"):
        module_name = f"{agent_package}.{name}"
        try:
            modules.append(importlib.import_module(module_name))
        except ModuleNotFoundError:
            continue

    config_factory = None
    try:
        cfg_module = importlib.import_module(f"{agent_package}.config")
        config_cls = getattr(cfg_module, "Config", None)
        if config_cls is not None:
            from_env = getattr(config_cls, "from_env", None)
            if callable(from_env):
                config_factory = from_env
            else:

                def _config_factory() -> Any:
                    return config_cls()

                config_factory = _config_factory
    except ModuleNotFoundError:
        config_factory = None

    for module in modules:
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__name__.endswith("Orchestrator"):
                execute = getattr(obj, "execute", None)
                if execute and inspect.iscoroutinefunction(execute):
                    return type(
                        "_Discovery",
                        (),
                        {
                            "kind": "orchestrator",
                            "target": obj,
                            "config_factory": config_factory,
                        },
                    )()

    for module in modules:
        builder = getattr(module, "build_planner", None)
        if builder and inspect.isfunction(builder):
            return type(
                "_Discovery",
                (),
                {
                    "kind": "planner",
                    "target": builder,
                    "config_factory": config_factory,
                },
            )()

    raise ValueError(f"Could not discover agent for package {agent_package!r} in {Path(project_root).resolve()}")


async def collect_traces(
    *,
    project_root: str | Path,
    state_store: Any,
    session_id: str,
    queries: Sequence[str | Mapping[str, Any]],
    agent_package: str | None = None,
) -> dict[str, Any]:
    """Collect traces by running discovered agent entrypoints over query cases.

    Why: this provides a minimal collector API for quick scenario testing while
    keeping Playground APIs untouched during ongoing UI-oriented changes.
    """

    runner = await _build_project_runner(
        project_root=project_root,
        state_store=state_store,
        agent_package=agent_package,
    )
    trace_ids: list[str] = []

    for raw_case in queries:
        case = _coerce_query_case(raw_case)
        if not case.query:
            continue
        result = await runner.chat(
            query=case.query,
            session_id=session_id,
            llm_context=case.llm_context,
            tool_context=case.tool_context,
        )
        trace_ids.append(result.trace_id)
        wait_for_trace_persistence = getattr(runner, "wait_for_trace_persistence", None)
        if callable(wait_for_trace_persistence):
            try:
                await _maybe_await(
                    wait_for_trace_persistence(
                        result.trace_id,
                        session_id,
                        timeout_s=1.0,
                    )
                )
            except TimeoutError:
                pass

        tags = _collect_tags(case)
        if tags and hasattr(state_store, "get_trajectory") and hasattr(state_store, "save_trajectory"):
            trajectory = await state_store.get_trajectory(result.trace_id, session_id)
            if trajectory is not None:
                metadata = dict(trajectory.metadata or {})
                existing_raw = metadata.get("tags", [])
                existing = [str(tag) for tag in existing_raw] if isinstance(existing_raw, list) else []
                metadata["tags"] = _merge_tag_lists(existing, tags)
                trajectory.metadata = metadata
                await state_store.save_trajectory(result.trace_id, session_id, trajectory)

    return {
        "trace_count": len(trace_ids),
        "trace_ids": trace_ids,
    }


def _gold_context_from_row(gold: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    llm_context: dict[str, Any] = {}
    tool_context: dict[str, Any] = {}
    trace = gold.get("gold_trace")
    if isinstance(trace, Mapping):
        inputs = trace.get("inputs")
        if isinstance(inputs, Mapping):
            raw_llm = inputs.get("llm_context")
            raw_tool = inputs.get("tool_context")
            if isinstance(raw_llm, Mapping):
                llm_context = dict(raw_llm)
            if isinstance(raw_tool, Mapping):
                tool_context = dict(raw_tool)
    return llm_context, tool_context


def _planner_events_payload(events: list[Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for event in events:
        payloads.append(
            {
                "event_type": getattr(event, "event_type", None),
                "ts": getattr(event, "ts", None),
                "trajectory_step": getattr(event, "trajectory_step", None),
                "node_name": getattr(event, "node_name", None),
                "latency_ms": getattr(event, "latency_ms", None),
                "error": getattr(event, "error", None),
                "extra": dict(getattr(event, "extra", {}) or {}),
            }
        )
    return payloads


async def _build_discovered_run_one(
    *,
    project_root: str | Path,
    state_store: Any,
    prediction_session_id: str,
    agent_package: str | None = None,
) -> Callable[[dict[str, Any], dict[str, Any] | None], Any]:
    runner = await _build_project_runner(
        project_root=project_root,
        state_store=state_store,
        agent_package=agent_package,
    )

    async def _run_one(gold: dict[str, Any], patch_bundle: dict[str, Any] | None = None) -> Any:
        query = str(gold.get("question") or gold.get("text") or "")
        llm_context, tool_context = _gold_context_from_row(gold)
        if patch_bundle is not None:
            # Why: default discovered execution does not apply workload-specific
            # patch semantics. We still surface bundle data in contexts so
            # project code can opt-in without custom run_one wiring.
            llm_context["__pf_patch_bundle"] = dict(patch_bundle)
            tool_context["__pf_patch_bundle"] = dict(patch_bundle)

        result = await runner.chat(
            query=query,
            session_id=prediction_session_id,
            llm_context=llm_context,
            tool_context=tool_context,
        )
        wait_for_trace_persistence = getattr(runner, "wait_for_trace_persistence", None)
        if callable(wait_for_trace_persistence):
            try:
                await _maybe_await(
                    wait_for_trace_persistence(
                        result.trace_id,
                        prediction_session_id,
                        timeout_s=1.0,
                    )
                )
            except TimeoutError:
                pass
        pred = str(result.answer or "")
        pred_trace: dict[str, Any] = {}

        get_trajectory = getattr(state_store, "get_trajectory", None)
        if get_trajectory is not None:
            trajectory = await _maybe_await(get_trajectory(result.trace_id, prediction_session_id))
            if trajectory is not None and hasattr(trajectory, "serialise"):
                pred_trace = dict(trajectory.serialise())

        list_planner_events = getattr(state_store, "list_planner_events", None)
        if list_planner_events is not None:
            events = await _maybe_await(list_planner_events(result.trace_id))
            if isinstance(events, list):
                pred_trace["planner_events"] = _planner_events_payload(events)

        return pred, pred_trace

    return _run_one


async def collect_and_export_traces(
    *,
    project_root: str | Path,
    query_suite_path: str | Path,
    output_dir: str | Path,
    session_id: str,
    dataset_tag: str,
    agent_package: str | None = None,
    state_store_spec: str | None = None,
) -> dict[str, Any]:
    """Collect traces from a query suite and export them, without evaluation.

    This exists for metric development and debugging: users often want to
    inspect exported trace artifacts (contexts, planner steps, events) before
    writing a metric or defining patch candidates.
    """

    ensure_project_on_sys_path(project_root)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if state_store_spec is not None:
        state_store_factory = resolve_callable(state_store_spec)
        state_store = await _maybe_await(state_store_factory())
    else:
        state_store = InMemoryStateStore()

    suite = load_query_suite(query_suite_path)
    query_rows = suite.get("queries", [])
    if not isinstance(query_rows, list) or not query_rows:
        raise ValueError("query_suite must contain at least one query")

    query_cases: list[str | Mapping[str, Any]] = []
    for row in query_rows:
        if not isinstance(row, Mapping):
            raise ValueError("query rows must be JSON objects")
        query_text = str(row.get("text") or row.get("query") or "").strip()
        if not query_text:
            raise ValueError("query_suite contains empty query text")
        tags = [str(dataset_tag)]
        query_id = row.get("query_id")
        if query_id is not None:
            tags.append(f"query_id:{query_id}")
        split_value = row.get("split")
        split = str(split_value) if split_value is not None else None
        query_cases.append(
            {
                "query": query_text,
                "split": split,
                "tags": tags,
            }
        )

    collect_result = await collect_traces(
        project_root=project_root,
        state_store=state_store,
        session_id=session_id,
        queries=query_cases,
        agent_package=agent_package,
    )
    trace_ids = [str(trace_id) for trace_id in collect_result.get("trace_ids", [])]
    if not trace_ids:
        raise ValueError("trace collection produced no trace ids")

    export_result = await export_dataset(
        state_store=state_store,
        output_dir=out_dir,
        selector=TraceSelector(include_tags=(str(dataset_tag),)),
        workload=agent_package,
    )

    return {
        "trace_count": len(trace_ids),
        "dataset_path": export_result["dataset_path"],
        "manifest_path": export_result["manifest_path"],
        "output_dir": str(out_dir),
    }


def _resolve_against(base_dir: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (base_dir / candidate).resolve()


def _resolve_project_root(*, payload: Mapping[str, Any], spec_path: Path, required: bool) -> Path | None:
    """Resolve project_root from spec payload using current working directory.

    Why: eval commands are invoked from a project workspace, and users expect
    `project_root: "."` to refer to that workspace regardless of where spec
    files are stored.
    """

    del spec_path
    raw = payload.get("project_root")
    if raw is None:
        if required:
            raise ValueError("spec missing required key: project_root")
        return None

    candidate = Path(str(raw))
    if candidate.is_absolute():
        return candidate.resolve()
    return (Path.cwd() / candidate).resolve()


def _resolution_base(*, project_root: Path | None, spec_path: Path) -> Path:
    """Return canonical base for all relative path-like fields.

    Why: using one base prevents fragile specs where moving the spec file
    silently changes some paths but not others.
    """

    return project_root if project_root is not None else spec_path.parent


def load_candidates(path: str | Path) -> list[dict[str, Any]]:
    """Load patch candidates with strict shape checks.

    Why: malformed candidate rows should fail fast, but an empty list is valid
    baseline-only mode so teams can score current behavior without proposing
    prompt patches.
    """

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("candidates file must be a JSON list")
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_patch_fingerprints: set[str] = set()
    for idx, row in enumerate(payload):
        if not isinstance(row, dict):
            raise ValueError(f"candidate row {idx} must be a JSON object")
        if "id" not in row:
            raise ValueError(f"candidate row {idx} is missing required key 'id'")
        if "patches" not in row:
            raise ValueError(f"candidate row {idx} is missing required key 'patches'")
        candidate_id = str(row["id"])
        if candidate_id in seen_ids:
            raise ValueError(f"duplicate candidate id: {candidate_id}")
        seen_ids.add(candidate_id)

        patches = row["patches"]
        if not isinstance(patches, dict):
            raise ValueError(f"candidate row {idx} field 'patches' must be a JSON object")
        if not patches:
            raise ValueError(
                "candidate patches must be non-empty patches; "
                "baseline is evaluated automatically and should not be listed"
            )

        fingerprint = json.dumps(patches, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if fingerprint in seen_patch_fingerprints:
            raise ValueError(f"duplicate patch set for candidate id: {candidate_id}")
        seen_patch_fingerprints.add(fingerprint)

        row_copy = dict(row)
        row_copy["id"] = candidate_id
        row_copy["patches"] = dict(patches)
        rows.append(row_copy)
    return rows


def load_eval_collect_spec(path: str | Path) -> EvalCollectSpec:
    """Load collect spec and resolve relative paths.

    Collect specs intentionally mirror the env-file and path normalization
    behavior of collect/evaluate specs so teams can move between commands
    without changing their secret-loading posture.
    """

    spec_path = Path(path).resolve()
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("eval collect spec must be a JSON object")

    required = ["project_root", "query_suite_path", "output_dir", "session_id", "dataset_tag"]
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"eval collect spec missing required keys: {', '.join(sorted(missing))}")

    project_root = _resolve_project_root(payload=payload, spec_path=spec_path, required=True)
    assert project_root is not None
    base_dir = _resolution_base(project_root=project_root, spec_path=spec_path)
    raw_env_files = payload.get("env_files", [])
    if raw_env_files is None:
        raw_env_files = []
    if not isinstance(raw_env_files, list):
        raise ValueError("eval collect spec field 'env_files' must be a list")

    env_files = tuple(_resolve_against(base_dir, str(item)) for item in raw_env_files)

    return EvalCollectSpec(
        project_root=project_root,
        query_suite_path=_resolve_against(base_dir, str(payload["query_suite_path"])),
        output_dir=_resolve_against(base_dir, str(payload["output_dir"])),
        session_id=str(payload["session_id"]),
        dataset_tag=str(payload["dataset_tag"]),
        env_files=env_files,
        agent_package=str(payload["agent_package"]) if payload.get("agent_package") is not None else None,
        state_store_spec=(str(payload["state_store_spec"]) if payload.get("state_store_spec") is not None else None),
    )


def load_eval_dataset_spec(path: str | Path) -> EvalDatasetSpec:
    """Load dataset-evaluation spec and resolve relative paths."""

    spec_path = Path(path).resolve()
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("eval dataset spec must be a JSON object")

    required = ["dataset_path", "metric_spec", "output_dir"]
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"eval dataset spec missing required keys: {', '.join(sorted(missing))}")

    project_root = _resolve_project_root(payload=payload, spec_path=spec_path, required=False)
    base_dir = _resolution_base(project_root=project_root, spec_path=spec_path)
    raw_env_files = payload.get("env_files", [])
    if raw_env_files is None:
        raw_env_files = []
    if not isinstance(raw_env_files, list):
        raise ValueError("eval dataset spec field 'env_files' must be a list")

    min_test_score = payload.get("min_test_score")
    if min_test_score is not None:
        if isinstance(min_test_score, bool) or not isinstance(min_test_score, (int, float)):
            raise ValueError("eval dataset spec field 'min_test_score' must be numeric when provided")
        min_test_score = float(min_test_score)

    return EvalDatasetSpec(
        dataset_path=_resolve_against(base_dir, str(payload["dataset_path"])),
        candidates_path=(
            _resolve_against(base_dir, str(payload["candidates_path"]))
            if payload.get("candidates_path") is not None
            else None
        ),
        metric_spec=str(payload["metric_spec"]),
        output_dir=_resolve_against(base_dir, str(payload["output_dir"])),
        report_path=(
            _resolve_against(base_dir, str(payload["report_path"])) if payload.get("report_path") is not None else None
        ),
        min_test_score=min_test_score,
        project_root=project_root,
        env_files=tuple(_resolve_against(base_dir, str(item)) for item in raw_env_files),
        agent_package=str(payload["agent_package"]) if payload.get("agent_package") is not None else None,
        run_one_spec=str(payload["run_one_spec"]) if payload.get("run_one_spec") is not None else None,
    )


def _as_score_payload(raw: float | dict[str, object]) -> tuple[float, str | None]:
    if isinstance(raw, dict):
        score_raw = raw.get("score", 0.0)
        score = 0.0
        if isinstance(score_raw, bool):
            score = 1.0 if score_raw else 0.0
        elif isinstance(score_raw, (int, float)):
            score = float(score_raw)
        elif isinstance(score_raw, str):
            try:
                score = float(score_raw)
            except ValueError:
                score = 0.0
        feedback = raw.get("feedback")
        return score, str(feedback) if feedback is not None else None
    if isinstance(raw, bool):
        return (1.0 if raw else 0.0), None
    if isinstance(raw, (int, float)):
        return float(raw), None
    return 0.0, None


def _is_baseline_bundle(bundle: dict[str, Any]) -> bool:
    """Return whether bundle contains no patches.

    Baseline-only mode emits an empty patch set and should not trigger an extra
    holdout run with identical inputs.
    """

    patches = bundle.get("patches")
    return isinstance(patches, dict) and not patches


def _split_dataset_payload_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    val_rows: list[dict[str, Any]] = []
    test_rows: list[dict[str, Any]] = []
    for item in rows:
        row = dict(item)
        split = str(row.get("split", "unknown"))
        if split == "val":
            val_rows.append(row)
        elif split == "test":
            test_rows.append(row)

    if not val_rows:
        raise ValueError("dataset must contain at least one val example")
    if not test_rows:
        raise ValueError("dataset must contain at least one test example")
    return val_rows, test_rows


def _split_dataset_rows(dataset_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for line in dataset_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        rows.append(json.loads(stripped))
    return _split_dataset_payload_rows(rows)


def _patch_size(candidate: dict[str, Any]) -> int:
    patches = candidate.get("patches", {})
    if isinstance(patches, dict):
        return len(patches)
    return 0


async def _evaluate_rows_mean(
    rows: list[dict[str, Any]],
    *,
    run_one: Callable[[dict[str, Any], dict[str, Any] | None], Any],
    metric: Callable[[object, object, object | None, str | None, object | None], float | dict[str, object]],
    pred_name: str,
    patch_bundle: dict[str, Any] | None,
) -> float:
    scores: list[float] = []
    for gold in rows:
        run_output = await _maybe_await(run_one(gold, patch_bundle))
        pred_trace = None
        pred = run_output
        if isinstance(run_output, tuple) and len(run_output) == 2:
            pred, pred_trace = run_output
        metric_raw = metric(gold, pred, gold, pred_name, pred_trace)
        score, _ = _as_score_payload(metric_raw)
        scores.append(score)
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


async def _evaluate_dataset_rows(
    *,
    val_rows: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
    run_one: Callable[[dict[str, Any], dict[str, Any] | None], Any],
    metric: Callable[[object, object, object | None, str | None, object | None], float | dict[str, object]],
    candidates: list[dict[str, Any]],
    workload: str | None,
    report_path: str | Path | None,
    min_test_score: float | None,
) -> dict[str, Any]:
    baseline_val_score = await _evaluate_rows_mean(
        val_rows,
        run_one=run_one,
        metric=metric,
        pred_name="baseline",
        patch_bundle=None,
    )
    rankings: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        candidate_id = str(candidate.get("id", f"candidate-{index + 1}"))
        score = await _evaluate_rows_mean(
            val_rows,
            run_one=run_one,
            metric=metric,
            pred_name=candidate_id,
            patch_bundle=candidate,
        )
        rankings.append(
            {
                "id": candidate_id,
                "score": score,
                "patch_size": _patch_size(candidate),
                "patches": dict(candidate.get("patches", {})),
                "patch_bundle": candidate,
            }
        )

    rankings.sort(key=lambda item: (-item["score"], item["patch_size"], item["id"]))
    winner = rankings[0] if rankings else {"id": "baseline", "score": baseline_val_score, "patches": {}}

    baseline_score = await _evaluate_rows_mean(
        test_rows,
        run_one=run_one,
        metric=metric,
        pred_name="test_baseline",
        patch_bundle=None,
    )
    if _is_baseline_bundle(winner):
        winner_score = baseline_score
    else:
        winner_score = await _evaluate_rows_mean(
            test_rows,
            run_one=run_one,
            metric=metric,
            pred_name="test_winner",
            patch_bundle=winner.get("patch_bundle"),
        )

    counts = {
        "val": len(val_rows),
        "test": len(test_rows),
        "total": len(val_rows) + len(test_rows),
    }
    if rankings:
        passed_holdout_regression = winner_score >= baseline_score
        baseline_threshold_ok = min_test_score is None or baseline_score >= min_test_score
        winner_threshold_ok = min_test_score is None or winner_score >= min_test_score
        passed_threshold = baseline_threshold_ok and winner_threshold_ok
        summary: dict[str, Any] = {
            "mode": "candidates",
            "winner_id": winner["id"],
            "val_baseline_score": baseline_val_score,
            "val_winner_score": winner["score"],
            "test_baseline_score": baseline_score,
            "test_winner_score": winner_score,
            "passed_holdout_regression": passed_holdout_regression,
            "passed_threshold": passed_threshold,
            "workload": str(workload or "unknown"),
            "counts": counts,
            "candidates": [{"id": item["id"], "score": item["score"]} for item in rankings],
        }
        if min_test_score is not None:
            summary["min_test_score"] = min_test_score
    else:
        passed_threshold = min_test_score is None or baseline_score >= min_test_score
        summary = {
            "mode": "baseline",
            "val_score": baseline_val_score,
            "test_score": baseline_score,
            "passed_threshold": passed_threshold,
            "workload": str(workload or "unknown"),
            "counts": counts,
        }
        if min_test_score is not None:
            summary["min_test_score"] = min_test_score

    resolved_report_path: str | None = None
    if report_path is not None:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        resolved_report_path = str(path)

    if rankings and not summary["passed_holdout_regression"]:
        raise ValueError("holdout regression detected")
    if not summary["passed_threshold"]:
        raise ValueError("min_test_score threshold not met")

    return {
        **summary,
        "report_path": resolved_report_path,
    }


async def evaluate_dataset(
    *,
    dataset_path: str | Path,
    output_dir: str | Path,
    run_one: Callable[[dict[str, Any], dict[str, Any] | None], Any],
    metric: Callable[[object, object, object | None, str | None, object | None], float | dict[str, object]],
    candidates: list[dict[str, Any]],
    workload: str | None = None,
    report_path: str | Path | None = None,
    min_test_score: float | None = None,
) -> dict[str, Any]:
    """Evaluate an existing dataset bundle with sweep + holdout gate.

    Baseline-only candidate lists are supported so teams can rerun regression
    checks without synthetic patch candidates.
    """

    del output_dir
    val_rows, test_rows = _split_dataset_rows(Path(dataset_path))
    metric_fn = wrap_metric(metric)
    return await _evaluate_dataset_rows(
        val_rows=val_rows,
        test_rows=test_rows,
        run_one=run_one,
        metric=metric_fn,
        candidates=candidates,
        workload=workload,
        report_path=report_path,
        min_test_score=min_test_score,
    )


async def evaluate_dataset_from_spec_file(path: str | Path) -> dict[str, Any]:
    """Evaluate from dataset spec file without requiring trace collection."""

    spec = load_eval_dataset_spec(path)
    if spec.project_root is not None:
        ensure_project_on_sys_path(spec.project_root)
    metric = wrap_metric(resolve_callable(spec.metric_spec))
    candidates = load_candidates(spec.candidates_path) if spec.candidates_path is not None else []

    if spec.run_one_spec is not None:
        run_one = resolve_callable(spec.run_one_spec)
    elif spec.project_root is not None:
        state_store = InMemoryStateStore()
        run_one = await _build_discovered_run_one(
            project_root=spec.project_root,
            state_store=state_store,
            prediction_session_id="eval-dataset-pred",
            agent_package=spec.agent_package,
        )
    else:
        raise ValueError("eval dataset spec must provide run_one_spec or project_root")

    return await evaluate_dataset(
        dataset_path=spec.dataset_path,
        output_dir=spec.output_dir,
        run_one=run_one,
        metric=metric,
        candidates=candidates,
        workload=spec.agent_package,
        report_path=spec.report_path,
        min_test_score=spec.min_test_score,
    )


__all__ = [
    "EvalCollectSpec",
    "EvalDatasetSpec",
    "QueryCase",
    "TraceSelector",
    "collect_traces",
    "collect_and_export_traces",
    "evaluate_dataset",
    "evaluate_dataset_from_spec_file",
    "ensure_project_on_sys_path",
    "export_dataset",
    "load_candidates",
    "load_eval_collect_spec",
    "load_eval_dataset_spec",
    "resolve_trace_refs",
    "resolve_callable",
    "wrap_metric",
]
