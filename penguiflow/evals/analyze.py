"""Analyze-only deterministic metrics for exported trace datasets."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def _quantile_nearest_rank(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return float(ordered[rank - 1])


def _row_metric(row: dict[str, Any]) -> dict[str, Any]:
    flow_events = row.get("events", {}).get("flow_events") or []
    planner_events = row.get("events", {}).get("planner_events") or []
    latency_values = [
        float(event["latency_ms"])
        for event in planner_events
        if isinstance(event, dict) and isinstance(event.get("latency_ms"), (int, float))
    ]
    has_tool_failure = any(
        isinstance(event, dict) and event.get("kind") in {"node_error", "node_timeout", "node_failed"}
        for event in flow_events
    )
    success = row.get("outputs", {}).get("status") == "ok"
    return {
        "trace_id": row.get("trace_id"),
        "split": row.get("trajectory", {}).get("split", "unknown"),
        "success": success,
        "tool_failure": has_tool_failure,
        "latency_values": latency_values,
    }


async def run_analyze_only(*, trace_path: str | Path, output_dir: str | Path) -> dict[str, Any]:
    """Compute deterministic diagnostics from ``trace.jsonl`` exports."""

    trace_rows: list[dict[str, Any]] = []
    for line in Path(trace_path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            trace_rows.append(json.loads(stripped))

    metric_rows = [_row_metric(row) for row in trace_rows]
    latencies = [value for row in metric_rows for value in row["latency_values"]]
    total = len(metric_rows)

    report = {
        "trace_count": total,
        "success_rate": (sum(1 for row in metric_rows if row["success"]) / total) if total else 0.0,
        "tool_failure_rate": (sum(1 for row in metric_rows if row["tool_failure"]) / total) if total else 0.0,
        "latency_ms": {
            "p50": _quantile_nearest_rank(latencies, 0.5),
            "p95": _quantile_nearest_rank(latencies, 0.95),
        },
        "cost_summary": None,
    }

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    metrics_path = out_dir / "metrics.jsonl"
    with metrics_path.open("w", encoding="utf-8") as handle:
        for row in metric_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    report_path = out_dir / "report.analyze.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")

    return {
        "trace_count": total,
        "metrics_path": str(metrics_path),
        "report_path": str(report_path),
    }


__all__ = ["run_analyze_only"]
