"""Optional, offline-safe PenguiFlow integration for the learning control plane."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from threading import Thread
from typing import Any, Protocol
from uuid import uuid4

from penguiflow.planner.trajectory import Trajectory
from penguiflow.skills.local_store import LocalSkillStore
from penguiflow.skills.models import SkillDefinition, SkillScopeMode, SkillTaskType

from .control_plane import ActivationReceipt, AdvisorySkillCandidate, DeliveryAuthorization
from .evaluation import EvaluationCase, EvaluationVariant
from .evidence import EvidenceContext, EvidenceEvent, EvidenceSink
from .investigation import InvestigationStatus, InvestigationTrajectoryV1, SourceTraceRef
from .investigation_publisher import InvestigationPublisher

logger = logging.getLogger("learning_control_plane.penguiflow")


@dataclass(frozen=True, slots=True)
class TrajectoryProjection:
    """Metadata-only summary of a PenguiFlow trajectory for offline learning."""

    step_count: int
    failed_step_count: int
    finish_reason: str | None
    has_final_answer: bool


@dataclass(frozen=True, slots=True)
class PenguiFlowInvestigationContext:
    """Trusted run identity needed to project one PenguiFlow trajectory safely."""

    source_trace_ref: SourceTraceRef
    agent_ref: str
    scope_ref: str
    execution_fingerprint: str
    started_at: datetime
    provider_ref: str = "penguiflow"
    redaction_profile: str = "penguiflow-investigation-safe:v1"
    allowed_node_names: frozenset[str] = frozenset()


class PenguiFlowInvestigationProjector:
    """Project a native trajectory into a redacted investigation document."""

    def __init__(self, context: PenguiFlowInvestigationContext) -> None:
        self._context = context

    def project(
        self,
        trajectory: Trajectory,
        *,
        completed_at: datetime | None = None,
        investigation_id: str | None = None,
    ) -> InvestigationTrajectoryV1:
        """Create a document without copying content-bearing trajectory fields."""

        projected_steps = [
            {
                "index": index,
                "node": self._safe_node_name(step.action.next_node),
                "status": "failed" if step.error or step.failure else "completed",
                "has_observation": step.observation is not None,
                "has_streams": bool(step.streams),
            }
            for index, step in enumerate(trajectory.steps)
        ]
        step_signature = ">".join(str(step["node"]) for step in projected_steps) or "no_steps"
        finish_reason = trajectory.finish_reason or "unknown"

        return InvestigationTrajectoryV1(
            investigation_id=investigation_id or self._investigation_id(),
            source_trace_ref=self._context.source_trace_ref,
            agent_ref=self._context.agent_ref,
            provider_ref=self._context.provider_ref,
            scope_ref=self._context.scope_ref,
            started_at=self._context.started_at,
            completed_at=completed_at,
            status=_investigation_status(finish_reason),
            execution_fingerprint=self._context.execution_fingerprint,
            request={
                "has_text": bool(trajectory.query),
                "input_part_count": len(trajectory.input_parts),
            },
            steps=projected_steps,
            redaction_profile=self._context.redaction_profile,
            step_signature=step_signature,
            execution_context={
                "step_count": len(projected_steps),
                "failed_step_count": sum(step["status"] == "failed" for step in projected_steps),
                "has_final_answer": trajectory.final_answer is not None,
            },
            termination_reason=_safe_termination_reason(finish_reason),
        )

    def _investigation_id(self) -> str:
        source_trace_ref = self._context.source_trace_ref
        identity = ":".join(
            (
                source_trace_ref.tracking_store_ref,
                source_trace_ref.experiment_id,
                source_trace_ref.mlflow_trace_id,
            )
        )
        return f"investigation_{sha256(identity.encode()).hexdigest()[:24]}"

    def _safe_node_name(self, value: str) -> str:
        """Keep only node names explicitly declared safe by the integration."""

        if value in self._context.allowed_node_names:
            return value
        return "redacted_node"


class PenguiFlowInvestigationPublicationHook:
    """Publish a projected investigation after a completed PenguiFlow trajectory."""

    def __init__(
        self,
        projector: PenguiFlowInvestigationProjector,
        publisher: InvestigationPublisher,
    ) -> None:
        self._projector = projector
        self._publisher = publisher

    def __call__(self, trajectory: Trajectory) -> None:
        """Publish in the background so an unavailable control plane cannot delay an agent."""

        try:
            Thread(target=self._publish, args=(trajectory,), daemon=True).start()
        except Exception:
            logger.warning("PenguiFlow investigation publication hook failed", exc_info=True)

    def _publish(self, trajectory: Trajectory) -> None:
        """Project before calling the publisher, so raw trajectory content cannot escape."""

        try:
            document = self._projector.project(trajectory, completed_at=datetime.now(UTC))
            self._publisher.publish(document)
        except Exception:
            logger.warning("PenguiFlow investigation publication failed", exc_info=True)


def project_trajectory(trajectory: Trajectory) -> TrajectoryProjection:
    """Project a trajectory without copying its query, observations, or answer."""

    return TrajectoryProjection(
        step_count=len(trajectory.steps),
        failed_step_count=sum(1 for step in trajectory.steps if step.error or step.failure),
        finish_reason=trajectory.finish_reason,
        has_final_answer=trajectory.final_answer is not None,
    )


def compile_advisory_skill(
    candidate: AdvisorySkillCandidate,
    *,
    trigger: str,
    title: str | None = None,
    task_type: SkillTaskType = "unknown",
) -> SkillDefinition:
    """Compile one approved advisory candidate into PenguiFlow's skill format."""

    cleaned_trigger = trigger.strip()
    if not cleaned_trigger:
        raise ValueError("trigger must be non-empty")

    skill_name = f"learned.{_slug(candidate.candidate_id)}"
    return SkillDefinition.model_validate(
        {
            "name": skill_name,
            "title": title.strip() if title else f"Learned guidance: {candidate.candidate_id}",
            "description": "Human-approved advisory guidance from the learning control plane.",
            "trigger": cleaned_trigger,
            "task_type": task_type,
            "steps": [candidate.advisory_skill],
            "lcp_candidate_id": candidate.candidate_id,
            "lcp_source_trace_ids": list(candidate.source_trace_ids),
        }
    )


