---

# 🧩 Prompt Template for Codex (edit where marked)

You can paste this into a new chat with Codex:

```
You are implementing a small Python library called "penguiflow" for async agent orchestration.

## Context files to read (EDIT ME)
- penguiflow/README.md
- penguiflow/core.py
- penguiflow/node.py
- penguiflow/types.py
- penguiflow/registry.py
- penguiflow/patterns.py
- penguiflow/middlewares.py
- tests/* (start with test_core.py, test_types.py)
- examples/*

If some files don’t exist yet, create them following README and the plan below.

## High-level goals (do not change)
- In-process asyncio orchestrator with: typed messages (Pydantic v2), backpressure, retries, timeouts, graceful stop, routing, fan-out/fan-in, dynamic controller loops, and callable subflows.
- Core must be repo-agnostic: product repos register their Pydantic models at startup via ModelRegistry.

## Work plan (execute phase by phase; run tests after each)
1) Phase 0: scaffold repo as per README. Add pyproject, CI stubs, empty tests. Ensure `pip install -e .` works.
2) Phase 1: implement core runtime:
   - IceContext, Floe, PenguiFlow (OpenSea/Rookery).
   - fetch_any(), queue maxsize, graceful stop, error boundaries, cycle detection (with opt-in flag placeholder).
   - Add tests in test_core.py and simple examples.
3) Phase 2: types + registry + node validation:
   - Message/Headers (Pydantic v2), ModelRegistry (TypeAdapter cache), Node with NodePolicy and in/out validation.
   - Add tests in test_types.py/test_registry.py; example in examples/quickstart.
4) Phase 3: retries/timeouts/logging:
   - Implement retry with backoff, timeouts, structured logs; middleware hook points.
   - Add tests inducing failure; verify logs.
5) Phase 4: patterns:
   - map_concurrent, join_k, routers (predicate + union). Examples for routing and fanout/join.
   - Tests in test_patterns.py.
6) Phase 5: controller loop:
   - WM/PlanStep/Thought/FinalAnswer models; allow opt-in cycles on controller.
   - Wire controller→controller and controller→answer. Enforce hop/deadline budgets.
   - Tests in test_controller.py; example controller_multihop.
7) Phase 6: playbooks:
   - call_playbook() helper; inheritance of trace_id/headers; cancellation propagation.
   - Example playbook_retrieval; tests for completion and cancellation.

## Expectations / Acceptance Criteria (do not relax)
- Each phase merges only when unit tests + example(s) for that phase pass.
- Maintain ~85%+ coverage on core modules.
- Keep public API stable as documented in README.
- Code is clear, documented, and small. Every helper earns a test and example.

## Nice-to-have (if time allows after all phases)
- viz.py to export Mermaid/DOT for a flow graph.
- Simple metrics callback interface (queue depths, per-node latencies).

Start with Phase 0 now. Ask only if something is truly unclear; otherwise follow the plan and produce code + tests + examples per phase.
```

If you want, I can also tweak the README text to match your internal style guide or add concrete example code stubs for Phase 1 to speed Codex up.
