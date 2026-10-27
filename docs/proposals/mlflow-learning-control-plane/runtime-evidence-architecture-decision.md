# Runtime Evidence Architecture Decision

**Status:** Decision input
**Scope:** Production evidence capture and handoff into the MLflow-backed learning lifecycle

## Decision Context

MLflow coupling already exists after runtime capture:

```text
Runtime evidence
  -> agent-owned dataset projection
  -> MLflow Evaluation Dataset
  -> MLflow evaluation and prediction traces
  -> MLflow scorers and assessments
  -> MLflow evaluation lineage
  -> Learning Control Plane decision
```

This decision does not compare an MLflow architecture with an MLflow-free
architecture. It asks whether decoupling the first operation, runtime evidence
capture, creates enough value to justify another persistence and integration
boundary.

The options change:

- runtime SDK and credential coupling;
- availability and failure domains;
- raw-evidence authority;
- operational observability integration;
- retention, access, and deletion boundaries;
- cost and ownership of handoff into MLflow.

They do not remove MLflow dependence from dataset, evaluation, scorer,
assessment, prediction-trace, or lineage operations.

## Executive Options

| Option | Runtime capture | Raw-evidence authority | MLflow role | Added operating surface |
|---|---|---|---|---|
| **A. MLflow-native** | MLflow tracing API | MLflow trace attachment | Runtime evidence and complete downstream learning workflow | Lowest |
| **B. OTel transport to MLflow** | OTel API and OTLP | Complete MLflow trace output within tested limits; no native OTLP attachment | Trace destination and complete downstream learning workflow | Low to medium |
| **C. OTel plus external evidence** | OTel metadata plus durable evidence write | External immutable object or existing StateStore | Dataset projection onward | Medium to high |
| **D. Owned evidence ledger** | Durable evidence event and artifact; OTel is an observability projection | Platform-owned ledger and artifact store | Downstream learning projection | Highest |
| **E. Bounded dual export** | MLflow tracing plus optional OTLP | MLflow trace attachment; OTLP is subordinate observability | Runtime evidence and complete downstream learning workflow | Medium to high |

## Trade-Off Matrix

Ratings are relative. `High` means option provides more of named property, not
that option is universally better.

| Decision property | A. MLflow-native | B. OTel to MLflow | C. OTel + external evidence | D. Evidence ledger | E. Bounded dual export |
|---|---|---|---|---|---|
| Delivery speed | High | Medium | Low | Lowest | Low |
| Native trace-to-attachment UX | High | Low | Low | Low | High on MLflow copy |
| End-to-end MLflow lineage simplicity | High | Medium | Medium | Low | Low |
| Live-code independence from MLflow SDK | Low | High | High | High | Low |
| Runtime independence from MLflow availability | Low to medium | Medium | High | High | Low |
| Independence from MLflow downstream | None | None | None under current scope | Partial through owned contracts | None |
| Existing enterprise OTel integration | Low | High | High | Medium | High |
| Evidence survival independent of trace sampling | Medium | Low unless separately guaranteed | High | High | Medium |
| Large-payload storage efficiency | Medium | Low | High | High | Medium |
| Single access, retention, and deletion boundary | High | High | Low | Low | Medium when metadata-only |
| Customer deployment flexibility | Low to medium | Medium to high | High | High | Medium |
| Cross-system reconciliation burden | Low | Medium | High | High | Low while OTLP is subordinate |
| Support and on-call surface | Low | Medium | High | Highest | Medium |
| Future runtime-backend option value | Low | Medium | High | Highest | Medium |

## Option Analysis

### A. MLflow-Native Runtime Evidence

```text
Runtime -> MLflow trace + attachment -> MLflow dataset/evaluation/scoring
```

**Value created**

- One native path from source execution to dataset, evaluation, assessment, and
  investigation UI.
- Attachment semantics already match canonical trajectory payload.
- Fewest identifiers, adapters, stores, and reconciliation states.
- Lowest implementation and support burden when MLflow is already approved and
  operated.

**Cost accepted**

- Learning-enabled runtime imports MLflow tracing APIs and holds MLflow
  credentials.
- MLflow version, ingestion behavior, retention, and attachment limits become
  runtime integration constraints.
- Runtime evidence portability requires later export and migration work.
- MLflow concentrates runtime evidence and downstream learning failure domains.

**Decision condition**

If highest business value is fastest closed learning loop, integrated evidence
review, and lowest near-term operating cost, Option A has strongest fit.

Option A weakens when customer policy prohibits MLflow access from live
workloads, existing operations require OTel, or evidence retention and access
must differ from MLflow trace policy.

