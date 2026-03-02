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

from penguiflow.cli.playground import discover_agent
from penguiflow.cli.playground_wrapper import ChatResult, PlannerAgentWrapper, _build_trajectory, _normalise_answer
from penguiflow.state import InMemoryStateStore

from .export import export_trace_dataset
from .inputs import load_query_suite
from .runner import run_harness_eval
from .sweep import run_manual_sweep
from .workflow import run_eval_workflow

MetricFn = Callable[[object, object, object | None, str | None, object | None], float | dict[str, object]]
RunOneFn = Callable[[dict[str, Any], dict[str, Any] | None], Any]


@dataclass(frozen=True)
class EvalSpec:
    """Declarative config for reproducible eval runs.

    Why: projects should be able to commit a compact, reviewable recipe that
    points to their metric/hooks and input files, while generating exports and
    reports locally on demand.
    """

    project_root: Path
    state_store_spec: str
    run_one_spec: str
    metric_spec: str
    query_suite_path: Path
    trace_ids_path: Path
    candidates_path: Path
    output_dir: Path
    session_id: str | None = None


@dataclass(frozen=True)
class EvalRunSpec:
    """Declarative config for collect->export->evaluate CLI/API runs.

    Why: the CLI and library entrypoints should share one parsing contract to
    prevent drift across required fields, defaults, and path resolution.
    """

    project_root: Path
    query_suite_path: Path
    candidates_path: Path
    metric_spec: str
    output_dir: Path
    session_id: str
    dataset_tag: str
    env_files: tuple[Path, ...] = ()
    agent_package: str | None = None
    state_store_spec: str | None = None
    run_one_spec: str | None = None


@dataclass(frozen=True)
class EvalDatasetSpec:
    """Declarative config for evaluation runs over an existing dataset bundle."""

    dataset_path: Path
    candidates_path: Path
    metric_spec: str
    output_dir: Path
    project_root: Path | None = None
    env_files: tuple[Path, ...] = ()
    agent_package: str | None = None
    run_one_spec: str | None = None


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class TraceSelector:
    """Selector for exporting traces from a StateStore.

    Why: API users should not manage trace-id lists manually. A selector keeps
    export intent compact (all traces by default, optional tag narrowing).
    """

    include_tags: tuple[str, ...] = ()
    exclude_tags: tuple[str, ...] = ()
    limit: int = 0


@dataclass(frozen=True)
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
            return raw  # type: ignore[return-value]

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
        return raw  # type: ignore[return-value]

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

    export_result = await export_trace_dataset(
        state_store=state_store,
        trace_ids=[str(ref["trace_id"]) for ref in refs],
        trace_refs=refs,
        output_dir=output_dir,
        redaction_profile=redaction_profile,
        workload=workload,
    )

    trace_path = Path(str(export_result["trace_path"]))
    rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    dataset_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        dataset_rows.append(
            {
                "example_id": str(row.get("trace_id") or f"trace-{idx + 1}"),
                "split": str(row.get("trajectory", {}).get("split") or "unknown"),
                "question": str(row.get("query") or ""),
                "gold_trace": row,
                "gold_trace_features": None,
            }
        )
    dataset_path = Path(output_dir) / "dataset.jsonl"
    with dataset_path.open("w", encoding="utf-8") as handle:
        for row in dataset_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    return {
        **export_result,
        "dataset_path": str(dataset_path),
    }


