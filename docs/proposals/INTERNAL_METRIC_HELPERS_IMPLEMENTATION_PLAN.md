# Internal Metric Helpers Implementation Plan

## Status

Partially implemented

## Current Status

Done:

- `penguiflow.evals.helpers` module added
- trace projection helpers shipped:
  - `extract_node_sequence(...)`
  - `extract_terminal_node(...)`
  - `extract_step_args(...)`
  - `extract_step_subset(...)`
- deterministic match helpers shipped:
  - `sequence_match(...)`
  - `step_args_match(...)`
  - `trajectory_subset_match(...)`
- native judge helper shipped:
  - `llm_judge(...)`
- enterprise example rewritten to use helper composition for deterministic route checks
- enterprise qualitative example added with `satisfaction_metric`
- enterprise eval README updated with helper composition notes
- main eval guide updated with helper examples and rule-of-thumb guidance

Pending:

- broadened documentation pattern catalog beyond the current guide examples if more cases emerge
- improve the qualitative judge example over time as a stronger reference rubric
- optional report-layer follow-up work:
  - criterion rollups
  - score spread diagnostics

Not planned in this workstream:

- evaluator factories
- dataset abstraction changes
- raw full-trajectory exact-match helpers

## Summary

This document scopes a small addition to `penguiflow.evals`: planner-native helper tools that make metric internals easier to write.

The goal is not to add a new evaluator framework. The goal is to reduce repeated parsing, matching, and LLM judge glue inside metrics that already follow the current GEPA-compatible contract:

```python
def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
    ...
```

Implementation priority for this plan is:

1. metric-internal helpers first
2. helper-proving example metrics second
3. helper composition docs and enterprise-example rewrite third
4. report-layer score and criteria aggregation later, only after helper scope is solid

## In Scope

- deterministic helpers for extracting stable projections from `pred_trace` and `gold_trace`
- deterministic helpers for comparing planner-native trajectory subsets derived from those projections
- a Pengui-native LLM judge helper built on `penguiflow.llm.LLMClient`
- example metrics showing helper usage inside normal metrics
- concise documentation of common helper-composition patterns inside normal metrics
- docs that frame these as metric-internal helpers, not as a separate eval runtime

## Out of Scope

- evaluator factories like `create_*_evaluator`
- new dataset abstractions
- changes to `evaluate_dataset()` orchestration
- full raw trajectory exact matching as a first-class pattern
- generic JSON comparison helper utilities
- mirroring `openevals` API shape

## Why This Exists

Current metrics often need to do one or more of these:

- re-parse `pred_trace["steps"]`
- extract node sequences by hand
- compare only one stable part of a trajectory
- call an LLM and normalize the result into `score` plus `feedback`

Those patterns are valid, but today each project has to rebuild them locally. This plan centralizes the repeated internals while keeping the top-level metric contract unchanged.

## Design Rules

1. Helpers operate inside metrics, not instead of metrics.
2. Prefer planner-native trajectory shapes over generic chat-message abstractions.
3. Keep helper APIs planner-native and hide the underlying JSON/dict shape.
4. Keep helper APIs small and unsurprising.
5. LLM judge uses PenguiFlow's native LLM layer.
6. For the judge helper, match planner ergonomics: accept `client` first, allow `model` fallback.
7. Preserve GEPA compatibility by keeping the metric callable contract unchanged.
8. Keep helper work as the primary implementation priority; reporting enhancements are secondary.

## Helper Acceptance Criteria

The extraction and matching building blocks should be sufficient to construct the following scenario classes inside normal metrics, without introducing a separate evaluator abstraction:

- exact node or route match
- subset node or route match where additional calls are allowed
- missing expected call detection
- partial argument matching on selected planner step fields
- repeated-call matching where any matching call satisfies the expectation
- multiple expected argument specs for the same node or tool, where each expected spec must be satisfiable by at least one observed call

These are acceptance criteria for `penguiflow.evals.helpers`, not a new public `ReliabilityEval`-style API.

## Proposed Surface

Start with one small public module:

`penguiflow.evals.helpers`

Initial helper families:

1. Trace projection helpers
2. Deterministic trajectory subset match helpers
3. Native LLM judge helper

Secondary helper-adjacent additions, after the helper surface is stable:

4. report-layer criterion rollups
5. report-layer score distribution stats

Documentation rollouts after helpers land:

6. rewrite the current enterprise example metrics to use helpers
7. add a native LLM judge example for a relevant enterprise-style qualitative metric
8. add a concise pattern catalog showing common helper combinations

Expected early functions:

```python
extract_node_sequence(pred_trace) -> list[str]
extract_terminal_node(pred_trace) -> str | None
extract_step_args(pred_trace, node_name: str | None = None) -> list[dict[str, Any]]
extract_step_subset(pred_trace, node_name: str | None = None, fields: list[str] | None = None) -> list[dict[str, Any]]

sequence_match(actual, expected, mode="strict|unordered|subset|superset") -> bool
step_args_match(actual, expected, mode="subset|superset", fields: list[str] | None = None) -> bool
trajectory_subset_match(pred_trace, expected_subset, mode="subset|superset") -> bool

await llm_judge(
    *,
    prompt: str,
    inputs: object | None = None,
    outputs: object | None = None,
    reference_outputs: object | None = None,
    client: LLMClient | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    use_reasoning: bool = True,
) -> dict[str, object]
```

