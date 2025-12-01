# PenguiFlow Guide

## Snapshot
- Built the entire library in under 14 days; no downstream consumers right now, so risk is mostly examples/back-compat.
- Baseline: v2.3 ReAct planner is in place on top of the v2.1 distributed/A2A core.
- Immediate focus: v2.4 API Refinement & Production Hardening (plan.md).
- Next: v2.5 endgame polish (adaptive re-plan, budgets, parallel polish).

## Completed
- v2.1 Distributed & A2A: StateStore, MessageBus, RemoteTransport, A2A server adapter; streaming/cancel propagation; telemetry/durability hooks.
- v2.3 ReAct planner baseline: JSON-only LiteLLM loop + dspy adaptor for other llm providers, typed catalog from nodes/recipes, validation/repair, pause/resume with summaries, streaming/cancel propagation across nodes, parallel fan-out + join, structured trajectory logging.

## v2.4 API Refinement (active)
Goals: clean API surface, typed tool context, explicit joins, modularized planner, doc/example parity, release polish.

Phases (from plan.md):
1) Context split: add llm_context vs tool_context on run/resume; deprecate SerializableContext and ctx.meta; validate llm_context is JSON-serializable; keep backward-compat warnings.
2) ToolContext typing: new protocol; export from penguiflow.planner; _PlannerContext conforms; helper AnyContext for shared tools.
3) Explicit join config: join.inject mapping replaces magic field injection; keep implicit with warnings; update prompts/tests.
4) Modularize planner: break react.py into models/context/trajectory/constraints/hints/parallel/pause/llm/join; react.py becomes coordinator; public API stays stable via re-exports.
5) Docs/examples: migrate context usage, add MIGRATION_v24, refresh integration guide, add parallel join + typed tools examples; remove deprecated patterns.
6) Cleanup/release: ruff/mypy/pytest with coverage >=85%; perf sanity; changelog/version bump to 2.4.

Guardrails: keep compatibility shims, add deprecation warnings, add tests for new APIs and join inject; keep prompt parity after refactor (snapshot/fixture tests).

## v2.5 Endgame (after 2.4)
- Adaptive re-plan on failure with constraint manager (deadline/hop budgets) and structured error channel — already in `planner/react.py`.
- Token-aware trajectory compression with optional cheaper summarizer LLM; pause/resume durability via StateStore when present — already implemented.
- Parallel fan-out + joins with approvals/await-input patterns and examples (`react_parallel`, `react_pause_resume`, `react_replan`) — implemented; join_k-style semantics are not yet integrated into the planner executor.

## Non-Goals
- No heavy dependencies; remain asyncio + Pydantic v2; no built-in endpoints/UI/storage.

## Risks & Mitigations
- Refactor risk: add snapshot tests for prompts/trajectory serialization before and after module moves.
- Join change risk: warn on implicit injection; migrate examples in lockstep; document prompt changes.
- Back-compat risk: re-export internals in planner.__init__ and react.py shim; add import tests; keep deprecation warnings.
- With no external dependents, speed is fine; primary break surface is examples—update them alongside code.

## Coverage Policy
Target: >=85% line coverage (hard minimum in CI). Every new feature needs at least one negative/error-path test. Blind spots: middlewares.py, viz.py (DOT/Mermaid), types.py beyond StreamChunk. CI produces coverage XML and uploads to Codecov/Coveralls; badges track trends.

## CI/CD Policy
Matrix:
- Python: 3.11, 3.12, 3.13
- OS: Ubuntu

Checks enforced before merge:
- Ruff (lint)
- Mypy (types)
- Pytest with coverage (>=85%)

Artifacts:
- Store .coverage.xml
- Badges: Add CI status + coverage badge in README.

Optional:
- Performance benchmarks (pytest-benchmark)
- Upload coverage to Codecov/Coveralls

## Examples Policy
- Each example must be runnable directly: `uv run python examples/<name>/flow.py`
- Include a short README.md inside the example folder.
- Example must cover at least one integration test scenario.
- Examples should demonstrate real usage but remain domain-agnostic.