### B. OTel Runtime Transport to MLflow

```text
Runtime -> OTLP -> MLflow trace -> MLflow dataset/evaluation/scoring
```

**Value created**

- Removes MLflow tracing API from application code.
- Reuses organization-wide OTel instrumentation, context propagation, collector
  routing, and exporter configuration.
- Preserves MLflow as trace destination and downstream learning platform.

**Cost accepted**

- OTLP does not create MLflow attachments.
- Canonical evidence must fit configured trace-output limits and remain
  retrievable without sampling or truncation for its required retention period;
  otherwise this option requires external evidence storage and becomes Option C.
- Collector and attribute-mapping behavior become additional dependencies.
- MLflow remains runtime trace destination, so storage and downstream coupling
  remain.
- This is instrumentation decoupling, not evidence-plane decoupling.

**Decision condition**

If highest business value is one runtime instrumentation standard while MLflow
remains approved trace and learning destination, Option B has strongest fit.

Option B adds little value when OTel only forwards every span to MLflow and no
collector routing, customer integration, or instrumentation standardization is
required.

### C. OTel Plus External Canonical Evidence

```text
Runtime -> OTel operational trace
        -> immutable evidence object
Evidence object -> agent-owned projection -> MLflow dataset/evaluation/scoring
```

OTel carries bounded status and correlation metadata. External storage carries
redacted, schema-versioned `InvestigationTrajectoryV1` bytes. Store-specific
durability and scale properties must be verified rather than assumed.

**Value created**

- Runtime evidence remains available when traces are sampled, truncated,
  expired, or routed outside MLflow.
- Suitable external storage can provide content addressing, lower large-payload cost,
  independent retention, and narrower access policy.
- Live workloads need no MLflow tracing API or credentials.
- Runtime observability can use customer-selected OTel backends.

**Cost accepted**

- MLflow remains mandatory from dataset projection onward.
- Evidence store or StateStore capability, manifest/index, authorization, retention, deletion, and
  reconciliation need named owners and SLOs.
- Object write, trace export, and MLflow projection cannot form one distributed
  transaction.
- Native MLflow attachment UX is replaced by a digest-bound external reference.

**Decision condition**

If highest business value is runtime isolation, customer-controlled
observability, large-payload economics, or evidence policy independent from
trace policy, Option C has strongest fit.

Option C is weak when no customer, regulatory, scale, or SRE requirement uses
the second boundary. In that case it relocates bytes while leaving all learning
semantics in MLflow.

### D. Owned Evidence Ledger With Projections

```text
Runtime -> durable evidence manifest + artifact
        -> OTel observability projection
        -> MLflow learning projection
```

**Value created**

- Platform owns canonical evidence identity, schema, provenance, replay, and
  lifecycle independently from telemetry or evaluation vendors.
- Multiple evaluators and future consumers can materialize from same source.
- Provides strongest migration option if MLflow downstream scope later changes.

**Cost accepted**

- Builds and operates a learning-data platform: ledger, schema evolution,
  idempotency, replay, access policy, retention, and projection services.
- MLflow adapters remain required under current architecture.
- Internal evidence contracts can become another form of lock-in.
- Highest implementation, governance, and on-call cost.

**Decision condition**

If highest business value is independently governed evidence reused by multiple
learning or compliance consumers, Option D has strongest fit.

Option D is premature when MLflow is the only funded consumer or portability is
not backed by a migration, regulatory, or multi-product requirement.

### E. Bounded Dual Export

```text
Runtime -> OTel trace
        -> MLflow trace + attachment
```

This is an optional extension of Option A, not a second evidence authority.
MLflow remains authoritative for runtime learning evidence. OTLP is disabled by
default through the MVP and may be enabled afterward only when the deployment
configures an OTel provider and exporter for a named operational destination. A
missing exporter is a no-op. OTLP failure neither blocks the customer response
nor changes MLflow evidence eligibility.

The default OTLP projection contains operational metadata, content digests, and
the stable PenguiFlow trace ID used to correlate distinct OTel and MLflow trace
IDs. Full prompts, responses, tool output, and trajectory content require
explicit opt-in plus independent redaction, access, retention, and deletion
policy.

**Value created**

- Preserves native MLflow attachments and independent OTel visibility.
- Reuses an existing operational telemetry destination without moving learning
  authority or projection.

**Cost accepted**

- No atomic commit or completeness guarantee exists across destinations.
- Instrumentation, export overhead, support, and incident diagnosis increase.
- Full-content export duplicates sensitive data and its governance obligations.
- OTLP adds no learning durability because MLflow remains authoritative.

