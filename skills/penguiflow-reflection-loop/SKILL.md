---
name: penguiflow-reflection-loop
description: Explain, configure, and troubleshoot the ReactPlanner reflection loop: how post-answer critique, revision, and clarification work; how to wire `ReflectionConfig` and optional `reflection_llm`; how env-driven app config exposes the knobs; and how to verify behavior through planner events, metadata, and cost counters. Use when a user asks about "reflection loop", "self-critique", "reflection_critique", "quality_threshold", "max_revisions", or wants to compare reflection-enabled vs reflection-disabled runs.
---

# PenguiFlow Reflection Loop

## When to use

- Explaining how ReactPlanner's built-in answer-quality loop works.
- Wiring reflection into a manual `ReactPlanner(...)` construction.
- Retrofitting reflection into an app's env-driven `config.py` and `planner.py` files.
- Debugging whether a run passed critique, requested a revision, or fell back to clarification.

## When NOT to use

- General planner configuration outside reflection: use [[penguiflow-reactplanner-config]].
- Frontend event wiring and reducer behavior: owned by your UI client; see [[penguiflow-streaming]] for delivery helpers.
- Generic eval frameworks or offline benchmarking pipelines not tied to `ReactPlanner`.

## Hard boundaries

This skill covers the built-in `ReactPlanner` reflection path only:

- `ReflectionConfig`
- optional `reflection_llm`
- critique, revision, and clarification behavior
- runtime signals in metadata, cost, events, and logs

It does not describe arbitrary multi-agent reviewer patterns.

## Workflow

### 1) Find planner construction

Locate where the app constructs `ReactPlanner(...)`.

Reflection is inactive unless that constructor receives `reflection_config=...`.

### 2) Enable the feature explicitly

Use the public API import:

```python
from penguiflow.planner import ReflectionConfig
```

Then pass:

```python
reflection_config=ReflectionConfig(
    enabled=True,
    quality_threshold=0.85,
    max_revisions=2,
)
```

### 3) Choose shared vs separate critique model

- Shared model: simplest rollout; omit `use_separate_llm` and `reflection_llm`.
- Separate critique model: set `use_separate_llm=True` and provide `reflection_llm`.

Important rule:
- the critique model judges answer quality;
- the main planner client still writes revisions.

### 4) Verify behavior with runtime signals

Look for:

- `result.metadata["reflection"]`
- `result.metadata["cost"]["reflection_llm_calls"]`
- planner event `reflection_critique`
- planner event `reflection_clarification_generated` when quality never reaches threshold

### 5) Interpret "nothing changed"

That can be the healthy case:

- if the answer passes on the first critique, the user often sees the same answer,
- the visible difference is mostly latency and cost,
- logs and metadata still show that reflection ran.

## Load these references as needed

- `docs/planner/reflection-loop.md` - shareable external-facing guide with code examples.
- `tests/test_react_reflection.py` - best source for expected runtime behavior.
- `examples/planner_enterprise_agent_v2/main.py` - production-style planner wiring.
- `examples/planner_enterprise_agent_v2/config.py` - env-driven reflection settings.
- `penguiflow/planner/react_runtime.py` - reflection loop control flow.
- `penguiflow/planner/llm.py` - critique, revision, and clarification generation.

