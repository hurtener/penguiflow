"""Default proactive report generator for background task completions."""

from __future__ import annotations

import json
from collections.abc import Sequence
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from penguiflow.planner import ReactPlanner

from .models import ProactiveReportContext, ProactiveReportRequest, StateUpdate, UpdateType
from .session import StreamingSession

_PROACTIVE_HOPS_KEY = "proactive_hops_remaining"
_PROACTIVE_MODE_KEY = "proactive_mode"

PROACTIVE_SYNTHESIS_PROMPT = """\
You previously started a background task.

Original task: {task_description}

Results received:
{results_summary}

Based on your short-term memory summary and these results, write a natural,
conversational update to inform the user. If the results suggest further action,
use your tools.
"""


def create_default_proactive_generator(
    planner_factory: Callable[[], Any],
    session: StreamingSession,
    *,
    max_hops: int = 2,
) -> Callable[[ProactiveReportRequest], Awaitable[None]]:
    """Create a default proactive report generator using a full planner loop."""

    async def _generator(request: ProactiveReportRequest) -> None:
        hops_remaining = request.proactive_hops_remaining
        if hops_remaining is None:
            hops_remaining = max_hops
        if hops_remaining <= 0:
            return
        next_hops = max(hops_remaining - 1, 0)

        planner = _unwrap_planner(planner_factory())
        results_summary = _format_results_summary(request)
        query = PROACTIVE_SYNTHESIS_PROMPT.format(
            task_description=request.task_description or "Background task",
            results_summary=results_summary,
        )
        llm_context = {
            "conversation_memory": dict(request.memory_summary or {}),
            "proactive_report": _build_proactive_context(request),
        }
        tool_context = dict(request.tool_context or {})
        tool_context.setdefault("session_id", request.session_id)
        tool_context.setdefault("task_id", request.message_id)
        if request.trace_id:
            tool_context.setdefault("trace_id", request.trace_id)
        tool_context.setdefault("is_subagent", False)
        tool_context[_PROACTIVE_MODE_KEY] = True
        tool_context[_PROACTIVE_HOPS_KEY] = next_hops

        if isinstance(planner, ReactPlanner) and next_hops <= 0:
            background_cfg = getattr(planner, "_background_tasks", None)
            if background_cfg is not None:
                planner._background_tasks = background_cfg.model_copy(
                    update={
                        "enabled": False,
                        "allow_tool_background": False,
                        "proactive_report_enabled": False,
                    }
                )

        result = await planner.run(
            query=query,
            llm_context=llm_context,
            tool_context=tool_context,
        )
        payload = getattr(result, "payload", None)
        metadata = getattr(result, "metadata", None)
        answer = _extract_answer(payload) or "Background task completed."
        artifacts = _collect_artifact_refs(request)
        ui_components = _collect_ui_component_chunks(metadata)

        session._publish(
            StateUpdate(
                session_id=request.session_id,
                task_id=request.task_id,
                trace_id=request.trace_id,
                update_type=UpdateType.RESULT,
                content={
                    "text": answer,
                    "channel": "answer",
                    "done": True,
                    "proactive": True,
                    "background_task_id": request.task_id,
                    "group_id": request.group_id,
                    "artifacts": artifacts or None,
                    "ui_components": ui_components or None,
                },
            )
        )

        task_ids = request.group_task_ids if request.is_group_report else [request.task_id]
        await session.mark_background_consumed(task_ids=task_ids)

    return _generator


def setup_proactive_reporting(
    session: StreamingSession,
    planner_factory: Callable[[], Any],
    *,
    enabled: bool,
    strategies: list[str] | None = None,
    max_queued: int = 5,
    timeout_s: float = 60.0,
    max_hops: int = 2,
    fallback_notification: bool = True,
) -> None:
    """Configure the session to use the default proactive generator."""
    if not enabled:
        return
    generator = create_default_proactive_generator(
        planner_factory,
        session,
        max_hops=max_hops,
    )
    session.configure_proactive_reporting(
        generator=generator,
        enabled=True,
        strategies=strategies,
        max_queued=max_queued,
        timeout_s=timeout_s,
        max_hops=max_hops,
        fallback_notification=fallback_notification,
    )


def _unwrap_planner(planner_or_bundle: Any) -> Any:
    if hasattr(planner_or_bundle, "planner"):
        return planner_or_bundle.planner
    return planner_or_bundle


def _extract_answer(payload: Any) -> str | None:
    if payload is None:
        return None
    if isinstance(payload, str):
        return payload
    if isinstance(payload, Mapping):
        for key in ("raw_answer", "answer", "text", "content", "message"):
            value = payload.get(key)
            if value is not None:
                return str(value)
    for attr in ("raw_answer", "answer", "text", "content", "message"):
        if hasattr(payload, attr):
            value = getattr(payload, attr)
            if value is not None:
                return str(value)
    return None


def _build_proactive_context(request: ProactiveReportRequest) -> dict[str, Any]:
    patch = request.patch
    context = ProactiveReportContext(
        task_id=request.task_id,
        task_description=request.task_description,
        digest=list(patch.digest or []),
        facts=dict(patch.facts or {}),
        artifacts=list(patch.artifacts or []),
        sources=list(patch.sources or []),
        execution_time_ms=request.execution_time_ms,
        context_diverged=bool(patch.context_diverged),
        merge_strategy=request.merge_strategy.value.upper(),
    )
    return context.model_dump(mode="json")


