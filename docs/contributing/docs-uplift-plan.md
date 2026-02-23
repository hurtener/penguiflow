# Docs uplift plan (phased)

This page is the canonical “how we get to enterprise depth” plan for PenguiFlow docs.

It is intentionally **procedural**: it tells you what to upgrade, in what order, and what “done” means.

## What “enterprise depth” means

For **Tier A** pages (the curated MkDocs site), the required rubric is defined in **[Docs style](docs-style.md)**.

For **Tier B** pages (long-form internal guides), the bar is:

- accurate to the code
- clearly labeled as “source material” vs “canonical guidance”
- includes pointers to the Tier A pages where operators should start

For Tier C notes (RFCs/proposals), the bar is: labeled and coherent, but not blocking.

## How work is tracked

- The complete file inventory and classification lives in **[Docs backlog](docs-backlog.md)**.
- A PR should update:
  - the Tier A pages it touches to meet the rubric, and
  - any Tier B source pages it extracts from to add canonical pointers.

## Tooling gates (must pass per PR)

- `uv run python scripts/check_md_links.py`
- `uv run mkdocs build --strict`

Optional (recommended once code snippets stabilize):

- add compile-only tests for the most important snippets (no network)

## Phase 0 — Triage + backlog (1 PR)

Goal: make the work measurable and avoid “shallow but long” docs.

Deliverables:

- `docs/contributing/docs-style.md` defines Tier A rubric and conventions
- `docs/contributing/docs-backlog.md` lists every `docs/**/*.md` file with Tier/area/outcome/phase

Done when:

- every file under `docs/` is listed in the backlog

## Phase 1 — ReactPlanner deepening (2–3 PRs)

Goal: make planner docs production-ready for implementers and operators.

Primary sources:

- `REACT_PLANNER_INTEGRATION_GUIDE.md` (Tier B)
- planner contracts in `penguiflow/planner/react.py` and `penguiflow/planner/models.py`

Tier A targets (must meet rubric):

- **[Planner overview](../planner/overview.md)** (core contracts, dispatch/concurrency, output contract)
- **[Actions & schema](../planner/actions-and-schema.md)** (LLM-facing contract, repair/arg-fill failure modes)
- **[Tool design](../planner/tool-design.md)** (typed schemas, side effects/idempotency, safe patterns)
- **[Parallel & joins](../planner/parallel-and-joins.md)** (join injection sources, skip rules, runbooks)
- **[Pause/resume (HITL)](../planner/pause-resume-hitl.md)** (StateStore requirements, token handling, recovery)
- **[Memory](../planner/memory.md)** (isolation, health states, persistence expectations)
- **[LLM clients](../planner/llm-clients.md)** (schema mode, streaming surfaces, provider gotchas)
- **[Observability](../planner/observability.md)** + **[Troubleshooting](../planner/troubleshooting.md)**

Done when:

- a new engineer can implement: catalog + tools + pause/resume + memory + parallel joins + event stream
- the planner pages include failure modes, monitoring guidance, and runnable patterns

## Phase 2 — ToolNode + integrations (2 PRs)

Goal: external tools are production-safe on paper (limits, redaction, OAuth, persistence).

Primary sources:

- `docs/tools/*-guide.md` (Tier B extraction sources)
- ToolNode contracts in `penguiflow/tools/*`

Tier A targets:

- `docs/tools/configuration.md`
- `docs/tools/artifacts-and-resources.md`
- `docs/tools/statestore.md`
- new pages: OAuth/HITL and MCP resources (when promoted into nav)

Done when:

- a platform team can deploy ToolNode-backed integrations with correct limits, redaction, OAuth, and durability

## Phase 3 — Core runtime deepening (1–2 PRs)

Goal: runtime semantics are precise (envelopes, backpressure, cancel, retries).

Primary sources:

- runtime code in `penguiflow/core.py`, `penguiflow/node.py`, `penguiflow/types.py`

Tier A targets:

- `docs/core/*`
- `docs/getting-started/*` (kept correct and runnable)

Done when:

- engineers can reason about ordering/backpressure/retries/cancel without reading runtime code

## Phase 4 — Operations runbooks (1–2 PRs)

Goal: deployment + observability docs are on-call ready.

Primary sources:

- `docs/deployment/worker-integration.md`
- observability primitives in `penguiflow/logging.py`, `penguiflow/metrics.py`, `penguiflow/middlewares.py`

Tier A targets:

- `docs/deployment/production-deployment.md`
- `docs/observability/*`

Done when:

- operators can answer: “why is it slow”, “why is it stuck”, “what broke”, “can I replay”

## Phase 5 — CLI + templates + contributor workflows (1 PR)

Goal: scaffolding and day-2 usage is obvious and correct.

Tier A targets:

- `docs/cli/*`
- `docs/contributing/*` (dev setup, testing, releasing)

Done when:

- a team can bootstrap an agent project and use the playground/tools without tribal knowledge

## Phase 6 — Legacy/manual consolidation (ongoing)

Goal: reduce duplication while keeping the curated site authoritative.

Primary sources:

- `manual.md`
- `PENGUIFLOW_BEST_PRACTICES.md`
- architecture/spec/pattern notes under `docs/`

Done when:

- no critical concept exists only in legacy long-form docs; a Tier A page exists and is linked
