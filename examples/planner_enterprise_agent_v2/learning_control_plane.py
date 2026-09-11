"""Learning-control-plane adapter for the enterprise planner policy suite."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol, runtime_checkable

from examples.planner_enterprise_agent_v2.config import AgentConfig
from examples.planner_enterprise_agent_v2.evals.metrics import policy_metric
from examples.planner_enterprise_agent_v2.main import EnterpriseAgentOrchestrator
from learning_control_plane.evaluation import EvaluationCase, EvaluationDataset, EvaluationVariant
from learning_control_plane.investigation_mining import build_held_out_evaluation_cases
from learning_control_plane.mining import TraceLearningRecord
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
    """The answer, planner path, and local runtime measurement for one isolated run."""

    answer: Any
    trace: Mapping[str, Any]
    latency_ms: float


@runtime_checkable
class EnterpriseOutcomeProvider(Protocol):
    """Provide approved real-world outcome labels for one evaluated Planner V2 run."""

    def metrics_for(self, case: EvaluationCase, output: EnterpriseEvaluationOutput) -> Mapping[str, float]:
        """Return externally observed outcome metrics without changing the evaluated agent."""
        ...


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


class EnterpriseOutcomeScorer:
    """Combine approved external outcomes with isolated Planner V2 runtime measurements."""

    def __init__(self, outcome_provider: EnterpriseOutcomeProvider | None = None) -> None:
        self._outcome_provider = outcome_provider

    def __call__(self, case: EvaluationCase, output: EnterpriseEvaluationOutput) -> Mapping[str, float]:
        """Return all available real outcome metrics for one Planner V2 evaluation run."""

        metrics: dict[str, float] = {}
        if self._outcome_provider is not None:
            metrics.update(self._outcome_provider.metrics_for(case, output))

        metrics.update(enterprise_policy_metric(case, output))
        metrics["latency_ms"] = output.latency_ms
        metrics["tool_error_rate"] = _tool_error_rate(output.trace)

        cost_usd = _reported_cost_usd(output.trace)
        if cost_usd is not None:
            metrics["cost_usd"] = cost_usd
        return metrics


class PlannerEnterpriseV2EvaluationRunner:
    """Run Planner Enterprise V2 with either no skill or one isolated advisory skill."""

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
            started_at = time.perf_counter()
            answer = await agent.execute(
                query,
                tenant_id=tenant_id,
                tool_context={"trace_id": f"lcp-eval-{case.case_id}-{variant.variant_id}"},
            )
            latency_ms = (time.perf_counter() - started_at) * 1000

        if trace is None:
            raise RuntimeError("enterprise agent did not publish a completed trajectory")
        return EnterpriseEvaluationOutput(answer=answer, trace=trace.serialise(), latency_ms=latency_ms)

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


# Kept for callers of the original policy-only adapter name.
EnterprisePolicyComplianceRunner = PlannerEnterpriseV2EvaluationRunner


def load_real_held_out_dataset(
    records: tuple[TraceLearningRecord, ...],
    *,
    dataset_id: str,
    version: str,
    case_loader: Callable[[TraceLearningRecord], EvaluationCase],
) -> EvaluationDataset:
    """Build a versioned evaluation dataset through the host's approved source-run lookup."""

    return EvaluationDataset(
        dataset_id=dataset_id,
        version=version,
        cases=build_held_out_evaluation_cases(records, case_loader),
    )


def _tool_error_rate(trace: Mapping[str, Any]) -> float:
    steps = trace.get("steps")
    if not isinstance(steps, list) or not steps:
        return 1.0
    failed_steps = sum(
        1
        for step in steps
        if isinstance(step, Mapping) and (step.get("error") is not None or step.get("failure") is not None)
    )
    return failed_steps / len(steps)


def _reported_cost_usd(trace: Mapping[str, Any]) -> float | None:
    metadata = trace.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    for metric_name in ("cost_usd", "planner_cost_usd"):
        value = metadata.get(metric_name)
        if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
            return float(value)
    return None