**Decision condition**

Option E fits after the MVP when a deployment has a named OTLP observability
consumer and accepts the added operating cost. It is unnecessary when OTLP is
enabled only for abstract portability. Reconciliation becomes required if a
future decision treats OTLP output as learning evidence.

## Marginal Decoupling Test

Partial runtime decoupling creates material value when one or more statements
are supported by current requirements or measured demand:

- Live workloads cannot hold MLflow credentials or reach MLflow endpoints.
- Existing production operations mandate OTel and a customer-controlled backend.
- Runtime availability must remain independent from MLflow ingestion.
- Learning evidence must survive operational sampling or shorter trace retention.
- Evidence size makes trace storage materially more expensive than object storage.
- Evidence requires different residency, access, retention, or deletion policy.
- More than one funded consumer needs canonical runtime evidence.
- Another framework can publish owned evidence but cannot support MLflow-native
  tracing consistently.
- Revenue is blocked without customer-selectable telemetry or storage.

Partial decoupling has limited value when:

- MLflow is already approved as runtime observability and learning platform.
- Evidence volume and retention fit tested MLflow limits.
- One team owns runtime tracing and evaluation operations.
- Native attachment and investigation UX has direct operator value.
- No funded alternate backend, consumer, region, or regulatory requirement
  exists.
- External storage duplicates evidence already persisted reliably in StateStore
  or MLflow.
- OTel is introduced only as a different API in front of the same MLflow
  destination.

## Required Evidence Before Decision

| Decision input | Evidence required |
|---|---|
| Business flexibility | Revenue or deployments requiring customer-owned OTel, storage, or regional isolation |
| Delivery cost | Engineering estimate including IAM, redaction, retention, deletion, replay, reconciliation, and support |
| Operating cost | Trace ingestion, attachment storage, object storage, egress, backup, and on-call cost at expected volume |
| Runtime risk | Tested behavior during MLflow outage, credential expiry, queue overflow, and sustained backpressure |
| Evidence scale | Representative p50, p95, and maximum trajectory size and daily volume |
| Learning latency | Maximum acceptable delay from runtime completion to MLflow dataset eligibility |
| Governance | Tenant isolation, content classification, residency, legal hold, and deletion obligations |
| User experience | Whether native MLflow attachment inspection changes reviewer time or decision quality |
| Option value | Named alternate consumer or tested migration path, not portability as an abstract goal |
| Ownership | Team and SLO for every collector, store, index, bridge, and reconciler introduced |

## Conditional Decision Guide

| Dominant value | Architecture with strongest fit |
|---|---|
| Fastest learning-loop delivery and lowest near-term TCO | A. MLflow-native |
| Standard runtime instrumentation with MLflow retained as destination | B. OTel transport to MLflow |
| Runtime outage isolation and customer-controlled observability | C. OTel plus external evidence |
| Independent evidence retention, access, or large-object economics | C. OTel plus external evidence |
| Multi-consumer evidence platform or credible downstream vendor exit | D. Owned evidence ledger |
| Native MLflow learning evidence plus required operational OTLP visibility | E. Bounded dual export |

## Invariants Across Options

Architecture selection does not remove these requirements:

- Generate stable evidence identity before first write.
- Bind raw evidence to tenant, producer identity, schema version, and content
  digest.
- Never let telemetry sampling determine learning evidence eligibility.
- Use at-least-once delivery with idempotent materialization rather than assume
  exactly-once writes.
- Represent missing, partial, expired, or inaccessible evidence explicitly.
- Bind each MLflow dataset record to source evidence digest and projector
  version.
- Keep OTel, native runtime, artifact, MLflow trace, dataset, and evaluation IDs
  as distinct typed references.
- Define reconciliation for orphan evidence, missing projections, digest
  mismatch, duplicate records, and incomplete deletion.
- Remote evidence publication must not gate the customer response. Profiles may
  enqueue or buffer evidence before the runtime span ends.

## Decision Boundary

This decision should be recorded as one of:

- **MLflow owns runtime evidence and downstream learning evidence.**
- **MLflow owns downstream learning evidence; OTel standardizes runtime
  transport.**
- **External storage owns raw runtime evidence; MLflow owns projected datasets,
  evaluations, assessments, and lineage.**
- **Platform-owned ledger owns canonical evidence; OTel and MLflow are
  projections.**

Each statement describes a different authority and operating model. None removes
the current structural MLflow dependency without a separate decision to replace
dataset, evaluation, scorer, assessment, and lineage semantics.