def _get_attr(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _normalize_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


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
        tool_ctx = dict(tool_context or {})
        if "tenant_id" in signature.parameters:
            kwargs["tenant_id"] = str(tool_ctx.get("tenant_id") or "default")
        if "user_id" in signature.parameters:
            kwargs["user_id"] = str(tool_ctx.get("user_id") or "eval-user")
        if "session_id" in signature.parameters:
            kwargs["session_id"] = session_id
        if "tool_context" in signature.parameters:
            kwargs["tool_context"] = tool_ctx

        response = await execute(**kwargs)
        metadata = _flatten_planner_metadata(_normalize_metadata(_get_attr(response, "metadata")))
        trace_id = str(metadata.get("trace_id") or _get_attr(response, "trace_id") or secrets.token_hex(8))
        answer = _normalise_answer(_get_attr(response, "answer"))
        if answer is None:
            answer = _normalise_answer(_get_attr(response, "text"))

        trajectory = _build_trajectory(query, session_id, trace_id, metadata, llm_context or {}, tool_ctx)
        if trajectory is not None and self._state_store is not None and hasattr(self._state_store, "save_trajectory"):
            await self._state_store.save_trajectory(trace_id, session_id, trajectory)

        return ChatResult(
            answer=answer,
            trace_id=trace_id,
            session_id=session_id,
            metadata=metadata,
        )


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

    if discovery.kind == "planner":
        planner_output = _call_builder(discovery.target, config, state_store=state_store)
        planner = planner_output.planner if hasattr(planner_output, "planner") else planner_output
        runner: Any = PlannerAgentWrapper(planner, state_store=state_store)
    else:
        orchestrator = _instantiate_orchestrator(discovery.target, config, state_store=state_store)
        runner = _EvalOrchestratorWrapper(orchestrator, state_store=state_store)

    await _maybe_await(runner.initialize())
    return runner


def _discover_agent_with_package(project_root: str | Path, agent_package: str | None) -> Any:
    if not agent_package:
        return discover_agent(Path(project_root))

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


async def run_eval(
    *,
    project_root: str | Path,
    query_suite_path: str | Path,
    candidates_path: str | Path,
    metric_spec: str,
    output_dir: str | Path,
    session_id: str,
    dataset_tag: str,
    state_store_spec: str | None = None,
    run_one_spec: str | None = None,
    agent_package: str | None = None,
) -> dict[str, Any]:
    """Run full eval flow from query suite in a single call.

    Why: API consumers should be able to run a PoC end-to-end with one command
    while still reusing stable low-level helpers under the hood.
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

    trace_ids_path = out_dir / "trace_ids.generated.txt"
    trace_ids_path.write_text("\n".join(trace_ids) + "\n", encoding="utf-8")

    bundle_result = await export_dataset(
        state_store=state_store,
        output_dir=out_dir / "bundle",
        selector=TraceSelector(include_tags=(str(dataset_tag),)),
        workload=agent_package,
    )

    metric = wrap_metric(resolve_callable(metric_spec))
    candidates = load_candidates(candidates_path)
    if run_one_spec is not None:
        run_one = resolve_callable(run_one_spec)
    else:
        run_one = await _build_discovered_run_one(
            project_root=project_root,
            state_store=state_store,
            prediction_session_id=f"{session_id}-pred",
            agent_package=agent_package,
        )

    slice_result = await run_eval_workflow(
        state_store=state_store,
        query_suite_path=query_suite_path,
        trace_ids_path=trace_ids_path,
        output_dir=out_dir,
        run_one=run_one,
        metric=metric,
        candidates=candidates,
        session_id=session_id,
        workload=agent_package,
    )

    return {
        **slice_result,
        "trace_ids_path": str(trace_ids_path),
        "collect_trace_count": len(trace_ids),
        "bundle_dataset_path": str(bundle_result["dataset_path"]),
        "bundle_manifest_path": str(bundle_result["manifest_path"]),
        "bundle_trace_path": str(bundle_result["trace_path"]),
    }


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

    trace_ids_path = out_dir / "trace_ids.generated.txt"
    trace_ids_path.write_text("\n".join(trace_ids) + "\n", encoding="utf-8")

    export_result = await export_trace_dataset(
        state_store=state_store,
        trace_ids=trace_ids,
        output_dir=out_dir,
        session_id=session_id,
        workload=agent_package,
    )

    return {
        "trace_count": len(trace_ids),
        "trace_ids_path": str(trace_ids_path),
        "trace_path": export_result["trace_path"],
        "manifest_path": export_result["manifest_path"],
        "output_dir": str(out_dir),
    }


def _resolve_against(base_dir: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (base_dir / candidate).resolve()


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


def load_eval_spec(path: str | Path) -> EvalSpec:
    """Load an eval spec file and resolve all relative paths.

    Why: spec files are the contract between reusable eval API and project code,
    so path normalization must be deterministic across local runs and CI.
    """

    spec_path = Path(path).resolve()
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("eval spec must be a JSON object")

    required = [
        "project_root",
        "state_store_spec",
        "run_one_spec",
        "metric_spec",
        "query_suite_path",
        "trace_ids_path",
        "candidates_path",
        "output_dir",
    ]
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"eval spec is missing required keys: {', '.join(sorted(missing))}")

    base_dir = spec_path.parent
    return EvalSpec(
        project_root=_resolve_against(base_dir, str(payload["project_root"])),
        state_store_spec=str(payload["state_store_spec"]),
        run_one_spec=str(payload["run_one_spec"]),
        metric_spec=str(payload["metric_spec"]),
        query_suite_path=_resolve_against(base_dir, str(payload["query_suite_path"])),
        trace_ids_path=_resolve_against(base_dir, str(payload["trace_ids_path"])),
        candidates_path=_resolve_against(base_dir, str(payload["candidates_path"])),
        output_dir=_resolve_against(base_dir, str(payload["output_dir"])),
        session_id=str(payload["session_id"]) if payload.get("session_id") is not None else None,
    )


def load_eval_run_spec(path: str | Path) -> EvalRunSpec:
    """Load run-eval spec and resolve relative paths.

    Why: `penguiflow eval run` and programmatic runners should behave exactly
    the same for spec validation and path handling.
    """

    spec_path = Path(path).resolve()
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("eval run spec must be a JSON object")

    required = [
        "project_root",
        "query_suite_path",
        "candidates_path",
        "metric_spec",
        "output_dir",
        "session_id",
        "dataset_tag",
    ]
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"eval run spec missing required keys: {', '.join(sorted(missing))}")

    base_dir = spec_path.parent
    raw_env_files = payload.get("env_files", [])
    if raw_env_files is None:
        raw_env_files = []
    if not isinstance(raw_env_files, list):
        raise ValueError("eval run spec field 'env_files' must be a list")

    env_files = tuple(_resolve_against(base_dir, str(item)) for item in raw_env_files)

    return EvalRunSpec(
        project_root=_resolve_against(base_dir, str(payload["project_root"])),
        query_suite_path=_resolve_against(base_dir, str(payload["query_suite_path"])),
        candidates_path=_resolve_against(base_dir, str(payload["candidates_path"])),
        metric_spec=str(payload["metric_spec"]),
        output_dir=_resolve_against(base_dir, str(payload["output_dir"])),
        session_id=str(payload["session_id"]),
        dataset_tag=str(payload["dataset_tag"]),
        env_files=env_files,
        agent_package=str(payload["agent_package"]) if payload.get("agent_package") is not None else None,
        state_store_spec=(str(payload["state_store_spec"]) if payload.get("state_store_spec") is not None else None),
        run_one_spec=(str(payload["run_one_spec"]) if payload.get("run_one_spec") is not None else None),
    )


def load_eval_collect_spec(path: str | Path) -> EvalCollectSpec:
    """Load collect spec and resolve relative paths.

    Collect specs intentionally mirror the env-file and path normalization
    behavior of eval run/evaluate specs so teams can move between commands
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

    base_dir = spec_path.parent
    raw_env_files = payload.get("env_files", [])
    if raw_env_files is None:
        raw_env_files = []
    if not isinstance(raw_env_files, list):
        raise ValueError("eval collect spec field 'env_files' must be a list")

    env_files = tuple(_resolve_against(base_dir, str(item)) for item in raw_env_files)

    return EvalCollectSpec(
        project_root=_resolve_against(base_dir, str(payload["project_root"])),
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

    required = ["dataset_path", "candidates_path", "metric_spec", "output_dir"]
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"eval dataset spec missing required keys: {', '.join(sorted(missing))}")

    base_dir = spec_path.parent
    raw_env_files = payload.get("env_files", [])
    if raw_env_files is None:
        raw_env_files = []
    if not isinstance(raw_env_files, list):
        raise ValueError("eval dataset spec field 'env_files' must be a list")

    return EvalDatasetSpec(
        dataset_path=_resolve_against(base_dir, str(payload["dataset_path"])),
        candidates_path=_resolve_against(base_dir, str(payload["candidates_path"])),
        metric_spec=str(payload["metric_spec"]),
        output_dir=_resolve_against(base_dir, str(payload["output_dir"])),
        project_root=(
            _resolve_against(base_dir, str(payload["project_root"]))
            if payload.get("project_root") is not None
            else None
        ),
        env_files=tuple(_resolve_against(base_dir, str(item)) for item in raw_env_files),
        agent_package=str(payload["agent_package"]) if payload.get("agent_package") is not None else None,
        run_one_spec=str(payload["run_one_spec"]) if payload.get("run_one_spec") is not None else None,
    )


def _mean_score(path: Path) -> float:
    scores: list[float] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        payload = json.loads(stripped)
        scores.append(float(payload.get("score", 0.0)))
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def _is_baseline_bundle(bundle: dict[str, Any]) -> bool:
    """Return whether bundle contains no patches.

    Baseline-only mode emits an empty patch set and should not trigger an extra
    holdout run with identical inputs.
    """

    patches = bundle.get("patches")
    return isinstance(patches, dict) and not patches


def _split_dataset_rows(dataset_path: Path, output_dir: Path) -> tuple[Path, Path]:
    val_rows: list[dict[str, Any]] = []
    test_rows: list[dict[str, Any]] = []
    for line in dataset_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        row = json.loads(stripped)
        split = str(row.get("split", "unknown"))
        if split == "val":
            val_rows.append(row)
        elif split == "test":
            test_rows.append(row)

    if not val_rows:
        raise ValueError("dataset must contain at least one val example")
    if not test_rows:
        raise ValueError("dataset must contain at least one test example")

    val_path = output_dir / "dataset.val.jsonl"
    test_path = output_dir / "dataset.test.jsonl"
    with val_path.open("w", encoding="utf-8") as handle:
        for row in val_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    with test_path.open("w", encoding="utf-8") as handle:
        for row in test_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return val_path, test_path


async def evaluate_dataset(
    *,
    dataset_path: str | Path,
    output_dir: str | Path,
    run_one: Callable[[dict[str, Any], dict[str, Any] | None], Any],
    metric: Callable[[object, object, object | None, str | None, object | None], float | dict[str, object]],
    candidates: list[dict[str, Any]],
    workload: str | None = None,
) -> dict[str, Any]:
    """Evaluate an existing dataset bundle with sweep + holdout gate.

    Baseline-only candidate lists are supported so teams can rerun regression
    checks without synthetic patch candidates.
    """

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    val_path, test_path = _split_dataset_rows(Path(dataset_path), out_dir)
    metric_fn = wrap_metric(metric)

    sweep_result = await run_manual_sweep(
        dataset_path=val_path,
        output_dir=out_dir,
        run_one=run_one,
        metric=metric_fn,
        candidates=candidates,
        workload=workload,
    )
    bundle_path = Path(sweep_result["bundle_path"])
    winner_bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

    baseline_test = await run_harness_eval(
        dataset_path=test_path,
        output_dir=out_dir,
        run_one=run_one,
        metric=metric_fn,
        mode="test_baseline",
        pred_name="test_baseline",
        patch_bundle=None,
    )

    baseline_score = _mean_score(Path(baseline_test["results_path"]))
    if _is_baseline_bundle(winner_bundle):
        winner_score = baseline_score
    else:
        winner_test = await run_harness_eval(
            dataset_path=test_path,
            output_dir=out_dir,
            run_one=run_one,
            metric=metric_fn,
            mode="test_winner",
            pred_name="test_winner",
            patch_bundle=winner_bundle,
        )
        winner_score = _mean_score(Path(winner_test["results_path"]))
    passed = winner_score >= baseline_score
    report_test = {
        "baseline_score": baseline_score,
        "winner_score": winner_score,
        "passed": passed,
        "winner_id": sweep_result["winner_id"],
    }
    report_test_path = out_dir / "report.test.json"
    report_test_path.write_text(json.dumps(report_test, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")

    if not passed:
        raise ValueError("holdout regression detected")

    return {
        "winner_id": sweep_result["winner_id"],
        "report_harness_path": str(out_dir / "report.harness.json"),
        "report_test_path": str(report_test_path),
        "bundle_path": str(bundle_path),
    }


async def evaluate_dataset_from_spec_file(path: str | Path) -> dict[str, Any]:
    """Evaluate from dataset spec file without requiring trace collection."""

    spec = load_eval_dataset_spec(path)
    metric = wrap_metric(resolve_callable(spec.metric_spec))
    candidates = load_candidates(spec.candidates_path)

    if spec.run_one_spec is not None:
        if spec.project_root is not None:
            ensure_project_on_sys_path(spec.project_root)
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
    )


async def run_eval_from_specs(
    *,
    project_root: str | Path,
    state_store_spec: str,
    run_one_spec: str,
    metric_spec: str,
    query_suite_path: str | Path,
    trace_ids_path: str | Path,
    output_dir: str | Path,
    candidates: list[dict[str, Any]],
    session_id: str | None = None,
) -> dict[str, Any]:
    """Run eval pipeline using import-path specs for state store, run_one, and metric."""

    ensure_project_on_sys_path(project_root)
    state_store_factory = resolve_callable(state_store_spec)
    run_one = resolve_callable(run_one_spec)
    metric = wrap_metric(resolve_callable(metric_spec))
    state_store = await _maybe_await(state_store_factory())

    return await run_eval_workflow(
        state_store=state_store,
        query_suite_path=query_suite_path,
        trace_ids_path=trace_ids_path,
        output_dir=output_dir,
        run_one=run_one,
        metric=metric,
        candidates=candidates,
        session_id=session_id,
    )


async def run_eval_from_spec_file(path: str | Path) -> dict[str, Any]:
    """Execute eval workflow from a committed recipe file.

    Why: this gives API consumers a one-call entrypoint that mirrors how they
    operate in examples and production scripts without custom orchestration code.
    """

    spec = load_eval_spec(path)
    return await run_eval_from_specs(
        project_root=spec.project_root,
        state_store_spec=spec.state_store_spec,
        run_one_spec=spec.run_one_spec,
        metric_spec=spec.metric_spec,
        query_suite_path=spec.query_suite_path,
        trace_ids_path=spec.trace_ids_path,
        output_dir=spec.output_dir,
        candidates=load_candidates(spec.candidates_path),
        session_id=spec.session_id,
    )


__all__ = [
    "EvalCollectSpec",
    "EvalSpec",
    "EvalRunSpec",
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
    "load_eval_spec",
    "load_eval_run_spec",
    "load_eval_dataset_spec",
    "resolve_trace_refs",
    "resolve_callable",
    "run_eval",
    "run_eval_from_spec_file",
    "run_eval_from_specs",
    "wrap_metric",
]
