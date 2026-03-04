from __future__ import annotations

import asyncio
import json

import pytest

from penguiflow.evals.analyze import run_analyze_only


@pytest.mark.asyncio
async def test_run_analyze_only_writes_metrics_and_aggregate_report(tmp_path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    rows = [
        {
            "trace_id": "t1",
            "outputs": {"status": "ok"},
            "trajectory": {"split": "val"},
            "events": {
                "flow_events": [{"kind": "node_succeeded"}],
                "planner_events": [
                    {"latency_ms": 100.0, "event_type": "step_complete"},
                    {"latency_ms": 200.0, "event_type": "step_complete"},
                ],
            },
        },
        {
            "trace_id": "t2",
            "outputs": {"status": "error"},
            "trajectory": {"split": "test"},
            "events": {
                "flow_events": [{"kind": "node_error"}],
                "planner_events": [
                    {"latency_ms": 300.0, "event_type": "step_complete"},
                ],
            },
        },
    ]
    trace_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    result = await run_analyze_only(trace_path=trace_path, output_dir=tmp_path)

    assert result["trace_count"] == 2
    assert (tmp_path / "metrics.jsonl").exists()
    report = json.loads((tmp_path / "report.analyze.json").read_text(encoding="utf-8"))
    assert report["success_rate"] == pytest.approx(0.5)
    assert report["tool_failure_rate"] == pytest.approx(0.5)
    assert report["latency_ms"]["p50"] == pytest.approx(200.0)
    assert report["latency_ms"]["p95"] == pytest.approx(300.0)


@pytest.mark.asyncio
async def test_run_analyze_only_offloads_file_io_with_to_thread(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        json.dumps(
            {
                "trace_id": "t1",
                "outputs": {"status": "ok"},
                "trajectory": {"split": "val"},
                "events": {"flow_events": [], "planner_events": []},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    calls = 0
    original_to_thread = asyncio.to_thread

    async def _to_thread(func, /, *args, **kwargs):
        nonlocal calls
        calls += 1
        return await original_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    await run_analyze_only(trace_path=trace_path, output_dir=tmp_path)

    assert calls > 0
