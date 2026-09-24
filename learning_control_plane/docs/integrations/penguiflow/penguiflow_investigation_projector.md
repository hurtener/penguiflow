# PenguiFlow investigation projector

`PenguiFlowInvestigationProjector` turns a completed PenguiFlow `Trajectory`
into an `InvestigationTrajectoryV1`. It is deliberately a one-way projection,
not a trajectory serializer.

The integration supplies `PenguiFlowInvestigationContext` when it starts a run.
That context carries the trusted source-trace locator, identity, scope,
deployment fingerprint, start time, and the exact set of safe node names.

## What the document keeps

- source trace reference and run identity supplied by the integration;
- terminal status and a known terminal-reason label;
- whether a text request and binary input parts were present;
- per-step index, allowlisted node name, success/failure state, and presence
  flags for observations and streams; and
- aggregate step counts and whether a final answer existed.

## What it never reads into the document

The projector excludes query text, LLM context, tool context, action arguments,
thoughts, observations, stream chunks, errors, failure payloads, artifacts,
sources, metadata, summaries, resume input, steering input, and final answers.

Unknown node names become `redacted_node`. This is intentional: a string that
looks like a tool name can still be customer content. A framework integration
must explicitly allowlist its configured node names to preserve them.

`PenguiFlowInvestigationPublicationHook` runs this projection before it calls an
`InvestigationPublisher`, from a daemon thread. Publication failures are logged
and cannot delay or fail an agent request. The existing
`PenguiFlowTracePublicationHook` remains available for metadata-only evidence.