class PlannerFactory(Protocol):
    """Create a fresh, isolated planner for one fixed evaluation variant."""

    def __call__(self, variant: EvaluationVariant) -> Any:
        """Return a planner whose configuration contains only this variant's skill."""
        ...


class PenguiFlowEvaluationRunner:
    """Adapt a host-provided isolated PenguiFlow planner factory to ``RunOne``."""

    def __init__(self, planner_factory: PlannerFactory) -> None:
        self._planner_factory = planner_factory

    async def __call__(self, case: EvaluationCase, variant: EvaluationVariant) -> Any:
        """Run one case with a newly created planner, never a serving planner."""

        query = case.inputs.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("PenguiFlow evaluation cases require a non-empty inputs['query'] string")

        planner = self._planner_factory(variant)
        return await planner.run(query, tool_context=dict(case.inputs.get("tool_context", {})))


class PenguiFlowTracePublisher:
    """Best-effort opt-in publisher called after a PenguiFlow run has completed."""

    def __init__(self, evidence_sink: EvidenceSink | None) -> None:
        self._evidence_sink = evidence_sink

    def publish(self, trajectory: Trajectory, context: EvidenceContext) -> bool:
        """Publish redacted trajectory metadata and never raise into an agent workload."""

        if self._evidence_sink is None:
            return False

        projection = project_trajectory(trajectory)
        event = EvidenceEvent(
            event_type="trace.recorded",
            context=context,
            attributes={
                "step_count": projection.step_count,
                "failed_step_count": projection.failed_step_count,
                "finish_reason": projection.finish_reason or "unknown",
                "has_final_answer": projection.has_final_answer,
            },
        )
        try:
            return self._evidence_sink.emit(event)
        except Exception:
            logger.warning("PenguiFlow trace publication failed", exc_info=True)
            return False


class PenguiFlowTracePublicationHook:
    """Attach this callback to ``ReactPlanner`` for safe post-run evidence publication."""

    def __init__(self, publisher: PenguiFlowTracePublisher, context: EvidenceContext) -> None:
        self._publisher = publisher
        self._context = context

    def __call__(self, trajectory: Trajectory) -> None:
        """Start best-effort evidence publication without delaying the planner result."""

        try:
            Thread(target=self._publish, args=(trajectory,), daemon=True).start()
        except Exception:
            logger.warning("PenguiFlow trace publication hook failed", exc_info=True)

    def _publish(self, trajectory: Trajectory) -> None:
        """Publish from a daemon thread after the planner has completed."""

        try:
            context = self._context_with_trace_id(trajectory)
            self._publisher.publish(trajectory, context)
        except Exception:
            logger.warning("PenguiFlow trace publication hook failed", exc_info=True)

    def _context_with_trace_id(self, trajectory: Trajectory) -> EvidenceContext:
        if self._context.trace_id is not None:
            return self._context
        tool_context = trajectory.tool_context or {}
        trace_id = tool_context.get("trace_id")
        if trace_id is None:
            return self._context
        return replace(self._context, trace_id=str(trace_id))


