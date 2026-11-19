# Public API surface

PenguiFlow’s public surface is exported from `penguiflow.__init__`.

## Import style

Prefer importing from `penguiflow` (top-level) for stable APIs:

```python
from penguiflow import ReactPlanner, build_catalog, tool
```

## Stability expectations

- Public types and functions exported at top-level aim to be stable within a major version.
- Internal modules and RFC/proposal documents may change without notice.

## Key entry points

- Planner: `ReactPlanner`, `Trajectory`, `PlannerFinish`, `PlannerAction`
- Skills: `SkillsConfig`, `SkillPackConfig`, `SkillProvider`, `SkillProviderFactory`, `SkillProposeRequest`
- Sessions: `StreamingSession`, `SessionManager`
- Tools: `tool`, `build_catalog`, `Node`, `NodePolicy`, `ModelRegistry`, `ToolLoadingMode`
- A2A: `penguiflow_a2a.A2AService` (serve a planner), `A2AAgentToolset` (call remote agents)

## Version source of truth

- Package version: `penguiflow.__version__`
- Packaging metadata: `pyproject.toml`
