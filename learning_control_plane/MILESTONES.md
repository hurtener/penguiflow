# Learning Control Plane milestones

## Milestone 0 — Foundation and boundaries

**Goal:** create the standalone package, document responsibility boundaries, and
make the package discoverable by the repository build.

**Includes:** this directory structure, MVP scope, architecture, and a package
layout test.

**Does not include:** runtime behavior, background jobs, telemetry setup, MLflow,
candidate generation, or delivery.

**Done when:** the package imports, the package layout is tested, and the remaining
milestones have explicit completion criteria.

## Milestone 1 — Evidence plumbing: OpenTelemetry and MLflow

**Goal:** publish versioned trace and evaluation evidence to OpenTelemetry and
MLflow without affecting agent availability.

**Includes:** trace/evaluation correlation IDs, redaction boundary, MLflow artifact
and lineage conventions, and a no-op/degraded mode when either backend is absent.

**Done when:** a standalone evaluation run produces attributable evidence that can
be queried by run, dataset, candidate, agent bundle, and metric version.

## Milestone 2 — Standalone evaluation backend

**Goal:** expose a framework-independent evaluation contract that can be adopted
without enabling learning.

**Includes:** immutable dataset manifest, `RunOne`, metric, evaluation backend,
baseline/candidate paired result format, and local backend adapter.

**Done when:** the same agent bundle can be evaluated with and without an advisory
skill on a fixed held-out dataset, with complete per-case evidence.

## Milestone 3 — Offline learning decision loop

**Goal:** build the LCP core as a scheduled/offline service.

**Includes:** job state machine, candidate registry, deterministic candidate gate,
policy versioning, evidence links, and fail-closed retry behavior.

**Done when:** the core can select a candidate, request an evaluation, and create a
promotion decision without importing any agent framework.

## Milestone 4 — Human approval and scoped delivery

**Goal:** govern promotion and delivery of advisory skills.

**Includes:** review queue, approval/rejection audit trail, scope authorization,
expiry/revocation, delivery request, and immutable activation receipt.

**Done when:** only a human-approved passing candidate can be delivered to an
authorized tenant/project scope, and every delivery can be revoked.

## Milestone 5 — PenguiFlow provider and optional learning hook

**Goal:** integrate PenguiFlow without moving learning into the request path.

**Includes:** trajectory projection, advisory-skill candidate compiler, isolated
evaluation runner, scoped activation adapter, and an opt-in offline trace-publication
hook.

**Done when:** PenguiFlow can use the standalone evaluation backend alone or opt
into the full learning loop while continuing to serve if the LCP is unavailable.

## Post-MVP

Auto-sequence edges, automated candidate mining, canary rollout, multi-framework
providers, and advanced statistical policies are deliberately deferred until the
advisory-skill path is proven in production.
