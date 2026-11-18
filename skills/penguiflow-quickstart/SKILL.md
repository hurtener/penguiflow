---
name: penguiflow-quickstart
description: Scaffold a new PenguiFlow agent project end-to-end using the CLI — choose the right template (minimal, react, parallel, flow, controller, rag_server, wayfinder, analyst, enterprise), pick the right `--with-*` flags (streaming, hitl, a2a, rich-output, background-tasks, --no-memory), run the playground, and iterate. Use when a user says "create a new agent", "scaffold", "start a PenguiFlow project", "which template should I use", "spec-driven agent", or asks about `penguiflow new`, `penguiflow generate`, `penguiflow dev`, `penguiflow init`. Covers both interactive scaffolding (`penguiflow new`) and declarative spec-first generation (`penguiflow generate`).
---

# PenguiFlow Quickstart (Greenfield Agents)

## When to use
- Brand-new agent project from scratch.
- User asks for "scaffold", "template", "create an agent", or names a template/flag.
- Picking between interactive (`penguiflow new`) and declarative (`penguiflow generate`).

## When NOT to use
- Cloning a working sibling agent → use [[penguiflow-agent-reset-template]].
- Configuring an already-scaffolded ReactPlanner → use [[penguiflow-reactplanner-config]].
- Wiring MCP tools into an existing agent → use [[penguiflow-mcp-integration]].

## Hard boundaries vs siblings
This skill takes the user from zero to a running playground. The moment the project boots, hand off to the subsystem skill (planner, A2A, memory, etc.). Don't duplicate planner/MCP/A2A guidance here — defer to siblings.

## Workflow

### 1) Choose the path: interactive vs declarative
- **`penguiflow new <name> -t <template> [--with-*]`** — fast, opinionated, template + flag combo. Use when the agent's shape is clear.
- **`penguiflow generate --init <name>` then edit YAML then `--spec`** — declarative, repeatable, CI-friendly. Use when the spec is authoritative and you want drift detection.

Read `references/templates-and-flags.md` to choose a template, then `references/spec-driven-generation.md` if you go declarative.

### 2) Pick the template
Templates: `minimal`, `react` (default), `parallel`, `flow`, `controller`, `rag_server`, `wayfinder`, `analyst`, `enterprise`. The `generate` command restricts to: `minimal`, `react`, `parallel`, `rag_server`, `wayfinder`, `analyst`, `enterprise`. Full picker table in `references/templates-and-flags.md`.

### 3) Pick enhancement flags (interactive path)
Compose orthogonally:
- `--with-streaming` — token streaming + SSE/WebSocket sink wiring.
- `--with-hitl` — pause/resume scaffolding + state-store hooks.
- `--with-a2a` — agent-card + HTTP binding scaffolding.
- `--with-rich-output` — `render_component`, `ui_form`/`ui_confirm`/`ui_select_option`.
- `--with-background-tasks` — `tasks.*` wiring + projection scaffolding.
- `--no-memory` — drop short-term memory (defaults to enabled).

Each flag's emitted scaffolding is enumerated in `references/templates-and-flags.md`.

### 4) Scaffold and install
```bash
uv run penguiflow new my-agent --template react --with-streaming
cd my-agent
uv sync
```

For declarative mode use the spec workflow in `references/spec-driven-generation.md`.

### 5) Run the dev loop
```bash
uv run penguiflow dev --project-root .
```
The playground runs in **penguiflow's Python environment**, not the agent's venv. If imports fail, see the environment recipe in `references/dev-loop.md`.

### 6) (Optional) Wire IDE helpers
```bash
uv run penguiflow init
```
Generates `.vscode/{launch,tasks,settings}.json` + snippets. Idempotent unless `--force`.

### 7) Hand off to the subsystem skill
After the agent boots, transition to: [[penguiflow-reactplanner-config]] (planner knobs), [[penguiflow-core-flows]] (flow topology), [[penguiflow-mcp-integration]] (external tools), [[penguiflow-memory]] (memory), [[penguiflow-hitl-pause-resume]] (HITL), [[penguiflow-streaming]] (token streaming), [[penguiflow-a2a-integration]] (remote agents), [[penguiflow-rich-output]] (UI components), [[penguiflow-background-tasks]] (async tasks).

## Troubleshooting (fast checks)
- **"Jinja2 is required for `penguiflow new`"** → install `penguiflow-lite[cli]` (or `pip install jinja2`).
- **"Unknown template"** → `penguiflow new --help` lists valid names; check spelling.
- **Files skipped on re-run** → target exists; use `--force` only if you intend to overwrite.
- **`penguiflow dev` imports fail** → the playground runs in penguiflow's venv. `uv pip install "penguiflow-lite[planner]"` inside that env, or `uv pip install -e <agent_project>` to expose your agent.
- **UI assets missing** (repo checkout only) → `cd penguiflow/cli/playground_ui && npm install && npm run build`.
- **`.env` not applied** → file must be at `<project_root>/.env`; process env wins, `.env` only fills missing keys.
- **OAuth without HITL in spec** → spec validator rejects this; set `agent.flags.hitl: true` or switch auth to bearer/none.
- **`fetch()` hangs** in a generated flow → no egress reaches Rookery; ensure a terminal node emits/returns.

## Worked example
`examples/quickstart/flow.py` is the canonical minimal flow the docs walk through. Run `uv run python examples/quickstart/flow.py` from a repo checkout.

## References (load only as needed)
- `references/templates-and-flags.md` — Every template and `--with-*` flag with what it scaffolds and when to choose it.
- `references/spec-driven-generation.md` — `penguiflow generate` end-to-end, spec schema, modes, validation rules.
- `references/dev-loop.md` — `penguiflow dev` environment model, `penguiflow init` IDE helpers, common failure modes.
