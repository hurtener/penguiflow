# Learning Plane external service — skill-first low-level design

> Implementation appendix to [the working-session note](LEARNING_PLANE_MVP_WORKING_SESSION.md).
> This is a deliberately small external service: a portable advisory skill is
> the primary product output; a bounded executable workflow is secondary.

## 1. Product position

The Learning Plane is a running service, separate from PenguiFlow. It owns
canonical skill packages, their immutable versions, evidence, review state, and
activation audit. It does not import PenguiFlow or read a PenguiFlow StateStore.

Framework adapters submit safe structural evidence, run target-side evaluation,
and deliver active packages to their own runtime. They may adapt a package for a
framework but must not redefine its canonical meaning.

The MVP learns one procedure and can express it in two assets:

~~~text
successful executions + outcome evidence
                  |
                  v
        LearnedProcedureCandidateV1
                  |
           +------+------+
           |             |
           v             v
portable advisory    bounded executable
skill package        workflow asset
primary MVP          secondary MVP
~~~

The service proves this loop:

1. ingest executions and a small outcome vocabulary;
2. identify a recurring successful procedure in window W;
3. create a standard advisory skill package;
4. evaluate it on distinct, later W+1 cases;
5. let a human approve and confirm delivery;
6. have the adapter make it available to the target;
7. optionally compile a deterministic, read-only portion of that same procedure
   into a small workflow for PenguiFlow Auto-Seq.

The service has no target write credential. A skill is advisory: it does not
grant permission or execute a tool by itself.

## 2. Deliberate MVP decisions

