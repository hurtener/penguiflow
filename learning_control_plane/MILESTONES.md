# Learning Control Plane milestones

## Milestone 0 — Foundation and boundaries

**Goal:** create the standalone package, document responsibility boundaries, and
make the package discoverable by the repository build.

**Includes:** the package, MVP scope, architecture, and a package layout test.

**Does not include:** runtime behavior, background jobs, telemetry setup, MLflow,
candidate generation, or delivery.

**Done when:** the package imports, the package layout is tested, and the remaining
milestones have explicit completion criteria.

**Implementation note:** Do not create empty future packages. Add a module or folder
only when it owns behavior delivered by its milestone.

## Milestone 1 — Evidence plumbing: OpenTelemetry and MLflow

**Goal:** publish versioned trace and evaluation evidence to OpenTelemetry and
MLflow without affecting agent availability.

**Includes:** trace/evaluation correlation IDs, redaction boundary, MLflow artifact
and lineage conventions, and a no-op/degraded mode when either backend is absent.

**Done when:** a standalone evaluation run produces attributable evidence that can
be queried by run, dataset, candidate, agent bundle, and metric version.

**Completed foundation:** lineage schema v1 reserves stable MLflow tags, metric
names, event names, and evidence-artifact paths. The standalone evaluation run that
emits those records is Milestone 2.

## Milestone 2 — Standalone evaluation backend

**Goal:** expose a framework-independent evaluation contract that can be adopted
without enabling learning.

**Includes:** immutable dataset manifest, `RunOne`, metric, evaluation backend,
baseline/candidate paired result format, and local backend adapter.

**Done when:** the same agent bundle can be evaluated with and without an advisory
skill on a fixed held-out dataset, with complete per-case evidence.

**Implementation:** `LocalEvaluationBackend` provides the standalone MVP contract.
It records every baseline and candidate result, including runner and metric failures,
and can emit aggregate redacted evidence through the Milestone 1 sink.

## Milestone 3 — Offline learning decision loop

**Goal:** build the LCP core as a scheduled/offline service.

**Includes:** job state machine, candidate registry, deterministic candidate gate,
policy versioning, evidence links, and fail-closed retry behavior.

**Done when:** the core can select a candidate, request an evaluation, and create a
promotion decision without importing any agent framework.

**Implementation:** `LearningControlPlane` registers advisory-skill candidates,
creates offline jobs, applies a versioned deterministic gate, and advances only to
`ready_for_review` or `rejected`. Delivery remains out of scope until Milestone 4.

## Milestone 4 — Human approval and scoped delivery

**Goal:** govern promotion and delivery of advisory skills.

**Includes:** review queue, approval/rejection audit trail, scope authorization,
expiry/revocation, delivery request, and immutable activation receipt.

**Done when:** only a human-approved passing candidate can be delivered to an
authorized tenant/project scope, and every delivery can be revoked.

**Implementation:** human review moves a gate-passing job to `approved` or
`rejected`. Approved jobs can create one expiring authorization per scope; matching
provider receipts are recorded and authorizations can be revoked.

## Milestone 5 — PenguiFlow provider and optional learning hook

**Goal:** integrate PenguiFlow without moving learning into the request path.

**Includes:** trajectory projection, advisory-skill candidate compiler, isolated
evaluation runner, scoped activation adapter, and an opt-in offline trace-publication
hook.

**Done when:** PenguiFlow can use the standalone evaluation backend alone or opt
into the full learning loop while continuing to serve if the LCP is unavailable.

**Implementation:** `learning_control_plane.penguiflow` provides an isolated
planner runner for the standalone evaluator, a metadata-only trajectory projection
and best-effort post-run publisher, and a human-authorization-aware adapter for
writing `learned` advisory skills into PenguiFlow's scoped local skill store. See
[`penguiflow.md`](penguiflow.md) for the host integration contract.

## Operational Milestone 6 — Durable local workflow state

**Goal:** preserve offline control-plane decisions across a local process restart.

**Implementation:** `SQLiteControlPlaneRepository` stores candidates, jobs,
reviews, authorizations, and receipts as one JSON-safe SQLite snapshot. Supplying
it to `LearningControlPlane` makes every state transition durable; omitting it
preserves the lightweight in-memory mode used by existing adopters.