def _collect_artifact_refs(request: ProactiveReportRequest) -> list[dict[str, Any]]:
    patches = [request.patch]
    if request.is_group_report and request.combined_patches:
        patches.extend(request.combined_patches)
    seen: set[str] = set()
    collected: list[dict[str, Any]] = []
    for patch in patches:
        for item in patch.artifacts or []:
            ref = _extract_artifact_ref(item)
            if ref is None:
                continue
            artifact_id = ref.get("id")
            if not isinstance(artifact_id, str) or not artifact_id:
                continue
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            collected.append(ref)
    return collected


def _extract_artifact_ref(item: Any) -> dict[str, Any] | None:
    if isinstance(item, Mapping):
        payload = item.get("artifact")
        if isinstance(payload, Mapping):
            ref_payload: Mapping[str, Any]
            if isinstance(payload.get("artifact"), Mapping):
                ref_payload = payload.get("artifact", {})
            else:
                ref_payload = payload
            return _normalize_artifact_ref(ref_payload)
        return _normalize_artifact_ref(item)
    return None


def _normalize_artifact_ref(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    artifact_id = payload.get("id") or payload.get("artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        return None
    source = payload.get("source")
    if not isinstance(source, Mapping):
        source = {}
    return {
        "id": artifact_id,
        "mime_type": payload.get("mime_type"),
        "size_bytes": payload.get("size_bytes"),
        "filename": payload.get("filename"),
        "sha256": payload.get("sha256"),
        "source": dict(source),
    }


def _format_results_summary(request: ProactiveReportRequest) -> str:
    if request.is_group_report and request.combined_patches:
        group_parts = [f"{len(request.combined_patches)} related tasks completed:"]
        for idx, patch in enumerate(request.combined_patches, 1):
            summary = " ".join(patch.digest or [])
            if patch.facts:
                summary = f"{summary} | Facts: {json.dumps(patch.facts, ensure_ascii=False)}"
            group_parts.append(f"{idx}. {summary or 'Completed'}")
        return "\n".join(group_parts)

    patch = request.patch
    summary_parts: list[str] = []
    if patch.digest:
        summary_parts.append("Summary: " + " ".join(patch.digest))
    if patch.facts:
        summary_parts.append("Facts: " + json.dumps(patch.facts, ensure_ascii=False))
    if patch.artifacts:
        summary_parts.append(f"Artifacts produced: {len(patch.artifacts)} items")
    if patch.recommended_next_steps:
        summary_parts.append("Suggested next steps: " + ", ".join(patch.recommended_next_steps))
    return "\n".join(summary_parts) or "Task completed successfully."


def _collect_ui_component_chunks(metadata: Any) -> list[dict[str, Any]]:
    """Extract ui_component artifact chunks from planner metadata.

    When proactive reporting runs a planner loop, rich UI tools emit `artifact_chunk`
    events that end up stored under trajectory steps as `streams`.

    The Playground session stream doesn't follow planner event streams for these
    proactive runs, so we include the ui_component chunks in the proactive RESULT
    payload for the frontend to render.
    """

    if not isinstance(metadata, Mapping):
        return []
    steps = metadata.get("steps")
    if not isinstance(steps, Sequence):
        return []

    collected: list[dict[str, Any]] = []
    seen: set[str] = set()

    for step in steps:
        if not isinstance(step, Mapping):
            continue
        streams = step.get("streams")
        if not isinstance(streams, Mapping):
            continue
        for stream_id, stream_chunks in streams.items():
            if not isinstance(stream_id, str) or not stream_id:
                continue
            if not isinstance(stream_chunks, Sequence):
                continue
            for item in stream_chunks:
                if not isinstance(item, Mapping):
                    continue
                if item.get("artifact_type") != "ui_component":
                    continue
                chunk_value = item.get("chunk")
                if not isinstance(chunk_value, Mapping):
                    continue
                component = chunk_value.get("component")
                props = chunk_value.get("props")
                if not isinstance(component, str) or not component:
                    continue
                if not isinstance(props, Mapping):
                    continue

                # Dedupe by stable fingerprint of component+props.
                try:
                    dedupe_raw = json.dumps(
                        {"component": component, "props": dict(props)},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                except Exception:
                    dedupe_raw = f"{component}:{repr(props)}"
                if dedupe_raw in seen:
                    continue
                seen.add(dedupe_raw)

                payload: dict[str, Any] = {
                    "stream_id": stream_id,
                    "seq": item.get("seq"),
                    "done": bool(item.get("done", True)),
                    "artifact_type": "ui_component",
                    "chunk": dict(chunk_value),
                }
                meta = item.get("meta")
                if isinstance(meta, Mapping):
                    payload["meta"] = dict(meta)
                ts = item.get("ts")
                if isinstance(ts, (int, float)):
                    payload["ts"] = float(ts)
                collected.append(payload)

    return collected


__all__ = [
    "create_default_proactive_generator",
    "setup_proactive_reporting",
]
