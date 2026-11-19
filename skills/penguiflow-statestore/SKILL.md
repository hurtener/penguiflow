---
name: penguiflow-statestore
description: "Deep expertise for PenguiFlow persistence: implement or review a `StateStore` backend (required + optional duck-typed capabilities), enforce correct contracts (idempotency, ordering, cursors, cancellation), wire A2A router continuity via conversation bindings, sessions/tasks/updates/steering and background-task orchestration, integrate `ArtifactStore`, support distributed execution (`RemoteBinding`), and back UI clients that need task updates, replay views and artifacts. Use when building/debugging StateStore or ArtifactStore implementations; designing database schemas; investigating missing/duplicated/out-of-order events; integrating A2A manager/specialist continuity, OAuth/HITL pause-resume; persisting trajectories/planner events; or deciding which store capabilities a frontend served by your HTTP layer depends on."
---

# PenguiFlow StateStore

## Overview

Use this skill to be the source-of-truth expert on PenguiFlow’s unified persistence surface:
- required `StateStore` audit-log + remote-binding methods,
- optional capabilities discovered via duck-typing (A2A conversation bindings, planner pause/resume, memory, tasks/updates/steering, trajectories, planner events, artifacts),
- how sessions/background tasks/projectors/frontends depend on these capabilities.

Hard boundaries vs siblings: the **application patterns** that consume these capabilities live elsewhere — [[penguiflow-hitl-pause-resume]] (pause/resume contract), [[penguiflow-memory]] (`save_memory_state`/`load_memory_state` usage), [[penguiflow-a2a-integration]] (`RemoteBinding` lifecycle), [[penguiflow-background-tasks]] (`TaskService` durability), [[penguiflow-deployment]] (production wiring). This skill is the persistence backend; siblings are the use cases.

## Quick navigation (open these first)

- `references/statestore-implementation-spec.md` — full contract (semantics, schemas, edge cases, checklist)
- `references/statestore-production-guide.md` — production guide + Postgres example + OAuth pause/resume notes
- `references/artifacts-and-resources.md` — `ArtifactStore` contract + planner/tool integration notes
- `references/distributed-execution.md` — distributed hooks, A2A router continuity, `RemoteBinding`

## Code map (workspace)

When you need exact behavior, open the code:
- `penguiflow/state/protocol.py`, `penguiflow/state/models.py`, `penguiflow/state/in_memory.py`, `penguiflow/state/adapters.py`
- `penguiflow/artifacts.py`
- `penguiflow/core.py` (best-effort audit log + best-effort `save_remote_binding`)
- `penguiflow/sessions/session.py`, `penguiflow/sessions/persistence.py`, `penguiflow/sessions/registry.py`, `penguiflow/sessions/task_service.py`
- `penguiflow/sessions/projections.py`
- `penguiflow/remote.py`, `penguiflow_a2a/*` (remote execution + bindings)

## Workflow decision tree

### Implement a new production `StateStore` backend

1. Decide which optional features must be durable in your deployment (A2A router continuity, OAuth/HITL pause, memory, tasks, steering, trajectories, planner events, artifacts).
2. Implement required methods first (`save_event`, `load_history`, `save_remote_binding`) with:
   - idempotent writes (dedupe/fingerprint/unique constraints),
   - stable ordering on reads (tie-breaker for equal timestamps),
   - cancellation-friendly async I/O and bounded timeouts.
3. If the deployment uses `A2AAgentToolset` router-to-specialist continuity, add the optional conversation binding methods: `find_binding`, `list_bindings`, `mark_binding_terminal`.
4. Add optional capabilities and verify cursor semantics (`since_id` is exclusive; ordering is stable).
5. Add an artifact store either by returning `artifact_store` or by having the store implement `ArtifactStore`.
6. Run and extend tests; include negative/error-path coverage for your backend.

### Debug “persistence is weird”

- Missing data: check capability detection (missing duck-typed methods) and feature wiring; use `require_capabilities`.
- Duplicates: verify idempotency keys/fingerprints and DB uniqueness.
- Out-of-order: ensure stable sorting and correct cursor handling.
- A2A specialist loses context: confirm `A2AAgentToolset` can see a store via `tool_context["state_store"]` or planner `_state_store`; confirm `save_remote_binding` persists `router_session_id`, `remote_skill`, `tenant_id`, `user_id`, `last_remote_task_id`, `is_terminal`, and `metadata`; confirm `find_binding` returns the newest non-terminal binding scoped by router session, agent URL, skill, tenant, and user.
- Steering failures: ensure steering sanitization and payload size limits are respected.
- OAuth/HITL resume fails across workers: ensure planner pause state is implemented with TTL and consume-on-load semantics.

### Build a frontend / UI client

- PenguiFlow doesn't ship a UI-facing HTTP server; expose sessions, replay and artifacts through your own HTTP layer.
- Ensure your deployment supports the store capabilities required for:
  - task state + updates (`list_tasks`, `list_updates`)
  - replay views (`load_history`, `list_planner_events`, `get_trajectory`)
  - artifacts (`artifact_store`)

## Helper scripts

- Run `scripts/statestore_inspect.py` to print the StateStore/ArtifactStore surface and (optionally) capability-check a concrete backend object.
- Run `scripts/sync_references_from_repo.py --repo <path>` to refresh bundled reference docs from a PenguiFlow repo checkout.
