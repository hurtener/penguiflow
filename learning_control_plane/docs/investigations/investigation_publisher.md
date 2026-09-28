# Investigation publisher, v1

`MlflowAttachmentPublisher.publish(document)` is the Milestone 13 boundary. It
accepts one `InvestigationTrajectoryV1` and returns its canonical SHA-256 digest.

The publisher searches the source experiment for the document's
`investigation_id` before writing:

- no prior trace: create one MLflow trace span, add the discovery-index tags,
  and attach the document's canonical bytes as `application/json`;
- matching prior digest: return the digest without another attachment; and
- different prior digest: raise an error rather than overwrite evidence.

The attachment appears under the span output key
`learning.investigation_trajectory`. The digest and discovery index are trace
tags, so candidate discovery can query them without reading document bodies.

This backend requires MLflow 3.12.0, the first project-pinned version with the
explicit trace attachment API.
