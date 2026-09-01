"""Idempotent MLflow attachment publisher for investigation documents."""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any, Protocol

from .investigation import InvestigationTrajectoryV1

INVESTIGATION_ID_TAG = "learning.investigation.id"
INVESTIGATION_DIGEST_TAG = "learning.investigation.digest"
INVESTIGATION_ATTACHMENT_OUTPUT_KEY = "learning.investigation_trajectory"


class InvestigationPublisher(Protocol):
    """Publish one canonical investigation document and return its digest."""

    def publish(self, document: InvestigationTrajectoryV1) -> str:
        """Publish one document exactly once per investigation ID."""
        ...


class MlflowAttachmentPublisher:
    """Publish canonical investigation bytes as an MLflow trace attachment."""

    def __init__(
        self,
        *,
        mlflow_module: Any | None = None,
        attachment_factory: Callable[..., Any] | None = None,
        trace_destination_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._mlflow = mlflow_module
        self._attachment_factory = attachment_factory
        self._trace_destination_factory = trace_destination_factory
        self._publish_lock = Lock()

    def publish(self, document: InvestigationTrajectoryV1) -> str:
        """Publish the document once, or return its prior digest on a retry."""

        with self._publish_lock:
            return self._publish_once(document)

    def _publish_once(self, document: InvestigationTrajectoryV1) -> str:
        """Publish after serializing local retries for one publisher instance."""

        digest = document.digest()
        mlflow, attachment_factory, trace_destination_factory = self._dependencies()
        trace_destination = trace_destination_factory(document.source_trace_ref.experiment_id)
        existing_digest = self._existing_digest(mlflow, document)

        if existing_digest is not None:
            if existing_digest != digest:
                raise ValueError(
                    f"investigation_id {document.investigation_id!r} was already published with a different digest"
                )
            return digest

        attachment = attachment_factory(
            content_type="application/json",
            content_bytes=document.canonical_bytes(),
        )
        tags = {
            **document.query_index(),
            INVESTIGATION_ID_TAG: document.investigation_id,
            INVESTIGATION_DIGEST_TAG: digest,
        }
        with mlflow.start_span(
            name="learning.investigation.publish",
            span_type="CHAIN",
            trace_destination=trace_destination,
        ) as span:
            mlflow.update_current_trace(tags=tags)
            span.set_outputs(
                {
                    INVESTIGATION_ATTACHMENT_OUTPUT_KEY: attachment,
                    "learning.investigation_digest": digest,
                }
            )

        flush = getattr(mlflow, "flush_trace_async_logging", None)
        if callable(flush):
            flush()
        return digest

    def _dependencies(self) -> tuple[Any, Callable[..., Any], Callable[..., Any]]:
        if self._mlflow is None:
            try:
                import mlflow
                from mlflow.tracing.attachments import Attachment
                from mlflow.tracing.destination import MlflowExperimentLocation
            except ImportError as error:
                raise RuntimeError("MLflow 3.12.0 or newer with trace attachments is required") from error
            self._mlflow = mlflow
            self._attachment_factory = Attachment
            self._trace_destination_factory = MlflowExperimentLocation

        if self._attachment_factory is None or self._trace_destination_factory is None:
            raise RuntimeError(
                "attachment_factory and trace_destination_factory are required with an injected mlflow_module"
            )
        return self._mlflow, self._attachment_factory, self._trace_destination_factory

    @staticmethod
    def _existing_digest(mlflow: Any, document: InvestigationTrajectoryV1) -> str | None:
        filter_string = f'tags.`{INVESTIGATION_ID_TAG}` = "{document.investigation_id}"'
        traces = mlflow.search_traces(
            locations=[document.source_trace_ref.experiment_id],
            filter_string=filter_string,
            return_type="list",
        )
        if not traces:
            return None
        if len(traces) > 1:
            raise RuntimeError(f"multiple MLflow traces use investigation_id {document.investigation_id!r}")

        tags = dict(traces[0].info.tags)
        existing_digest = tags.get(INVESTIGATION_DIGEST_TAG)
        if not isinstance(existing_digest, str) or not existing_digest:
            raise RuntimeError(f"existing investigation {document.investigation_id!r} has no digest tag")
        return existing_digest


__all__ = [
    "INVESTIGATION_ATTACHMENT_OUTPUT_KEY",
    "INVESTIGATION_DIGEST_TAG",
    "INVESTIGATION_ID_TAG",
    "InvestigationPublisher",
    "MlflowAttachmentPublisher",
]