## Operational Milestone 7 — Offline evaluation worker

**Goal:** execute persisted draft jobs without placing evaluation in an agent's
request path.

**Implementation:** `OfflineEvaluationWorker` takes the host's fixed `RunOne` and
metric functions and evaluates a bounded deterministic batch of `draft` jobs. Each
job is saved through `LearningControlPlane` as `ready_for_review`, `rejected`, or
`failed`; a scheduler can invoke `run_pending()` later without changing this
contract.

## Operational Milestone 8 — PenguiFlow post-run publication hook

**Goal:** make metadata-only trace publication opt-in and automatic after a
terminal PenguiFlow run.

**Implementation:** `ReactPlanner` accepts `on_trajectory_complete`. Configure it
with `PenguiFlowTracePublicationHook`, which starts best-effort publication on a
daemon thread and never changes the planner's result when the evidence backend is
unavailable.

## Operational Milestone 9 — Conservative candidate mining

**Goal:** turn repeated successful, pre-redacted trace summaries into auditable
advisory-skill drafts without training on the cases used for evaluation.

**Implementation:** `reserve_later_held_out_cohort` reserves newest traces before
mining. `CandidateMiner` groups earlier successful records by agent deployment and
host-supplied pattern key, requires a minimum cohort size, and calls a host-provided
offline drafter. The resulting candidate remains subject to the existing offline
evaluation and human approval workflow.

## Operational Milestone 10 — Local end-to-end demonstration

**Goal:** exercise the full MVP loop in one reproducible local run.

**Implementation:** `examples/learning_control_plane_local_demo/flow.py` chains
safe-summary mining, held-out evaluation, SQLite persistence, offline worker,
human approval, scoped skill activation, and receipt recording. The matching
integration test proves the same workflow without external services.

## Operational Milestone 11 — Human review queue and audit records

**Goal:** give a reviewer read-only evidence for an explicit approval or rejection.

**Implementation:** `list_review_queue()` returns only gate-passing jobs awaiting
review, with their advisory candidate. `get_job_audit_record()` returns the job,
candidate, evaluation/gate/review state, and all matching delivery authorizations
and receipts. Reviewers still use the existing durable `review_job()` command to
make the decision.

## Milestone 12 — Investigation trajectory contract and canonical digest

**Goal:** establish the portable, redacted evidence contract before choosing a
storage or transport mechanism.

**Includes:** `InvestigationTrajectoryV1`, a complete `source_trace_ref`, the
string-only discovery index, canonical UTF-8 JSON bytes, and a SHA-256 digest.

**Done when:** the same logical document always produces identical bytes and
digest despite input mapping order; timestamps and numbers have fixed formats;
and tests prove the source reference locates a native run without reading it.

## Milestone 13 — Idempotent investigation publisher

**Goal:** publish one investigation document through one idempotent call.

**Includes:** `publish(document) -> digest`, exactly-once behavior per
`investigation_id`, and one MLflow attachment implementation.

**Done when:** retries return the original digest and do not create a second
attachment or index entry.

**Implementation:** `MlflowAttachmentPublisher` searches the source experiment
by `investigation_id`, writes canonical document bytes as an `application/json`
trace attachment only when absent, and records the digest plus discovery index as
trace tags. Matching retries return the existing digest; different bytes fail.
MLflow is pinned to 3.12.0, the first project version with this attachment API.

## Milestone 14 — MLflow and OpenTelemetry dual-export spike

**Goal:** answer whether MLflow attachments survive dual export and whether the
OTLP result meets the observability consumer's needs.

**Includes:** a timeboxed integration test against the selected MLflow and OTel
versions, a recorded pass/fail result, and no object-store implementation.

**Done when:** the evidence documents either survive the tested path with an
adequate OTLP representation, or the exact failure is recorded as the decision
for a later storage design.

**Implementation:** passed locally with MLflow 3.12.0 and the OTLP/HTTP protobuf
exporter. MLflow retained each attachment as a separate artifact; OTLP carried
the attachment reference, digest, experiment ID, and trace ID, but not canonical
document bytes. See `investigation_dual_export.md`.

