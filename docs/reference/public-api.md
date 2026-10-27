# Public API surface

PenguiFlow’s public surface is exported from `penguiflow.__init__`.

## Import style

Prefer importing from `penguiflow` (top-level) for stable APIs:

```python
from penguiflow import PenguiFlow, Context, NodePolicy, ReactPlanner
```

## Stability expectations

- Public types and functions exported at top-level aim to be stable across 2.x.
- Internal modules and RFC/proposal documents may change without notice.

## Key entry points

- Runtime: `create()`, `PenguiFlow`, `Node`, `Context`, `NodePolicy`
- Concurrency: `map_concurrent`, `join_k`, routers
- Planner: `ReactPlanner`, `Trajectory`
- Skills: `SkillsConfig`, `SkillPackConfig`, `SkillProvider`, `SkillProviderFactory`, `SkillProposeRequest`
- Sessions: `StreamingSession`, `SessionManager`
- Tools: `tool`, `build_catalog`, `ToolLoadingMode`

## Eval namespace

`penguiflow.evals` is a supported workflow surface for trace-derived evaluation.

- Primary APIs: `collect_traces()`, `collect_and_export_traces()`, `export_dataset()`, `evaluate_dataset()`
- Spec loaders: `load_eval_collect_spec()`, `load_eval_dataset_spec()`
- CLI parity: these power `penguiflow eval collect` and `penguiflow eval evaluate`

Why: keeping eval orchestration in one namespace lets projects provide only
metric/run hooks while reusing stable collection/export/sweep primitives.

## Version source of truth

- Package version: `penguiflow.__version__`
- Packaging metadata: `pyproject.toml`
