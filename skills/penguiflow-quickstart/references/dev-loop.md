# Dev Loop: `penguiflow dev` and `penguiflow init`

This reference covers the local iteration loop after scaffolding.

## `penguiflow dev`

Launches a local playground (FastAPI backend + bundled UI) for an agent project.

```bash
penguiflow dev [--project-root PATH] [--host HOST] [--port PORT] [--no-browser]
```

Behavior:
- Serves UI at `http://{host}:{port}` (default `127.0.0.1:8000`).
- Serves `GET /health` for liveness checks.
- Loads `<project-root>/.env` if present, **without overriding** already-set environment variables (process env wins; `.env` only fills missing values).
- Opens the system browser by default; `--no-browser` for headless / SSH sessions.

### The #1 gotcha: which Python environment runs the agent?

`penguiflow dev` runs the agent in **penguiflow's Python environment**, not the agent project's venv. This is the single most common source of "it imports in tests but not in the playground."

Two workflows that work:

**Option A — install the agent project into penguiflow's env (recommended):**
```bash
cd <agent_project> && uv sync
cd <where_penguiflow_is_installed>
uv pip install -e <agent_project>
uv run penguiflow dev --project-root <agent_project>
```

**Option B — install planner extras into the penguiflow env:**
```bash
# In the env that runs `penguiflow dev`
uv pip install "penguiflow-lite[planner]"
```

Use Option A when your agent project has additional dependencies beyond planner extras (custom MCP clients, vendor SDKs). Option B is sufficient for a pure ReactPlanner agent that only uses bundled extras.

### Operational defaults
- Keep `--host` bound to `127.0.0.1` unless you explicitly need LAN access.
- Put API keys in `<project_root>/.env` (never commit).
- Refresh the browser to pick up code changes (no hot reload).

### Failure modes and recovery

| Symptom | Cause | Fix |
|---|---|---|
| `Address already in use` | Port conflict | `--port 8002` (or another free port) |
| `ModuleNotFoundError: litellm` | Planner extras not in penguiflow's env | `uv pip install "penguiflow-lite[planner]"` |
| `ModuleNotFoundError: <agent>` | Agent project not installed in penguiflow's env | `uv pip install -e <agent_project>` |
| UI assets missing | Repo checkout without UI build | `cd penguiflow/cli/playground_ui && npm install && npm run build` |
| `.env` ignored | Process env already has the var | Unset the shell variable or use a `.env.local` precedence layer |
| Spec not discovered | `agent.yaml` missing | Re-run `penguiflow generate --spec` (writes `agent.yaml`) |

### Observability inside the dev loop
- `uvicorn` request logs go to stdout — watch them for tool/route errors.
- Attach structured logging in your agent: `from penguiflow import configure_logging; configure_logging(structured=True)`.
- For event capture (planner trajectory, flow events), wire a `StateStore` even in dev — covered by [[penguiflow-statestore]].

## `penguiflow init`

Writes VS Code helpers to `.vscode/` (no dependency installation).

```bash
penguiflow init [--force] [--dry-run] [--no-launch] [--no-tasks] [--no-settings] [-q/--quiet]
```

Files generated (by default):
- `.vscode/launch.json` — debug configurations for runtime + planner flows.
- `.vscode/tasks.json` — common dev tasks (lint, typecheck, tests).
- `.vscode/settings.json` — editor settings tuned for PenguiFlow's toolchain.
- `.vscode/penguiflow.code-snippets` — snippets for common patterns (node, registry, planner factory).

Flags `--no-launch` / `--no-tasks` / `--no-settings` selectively skip files. Idempotent unless `--force`.

### When to run
- Once at repo start.
- After upgrading PenguiFlow major versions (snippets may have evolved).

### When to skip
- Non-VS Code editors (use the output as a reference, not a directive).
- Repos with strict team-wide editor conventions — review before committing `.vscode/*`.

### Failure modes
- "Files skipped" — exist; use `--force` if overwriting is intended.
- Permission errors — ensure repo dir is writable.

## Putting it together: typical day-1 sequence

```bash
# 1) scaffold
uv run penguiflow new my-agent --template react --with-streaming
cd my-agent
uv sync

# 2) IDE helpers (optional)
uv run penguiflow init

# 3) wire env
cp .env.example .env  # or whatever the template emitted
# edit .env: add API keys

# 4) run
uv run penguiflow dev --project-root .
```

If the agent boots and `GET /health` returns OK, hand off to the subsystem skill the user actually wants.