class ScopedSkillActivationAdapter:
    """Deliver an authorized advisory skill to PenguiFlow's scoped local store."""

    def __init__(self, skill_store: LocalSkillStore) -> None:
        self._skill_store = skill_store

    def deliver(
        self,
        authorization: DeliveryAuthorization,
        candidate: AdvisorySkillCandidate,
        skill: SkillDefinition,
        *,
        now: datetime | None = None,
    ) -> ActivationReceipt:
        """Activate a skill only for a matching, currently authorized scope."""

        delivered_at = now or datetime.now(UTC)
        if not authorization.is_active(delivered_at):
            raise ValueError("delivery authorization is expired or revoked")
        if authorization.candidate_id != candidate.candidate_id:
            raise ValueError("candidate does not match delivery authorization")

        scope_mode, tenant_id, project_id = _parse_scope_ref(authorization.scope_ref)
        scoped_skill = skill.model_copy(update={"name": _scoped_skill_name(skill.name, authorization.scope_ref)})
        self._skill_store.upsert_learned_skill(
            scoped_skill,
            authorization_id=authorization.authorization_id,
            scope_mode=scope_mode,
            scope_tenant_id=tenant_id,
            scope_project_id=project_id,
        )
        stored = self._skill_store.get_by_name([scoped_skill.name or ""], scope_clause="", scope_params=())
        if not stored:
            raise RuntimeError("learned skill was not found after delivery")

        return ActivationReceipt(
            receipt_id=f"receipt_{uuid4().hex}",
            authorization_id=authorization.authorization_id,
            candidate_id=candidate.candidate_id,
            scope_ref=authorization.scope_ref,
            provider_ref=f"penguiflow.skills:{stored[0].id}",
            delivered_at=delivered_at,
        )


def _parse_scope_ref(scope_ref: str) -> tuple[SkillScopeMode, str | None, str | None]:
    if scope_ref == "global":
        return "global", None, None
    scope_kind, separator, identifier = scope_ref.partition(":")
    if separator != ":" or not identifier.strip():
        raise ValueError("scope_ref must be 'global', 'tenant:<id>', or 'project:<id>'")
    if scope_kind == "tenant":
        return "tenant", identifier.strip(), None
    if scope_kind == "project":
        return "project", None, identifier.strip()
    raise ValueError("scope_ref must be 'global', 'tenant:<id>', or 'project:<id>'")


def _scoped_skill_name(name: str | None, scope_ref: str) -> str:
    if not name:
        raise ValueError("compiled skill must have a name")
    return f"{name}.{_slug(scope_ref)}"


def _investigation_status(finish_reason: str) -> InvestigationStatus:
    """Map PenguiFlow's terminal reason to the portable investigation status."""

    statuses: dict[str, InvestigationStatus] = {
        "answer_complete": "completed",
        "budget_exhausted": "timed_out",
        "cancelled": "cancelled",
        "pause": "interrupted",
        "paused": "interrupted",
        "interrupted": "interrupted",
        "no_path": "failed",
    }
    return statuses.get(finish_reason, "unknown")


def _safe_termination_reason(value: str) -> str:
    """Keep only known terminal reason labels from a native trajectory."""

    allowed = {
        "answer_complete",
        "budget_exhausted",
        "cancelled",
        "pause",
        "paused",
        "interrupted",
        "no_path",
    }
    if value in allowed:
        return value
    return "unknown"


def _slug(value: str) -> str:
    """Return a stable identifier fragment accepted by the skills store."""

    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


__all__ = [
    "PenguiFlowEvaluationRunner",
    "PenguiFlowInvestigationContext",
    "PenguiFlowInvestigationProjector",
    "PenguiFlowInvestigationPublicationHook",
    "PenguiFlowTracePublisher",
    "PenguiFlowTracePublicationHook",
    "ScopedSkillActivationAdapter",
    "TrajectoryProjection",
    "compile_advisory_skill",
    "project_trajectory",
]
