"""Optional, offline-safe PenguiFlow integration for the learning control plane."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from penguiflow.planner.trajectory import Trajectory
from penguiflow.skills.local_store import LocalSkillStore
from penguiflow.skills.models import SkillDefinition, SkillScopeMode, SkillTaskType

from .control_plane import ActivationReceipt, AdvisorySkillCandidate, DeliveryAuthorization
from .evaluation import EvaluationCase, EvaluationVariant
from .evidence import EvidenceContext, EvidenceEvent, EvidenceSink

logger = logging.getLogger("learning_control_plane.penguiflow")


@dataclass(frozen=True, slots=True)
class TrajectoryProjection:
    """Metadata-only summary of a PenguiFlow trajectory for offline learning."""

    step_count: int
    failed_step_count: int
    finish_reason: str | None
    has_final_answer: bool


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


def _slug(value: str) -> str:
    """Return a stable identifier fragment accepted by the skills store."""

    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


__all__ = [
    "PenguiFlowEvaluationRunner",
    "PenguiFlowTracePublisher",
    "ScopedSkillActivationAdapter",
    "TrajectoryProjection",
    "compile_advisory_skill",
    "project_trajectory",
]
