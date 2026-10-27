"""Project one MLflow root trace into replayable PenguiFlow evaluation data."""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from penguiflow.evals.api import evaluate_dataset_from_spec_file
from penguiflow.planner import Trajectory

_EPHEMERAL_TOOL_CONTEXT_KEYS = {
    "status_publisher",
    "status_logger",
    "telemetry",
    "trace_id",
    "session_id",
}


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"MLflow trace {field} must be a mapping")
    return dict(value)


def _field(value: object, name: str, default: object = None) -> object:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _root_span(trace: object) -> object:
    spans = _field(_field(trace, "data"), "spans", [])
    if not isinstance(spans, list):
        raise ValueError("MLflow trace has no spans")
    for span in spans:
        if _field(span, "parent_id") is None:
            return span
    raise ValueError("MLflow trace has no root span")


def _answer(payload: dict[str, Any]) -> str | None:
    for key in ("raw_answer", "answer", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    extra = payload.get("extra")
    if isinstance(extra, dict):
        value = extra.get("answer")
        if isinstance(value, str):
            return value
    return None


def _safe_tool_context(tool_context: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in tool_context.items() if key not in _EPHEMERAL_TOOL_CONTEXT_KEYS}


def _replay_llm_context(inputs: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    """Use context after planner memory hydration, excluding runtime progress."""

    effective = metadata.get("llm_context")
    context = _mapping(effective, field="root output metadata.llm_context") if effective is not None else _mapping(
        inputs.get("llm_context", {}), field="root llm_context"
    )
    context.pop("status_history", None)
    return context


def trace_to_dataset_row(trace: object, *, split: str) -> dict[str, Any]:
    """Project an MLflow root trace into current PenguiFlow dataset row shape."""

    root = _root_span(trace)
    inputs = _mapping(_field(root, "inputs"), field="root inputs")
    outputs = _mapping(_field(root, "outputs"), field="root outputs")
    query = inputs.get("query")
    if not isinstance(query, str) or not query:
        raise ValueError("MLflow root trace has no query")

    raw_tool_context = _mapping(inputs.get("tool_context", {}), field="root tool_context")
    tool_context = _safe_tool_context(raw_tool_context)
    metadata = _mapping(outputs.get("metadata", {}), field="root output metadata")
    llm_context = _replay_llm_context(inputs, metadata)
    payload = _mapping(outputs.get("payload", {}), field="root output payload")
    reason = str(outputs.get("reason") or metadata.get("reason") or "unknown")
    answer = _answer(payload)
    steps = metadata.get("steps", [])
    if not isinstance(steps, list):
        raise ValueError("MLflow root output metadata.steps must be a list")

    trace_id = str(_field(_field(trace, "info"), "trace_id") or "")
    if not trace_id:
        raise ValueError("MLflow trace has no trace_id")
    penguiflow_trace_id = str(raw_tool_context.get("trace_id") or trace_id)
    trajectory_metadata = {
        key: value
        for key, value in metadata.items()
        if key not in {"steps", "llm_context", "tool_context", "summary", "finish_reason", "final_answer"}
    }
    trajectory_full = {
        "query": query,
        "llm_context": llm_context,
        "tool_context": tool_context,
        "input_parts": inputs.get("input_parts", []),
        "artifacts": metadata.get("artifacts", {}),
        "sources": metadata.get("sources", []),
        "metadata": trajectory_metadata,
        "steps": steps,
        "summary": metadata.get("summary"),
        "hint_state": metadata.get("hint_state", {}),
        "resume_user_input": metadata.get("resume_user_input"),
        "steering_inputs": metadata.get("steering_inputs", []),
        "background_results": metadata.get("background_results", {}),
        "final_answer": answer,
        "finish_reason": reason,
    }
    trace_state = str(_field(_field(trace, "info"), "state", "OK"))
    status = "ok" if reason == "answer_complete" and trace_state == "OK" else "error"
    gold_trace = {
        "schema_version": "TraceExampleV1",
        "trace_id": penguiflow_trace_id,
        "query": query,
        "inputs": {"llm_context": llm_context, "tool_context": tool_context},
        "outputs": {"status": status, "final": answer, "error": None},
        "trajectory": {
            "metadata": trajectory_metadata,
            "steps": len(steps),
            "summary": metadata.get("summary"),
            "tags": [],
            "split": split,
            "finish_reason": reason,
        },
        "trajectory_full": trajectory_full,
        "events": {"flow_events": [], "planner_events": None},
        "redaction": {
            "profile": "none-feasibility-only",
            "rules_version": "v1",
            "fields_included": ["root.inputs", "root.outputs", "root.outputs.metadata.steps"],
            "fields_omitted": list(_EPHEMERAL_TOOL_CONTEXT_KEYS),
        },
        "provenance": {
            "exported_at": datetime.now(UTC).isoformat(),
            "exporter": "planner_enterprise_agent_v2.mlflow_trace_dataset_feasibility",
            "mlflow_trace_id": trace_id,
        },
    }
    return {
        "example_id": trace_id,
        "split": split,
        "question": query,
        "gold_trace": gold_trace,
        "__pf.trace_id": penguiflow_trace_id,
    }


def execution_to_dataset_row(query: str, result: object, *, split: str) -> dict[str, Any]:
    """Project finalized Enterprise v2 execution evidence without reading MLflow traces."""

    metadata = _mapping(_field(result, "metadata", {}), field="result.metadata")
    planner = _mapping(metadata.get("planner"), field="result.metadata.planner")
    llm_context = _mapping(planner.get("llm_context", {}), field="planner.llm_context")
    raw_tool_context = _mapping(planner.get("tool_context", {}), field="planner.tool_context")
    tool_context = _safe_tool_context(raw_tool_context)
    steps = planner.get("steps", [])
    if not isinstance(steps, list):
        raise ValueError("planner.steps must be a list")
    otel_trace_id = str(metadata.get("otel_trace_id") or "")
    penguiflow_trace_id = str(metadata.get("trace_id") or otel_trace_id)
    if not otel_trace_id:
        raise ValueError("finalized execution has no otel_trace_id")

    answer = _field(result, "text")
    if not isinstance(answer, str):
        raise ValueError("finalized execution has no text answer")
    reason = str(planner.get("reason") or "unknown")
    trajectory_metadata = {
        key: value
        for key, value in planner.items()
        if key not in {"steps", "llm_context", "tool_context", "summary", "finish_reason", "final_answer"}
    }
    trajectory_full = {
        "query": query,
        "llm_context": llm_context,
        "tool_context": tool_context,
        "input_parts": [],
        "artifacts": _field(result, "artifacts", {}),
        "sources": [],
        "metadata": trajectory_metadata,
        "steps": steps,
        "summary": None,
        "hint_state": {},
        "resume_user_input": None,
        "steering_inputs": [],
        "background_results": {},
        "final_answer": answer,
        "finish_reason": reason,
    }
    gold_trace = {
        "schema_version": "TraceExampleV1",
        "trace_id": penguiflow_trace_id,
        "query": query,
        "inputs": {"llm_context": llm_context, "tool_context": tool_context},
        "outputs": {"status": "ok" if reason == "answer_complete" else "error", "final": answer, "error": None},
        "trajectory": {
            "metadata": trajectory_metadata,
            "steps": len(steps),
            "summary": None,
            "tags": [],
            "split": split,
            "finish_reason": reason,
        },
        "trajectory_full": trajectory_full,
        "events": {"flow_events": [], "planner_events": None},
        "redaction": {
            "profile": "none-feasibility-only",
            "rules_version": "v1",
            "fields_included": ["finalized_execution"],
            "fields_omitted": list(_EPHEMERAL_TOOL_CONTEXT_KEYS),
        },
        "provenance": {
            "exporter": "planner_enterprise_agent_v2.finalized_execution",
            "otel_trace_id": otel_trace_id,
        },
    }
    return {
        "example_id": otel_trace_id,
        "split": split,
        "question": query,
        "gold_trace": gold_trace,
        "__pf.trace_id": penguiflow_trace_id,
    }


def dataset_row_to_mlflow_record(case: dict[str, Any]) -> dict[str, Any]:
    """Render a replayable case as one MLflow evaluation dataset record."""

    gold_trace = _mapping(case.get("gold_trace"), field="gold_trace")
    inputs = _mapping(gold_trace.get("inputs"), field="gold_trace.inputs")
    provenance = _mapping(gold_trace.get("provenance"), field="gold_trace.provenance")
    return {
        "inputs": {
            "question": case["question"],
            "llm_context": inputs["llm_context"],
            "tool_context": inputs["tool_context"],
        },
        "outputs": {"observed_answer": _mapping(gold_trace.get("outputs"), field="gold_trace.outputs").get("final")},
        "expectations": {"penguiflow_gold_trace": gold_trace},
        "tags": {"split": str(case["split"]), "penguiflow_schema": "TraceExampleV1"},
        "source": {
            "source_type": "TRACE",
            "source_data": {
                "trace_id": provenance.get("mlflow_trace_id") or provenance["otel_trace_id"],
                "penguiflow_trace_id": case["__pf.trace_id"],
            },
        },
    }


def evaluation_cases(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Make val/test copies solely to satisfy current harness split requirements."""

    cases: list[dict[str, Any]] = []
    for split in ("val", "test"):
        copied = deepcopy(case)
        copied["example_id"] = f"{case['example_id']}-{split}"
        copied["split"] = split
        copied["gold_trace"]["trajectory"]["split"] = split
        cases.append(copied)
    return cases


def mlflow_record_to_dataset_row(record: object) -> dict[str, Any]:
    """Recover current PenguiFlow dataset row from an MLflow dataset record."""

    inputs = _mapping(_field(record, "inputs"), field="record.inputs")
    expectations = _mapping(_field(record, "expectations"), field="record.expectations")
    gold_trace = _mapping(expectations.get("penguiflow_gold_trace"), field="record expectations gold trace")
    tags = _mapping(_field(record, "tags", {}), field="record.tags")
    source_data = _mapping(_field(_field(record, "source"), "source_data"), field="record.source.source_data")
    return {
        "example_id": str(source_data.get("trace_id") or source_data["otel_trace_id"]),
        "split": str(tags.get("split", "unknown")),
        "question": inputs["question"],
        "gold_trace": gold_trace,
        "__pf.trace_id": str(source_data.get("penguiflow_trace_id") or gold_trace["trace_id"]),
    }


def _latest_enterprise_trace() -> object:
    from mlflow import MlflowClient

    client = MlflowClient("http://localhost:5000")
    experiment = client.get_experiment_by_name("Default")
    if experiment is None:
        raise ValueError("MLflow Default experiment does not exist")
    traces = client.search_traces(experiment_ids=[experiment.experiment_id], max_results=100, include_spans=True)
    for trace in traces:
        tags = _field(_field(trace, "info"), "tags", {})
        if isinstance(tags, dict) and tags.get("mlflow.traceName") == "enterprise_agent_v2":
            if str(_field(_field(trace, "info"), "state", "")) == "OK":
                return trace
    raise ValueError("no successful enterprise_agent_v2 trace found")


async def _run_eval(spec_path: Path) -> dict[str, Any]:
    return await evaluate_dataset_from_spec_file(spec_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-id")
    parser.add_argument("--split", default="test")
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/penguiflow-mlflow-trace-feasibility"))
    parser.add_argument("--run-eval", action="store_true")
    args = parser.parse_args()

    import mlflow

    mlflow.set_tracking_uri("http://localhost:5000")

    if args.trace_id:
        from mlflow import MlflowClient

        trace = MlflowClient("http://localhost:5000").get_trace(args.trace_id, display=False)
    else:
        trace = _latest_enterprise_trace()
    case = trace_to_dataset_row(trace, split=args.split)
    Trajectory.from_serialised(case["gold_trace"]["trajectory_full"])

    args.output_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = args.output_dir / "dataset.jsonl"
    jsonl_cases = evaluation_cases(case) if args.run_eval else [case]
    dataset_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in jsonl_cases),
        encoding="utf-8",
    )

    from mlflow.genai.datasets import create_dataset

    dataset_name = f"penguiflow-trace-feasibility-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    dataset = create_dataset(name=dataset_name)
    dataset.merge_records([dataset_row_to_mlflow_record(case)])
    records = dataset.to_df().to_dict(orient="records")
    if len(records) != 1:
        raise ValueError(f"expected one MLflow dataset record, got {len(records)}")
    round_trip = mlflow_record_to_dataset_row(records[0])
    Trajectory.from_serialised(round_trip["gold_trace"]["trajectory_full"])

    result: dict[str, Any] = {"dataset_path": str(dataset_path), "mlflow_dataset_id": dataset.dataset_id}
    if args.run_eval:
        spec_path = args.output_dir / "evaluate.spec.json"
        spec_path.write_text(
            json.dumps(
                {
                    "project_root": "examples",
                    "dataset_path": str(dataset_path),
                    "metric_spec": "examples.planner_enterprise_agent_v2.evals.metrics:policy_metric",
                    "output_dir": str(args.output_dir / "artifacts"),
                    "agent_package": "planner_enterprise_agent_v2",
                }
            ),
            encoding="utf-8",
        )
        result["eval"] = asyncio.run(_run_eval(spec_path))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
