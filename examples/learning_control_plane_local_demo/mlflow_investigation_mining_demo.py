"""Publish local investigations to MLflow, then inspect their safe mining records."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from learning_control_plane import (
    InvestigationSelection,
    InvestigationTrajectoryV1,
    MlflowAttachmentPublisher,
    MlflowInvestigationReader,
    SourceTraceRef,
)


def _document(experiment_id: str, index: int) -> InvestigationTrajectoryV1:
    """Create one locally safe investigation document for the demonstration."""

    return InvestigationTrajectoryV1(
        investigation_id=f"local-mining-{index}",
        source_trace_ref=SourceTraceRef(
            tracking_store_ref="local-mlflow",
            experiment_id=experiment_id,
            mlflow_trace_id=f"planner-run-{index}",
            deployment_ref="sha256:planner-demo",
        ),
        agent_ref="planner_enterprise_agent_v2",
        provider_ref="penguiflow:v1",
        scope_ref="tenant:demo",
        started_at=datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=index),
        status="completed",
        execution_fingerprint="sha256:planner-demo",
        request={"has_text": True, "input_part_count": 1},
        steps=({"node": "classify", "status": "completed"},),
        redaction_profile="demo-safe:v1",
        step_signature="classify>plan",
        execution_context={"failed_step_count": 0},
    )


def main() -> None:
    """Run a complete local MLflow attachment-read and cohort-reservation flow."""

    import mlflow

    previous_tracking_uri = mlflow.get_tracking_uri()
    with tempfile.TemporaryDirectory(prefix="penguiflow-lcp-mining-") as directory:
        working_directory = Path(directory)
        mlflow.set_tracking_uri(f"sqlite:///{working_directory / 'mlflow.db'}")
        try:
            experiment_id = mlflow.create_experiment(
                "lcp-investigation-mining-demo",
                artifact_location=(working_directory / "artifacts").as_uri(),
            )
            publisher = MlflowAttachmentPublisher()
            for index in range(3):
                publisher.publish(_document(experiment_id, index))

            reader = MlflowInvestigationReader()
            cohorts = reader.load_cohorts(
                InvestigationSelection(experiment_id=experiment_id, agent_ref="planner_enterprise_agent_v2"),
                held_out_count=1,
            )
            print(
                {
                    "mining_trace_ids": [record.trace_id for record in cohorts.mining_records],
                    "held_out_trace_ids": [record.trace_id for record in cohorts.held_out_records],
                    "safe_summaries": [record.safe_summary for record in cohorts.mining_records],
                }
            )
        finally:
            mlflow.set_tracking_uri(previous_tracking_uri)


if __name__ == "__main__":
    main()
