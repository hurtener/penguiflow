# ReAct planner eval guide

## What this guide solves

This guide shows how to create a fresh eval loop for a new ReAct planner agent, including:

- interactive dataset creation in Playground
- multi-turn scenario curation
- metric authoring with structured checks
- operationalizing the final loop in `penguiflow eval`

## Mental model

Use both surfaces together:

- Playground: discover behavior, curate cases, debug failures
- `penguiflow eval`: run committed specs, rerun deterministically, gate CI

In short: author in Playground, operationalize in CLI.

## Step 0: project and env setup

Create your project and put secrets in project-root `.env`:

```bash
uv run penguiflow new my-agent --template react
cd my-agent
uv sync
```

Add credentials and model settings in `./.env`.

Env behavior (same as `penguiflow dev`):

- eval commands autoload `<project_root>/.env`
- existing process env vars are not overwritten
- do not rely on spec-level env file lists

## Step 1: run Playground and exercise real scenarios

```bash
uv run penguiflow dev --project-root .
```

Use `Chat` to run realistic prompts (not only happy paths):

- normal requests
- ambiguous/underspecified requests
- policy-sensitive or tool-routing-sensitive requests
- follow-up turns that force the planner to revise course

## Step 2: build a dataset interactively from traces

In `Eval` / `Trajectory`:

- tag traces you want to keep
- export tagged traces into a dataset bundle (`dataset.jsonl`, `manifest.json`)
- load existing datasets for review/reruns

Export defaults in Playground:

  - with `agent_package`: `<project_root>/src/<agent_package>/evals/playground_export/dataset` when that package exists below `src/`, otherwise `<project_root>/<agent_package>/evals/playground_export/dataset`
- without `agent_package`: `<project_root>/evals/playground_export/dataset`
- existing targets are auto-renamed (`dataset-2`, `dataset-3`, ...) instead of overwritten

Dataset split rule:

- at least one `val` row is required
- `test` rows are optional for diagnostic iterations
- if `min_test_score` is set but no `test` split exists, threshold status is reported as `null`

## Step 3: curate multi-turn cases (important)

Even though eval runs one row at a time, rows can still come from multi-turn sessions.

Use this rule:

- one dataset row = one evaluation target

That target may be derived from:

- a whole single-turn trace
- the final outcome of a multi-turn session
- a specific intermediate turn/subtask from a multi-turn session

Why this matters for ReAct planners:

- many regressions happen in routing/tool choice before final answer text
- intermediate mistakes can be the real failure target

## Step 4: write a structured metric

Use a deterministic, trace-aware metric first.

For planner-native metrics, prefer `penguiflow.evals.helpers` inside a normal metric instead of ad hoc trace parsing.

Recommended shape:

- define with `@metric(...)`
- include stable criterion ids
- return per-case `checks`
- keep `feedback` concise and failure-oriented

Rule of thumb:

- use plain Python equality for trivial scalar checks
- use `extract_node_sequence(...)` when you only need route/tool order
- use `sequence_match(...)` for exact route or route-prefix checks
- use `trajectory_subset_match(...)` when extra observed calls are acceptable but one expected route/tool pattern must appear
- use `extract_step_args(...)` / `extract_step_subset(...)` plus `step_args_match(...)` for argument-level checks
- use `llm_judge(...)` for qualitative outcome checks, not for basic routing validation

Example skeleton:

```python
from penguiflow.evals import metric
from penguiflow.evals.helpers import extract_node_sequence, sequence_match, trajectory_subset_match


@metric(
    name="Policy Compliance",
    criteria=[
        {"id": "starts_with_triage", "label": "Starts with triage"},
        {"id": "uses_expected_tool", "label": "Uses expected tool"},
    ],
)
def score(gold, pred, trace=None, pred_name=None, pred_trace=None):
    tools = extract_node_sequence(pred_trace)
    checks = {
        "starts_with_triage": sequence_match(tools[:1], ["triage_query"], mode="strict"),
        "uses_expected_tool": trajectory_subset_match(
            pred_trace,
            {"node_sequence": ["answer_general"]},
            mode="subset",
        ),
    }
    failed = [key for key, ok in checks.items() if not ok]
    return {
        "score": 1.0 if not failed else 0.0,
        "checks": checks,
        "feedback": "all checks pass" if not failed else f"failed: {', '.join(failed)}",
    }
```

