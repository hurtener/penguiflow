"""Prove MLflow-native trace, dataset, and evaluation persistence."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import mlflow
from mlflow.genai.datasets import create_dataset
from mlflow.genai.scorers import scorer

from examples.planner_enterprise_agent_v2.config import AgentConfig
from examples.planner_enterprise_agent_v2.evals.mlflow_trace_dataset_feasibility import (
    dataset_row_to_mlflow_record,
    execution_to_dataset_row,
    trace_to_dataset_row,
)
from examples.planner_enterprise_agent_v2.evals.metrics import policy_metric
from examples.planner_enterprise_agent_v2.main import EnterpriseAgentOrchestrator


@scorer(name="required_answer_facts")
def required_answer_facts(outputs: dict[str, Any], expectations: dict[str, Any]) -> bool:
    answer = str(outputs.get("answer") or "").lower()
    return all(str(fact).lower() in answer for fact in expectations["required_answer_facts"])


def _predict_agent(question: str, llm_context: dict[str, Any], tool_context: dict[str, Any]) -> dict[str, Any]:
    agent = EnterpriseAgentOrchestrator(AgentConfig.from_env())
    result = asyncio.run(
        agent.execute(
            question,
            llm_context=llm_context,
            tenant_id=str(tool_context.get("tenant_id") or "default"),
            user_id=str(tool_context.get("user_id") or "eval-user"),
            tool_context=tool_context,
        )
    )
    case = execution_to_dataset_row(question, result, split="prediction")
    return {
        "answer": result.text,
        "route": result.route,
        "otel_trace_id": result.metadata["otel_trace_id"],
        "trajectory": case["gold_trace"]["trajectory_full"],
    }


@scorer(name="policy_compliance")
def policy_compliance(inputs: dict[str, Any], outputs: dict[str, Any]) -> float:
    """Score fresh prediction trajectory with Enterprise v2 policy metric."""

    scored = policy_metric({"question": str(inputs.get("question") or "")}, None, pred_trace=outputs.get("trajectory"))
    return float(scored["score"])


async def collect_suite_cases(
    suite: Mapping[str, Any],
    execute: Callable[[str], Awaitable[Any]],
) -> list[dict[str, Any]]:
    """Execute each frozen suite query and retain finalized trajectory evidence."""

    queries = suite.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError("query suite must contain at least one query")
    cases: list[dict[str, Any]] = []
    for item in queries:
        if not isinstance(item, Mapping):
            raise ValueError("query suite entries must be mappings")
        query_id = item.get("query_id")
        query = item.get("text")
        split = item.get("split")
        if not all(isinstance(value, str) and value for value in (query_id, query, split)):
            raise ValueError("query suite entries require query_id, text, and split")
        case = execution_to_dataset_row(query, await execute(query), split=split)
        case["example_id"] = query_id
        cases.append(case)
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--trace-id")
    source.add_argument("--query")
    source.add_argument("--suite", type=Path)
    parser.add_argument("--required-fact", action="append")
    args = parser.parse_args()

    mlflow.set_tracking_uri("http://localhost:5000")
    mlflow.set_experiment("Default")
    if args.trace_id:
        trace = mlflow.MlflowClient().get_trace(args.trace_id, display=False)
        cases = [trace_to_dataset_row(trace, split="feasibility")]
    elif args.query:
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.trace import ProxyTracerProvider

        if isinstance(otel_trace.get_tracer_provider(), ProxyTracerProvider):
            otel_trace.set_tracer_provider(TracerProvider())
        agent = EnterpriseAgentOrchestrator(AgentConfig.from_env())
        execution = asyncio.run(agent.execute(args.query))
        cases = [execution_to_dataset_row(args.query, execution, split="feasibility")]
    else:
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.trace import ProxyTracerProvider

        if isinstance(otel_trace.get_tracer_provider(), ProxyTracerProvider):
            otel_trace.set_tracer_provider(TracerProvider())
        suite = json.loads(args.suite.read_text(encoding="utf-8"))
        if not isinstance(suite, Mapping):
            raise ValueError("query suite root must be a mapping")
        agent = EnterpriseAgentOrchestrator(AgentConfig.from_env())
        cases = asyncio.run(collect_suite_cases(suite, agent.execute))

    records = [dataset_row_to_mlflow_record(case) for case in cases]
    for record in records:
        record["tags"]["case_type"] = "conversation_snapshot"
        if args.required_fact:
            record["expectations"]["required_answer_facts"] = args.required_fact

    dataset = create_dataset(name=f"penguiflow-learning-plane-{datetime.now(UTC):%Y%m%d%H%M%S}")
    dataset.merge_records(records)
    scorers = [policy_compliance] if args.suite else [required_answer_facts]
    result = mlflow.genai.evaluate(
        data=dataset,
        predict_fn=_predict_agent,
        scorers=scorers,
    )
    print(
        json.dumps(
            {
                "dataset_id": dataset.dataset_id,
                "evaluation_run_id": result.run_id,
                "metrics": result.metrics,
                "source_cases": [
                    {
                        "otel_trace_id": case["gold_trace"]["provenance"].get("otel_trace_id"),
                        "query_id": case["example_id"],
                        "split": case["split"],
                        "steps": len(case["gold_trace"]["trajectory_full"]["steps"]),
                    }
                    for case in cases
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
