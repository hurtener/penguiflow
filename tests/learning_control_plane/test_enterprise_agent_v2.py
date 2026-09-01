from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from examples.planner_enterprise_agent_v2.config import AgentConfig
from examples.planner_enterprise_agent_v2.learning_control_plane import (
    EnterprisePolicyComplianceRunner,
    enterprise_policy_metric,
    load_policy_compliance_dataset,
)
from learning_control_plane.evaluation import EvaluationCase, EvaluationVariant
from penguiflow.planner.models import PlannerAction
from penguiflow.planner.trajectory import Trajectory, TrajectoryStep


def test_load_policy_compliance_dataset_keeps_the_held_out_test_split(tmp_path: Path) -> None:
    suite_path = tmp_path / "query_suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "suite_id": "enterprise-policy-v1",
                "queries": [
                    {"query_id": "validation", "text": "A validation request", "split": "val"},
                    {"query_id": "held-out", "text": "A held out request", "split": "test"},
                ],
            }
        )
    )

    dataset = load_policy_compliance_dataset(suite_path)

    assert dataset.version == "enterprise-policy-v1:test"
    assert [case.case_id for case in dataset.cases] == ["held-out"]


@pytest.mark.asyncio
async def test_runner_only_adds_a_temporary_skill_to_the_candidate() -> None:
    received_skills: list[bool] = []

    class FakeAgent:
        async def execute(self, query: str, **_: Any) -> dict[str, str]:
            return {"answer": query}

    def build_agent(skills: Any, provider: Any, callback: Callable[[Trajectory], None]) -> FakeAgent:
        received_skills.append(skills is not None and provider is not None)
        callback(
            Trajectory(
                query="test",
                finish_reason="answer_complete",
                steps=[TrajectoryStep(action=PlannerAction(next_node="triage_query"))],
            )
        )
        return FakeAgent()

    runner = EnterprisePolicyComplianceRunner(AgentConfig.from_env(), agent_factory=build_agent)
    case = EvaluationCase(case_id="pc-003", inputs={"query": "Give team communication guidance"})

    baseline = await runner(case, EvaluationVariant(variant_id="baseline"))
    candidate = await runner(case, EvaluationVariant(variant_id="candidate", advisory_skill="Use the policy."))

    assert received_skills == [False, True]
    assert baseline.trace["steps"][0]["action"]["next_node"] == "triage_query"
    assert candidate.answer == {"answer": "Give team communication guidance"}


def test_enterprise_policy_metric_uses_the_existing_trace_based_policy() -> None:
    output = type("Output", (), {"answer": {"text": "Guidance"}, "trace": {"steps": [
        {"action": {"next_node": "triage_query"}},
        {"action": {"next_node": "answer_general"}},
    ]}})()
    case = EvaluationCase(case_id="pc-003", inputs={"query": "Need communication norms"})

    assert enterprise_policy_metric(case, output)["policy_compliance"] == 1.0
