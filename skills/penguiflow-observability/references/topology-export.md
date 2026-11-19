# Catalog Export: What the Agent Can Do

A ReactPlanner agent has no graph to draw — its "topology" is the **tool catalog** it can call (local tools, ToolNode/MCP tools, A2A specialists, skills tools). Export it for reviews, docs and CI diffs.

## `NodeSpec.to_tool_record()`

Every catalog entry (`NodeSpec`, from `build_catalog(...)`, `ToolNode`, `A2AAgentToolset.tool(...)`, ...) renders a JSON-serializable record:

```python
record = spec.to_tool_record()
# {
#   "name", "desc", "side_effects", "loading_mode", "tags", "auth_scopes",
#   "cost_hint", "latency_hint_ms", "safety_notes",
#   "args_schema", "out_schema",      # JSON Schema of the Pydantic models
#   "extra", "examples",
# }
```

This is the same information the planner renders into its prompt, so the export reflects what the LLM actually sees (before visibility policies).

## CI integration: as-built catalog

Generate the catalog on every commit and fail on unintended changes:

```bash
uv run python -c "import json; from app.agent import build_catalog_for_prod as c; \
  print(json.dumps([s.to_tool_record() for s in c()], indent=2, sort_keys=True))" > docs/tool-catalog.json
git diff --exit-code docs/tool-catalog.json   # fail if changed without intent
```

Review signals in the diff:
- New tools with `side_effects` other than `"pure"` / `"read"`.
- Changed `args_schema` (breaking for prompts, examples and recorded trajectories).
- Changed `loading_mode` (always vs deferred) — affects prompt size and tool discovery.

## A readable summary table

```python
for spec in catalog:
    print(f"| {spec.name} | {spec.side_effects} | {', '.join(spec.tags)} | {spec.loading_mode.value} |")
```

Paste it into the agent's README next to its dashboard links.

## "Why doesn't the export match what the planner shows?"

Common causes:
- The exported catalog isn't the one your runtime builds (different factory or feature flags).
- A `ToolVisibilityPolicy` / `ToolPolicy` filters tools per request — the export is pre-policy.
- Deferred tools (`ToolLoadingMode.DEFERRED`) only appear in the prompt after `tool_search` / `tool_get` activates them.
- ToolNode/MCP tools are discovered at connect time; export after connecting.

Fix: export from the **same factory** your runtime uses, with the same configuration.

## Operational defaults

- Keep tool `name`s stable — they appear in metrics labels, trajectories and memory.
- Sort keys in the export so diffs stay meaningful.
- Record `latency_hint_ms` / `cost_hint` for expensive tools; they help reviews and planner prompts.

## Security

Catalog exports can leak architecture details:
- Internal service names and MCP namespaces.
- Specialist agent URLs (A2A tools).
- Auth scopes.

If the repo is public but the catalog is sensitive, keep exports in private CI artifacts.

## What goes well next to the catalog

- The planner configuration summary (model, `max_iters`, budgets, guardrails, visibility policies) — see [[penguiflow-reactplanner-config]].
- The registered models per tool (`ModelRegistry`).
- Links to the relevant dashboards and the LLM tracing project.
