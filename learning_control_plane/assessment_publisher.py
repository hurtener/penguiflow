"""Best-effort MLflow publication of safe investigation assessments."""

from __future__ import annotations

import logging
from collections.abc import Callable
from time import sleep
from typing import Any, Protocol

from .investigation import InvestigationTrajectoryV1

logger = logging.getLogger("learning_control_plane.assessment_publisher")


class InvestigationAssessmentPublisher(Protocol):
    """Publish safe assessment scores linked to an investigation's source trace."""

    def publish(self, document: InvestigationTrajectoryV1) -> tuple[str, ...]:
        """Return identifiers for assessment records accepted by the backend."""
        ...


class MlflowAssessmentPublisher:
    """Log deterministic final-answer feedback on the source MLflow trace."""

    def __init__(
        self,
        *,
        mlflow_module: Any | None = None,
        trace_availability_delays: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 4.0),
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        self._mlflow = mlflow_module
        self._trace_availability_delays = trace_availability_delays
        self._sleep = sleep_fn

    def publish(self, document: InvestigationTrajectoryV1) -> tuple[str, ...]:
        """Publish only scores and reason codes; never publish answer content."""

        for delay in (*self._trace_availability_delays, None):
            try:
                return self._publish_once(document)
            except Exception as error:
                if delay is None or not _trace_is_not_available(error):
                    raise
                logger.info(
                    "MLflow trace is not available for assessment publication; retrying in %ss",
                    delay,
                )
                self._sleep(delay)

        raise RuntimeError("unreachable")

    def _publish_once(self, document: InvestigationTrajectoryV1) -> tuple[str, ...]:
        """Publish one complete assessment set after the source trace is available."""

        verification = document.extensions.get("learning.verification")
        if not isinstance(verification, dict):
            return ()
        final_answer = verification.get("final_answer")
        assessment_ref = verification.get("assessment_ref")
        if not isinstance(final_answer, dict) or not isinstance(assessment_ref, str):
            return ()

        mlflow, assessment_source = self._dependencies()
        metadata = {
            "assessment_ref": assessment_ref,
            "investigation_id": document.investigation_id,
            "rubric_version": str(final_answer.get("rubric_version", "unknown")),
        }
        published_ids = [
            self._log_feedback(
                mlflow,
                assessment_source,
                trace_id=document.source_trace_ref.mlflow_trace_id,
                name="lcp_final_answer_accuracy",
                value=float(final_answer.get("score", 0.0)),
                reason_codes=final_answer.get("hard_failure_codes"),
                metadata=metadata,
            )
        ]
        for criterion in final_answer.get("criteria", []):
            if not isinstance(criterion, dict) or criterion.get("score") is None:
                continue
            criterion_id = criterion.get("criterion_id")
            if not isinstance(criterion_id, str):
                continue
            published_ids.append(
                self._log_feedback(
                    mlflow,
                    assessment_source,
                    trace_id=document.source_trace_ref.mlflow_trace_id,
                    name=f"lcp_final_answer_accuracy_{criterion_id}",
                    value=float(criterion["score"]),
                    reason_codes=criterion.get("reason_codes"),
                    metadata=metadata,
                )
            )
        published_ids.append(
            self._log_feedback(
                mlflow,
                assessment_source,
                trace_id=document.source_trace_ref.mlflow_trace_id,
                name="lcp_verified_success",
                value=bool(verification.get("verified_success")),
                reason_codes=(),
                metadata=metadata,
            )
        )
        return tuple(published_ids)

    def _dependencies(self) -> tuple[Any, Any]:
        if self._mlflow is None:
            try:
                import mlflow
                from mlflow.entities import AssessmentSource
            except ImportError as error:
                raise RuntimeError("MLflow with trace assessments is required") from error
            self._mlflow = mlflow
            return mlflow, AssessmentSource(source_type="CODE", source_id="learning-control-plane")

        assessment_source_factory = getattr(self._mlflow, "AssessmentSource", None)
        if assessment_source_factory is None:
            return self._mlflow, None
        return self._mlflow, assessment_source_factory(source_type="CODE", source_id="learning-control-plane")

    @staticmethod
    def _log_feedback(
        mlflow: Any,
        source: Any,
        *,
        trace_id: str,
        name: str,
        value: float | bool,
        reason_codes: Any,
        metadata: dict[str, str],
    ) -> str:
        codes = [str(code) for code in reason_codes or ()]
        assessment_name = name.replace(".", "_")
        assessment = mlflow.log_feedback(
            trace_id=trace_id,
            name=assessment_name,
            value=value,
            source=source,
            rationale=",".join(codes) if codes else None,
            metadata=metadata,
        )
        assessment_id = getattr(assessment, "assessment_id", None)
        return str(assessment_id or f"{metadata['assessment_ref']}:{assessment_name}")


def _trace_is_not_available(error: Exception) -> bool:
    """Recognize MLflow's transient response before an async trace export finishes."""

    message = str(error).lower()
    return "trace with id" in message and "not found" in message


__all__ = ["InvestigationAssessmentPublisher", "MlflowAssessmentPublisher"]
