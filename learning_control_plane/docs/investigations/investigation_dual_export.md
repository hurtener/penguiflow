# MLflow and OTLP dual-export spike

**Decision:** pass for the local MVP path. No object-store implementation is
needed for Milestone 14.

## What was tested

The integration test starts a temporary, local OTLP/HTTP collector and a fresh
Python process with:

- MLflow `3.12.0`;
- `opentelemetry-exporter-otlp-proto-http` `1.44.0`;
- `MLFLOW_TRACE_ENABLE_OTLP_DUAL_EXPORT=true`; and
- a local SQLite MLflow tracking store with a local artifact directory.

That process publishes one redacted `InvestigationTrajectoryV1` through
`MlflowAttachmentPublisher`. The test then reads both destinations directly.

## Observed result

MLflow stores the canonical document as a separate attachment artifact. Its
trace span contains an `mlflow-attachment://` reference, and the stored artifact
bytes exactly equal the document's canonical bytes.

The OTLP collector receives one `learning.investigation.publish` span. Its
`mlflow.spanOutputs` attribute is JSON containing the attachment reference and
the investigation digest. It also receives `mlflow.experimentId` and
`mlflow.traceRequestId`, which identify the MLflow trace. The collector does not
receive the canonical document bytes.

## Consequence

For this MVP, MLflow remains the records room for the document bytes. OTLP is a
metadata-only observability path: consumers can correlate an observation to the
MLflow investigation by experiment, trace ID, attachment reference, and digest,
but must retrieve the document from MLflow when authorized.

This result covers MLflow's supported OTLP/HTTP protobuf exporter and a direct
collector. A production collector that rewrites or drops span attributes needs
the same test before being relied on for correlation.
