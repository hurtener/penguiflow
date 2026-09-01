"""Learning-control-plane adapter for the enterprise planner policy suite."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from examples.planner_enterprise_agent_v2.config import AgentConfig
from examples.planner_enterprise_agent_v2.evals.metrics import policy_metric
from examples.planner_enterprise_agent_v2.main import EnterpriseAgentOrchestrator
from learning_control_plane.evaluation import EvaluationCase, EvaluationDataset, EvaluationVariant
from penguiflow.planner.trajectory import Trajectory
from penguiflow.skills import LocalSkillProvider, LocalSkillStore, SkillDefinition, SkillProvider, SkillsConfig


class EnterpriseAgent(Protocol):
    """The small portion of the enterprise agent required for offline evaluation."""

    def execute(
        self,
        query: str,
        *,
        tenant_id: str = "default",
        memories: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
        tool_context: Mapping[str, Any] | None = None,
    ) -> Awaitable[Any]:
        ...


AgentFactory = Callable[[SkillsConfig | None, SkillProvider | None, Callable[[Trajectory], None]], EnterpriseAgent]


@dataclass(frozen=True, slots=True)
class EnterpriseEvaluationOutput:
    """The answer and planner path needed by the policy-compliance metric."""

    answer: Any
    trace: Mapping[str, Any]


def load_policy_compliance_dataset(path: str | Path, *, split: str = "test") -> EvaluationDataset:
    """Load one immutable split of the enterprise agent's named policy suite."""

    suite_path = Path(path)
    payload = json.loads(suite_path.read_text())
    suite_id = str(payload["suite_id"])
    cases = tuple(
        EvaluationCase(
            case_id=str(query["query_id"]),
            inputs={"query": str(query["text"]), "tenant_id": "evaluation"},
            expected={"split": split},
        )
        for query in payload["queries"]
        if query.get("split") == split
    )
    if not cases:
        raise ValueError(f"policy suite {suite_id!r} has no {split!r} cases")

    return EvaluationDataset(dataset_id=suite_id, version=f"{suite_id}:{split}", cases=cases)


def enterprise_policy_metric(case: EvaluationCase, output: EnterpriseEvaluationOutput) -> Mapping[str, float]:
    """Score one enterprise run with its existing deterministic policy metric."""

    result = policy_metric(
        {"question": case.inputs["query"]},
        output.answer,
        pred_trace=output.trace,
    )
    return {"policy_compliance": float(result["score"])}


class EnterprisePolicyComplianceRunner:
    """Run the enterprise agent with either no skill or one isolated advisory skill."""

    def __init__(self, config: AgentConfig, *, agent_factory: AgentFactory | None = None) -> None:
        self._config = config
        self._agent_factory = agent_factory or self._build_agent

    async def __call__(self, case: EvaluationCase, variant: EvaluationVariant) -> EnterpriseEvaluationOutput:
        """Create a fresh agent, run one named request, and retain its planner path."""

        query = case.inputs.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("enterprise evaluation cases require a non-empty inputs.query")

        tenant_id = str(case.inputs.get("tenant_id") or "evaluation")
        trace: Trajectory | None = None

        def capture_trajectory(completed: Trajectory) -> None:
            nonlocal trace
            trace = completed

        with TemporaryDirectory(prefix="penguiflow-lcp-eval-") as directory:
            skills, provider = self._candidate_skills(variant, Path(directory))
            agent = self._agent_factory(skills, provider, capture_trajectory)
            answer = await agent.execute(
                query,
                tenant_id=tenant_id,
                tool_context={"trace_id": f"lcp-eval-{case.case_id}-{variant.variant_id}"},
            )

        if trace is None:
            raise RuntimeError("enterprise agent did not publish a completed trajectory")
        return EnterpriseEvaluationOutput(answer=answer, trace=trace.serialise())

    def _build_agent(
        self,
        skills: SkillsConfig | None,
        provider: SkillProvider | None,
        on_trajectory_complete: Callable[[Trajectory], None],
    ) -> EnterpriseAgentOrchestrator:
        return EnterpriseAgentOrchestrator(
            self._config,
            skills=skills,
            skills_provider=provider,
            on_trajectory_complete=on_trajectory_complete,
        )

    @staticmethod
    def _candidate_skills(
        variant: EvaluationVariant,
        directory: Path,
    ) -> tuple[SkillsConfig | None, SkillProvider | None]:
        if variant.advisory_skill is None:
            return None, None

        skills = SkillsConfig(enabled=True, cache_dir=str(directory), scope_mode="global")
        store = LocalSkillStore(db_path=directory / "skills.db")
        store.upsert_pack_skill(
            SkillDefinition(
                name="lcp.evaluation.candidate",
                title="Evaluation-only advisory candidate",
                description="A temporary advisory skill used only for paired evaluation.",
                trigger="Use when the current request matches this evaluation case.",
                steps=[variant.advisory_skill],
            ),
            pack_name="learning-control-plane-evaluation",
            scope_mode="global",
            update_existing=True,
        )
        return skills, LocalSkillProvider(skills, store=store)