## Recommended First Metrics

1. Route compliance
Why: deterministic, cheap, and already common in planner evals.

2. Terminal-node correctness
Why: clear example of using plain Python equality on a stable projection while helpers handle extraction.

3. User satisfaction judge
Why: strong first LLM-judge example that does not depend on exact wording.

## Documentation Intent

The docs for this work should show how the helper building blocks compose inside real metrics.

Prefer:

- a short pattern catalog with concrete planner-oriented cases
- enterprise example metrics rewritten to use helpers
- one relevant native LLM judge example

Avoid:

- large abstract combinator matrices
- generic evaluator-framework framing
- examples that speak in JSON utility terms instead of planner terms

## Increment Plan

### Increment 0: Freeze Scope and Names

Status: done

Goal:
- commit to helper-only scope
- avoid evaluator-factory creep
- choose the first public module path

Deliverables:
- this plan document
- agreed naming for `penguiflow.evals.helpers`
- agreed LLM judge shape: `client` or `model`

Exit criteria:
- no ambiguity about scope
- no dependency on broader eval refactors

### Increment 1: Add Trace Projection Helpers

Status: done

Goal:
- remove repeated `pred_trace` parsing from project metrics

Deliverables:
- `extract_node_sequence(pred_trace)`
- `extract_terminal_node(pred_trace)`
- `extract_step_args(pred_trace, node_name=None)`

Behavior notes:
- accept malformed or partial traces safely
- return empty lists / `None` instead of throwing for common missing-shape cases
- document that these helpers target planner-native `steps[].action`

Tests:
- happy path with normal trajectory steps
- missing `steps`
- non-mapping `pred_trace`
- mixed malformed step payloads
- node filter behavior for `extract_step_args`

Exit criteria:
- example project metric no longer needs bespoke step parsing helpers

### Increment 2: Add Deterministic Trajectory Subset Match Helpers

Status: done

Goal:
- support planner-native subset comparisons without pushing users toward raw JSON or full trajectory equality

Deliverables:
- `sequence_match(..., mode="strict|unordered|subset|superset")`
- `step_args_match(..., mode="subset|superset", fields=None)`
- `trajectory_subset_match(pred_trace, expected_subset, mode="subset|superset")`

Behavior notes:
- `sequence_match` is the main trajectory-oriented primitive
- `step_args_match(fields=[...])` allows cherry-picked arg comparison without exposing generic JSON helper APIs
- `trajectory_subset_match(...)` should accept planner-native expected subsets derived from extraction helpers, not arbitrary JSON DSLs
- trivial scalar equality should remain plain Python inside metrics
- these helpers should be sufficient to reconstruct common planner reliability scenarios inside normal metrics
- subset route matching should support "extra observed nodes allowed" semantics
- repeated node occurrences should behave deterministically
- partial argument matching should operate on extracted planner step args, not generic tool-call envelopes

Tests:
- sequence strict vs unordered vs subset vs superset
- step-arg subset vs superset
- selected-field compare including dotted nested paths if supported in v1
- subset matcher on small planner-native expected shapes
- subset route matching where extra observed nodes are allowed
- strict route matching where unexpected observed nodes cause failure
- repeated node occurrences
- multiple expected arg specs for the same node or tool
- malformed step payloads or missing action payloads should not break helper behavior

Exit criteria:
- a route-compliance metric can be expressed mostly as helper calls
- helper composition is sufficient to express the acceptance scenarios listed above inside normal metrics

### Increment 3: Add Pengui-Native LLM Judge Helper

Status: done

Goal:
- remove repeated judge prompt + structured parsing glue from metrics

Deliverables:
- `llm_judge(...)` in `penguiflow.evals.helpers`
- internal response model for `score` and optional `feedback`

Behavior notes:
- require exactly one of `client` or `model`
- prefer `client` when provided
- use `penguiflow.llm.LLMClient`
- return normal metric payload shape: `{"score": ..., "feedback": ...}`
- keep prompt API minimal in v1

Tests:
- passing prebuilt client
- fallback model string path
- invalid `client` and `model` combination
- structured response normalization
- judge failure surfaces clearly to metric authors

Exit criteria:
- an example satisfaction metric can call one helper instead of owning LLM glue

### Increment 4: Add Example Metrics

Status: partial

Implemented now:
- deterministic route compliance example
- deterministic stricter failure/demo example
- qualitative `llm_judge(...)` example via `satisfaction_metric`

Remaining if desired later:
- additional committed example modules beyond the enterprise example
- more standalone example coverage for terminal-node-only or selected-step-field-only checks

Goal:
- prove helpers are enough for real metric internals

Deliverables:
- deterministic example using route/node helpers
- focused structural comparison example on terminal node, label, or selected step fields
- LLM-judge example for user satisfaction or conversation outcome
- deterministic example showing route compliance with extra calls allowed
- deterministic example showing missing expected node detection
- deterministic example showing argument-level validation across one or more repeated node or tool calls