Tiny qualitative example:

```python
import os

from penguiflow.evals import metric
from penguiflow.evals.helpers import llm_judge


@metric(
    name="User Satisfaction",
    criteria=[{"id": "helpful_outcome", "label": "Helpful outcome"}],
)
async def satisfaction_metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
    del trace, pred_name, pred_trace
    question = str(gold.get("question") or "") if isinstance(gold, dict) else ""
    return await llm_judge(
        prompt="Score whether the answer is helpful for the user's request.",
        inputs={"question": question},
        outputs={"answer": str(pred or "")},
        model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
    )
```

Helper composition patterns:

Missing expected node detection:

```python
from penguiflow.evals.helpers import trajectory_subset_match


def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
    del gold, pred, trace, pred_name
    found_expected_tool = trajectory_subset_match(
        pred_trace,
        {"node_sequence": ["recommend_fix"]},
        mode="subset",
    )
    return {
        "score": 1.0 if found_expected_tool else 0.0,
        "checks": {"uses_recommend_fix": found_expected_tool},
        "feedback": None if found_expected_tool else "Expected tool 'recommend_fix' was not used.",
    }
```

Repeated-call arg validation:

```python
from penguiflow.evals.helpers import extract_step_args, step_args_match


def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
    del gold, pred, trace, pred_name
    observed = extract_step_args(pred_trace, node_name="search_documents")
    matched = any(
        step_args_match(actual, {"query": "architecture constraints"}, mode="subset")
        for actual in observed
    )
    return {
        "score": 1.0 if matched else 0.0,
        "checks": {"searched_for_constraints": matched},
        "feedback": None if matched else "No search_documents call used the expected query.",
    }
```

Multiple expected arg specs for the same tool:

```python
from penguiflow.evals.helpers import extract_step_args, step_args_match


def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
    del gold, pred, trace, pred_name
    expected_specs = [
        {"query": "architecture"},
        {"query": "constraints"},
    ]
    observed = extract_step_args(pred_trace, node_name="search_documents")
    checks = {
        f"expected_search_{index}": any(
            step_args_match(actual, expected, mode="subset") for actual in observed
        )
        for index, expected in enumerate(expected_specs, start=1)
    }
    score = sum(1.0 if ok else 0.0 for ok in checks.values()) / len(checks)
    return {"score": score, "checks": checks}
```

Avoid:

- exact-matching full raw trajectories
- generic JSON containment helpers in metric code
- using an LLM judge where one deterministic route check would do

## Step 5: debug the metric in Playground

Run eval in Playground with your `metric_spec` and inspect:

- low-scoring rows
- failed criteria/checks
- prediction traces linked from rows
- divergence review between reference and actual

Use copy actions for external triage:

- `Copy` in `actual` view (full trajectory JSON)
- `Copy` in `reference` view (full trajectory JSON)
- `Copy` in `divergence` view (structured diff JSON)

These are optimized for PR comments/issues/prompts where raw JSON is noisy.

## Step 6: freeze the workflow into committed specs

Create and commit:

- `collect.spec.json`
- `evaluate.spec.json`
- dataset bundle (`dataset.jsonl`, `manifest.json`)
- metric module

Then run CLI commands:

```bash
uv run penguiflow eval collect --spec path/to/collect.spec.json
uv run penguiflow eval evaluate --spec path/to/evaluate.spec.json
```

Start baseline-only first (omit `candidates_path`) and add candidate sweeps later.

## Step 7: CI and regression policy

Use `min_test_score` to enforce a threshold gate in CI.

Typical progression:

1. stabilize baseline metric and dataset
2. validate repeatability locally
3. enforce threshold in CI
4. add candidate sweeps only when needed

## Common mistakes to avoid

- over-indexing on final answer text for planner metrics
- mixing many targets into a single row
- skipping multi-turn/intermediate behaviors during dataset curation
- storing secrets in committed files instead of `.env`

## Related docs

- [`penguiflow eval`](eval-command.md)
- [Playground eval workflow](playground-evals.md)
- [`penguiflow dev`](dev-command.md)
- Enterprise reference example: `examples/planner_enterprise_agent_v2/evals/policy_compliance_v1/README.md`
