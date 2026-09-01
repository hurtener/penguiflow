# PenguiFlow integration (Milestone 5)

`learning_control_plane.penguiflow` is an optional integration layer. It is not
called by PenguiFlow's request path and it does not start background work itself.

## Standalone evaluation

Use `PenguiFlowEvaluationRunner` with a factory that creates a fresh planner for
each `EvaluationVariant`. The factory is responsible for constructing the exact
same agent/model/tool bundle for both variants and for adding only the candidate
advisory skill to the candidate planner. This lets applications adopt
`LocalEvaluationBackend` without enabling candidate mining, review, or delivery.

## Optional trace publication

After a planner run completes, a host may call
`PenguiFlowTracePublisher.publish(trajectory, evidence_context)`. The publisher
emits only a `TrajectoryProjection`: step counts, failure counts, finish reason,
and whether a final answer exists. It deliberately excludes the query, tool
context, observations, and answer. Missing or failed evidence sinks return
`False`; they cannot interrupt a customer request.

Call this from the host's existing post-run/background mechanism, not directly in
the latency-sensitive execution loop.

## Candidate delivery

1. Compile a registered `AdvisorySkillCandidate` with `compile_advisory_skill`.
2. After Milestone 4 produces a human-approved `DeliveryAuthorization`, call
   `ScopedSkillActivationAdapter.deliver`.
3. Record the returned `ActivationReceipt` with
   `LearningControlPlane.record_activation_receipt`.

The adapter accepts only `global`, `tenant:<id>`, and `project:<id>` scopes. It
writes a `learned` record into PenguiFlow's existing `LocalSkillStore`; normal
skill retrieval keeps applying its scope filters. The generated skill is guidance
text only: it has no tools, permissions, executable code, or activation behavior.
