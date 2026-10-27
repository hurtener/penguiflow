"""Feasibility coverage for projecting MLflow traces into PenguiFlow eval cases."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from examples.planner_enterprise_agent_v2.evals import mlflow_learning_plane_feasibility as learning_feasibility
from examples.planner_enterprise_agent_v2.evals import mlflow_trace_dataset_feasibility as feasibility
from examples.planner_enterprise_agent_v2.nodes import FinalAnswer
from penguiflow.planner import Trajectory


def _trace() -> SimpleNamespace:
    root = SimpleNamespace(
        parent_id=None,
        inputs={
            "query": "What is the status of the API service?",
            "llm_context": {"memories": [{"role": "user", "content": "API deploy"}]},
            "tool_context": {
                "tenant_id": "tenant-1",
                "session_id": "source-session",
                "trace_id": "pf-source-trace",
                "status_publisher": "<function>",
                "telemetry": "<AgentTelemetry>",
            },
        },
        outputs={
            "reason": "answer_complete",
            "payload": {"raw_answer": "The API service is healthy."},
            "metadata": {
                "steps": [
                    {"action": {"next_node": "triage_query", "args": {}, "thought": "route"}},
                    {"action": {"next_node": "answer_general", "args": {}, "thought": "answer"}},
                ],
                "cost": {"total_cost_usd": 0.01},
            },
        },
    )
    return SimpleNamespace(
        info=SimpleNamespace(trace_id="mlflow-trace-1", state="OK"),
        data=SimpleNamespace(spans=[root]),
    )


def test_trace_projects_to_replayable_case_and_mlflow_record() -> None:
    case = feasibility.trace_to_dataset_row(_trace(), split="test")

    assert case["example_id"] == "mlflow-trace-1"
    assert case["split"] == "test"
    assert case["question"] == "What is the status of the API service?"
    assert case["gold_trace"]["inputs"]["tool_context"] == {"tenant_id": "tenant-1"}
    assert case["gold_trace"]["outputs"] == {
        "status": "ok",
        "final": "The API service is healthy.",
        "error": None,
    }

    trajectory = Trajectory.from_serialised(case["gold_trace"]["trajectory_full"])
    assert [step.action.next_node for step in trajectory.steps] == ["triage_query", "answer_general"]

    record = feasibility.dataset_row_to_mlflow_record(case)
    sdk_record = SimpleNamespace(
        **{key: value for key, value in record.items() if key != "source"},
        source=SimpleNamespace(source_data=record["source"]["source_data"]),
    )
    round_trip = feasibility.mlflow_record_to_dataset_row(sdk_record)
    assert round_trip["question"] == case["question"]
    assert round_trip["gold_trace"]["inputs"] == case["gold_trace"]["inputs"]
    assert round_trip["gold_trace"]["trajectory_full"] == case["gold_trace"]["trajectory_full"]


def test_evaluation_cases_adds_required_validation_split() -> None:
    source_case = feasibility.trace_to_dataset_row(_trace(), split="test")
    cases = feasibility.evaluation_cases(source_case)

    assert [case["split"] for case in cases] == ["val", "test"]
    assert cases[0]["example_id"] == "mlflow-trace-1-val"
    assert cases[1]["example_id"] == "mlflow-trace-1-test"
    assert cases[0]["question"] == source_case["question"]


def test_finalized_execution_projects_to_replayable_case_without_mlflow_trace() -> None:
    result = FinalAnswer(
        text="The API service is healthy.",
        route="general",
        metadata={
            "trace_id": "pf-source-trace",
            "otel_trace_id": "0123456789abcdef0123456789abcdef",
            "planner": {
                "reason": "answer_complete",
                "llm_context": {"memories": [{"role": "user", "content": "API deploy"}]},
                "tool_context": {"tenant_id": "tenant-1", "status_publisher": "<function>"},
                "steps": [
                    {"action": {"next_node": "triage_query", "args": {}, "thought": "route"}},
                    {"action": {"next_node": "answer_general", "args": {}, "thought": "answer"}},
                ],
            },
        },
    )

    case = feasibility.execution_to_dataset_row("What is API status?", result, split="test")

    assert case["example_id"] == "0123456789abcdef0123456789abcdef"
    assert case["gold_trace"]["outputs"] == {
        "status": "ok",
        "final": "The API service is healthy.",
        "error": None,
    }
    assert case["gold_trace"]["inputs"]["tool_context"] == {"tenant_id": "tenant-1"}
    assert case["gold_trace"]["provenance"] == {
        "exporter": "planner_enterprise_agent_v2.finalized_execution",
        "otel_trace_id": "0123456789abcdef0123456789abcdef",
    }
    trajectory = Trajectory.from_serialised(case["gold_trace"]["trajectory_full"])
    assert [step.action.next_node for step in trajectory.steps] == ["triage_query", "answer_general"]


def test_collect_suite_cases_preserves_query_id_split_and_trajectory() -> None:
    async def execute(query: str) -> FinalAnswer:
        return FinalAnswer(
            text=f"answer for {query}",
            route="general",
            metadata={
                "trace_id": f"pf-{query}",
                "otel_trace_id": f"otel-{query}",
                "planner": {
                    "reason": "answer_complete",
                    "llm_context": {},
                    "tool_context": {},
                    "steps": [{"action": {"next_node": "triage_query", "args": {}, "thought": "route"}}],
                },
            },
        )

    cases = asyncio.run(
        learning_feasibility.collect_suite_cases(
            {
                "queries": [
                    {"query_id": "pc-001", "text": "first", "split": "val"},
                    {"query_id": "pc-002", "text": "second", "split": "test"},
                ]
            },
            execute,
        )
    )

    assert [(case["example_id"], case["split"]) for case in cases] == [("pc-001", "val"), ("pc-002", "test")]
    assert all(case["gold_trace"]["trajectory_full"]["steps"] for case in cases)


def test_predict_agent_returns_fresh_trajectory(monkeypatch) -> None:
    class FakeAgent:
        async def execute(self, query: str, **_kwargs) -> FinalAnswer:
            return FinalAnswer(
                text="answer",
                route="bug",
                metadata={
                    "trace_id": "pf-prediction",
                    "otel_trace_id": "otel-prediction",
                    "planner": {
                        "reason": "answer_complete",
                        "llm_context": {},
                        "tool_context": {},
                        "steps": [{"action": {"next_node": "recommend_fix", "args": {}, "thought": query}}],
                    },
                },
            )

    monkeypatch.setattr(learning_feasibility, "EnterpriseAgentOrchestrator", lambda _config: FakeAgent())

    output = learning_feasibility._predict_agent("Bug report", {}, {})

    assert output["answer"] == "answer"
    assert output["otel_trace_id"] == "otel-prediction"
    assert output["trajectory"]["steps"][0]["action"]["next_node"] == "recommend_fix"