## Milestone 15 — PenguiFlow investigation projector

**Goal:** turn a native PenguiFlow run into a redacted `InvestigationTrajectoryV1`.

**Includes:** pre-write redaction, safe step/event projections, source-trace
references, and explicit terminal states. The old metadata-only publisher remains
compatible until this projector replaces it.

**Done when:** tests prove forbidden fields cannot reach canonical bytes or the
publisher, including failed, paused, and cancelled runs.

**Implementation:** `PenguiFlowInvestigationProjector` creates an allowlisted,
redacted document before the publisher is called. `PenguiFlowInvestigationPublicationHook`
keeps publication outside the agent request path; the existing metadata-only hook
remains compatible. See `penguiflow_investigation_projector.md`.

## Milestone 16 — Digest-reference downstream integration

**Goal:** link the new evidence documents to the existing evaluation and
governance workflow without redesigning that workflow.

**Includes:** source-trace digest/reference fields in discovery, evaluation
cohorts, gate evidence, approval records, delivery, and receipts.

**Done when:** an end-to-end run can follow a candidate from investigation digest
to scoped delivery receipt while the evaluation, gate, and approval contracts
remain unchanged.

**Implementation:** investigation digests now travel from mining records and
held-out evaluation cases through candidates, gate decisions, review decisions,
delivery authorizations, and activation receipts. SQLite persists the complete
chain, and the local demo prints it. See `investigation_lineage.md`.

## Milestone 17 — Constrained LLM skill drafting

**Goal:** optionally draft one advisory skill from redacted repeated-success
patterns without giving the drafting model access to native trace content.

**Done when:** only allowlisted pattern fields reach the provider; malformed or
unsafe drafts fail closed; and the existing callback-based miner remains
compatible.

**Implementation:** `LlmSkillDrafter` uses a provider-neutral `complete(prompt)`
boundary, strict JSON output, local content validation, and prompt/draft digests.
See `skill_drafting.md`.

## Milestone 18 — Direction-aware outcome scoring

**Goal:** make real agent outcome metrics comparable without incorrectly treating
cost, latency, or error rates as higher-is-better scores.

**Done when:** a promotion policy declares each metric direction; paired evaluation
evidence retains raw per-case values and direction-normalized improvements; and the
gate applies its primary and protected checks against those normalized values.

**Implementation:** `MetricSpecification` declares `higher_is_better` or
`lower_is_better`. `MetricSummary` retains the complete paired evidence and reports
mean, median, minimum, and maximum improvement. `GateDecision` persists those
summaries alongside direction-normalized mean improvements. See `scoring.md`.

## Milestone 19 — Verified MLflow investigation mining

**Goal:** let the existing candidate miner consume real production investigation
evidence without exposing raw request or trajectory content to mining or drafting.

**Done when:** MLflow attachments are verified against their canonical bytes,
digest, and discovery index; only allowlisted safe records are emitted; and the
newest records are reserved as held-out cases before candidate mining.

**Implementation:** `MlflowInvestigationReader` reads published MLflow attachments
through a read-only boundary, fails closed on invalid evidence, and projects safe
`TraceLearningRecord` values. The host builds real held-out evaluation inputs via
an explicit callback, preserving digest lineage without giving those inputs to the
miner. See `investigation_mining.md`.

## Milestone 20 — Planner Enterprise V2 real offline outcomes

**Goal:** run the first provider against approved held-out production inputs and
score real runtime and outcome evidence without touching the serving request path.

**Done when:** every case uses a fresh isolated Planner V2 instance; the host owns
real input and outcome lookup; and paired evaluation can score policy, task,
feedback, cost, latency, and tool-error metrics when they are available.

**Implementation:** `PlannerEnterpriseV2EvaluationRunner` isolates each run and
measures latency. `EnterpriseOutcomeScorer` combines deterministic policy and
trajectory metrics with a host-provided outcome provider. The existing Planner
demo now uses the multi-metric scorer. See `planner_enterprise_v2.md`.

## Post-MVP

Auto-sequence edges, automated candidate mining, canary rollout, multi-framework
providers, and advanced statistical policies are deliberately deferred until the
advisory-skill path is proven in production.