| Concern | Decision |
| --- | --- |
| Runtime | Python 3.12, FastAPI, Pydantic v2. |
| Data | PostgreSQL 16 is the source of truth. |
| Deployment | One image, with API and worker processes. |
| Canonical skill | [Agent Skills](https://agentskills.io/specification) directory/ZIP. |
| Skill ownership | The external service owns immutable package/version content. |
| User authoring | Upload or constrained natural-language draft; private to author. |
| Learned asset | One advisory skill generated from evidence. |
| Executable asset | Optional 2–4 node, read-only, linear workflow. |
| Evaluation | Adapter runs local baseline/candidate evaluation on W+1. |
| Ingestion | Atomic HTTPS JSON batches and idempotency keys. |
| Background work | Worker claims learning runs directly from Postgres; no jobs framework. |
| Outcomes | positive, negative, retry, unknown; no general label platform. |

Out: UI, Kafka, Redis, vector DB, optimizer, marketplace, global skills,
automatic activation, scripts/code execution, writes, workflow branching,
loops, parallelism, and cross-tenant learning.

## 3. Ownership and runtime

~~~mermaid
flowchart LR
  subgraph Target["Agent environment"]
    A["Agent / framework"]
    AD["Framework adapter"]
    EV["Local evaluator"]
    A <--> AD
    AD --> EV
  end
  subgraph Plane["External Learning Plane"]
    API["FastAPI"]
    WK["Worker"]
    DB[("PostgreSQL")]
    API <--> DB
    WK <--> DB
  end
  AD -->|"catalog, observations, outcomes, capabilities"| API
  API -->|"skills, proposals, evaluation requests"| AD
  EV -->|"evaluation result"| API
  AD -->|"delivery confirmation"| API
~~~

The Learning Plane owns contract validation, package storage, procedure mining,
evaluation requests, proposals, and audit. The adapter owns source redaction,
raw local cases, live policy/capability checks, framework formatting, evaluator
execution, and target delivery. An operator or end user owns confirmation.

## 4. Canonical skill package

The canonical artifact follows the Agent Skills directory convention:

~~~text
customer-document-research/
  SKILL.md                 required
  references/              optional documents
  assets/                  optional non-executable resources
  scripts/                 standard directory, but not eligible in our MVP profile
~~~

SKILL.md has YAML frontmatter and Markdown content. The validator enforces the
published Agent Skills rules exactly: name is 1–64 lowercase alphanumeric or
hyphen characters, has no leading/trailing or consecutive hyphens, and matches
the root directory; description is non-empty and at most 1024 characters.
Optional standard fields such as compatibility, license, metadata, and
allowed-tools are preserved when valid. allowed-tools is informational standard
content only: it cannot widen the target's ToolPolicy or visibility.

Vendor/service metadata does not enter SKILL.md. Ownership, scope, origin,
evaluation, activation, and framework delivery live in the service records and
delivery envelope. A future portable extension must use a namespaced metadata
key, never an unprefixed vendor field.

### Advisory profile

The service can store a valid standard package, including its optional standard
directories, but the advisory-v1 activation profile is deliberately narrower:

- learned packages have SKILL.md and optional bounded UTF-8 references only;
- any package containing scripts, assets, or other binary files is stored as an
  immutable draft but is ineligible for MVP confirmation and activation;
- adapters inject SKILL.md and eligible UTF-8 references as planner context;
- adapters never mount or invoke package scripts;
- instructions can describe permitted tools, but cannot grant access or trigger
  them automatically.

This is a policy subset of the standard, not a format fork.

Example:

~~~text
---
name: customer-document-research
description: Research a customer document question when the user requests evidence. Use permitted document search and retrieval capabilities, then cite the inspected source.
compatibility: Requires document-search and document-retrieval capabilities.
---

# Customer document research

1. Search for candidate documents using the request.
2. Inspect the best permitted document before making claims.
3. If sources conflict or are incomplete, state what is uncertain.
4. Answer from the inspected source and cite it when supported.

If no permitted source is found, explain the limitation and ask for a narrower
request or a source.
~~~

## 5. Identity, scope, and authorization

Every request has an authenticated principal. Tenant and user identity are
derived from verified credentials, never trusted from payload, query, or header
values.

| Principal | Permitted MVP actions |
| --- | --- |
| Adapter service token | register target/capabilities, submit evidence, claim evaluation, fetch delivery bundle, submit delivery proof |
| Authenticated user | upload, draft, list, download, confirm, deactivate their own private skill |
| Tenant operator | start a learning run; view, approve/reject, and confirm a learned target skill |

A target registration binds tenant, agent deployment, registered OIDC
issuer/audience, and adapter. The adapter
publishes an immutable CapabilitySnapshotV1 with only safe facts:

~~~json
{
  "schema_version": "CapabilitySnapshotV1",
  "target_id": "uuid",
  "catalog_fingerprint": "sha256:...",
  "configuration_fingerprint": "sha256:...",
  "allowed_tool_names": ["documents.search", "documents.get"],
  "allowed_namespaces": ["documents"],
  "tool_side_effects": {"documents.search": "read", "documents.get": "read"},
  "skill_policy": {
    "max_skill_chars": 16000,
    "allow_references": true,
    "allow_scripts": false,
    "allow_auto_seq_workflows": true
  },
  "captured_at": "2026-08-26T15:00:00Z"
}
~~~

The snapshot constrains authoring and compatibility; it is not a permission
grant. The adapter rechecks live policy during delivery and evaluation.

Scopes are exact:

- learned skill: tenant + target + optional project pseudonym;
- user upload/draft: the same scope + exactly one owner_user_id;
- no global, shared, or cross-tenant skills in MVP.

A personal skill is visible, downloadable, confirmable, deactivatable, and
deliverable only to its authenticated author. For private delivery, the adapter
must present both its service credential and a target-issued OIDC/JWT user
assertion. The Learning Plane validates issuer, audience, signature, expiry,
and target binding against the registration, then derives owner_user_id as an
HMAC pseudonym of verified issuer plus subject. It never accepts an owner ID
from a query/header and does not retain the raw JWT. A tenant operator cannot
silently promote, confirm, or deactivate a personal skill. Conversely, a
learned skill has no owner and can be confirmed/deactivated only by a tenant
operator. The adapter fetches a personal version only for the same verified
subject.

## 6. External contracts

All JSON uses an exact schema_version. Unknown fields are rejected. Writes
require Idempotency-Key; the server stores the producer event key and canonical
payload digest. Same key/digest returns the original result; same key with new
content is 409. Raw prompts, tool arguments/results, secrets, and customer
documents are excluded.

### ExecutionObservationV1

~~~json
{
  "schema_version": "ExecutionObservationV1",
  "source_event_id": "obs-01J...",
  "target_id": "uuid",
  "source_trace_id": "trace-01J...",
  "source_session_id": "session-01J...",
  "started_at": "2026-08-25T14:30:00Z",
  "completed_at": "2026-08-25T14:30:08Z",
  "status": "ok",
  "configuration_fingerprint": "sha256:...",
  "catalog_fingerprint": "sha256:...",
  "intent_descriptor": {
    "taxonomy_version": "support-procedures-v1",
    "procedure_class": "customer_document_research",
    "confidence": "high"
  },
  "steps": [{
    "index": 0,
    "kind": "tool",
    "tool_name": "documents.search",
    "side_effect": "read",
    "input_shape": ["query"],
    "output_shape": ["selected_document_id"],
    "output_schema_fingerprint": "sha256:...",
    "duration_ms": 141
  }],
  "local_trace_ref": "opaque-local-ref",
  "redaction_profile": "structural-v1"
}
~~~

intent_descriptor is a bounded target-taxonomy classification produced by the
adapter, never raw user text. procedure_class must be one registered value for
the target; unknown is allowed for observation but cannot seed positive skill
learning. The server validates order, sizes, timestamps, taxonomy, and that
tool facts agree with the capability snapshot. W and W+1 manifests pin taxonomy
version/procedure_class and mining never crosses procedure classes. This gives
the learning service a meaningful trigger/procedure signal without collecting
raw prompts. The local reference stays meaningful only to the adapter and is
used to retrieve a local W+1 case.

### OutcomeSignalV1

~~~json
{
  "schema_version": "OutcomeSignalV1",
  "source_event_id": "out-01J...",
  "target_id": "uuid",
  "source_trace_id": "trace-01J...",
  "source_session_id": "session-01J...",
  "observed_at": "2026-08-25T14:31:00Z",
  "kind": "explicit_feedback",
  "label": "positive",
  "source": "product-feedback"
}
~~~

Allowed labels: positive, negative, retry, unknown. Signals may arrive before
observations and correlate by target plus trace/session identity. Missing is
unknown, not success. Positive signals select discovery material; negative/retry
excludes it. W evidence is never the final proposal authority: independent W+1
goal/outcome success is.

### SkillPackageManifestV1

Uploaders submit only a standard ZIP. The service derives and stores:

~~~json
{
  "schema_version": "SkillPackageManifestV1",
  "format": "agent-skills",
  "root_directory": "customer-document-research",
  "files": [{"path": "SKILL.md", "sha256": "sha256:...", "bytes": 1120}],
  "content_digest": "sha256:...",
  "has_scripts": false,
  "validation_profile": "advisory-v1"
}
~~~

content_digest uses sorted relative paths plus file bytes, not ZIP metadata.
Reject traversal, duplicate paths, symlinks, malformed YAML, missing
frontmatter, root/name mismatch, or oversize files. Scripts/assets/binary files
are valid for archival only when they meet package safety limits; they make a
version ineligible for advisory-v1 activation and delivery.

### LearnedProcedureCandidateV1

This is internal evidence, not a portable format:

~~~json
{
  "candidate_id": "uuid",
  "target_id": "uuid",
  "discovery_manifest_digest": "sha256:...",
  "capability_snapshot_digest": "sha256:...",
  "procedure_summary": {
    "intent_cluster": "customer-document-research",
    "recurring_tool_pattern": ["documents.search", "documents.get"],
    "positive_runs": 27,
    "negative_or_retry_runs": 1
  },
  "generated_skill_content_digest": "sha256:...",
  "optional_workflow_digest": "sha256:..."
}
~~~

### ExecutableWorkflowAssetV1

This is a portable structural asset, never executable code:

~~~json
{
  "schema_version": "ExecutableWorkflowAssetV1",
  "kind": "read_only_chain",
  "entry": {"after_tool": "documents.search"},
  "nodes": [{
    "id": "get-document",
    "tool_name": "documents.get",
    "side_effect": "read",
    "arguments": {"document_id": "$previous.selected_document_id"}
  }],
  "max_nodes": 4,
  "fallback": "return_to_planner",
  "catalog_fingerprint": "sha256:..."
}
~~~

It has 2–4 existing pure/read nodes, linear typed bindings, no customer-derived
literals, no branches/loops/parallelism/code, and returns to the planner for
ambiguity, error, changed catalog, or policy denial. A complete workflow is
evaluated as one unit; individually validated edges do not compose themselves.

### Evaluation and proposal

EvaluationRequestV1 pins candidate, W+1 manifest, capability snapshot, asset
kinds, and a target-local evaluator/rubric identifier, version, and digest.
Historical outcomes select and stratify cases; they do not score the candidate.
EvaluationResultV1 includes the same pins, per-case baseline/candidate verdicts
from that declared independent evaluator/reviewer, aggregate goal success,
trajectory tripwire results, and an adapter-owned per-case
CandidateSkillUseReceiptV1:

~~~json
{
  "evaluator": {
    "id": "customer-document-goal-rubric",
    "version": "1",
    "digest": "sha256:..."
  },
  "case_verdicts": [{
    "case_ref": "opaque-local-ref",
    "baseline": "pass",
    "candidate": "pass",
    "baseline_decision_path_digest": "sha256:...",
    "candidate_decision_path_digest": "sha256:..."
  }],
  "aggregate": {"baseline_goal_success": 0.73, "candidate_goal_success": 0.80},
  "candidate_skill_use_receipts": [{
    "case_ref": "opaque-local-ref",
    "candidate_content_digest": "sha256:...",
    "projection_digest": "sha256:...",
    "delivery_mode": "preflight",
    "selected": true
  }],
  "candidate_changed_case_count": 4,
  "improved_case_count": 3,
  "regressed_case_count": 0,
  "workflow_compiled": true,
  "candidate_configuration_fingerprint": "sha256:..."
}
~~~

CandidateSkillUseReceiptV1 is produced by the adapter, not inferred from a
planner event. candidate_content_digest must equal the exact requested package;
projection_digest identifies the deterministic framework projection;
delivery_mode is preflight or skill_get; and selected must be true for every
candidate case. PenguiFlow skills_retrieved and skill_get events corroborate
the receipt but are not proof because they do not carry the package digest.

Skill evaluation is governed by W+1 goal success from the pinned independent
evaluator/reviewer. A selected/injected candidate is necessary but insufficient:
the result must show at least one changed case, at least one improved case, zero
unexpected regressions, and a non-negative aggregate. Changed/improved/regressed
counts come from the pinned per-case verdicts and output or decision-path
digests. A fully tied unchanged candidate is rejected as no_effect. Trajectory
similarity is only a regression tripwire. A workflow additionally requires
whole-path compile/execution proof.

ImprovementProposalV1 references one primary canonical skill and zero or one
workflow. proposal_bundle_digest covers assets, scope, snapshot, and evaluation.
Individual package/workflow content digests retain artifact identity.

## 7. API

| Method | Path | Caller | Purpose |
| --- | --- | --- | --- |
| POST | /v1/targets | adapter admin | Register target. |
| POST | /v1/targets/{id}/capability-snapshots | adapter | Publish capability snapshot. |
| POST | /v1/executions:batch | adapter | Atomically ingest up to 100 observations. |
| POST | /v1/outcomes:batch | adapter | Atomically ingest up to 100 outcomes. |
| POST | /v1/learning-runs | tenant operator | Start W learning run. |
| POST | /v1/evaluation-requests:claim | adapter | Lease one target request. |
| POST | /v1/evaluation-requests/{id}/results | adapter | Submit local evaluation. |
| GET | /v1/proposals | tenant operator | List learned proposals. |
| POST | /v1/proposals/{id}/decisions | tenant operator | Approve/reject learned proposal. |
| POST | /v1/skills:upload | user | Upload private standard ZIP draft. |
| POST | /v1/skills:draft | user | Natural-language private preview. |
| GET | /v1/skills | user/operator | List authorized skills. |
| GET | /v1/skills/{id}/versions/{v}/download | authorized | Download canonical ZIP. |
| POST | /v1/skills/{id}/versions/{v}/confirm | owner or learned-skill operator | Atomically activate exact version. |
| POST | /v1/skills/{id}/versions/{v}/deactivate | owner or learned-skill operator | Deactivate exact active version. |
| GET | /v1/delivery/skills | adapter | Fetch active compatible packages. |
| POST | /v1/activations | adapter | Record actual delivery identity. |

All batches are all-or-nothing. There is no exposure endpoint: ordinary
post-activation observations contain the configuration fingerprint and can later
be used as descriptive evidence.

### Upload and draft

Upload validates the ZIP, persists immutable bytes/manifest, creates a private
draft, and returns a preview: frontmatter, rendered body, validation warnings,
content digest, and snapshot digest. It never activates automatically.

Natural-language draft receives target_id, user request, and optional project
pseudonym. The raw request is accepted transiently, size-bounded, excluded from
logs/persistence, and discarded after package creation. It is not an exception
to the raw-execution-prompt exclusion. The service uses the selected capability
snapshot to ask one configured draft model for a standard package. The prompt
requires advisory, bounded instructions, no secrets/customer raw text/code/
scripts, and no tool authority claim. The same validator returns a preview
only. The author must call confirm. Snapshot change requires a new preview.

### Delivery

The adapter fetches active packages using target, current snapshot digest, and
the verified user assertion where private delivery is requested. It receives
learned target packages plus personal packages only for that exact verified
subject. It adapts, rechecks live policy, and delivers through the framework
mechanism. Every response and receipt names skill_id, version, and
content_digest, plus configuration digest and optional workflow compilation.
This is an audit fact, not an instruction to turn on automatic execution.

## 8. Persistence and worker

Postgres has these durable tenant-scoped tables:

| Table | Purpose |
| --- | --- |
| targets | Agent deployment registration. |
| capability_snapshots | Immutable safe policy/catalog/config facts. |
| execution_observations | Structural evidence and idempotency digest. |
| outcome_signals | Minimal correlated labels. |
| learning_runs | W/W+1 lifecycle and direct worker lease. |
| learned_procedures | Candidates and immutable manifests. |
| skill_packages | Logical package identity, origin, scope, owner, state. |
| skill_versions | Immutable ZIP/manifest/content records. |
| workflow_assets | Optional immutable workflow. |
| evaluation_requests | Adapter-leased target-side evaluation. |
| proposals | Proposal bundle and append-only decisions/activation facts. |

Critical fields:

~~~sql
CREATE TABLE skill_packages (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, target_id uuid NOT NULL,
  owner_user_id text NULL,
  origin text NOT NULL CHECK (origin IN ('learned','user_upload','user_draft')),
  scope_project_pseudonym text NULL,
  state text NOT NULL CHECK (state IN ('draft','review','inactive','rejected')),
  current_version integer NOT NULL, active_version integer NULL,
  created_at timestamptz NOT NULL,
  UNIQUE (tenant_id, id)
);
CREATE TABLE skill_versions (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, skill_id uuid NOT NULL,
  version integer NOT NULL, content_digest text NOT NULL, manifest jsonb NOT NULL,
  package_bytes bytea NOT NULL, capability_snapshot_digest text NOT NULL,
  created_by_user_id text NULL, created_at timestamptz NOT NULL,
  UNIQUE (tenant_id, skill_id, version)
);
CREATE TABLE learning_runs (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, target_id uuid NOT NULL,
  discovery_manifest jsonb NOT NULL, evaluation_manifest jsonb NULL,
  capability_snapshot_digest text NOT NULL,
  state text NOT NULL, lease_owner text NULL, lease_until timestamptz NULL,
  created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL
);
~~~

Composite tenant foreign keys bind all related rows. Invariants: versions are
immutable; personal skills always have an owner and learned skills do not.
active_version being non-null is the sole activation truth; review state does
not activate delivery. Confirm atomically sets one exact version and deactivate
clears it only when it is that exact version. Identical package content may be
privately owned by different users. Active delivery requires active_version plus
a compatible snapshot; proposal has one skill and at most one workflow;
evaluation result digests must match its request.

No generic jobs table exists. The worker claims queued learning runs with
FOR UPDATE SKIP LOCKED, sets a ten-minute lease, refreshes before mining, and
reclaims expired leases. Failure is recorded with a concise diagnostic; weak
candidate supply is stopped, not failed. Evaluation requests use the same small
lease pattern because duplicate local evaluation is expensive.

## 9. Workflows

### Learn

1. Operator starts a W run for one target, snapshot, taxonomy version, and
   procedure class.
2. Worker checks that W has sufficient correlated positive evidence.
3. It groups clearly similar successful patterns within that procedure class;
   unknown, negative, and retry cases cannot seed positive learning.
4. It drafts and validates an Agent Skills package.
5. It may derive a compliant read-only workflow by rule, never by arbitrary code.
6. It snapshots a later, disjoint W+1 manifest and creates an evaluation request.

The implementation may use deterministic grouping plus one configured LLM draft
generator. The replaceable miner is not the product contract.

### Evaluate

The adapter claims the request and retrieves local raw W+1 cases by opaque
reference. Historical outcomes only select/stratify the cases. It runs baseline,
canonical skill, and optional skill-plus-workflow through the exact pinned
target-local evaluator/rubric. It returns per-case baseline/candidate verdicts,
aggregate goal success, and proof of injection/compilation. Default gate:

~~~text
minimum positive W observations: 20
minimum W+1 cases: 20
maximum candidate skill packages per run: 3
required W+1 goal-success delta: non-negative
maximum unexpected trajectory regressions: 0
minimum changed cases: 1
minimum improved cases: 1
maximum regressed cases: 0
workflow: no goal-success loss and whole-path proof
~~~

These are pilot configuration defaults, recorded with the proposal, not claims
of statistical certainty.

### Confirm and deliver

Learned: result passes -> proposal -> tenant operator approves -> confirms ->
adapter delivers -> adapter records delivery fact.

Personal: upload/draft -> author preview -> author confirms -> adapter delivers
only in that author scope -> author can deactivate. An adapter refusal after a
live policy change records a failed delivery and does not mutate package bytes.

## 10. PenguiFlow adapter

The adapter exports PR #115 trace/evaluation material into structural contracts
after redaction. It must not export trajectory_full, LLM context, raw tool
arguments, or raw outputs by default.

PenguiFlowSkillAdapter is an explicit adapter seam. It deterministically
projects a canonical package to a PenguiFlow runtime SkillProvider: description
becomes the local trigger and the canonical Markdown body is retained as the
instruction source without semantic rewriting. The same provider path is used
for evaluation and delivery. Per case, the adapter emits
CandidateSkillUseReceiptV1 with the exact candidate content digest, its
projection digest, selected flag, and preflight/skill_get mode. Existing
skills_retrieved and skill_get events are corroboration only. PR #115 supplies
only datasets and the evaluation harness; it is not this adapter or the
canonical skill contract.

PenguiFlow's current Auto-Seq does not provide learned multi-node workflow
composition. Slice 3 adds a narrow LearnedAutoSeqResolver/apply_workflow seam.
It matches the declared prior-tool entry, validates typed field projection and
binding at every node, invokes the exact declared tool only, caps the chain at
four pure/read nodes, and rechecks live catalog, ToolPolicy, and visibility.
Any mismatch, ambiguity, policy denial, or tool error returns to the planner.
The adapter records a chain-level receipt and evaluation proof. Normal
PenguiFlow gates remain authoritative; this seam never turns on a disabled
planner-level Auto-Seq switch.

## 11. State, failure, retention

~~~text
learning run: queued -> mining -> awaiting_evaluation -> complete
                       -> stopped | failed
skill review: draft -> review -> inactive
              draft/review -> rejected
activation truth: active_version NULL -> exact version -> NULL
proposal: proposed -> approved -> confirmed -> delivered
          proposed/approved -> rejected
evaluation: queued -> leased -> completed | invalid
            leased -> queued on expiry
~~~

Invalid ZIP, unsafe script, malformed frontmatter, invalid outcome, or changed
idempotency content is rejected. W/W+1 overlap, digest mismatch, a missing/
unselected digest-bound skill receipt, no_effect, or missing whole-path workflow
proof rejects the result. Workflow failure may still leave a skill-only
proposal. Worker crash is lease recovery.

Store observations/outcomes for 90 days by default. Retain immutable package
versions, proposal/evaluation summaries, and delivery audit until tenant
deletion. Before evidence ages out, retain compact aggregates cited by an
accepted proposal. Do not put tenant/user/trace/package identifiers in metric
labels.

## 12. Implementation slices and done

### Slice 0 — sizing

Use PR #115 material locally to prove one target has enough positive
outcome-linked W cases and later W+1 cases. Confirm canonical SKILL.md delivery
through a PenguiFlow runtime provider, and one evaluated 2–4 node Auto-Seq
chain. Stop if no credible procedure evidence exists.

### Slice 1 — authoring

Targets/capability snapshots, auth, atomic evidence ingestion, package
validator, package versions, private upload/draft/preview/confirm/deactivate,
and one PenguiFlow personal-skill delivery adapter.

### Slice 2 — learned skill

Direct learning-run claim, outcome correlation, procedure grouping, draft
generator, W+1 evaluation, proposal approval/confirmation, and delivery proof.

### Slice 3 — workflow

Rule-based chain extraction, strict validator, whole-path evaluation, and
PenguiFlow compilation. Workflow failure must not block a passing skill.

Tests must cover exact standard package validation across all three origins;
batch atomicity; idempotency; tenant/user isolation; verified-issuer/audience
OIDC owner derivation (no query/header owner override); immutable exact-version
activation/deactivation; W/W+1 taxonomy/class separation; outcome handling;
pinned independent-evaluator verdicts; exact digest-bound candidate-use receipts;
no_effect rejection for tied unchanged cases; workflow rejection for writes,
ambiguity, branches, loops, untyped bindings, or >4 nodes; and end-to-end
personal and learned skill delivery.

The MVP is done when an independent service receives a safe observation,
outcome, and capability snapshot; a user can preview/confirm a private standard
skill; one learned standard advisory skill passes W+1 and human approval; the
PenguiFlow adapter delivers it without enabling tool execution; and, only when
eligible, a complete bounded workflow is proven to compile to Auto-Seq.

## 13. Deferred

Shared skills, scripts, general DAGs, auto-activation, canaries/rollback,
global learning, broad outcome algebra, marketplace/search ranking, UI, and a
second framework adapter are explicit follow-up work.
 