"""Integration spike for MLflow trace attachments with OTLP dual export."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from learning_control_plane.investigation import InvestigationTrajectoryV1, SourceTraceRef


class _CapturedOtlpRequests:
    """Collect OTLP request bodies received by the local test collector."""

    def __init__(self) -> None:
        self.payloads: list[bytes] = []
        self.received = threading.Event()


class _OtlpRequestHandler(BaseHTTPRequestHandler):
    """Accept OTLP/HTTP protobuf requests without forwarding them anywhere."""

    collector: _CapturedOtlpRequests

    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers["Content-Length"])
        self.collector.payloads.append(self.rfile.read(content_length))
        self.collector.received.set()
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        """Keep the integration test output focused on failures."""


def _investigation(experiment_id: str) -> InvestigationTrajectoryV1:
    """Create one small, redacted document for the dual-export path."""

    return InvestigationTrajectoryV1(
        investigation_id="dual-export-investigation",
        agent_ref="planner_enterprise_agent_v2",
        provider_ref="penguiflow",
        scope_ref="customer:example",
        started_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        status="completed",
        execution_fingerprint="sha256:planner-v2",
        request={"intent": "planning"},
        steps=({"kind": "plan", "outcome": "success"},),
        redaction_profile="investigation-safe-v1",
        step_signature="plan",
        source_trace_ref=SourceTraceRef(
            tracking_store_ref="local-mlflow",
            experiment_id=experiment_id,
            mlflow_trace_id="native-trace-42",
            deployment_ref="sha256:planner-v2",
        ),
    )


def _publish_in_clean_process(
    *,
    tracking_database: Path,
    artifact_directory: Path,
    otlp_endpoint: str,
) -> dict[str, str]:
    """Publish after configuring MLflow's tracing provider in a fresh process."""

    script = """
import json
import sys
from pathlib import Path

import mlflow

from learning_control_plane.investigation_publisher import MlflowAttachmentPublisher
from tests.learning_control_plane.test_investigation_dual_export import _investigation

tracking_database, artifact_directory = sys.argv[1:]
mlflow.set_tracking_uri(f"sqlite:///{tracking_database}")
experiment_id = mlflow.create_experiment(
    "lcp-dual-export-test",
    artifact_location=Path(artifact_directory).as_uri(),
)
document = _investigation(experiment_id)
digest = MlflowAttachmentPublisher().publish(document)
print(json.dumps({"digest": digest, "experiment_id": experiment_id}))
"""
    environment = {
        **os.environ,
        "MLFLOW_TRACE_ENABLE_OTLP_DUAL_EXPORT": "true",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": otlp_endpoint,
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
        "OTEL_SERVICE_NAME": "lcp-dual-export-spike",
    }
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tracking_database), str(artifact_directory)],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    )
    return json.loads(completed.stdout.splitlines()[-1])


def _otlp_span_attributes(payloads: list[bytes]) -> dict[str, str]:
    """Return string attributes from the one exported investigation span."""

    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    request = ExportTraceServiceRequest()
    request.ParseFromString(payloads[0])
    spans = [
        span
        for resource_span in request.resource_spans
        for scope_span in resource_span.scope_spans
        for span in scope_span.spans
        if span.name == "learning.investigation.publish"
    ]

    assert len(spans) == 1
    return {
        attribute.key: attribute.value.string_value
        for attribute in spans[0].attributes
        if attribute.value.HasField("string_value")
    }


def test_dual_export_keeps_the_attachment_in_mlflow_and_sends_only_its_reference_to_otlp(
    tmp_path: Path,
) -> None:
    """Prove the MLflow attachment remains private while OTLP carries a reference."""

    mlflow = pytest.importorskip("mlflow")
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    collector = _CapturedOtlpRequests()
    _OtlpRequestHandler.collector = collector
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OtlpRequestHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    tracking_database = tmp_path / "mlflow.db"
    artifact_directory = tmp_path / "artifacts"
    endpoint = f"http://127.0.0.1:{server.server_port}/v1/traces"
    previous_tracking_uri = mlflow.get_tracking_uri()

    try:
        published = _publish_in_clean_process(
            tracking_database=tracking_database,
            artifact_directory=artifact_directory,
            otlp_endpoint=endpoint,
        )
        assert collector.received.wait(timeout=10)

        mlflow.set_tracking_uri(f"sqlite:///{tracking_database}")
        traces = mlflow.search_traces(
            locations=[published["experiment_id"]],
            filter_string='tags.`learning.investigation.id` = "dual-export-investigation"',
            return_type="list",
        )

        assert len(traces) == 1
        attachment_reference = traces[0].data.spans[0].outputs["learning.investigation_trajectory"]
        assert attachment_reference.startswith("mlflow-attachment://")
        assert traces[0].info.tags["learning.investigation.digest"] == published["digest"]
        from mlflow.tracing.attachments import Attachment

        attachment_details = Attachment.parse_ref(attachment_reference)
        assert attachment_details is not None
        attachment_path = (
            artifact_directory
            / "traces"
            / str(attachment_details["trace_id"])
            / "artifacts"
            / "attachments"
            / str(attachment_details["attachment_id"])
        )
        assert attachment_path.read_bytes() == _investigation(published["experiment_id"]).canonical_bytes()

        otlp_attributes = _otlp_span_attributes(collector.payloads)
        assert json.loads(otlp_attributes["mlflow.spanOutputs"]) == {
            "learning.investigation_digest": published["digest"],
            "learning.investigation_trajectory": attachment_reference,
        }
        assert json.loads(otlp_attributes["mlflow.experimentId"]) == published["experiment_id"]
        assert json.loads(otlp_attributes["mlflow.traceRequestId"]) == attachment_details["trace_id"]
        assert _investigation(published["experiment_id"]).canonical_bytes() not in collector.payloads[0]
    finally:
        mlflow.set_tracking_uri(previous_tracking_uri)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=10)