Behavior notes:
- examples should stay small and readable
- examples should show helper composition inside plain metrics
- examples should not introduce new framework layers
- examples should speak in planner concepts, not JSON utility concepts

Tests:
- example metric unit tests
- at least one negative case per example

Exit criteria:
- docs can point to concrete examples instead of pseudocode only

### Increment 5: Rewrite Enterprise Example and Add Composition Docs

Status: done

Note:
- helper composition guidance now lives in the enterprise README and main eval guide
- the example code itself is treated as the smoke-test/reference example rather than something that needs dedicated unit-test coverage

Goal:
- make the helper layer concrete through the existing enterprise reference example and concise composition guidance

Deliverables:
- rewrite `examples/planner_enterprise_agent_v2/evals/metrics.py` to use the new helpers
- update the enterprise eval README to explain the helper-based metrics
- add a short helper-composition section to eval docs
- add one native LLM judge example with a relevant enterprise-style qualitative metric such as user satisfaction

Behavior notes:
- use the enterprise example as the canonical proof that helpers are enough for real metrics
- keep composition guidance pragmatic: exact route match, subset route match, missing expected node, repeated-call arg validation, and qualitative judge example
- the LLM judge example should complement the deterministic route metric instead of replacing it

Tests:
- existing enterprise metric tests updated to reflect helper usage
- at least one deterministic and one LLM-judge example referenced in docs

Exit criteria:
- users can learn helper composition from one real example instead of only proposal text

### Increment 6: Add Secondary Report-Layer Aggregations

Status: pending

Goal:
- improve eval summaries without changing helper scope or metric signatures

Priority:
- secondary to helper implementation and helper-proving examples

Deliverables:
- criterion rollups derived from structured metric `checks`
- lightweight score spread diagnostics beyond mean

Behavior notes:
- this work lives at the eval summary, optional JSON report, and Playground eval-result presentation layers
- this work must not change dataset schemas, trace export schemas, or the metric callable contract
- criterion rollups should summarize pass count, fail count, and pass rate when metrics return structured `checks`
- score diagnostics should stay lightweight, such as min, max, and median and/or stddev

Tests:
- summary aggregation when metrics return structured `checks`
- summary behavior when metrics return score-only payloads
- score spread reporting on small deterministic datasets

Exit criteria:
- eval summaries provide richer aggregate signal while preserving the existing metric API unchanged

### Increment 7: Document Usage in Eval Guides

Status: mostly done

Implemented now:
- rule-of-thumb helper guidance in the main eval guide
- helper composition examples for route checks, missing expected node detection, repeated-call arg validation, multiple expected arg specs, and qualitative judging

Remaining if desired later:
- further doc polish if more helper patterns emerge in real projects

Goal:
- teach users when to use helpers and when not to use them

Deliverables:
- update eval guide docs with helper-based examples
- brief rule-of-thumb section:
  - use plain Python equality for trivial scalar checks
  - use extraction helpers to derive planner-native subsets
  - use sequence and step-arg helpers for trajectory-derived checks
  - use LLM judge for qualitative outcome checks

Behavior notes:
- emphasize that helpers live inside normal metrics
- explicitly warn against exact-matching full complex trajectories
- explicitly warn against adding generic JSON helper semantics to this surface

Exit criteria:
- users can author helper-based metrics without reading proposal docs

## Risks and Guardrails

### Risk: Helper scope expands into a second eval framework

Guardrail:
- do not add evaluator factories in this workstream

### Risk: Users treat exact match as full trajectory equality

Guardrail:
- do not ship an `exact_match` helper in this workstream
- docs and examples should show plain Python equality for trivial checks

### Risk: Helper surface drifts into generic JSON utilities

Guardrail:
- helper names and docs should use planner terms like node sequence, step args, and trajectory subset
- avoid exposing generic JSON containment/distance APIs from `penguiflow.evals.helpers`

### Risk: Judge helper becomes provider-specific glue

Guardrail:
- build only on `LLMClient`
- keep provider handling inside the native LLM layer

### Risk: Too many helpers too early

Guardrail:
- ship the smallest set that unlocks real metrics
- add more only when two or more metrics need the same pattern

## Acceptance Standard

This work is successful when:

- metric authors can write trace-aware metrics with much less bespoke parsing
- the helper building blocks are sufficient to express common route, missing-call, and partial-arg planner checks inside normal metrics
- qualitative metrics can reuse one native LLM judge helper
- the GEPA-compatible metric contract stays unchanged
- no new evaluator factory layer is introduced
- trivial equality checks remain plain Python instead of being wrapped for style only
- deterministic helpers remain planner-native instead of becoming generic JSON utilities
- any criteria rollups or score distribution summaries remain downstream report concerns, not part of the metric callable interface

## Nice Later, Not First

- richer nested-field selection helpers
- helper support for trajectory observation comparisons
- optional multi-check structured judge outputs
- helper-level cost metadata propagation for judge calls
- prebuilt prompt templates for common qualitative metrics
