"""Trace dataset export helpers for eval workflows."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from penguiflow.planner import PlannerEvent, Trajectory


def _infer_status_from_history(history: list[Any]) -> str:
    kinds = {getattr(item, "kind", "") for item in history}
    if {"node_error", "node_failed", "error", "node_timeout"} & kinds:
        return "error"
    if history:
        return "ok"
    return "unknown"


def _infer_split_from_tags(tags: list[str]) -> str:
    for tag in tags:
        if tag.startswith("split:"):
            return tag.split(":", 1)[1]
    return "unknown"


def _planner_event_payloads(events: list[PlannerEvent]) -> list[dict[str, Any]]:
    return [
        {
            "event_type": event.event_type,
            "ts": event.ts,
            "trajectory_step": event.trajectory_step,
            "node_name": event.node_name,
            "latency_ms": event.latency_ms,
            "error": event.error,
            "extra": dict(event.extra),
        }
        for event in events
    ]


def _infer_status(*, trajectory: Trajectory | None, history: list[Any]) -> str:
    if trajectory is not None:
        for step in trajectory.steps:
            if step.error:
                return "error"
        return "ok"
    return _infer_status_from_history(history)


def _build_trace_example(
    *,
    trace_id: str,
    trajectory: Trajectory | None,
    planner_events: list[PlannerEvent],
    history: list[Any],
    redaction_profile: str,
    source_priority: str,
) -> dict[str, Any]:
    """Build portable trace export row with maximal trajectory detail.

    Why: downstream metric design and offline debugging need access to step-level
    observations/actions whenever the state store can provide a trajectory.
    """

    tags: list[str] = []
    if trajectory is not None and isinstance(trajectory.metadata.get("tags"), list):
        tags = [str(tag) for tag in trajectory.metadata["tags"]]

    trajectory_full = trajectory.serialise() if trajectory is not None else None

    return {
        "schema_version": "TraceExampleV1",
        "trace_id": trace_id,
        "query": trajectory.query if trajectory is not None else None,
        "inputs": {
            "llm_context": dict(trajectory.llm_context or {}) if trajectory is not None else {},
            "tool_context": dict(trajectory.tool_context or {}) if trajectory is not None else {},
        },
        "outputs": {
            "status": _infer_status(trajectory=trajectory, history=history),
            "final": None,
            "error": None,
        },
        "trajectory": {
            "metadata": dict(trajectory.metadata) if trajectory is not None else {},
            "steps": len(trajectory.steps) if trajectory is not None else 0,
            "summary": trajectory.summary.model_dump(mode="json") if trajectory and trajectory.summary else None,
            "tags": tags,
            "split": _infer_split_from_tags(tags),
        },
        "trajectory_full": trajectory_full,
        "events": {
            "flow_events": [
                {
                    "ts": getattr(event, "ts", None),
                    "kind": getattr(event, "kind", None),
                    "node_name": getattr(event, "node_name", None),
                }
                for event in history
            ],
            "planner_events": _planner_event_payloads(planner_events) if planner_events else None,
        },
        "redaction": {
            "profile": redaction_profile,
            "rules_version": "v1",
            "fields_included": [
                "trace_id",
                "outputs.status",
                "events.flow_events",
                "inputs.llm_context",
                "inputs.tool_context",
                "trajectory.metadata",
                "trajectory.summary",
                "trajectory.tags",
                "trajectory.split",
                "trajectory.steps",
                "trajectory_full",
            ],
            "fields_omitted": [],
        },
        "provenance": {
            "exported_at": datetime.now(UTC).isoformat(),
            "exporter": "penguiflow.evals.export",
            "state_store": {"source_priority": source_priority},
        },
    }


def _split_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {"total": len(rows)}
    for row in rows:
        split = row.get("trajectory", {}).get("split", "unknown")
        key = str(split)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _build_manifest(
    *,
    rows: list[dict[str, Any]],
    trace_ids: list[str],
    session_id: str | None,
    redaction_profile: str,
    workload: str,
) -> dict[str, Any]:
    return {
        "dataset_id": f"eval-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
        "schema_versions": {"trace": "TraceExampleV1"},
        "workload": workload,
        "source": {
            "trace_ids": list(trace_ids),
            "session_id": session_id,
            "source_priority": ["trajectory", "planner_events", "history"],
        },
        "counts": _split_counts(rows),
        "redaction_policy": redaction_profile,
        "export_command": "export_trace_dataset",
    }


def _normalize_trace_refs(
    *,
    trace_ids: list[str],
    trace_refs: list[dict[str, str]] | None,
    session_id: str | None,
) -> list[dict[str, str]]:
    if trace_refs is not None:
        return [
            {
                "trace_id": str(ref.get("trace_id", "")),
                "session_id": str(ref.get("session_id", "")) if ref.get("session_id") is not None else "",
            }
            for ref in trace_refs
            if isinstance(ref, dict) and ref.get("trace_id")
        ]
    return [
        {
            "trace_id": trace_id,
            "session_id": session_id or "",
        }
        for trace_id in trace_ids
    ]


async def export_trace_dataset(
    *,
    state_store: Any,
    trace_ids: list[str],
    output_dir: str | Path,
    session_id: str | None = None,
    redaction_profile: str = "internal_safe",
    trace_refs: list[dict[str, str]] | None = None,
    workload: str | None = None,
) -> dict[str, Any]:
    """Export TraceExample rows plus manifest for eval workflows.

    Why: workload identity should be provided by the caller (typically
    ``agent_package``) so exported manifests are reusable across projects and
    never depend on example-specific hardcoded values.
    """

    rows: list[dict[str, Any]] = []
    normalized_refs = _normalize_trace_refs(trace_ids=trace_ids, trace_refs=trace_refs, session_id=session_id)
    for ref in normalized_refs:
        trace_id = str(ref["trace_id"])
        trace_session_id = ref.get("session_id") or None
        trajectory: Trajectory | None = None
        if trace_session_id is not None and hasattr(state_store, "get_trajectory"):
            trajectory = await state_store.get_trajectory(trace_id, trace_session_id)

        planner_events: list[PlannerEvent] = []
        if hasattr(state_store, "list_planner_events"):
            planner_events = list(await state_store.list_planner_events(trace_id))

        history = list(await state_store.load_history(trace_id))

        source_priority = "history"
        if planner_events:
            source_priority = "planner_events"
        if trajectory is not None:
            source_priority = "trajectory"

        rows.append(
            _build_trace_example(
                trace_id=trace_id,
                trajectory=trajectory,
                planner_events=planner_events,
                history=history,
                redaction_profile=redaction_profile,
                source_priority=source_priority,
            )
        )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    trace_path = out_dir / "trace.jsonl"
    with trace_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    manifest = _build_manifest(
        rows=rows,
        trace_ids=[str(ref["trace_id"]) for ref in normalized_refs],
        session_id=session_id,
        redaction_profile=redaction_profile,
        workload=str(workload or "unknown"),
    )
    manifest["source"]["trace_refs"] = normalized_refs
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")

    return {
        "trace_count": len(rows),
        "trace_path": str(trace_path),
        "manifest_path": str(manifest_path),
    }


__all__ = ["export_trace_dataset"]
