# Templates and Enhancement Flags

This is the picker reference. SKILL.md routes you here when the user needs to choose a template + flag combo.

## Template catalog

| Template | Best for | Planner included | Notes |
|---|---|---|---|
| `minimal` | Learning, smoke tests, smallest runnable skeleton | No (runtime only) | Start here if the user doesn't know what they want. |
| `react` (default) | LLM-driven agents using ReactPlanner | Yes | Default when omitted. Pick this for general agents. |
| `parallel` | Fan-out/fan-in workloads, parallel tool calls | Yes | Adds `map_concurrent` + `join_k` patterns. |
| `flow` | Deterministic DAG pipelines, no planner | No | Runtime-first; bypasses ReactPlanner entirely. |
| `controller` | Controller-style multi-hop loops with explicit contracts | No | Closer to v1 controller pattern; deterministic with budgets. |
| `rag_server` | Retrieval/RAG service skeleton | Yes | Tools + storage layer wiring. |
| `wayfinder` | Navigation/wayfinding patterns | Yes | Routing-heavy specialist. |
| `analyst` | Reporting/analysis specialist | Yes | Synthesis-heavy outputs. |
| `enterprise` | Multi-tenant agents with stronger defaults | Yes | HITL, policies, audit hooks pre-wired. |

**Important:** `penguiflow generate` (declarative) restricts `agent.template` to: `minimal`, `react`, `parallel`, `rag_server`, `wayfinder`, `analyst`, `enterprise`. `flow` and `controller` are interactive-only via `penguiflow new`.

### Picker heuristics
- "I want an LLM agent" → `react`.
- "I want a deterministic pipeline, no LLM" → `flow`.
- "I want multi-hop with budgets but no LLM" → `controller`.
- "I want a RAG service" → `rag_server`.
- "I'm building for production with multiple tenants" → `enterprise`.
- "I'm benchmarking parallel tool calls" → `parallel`.
- "I'm learning the library" → `minimal`.

## Enhancement flags (interactive path)

All flags compose. Defaults: memory **on**, all other features **off**.

### `--with-streaming`
Scaffolds final-response streaming for the ReactPlanner template and emits `StreamChunk`s via the runtime helpers. The generated project wires a JSON-LLM client whose protocol exposes `stream` and `on_stream_chunk`. Pair with [[penguiflow-streaming]] for the protocol-agnostic chunk model and [[penguiflow-agui-events]] for AG-UI frontend reducers.

### `--with-hitl`
Scaffolds OAuth / human-in-the-loop pause-and-resume wiring. Adds planner pause state hooks that a downstream `StateStore` can persist for cross-worker resume. Pair with [[penguiflow-hitl-pause-resume]] and [[penguiflow-statestore]].

### `--with-a2a`
Scaffolds an agent-card endpoint and the FastAPI A2A binding so the agent exposes itself as an A2A specialist (or fronts as a manager). Pair with [[penguiflow-a2a-integration]] for spec depth.

### `--with-rich-output`
Scaffolds rich-output node attachment (`render_component`, `describe_component`, `ui_form`, `ui_confirm`, `ui_select_option`, `list_artifacts`). Pair with [[penguiflow-rich-output]] for component authoring.

### `--with-background-tasks`
Scaffolds `tasks.*` planner tools, a task service, and projection plumbing. Pair with [[penguiflow-background-tasks]].

### `--no-memory`
Drops short-term memory scaffolding (memory is on by default). Use when the agent is stateless. Re-add later by following [[penguiflow-memory]].

## Flag matrix recommendations

| Profile | Template | Flags |
|---|---|---|
| Toy / learning | `minimal` | (none) |
| Standard chat agent | `react` | `--with-streaming` |
| Production agent with UI | `react` or `enterprise` | `--with-streaming --with-hitl --with-rich-output` |
| Specialist agent in A2A network | `react` or `enterprise` | `--with-a2a` |
| Long-running async agent | `enterprise` | `--with-streaming --with-hitl --with-background-tasks` |
| Stateless RAG endpoint | `rag_server` | `--no-memory` |
| Parallel tool benchmark | `parallel` | (none) |
| Pure DAG service | `flow` | `--no-memory` |

## Command surface

```bash
penguiflow new [OPTIONS] NAME

  --template/-t              minimal|react|parallel|flow|controller|rag_server|
                             wayfinder|analyst|enterprise  (default: react)
  --output-dir PATH          where to create the project (default: cwd)
  --dry-run                  print what would be created, don't write
  --force                    overwrite existing files
  -q/--quiet                 suppress stdout

  --with-streaming
  --with-hitl
  --with-a2a
  --with-rich-output
  --with-background-tasks
  --no-memory
```

The `run_new(...)` Python API exposes the same flags as keyword arguments — useful when scripting scaffolding inside CI.

## What gets written

Every template produces the same top-level skeleton:

```
<project>/
  pyproject.toml          # runnable with `uv sync`
  README.md               # project-specific quickstart
  ENV_SETUP.md            # env var checklist
  src/<package_name>/     # the agent code
  tests/                  # smoke tests
  agent.yaml              # playground discovery (when generate is used)
```

Templates differ in what lives under `src/<package_name>/`: tools modules, planner factories, flow definitions, optional A2A binding modules. Inspect a scaffold with `--dry-run` to see the exact file list before committing.

## Failure modes specific to scaffolding
- "Unknown template" — typo in `--template`; run `penguiflow new --help`.
- "Files skipped (exists)" — target already populated; pass `--force` only if you mean it.
- "Cannot write file" / permission errors — pick a writable `--output-dir`.
- "Jinja2 is required" — `pip install "penguiflow-lite[cli]"` or `pip install jinja2` in the same env.
