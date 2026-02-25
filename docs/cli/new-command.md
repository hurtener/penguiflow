# `penguiflow new`

## What it is / when to use it

`penguiflow new` scaffolds a new agent project directory from a curated template set.

Use it when you want a runnable project skeleton with:

- a consistent project layout,
- a starter planner/runtime wiring,
- optional streaming/HITL/A2A/background-task scaffolding toggles.

## Non-goals / boundaries

- This command does not validate your LLM credentials or tool connectivity (use `penguiflow dev` and `penguiflow tools` for that).
- The scaffold is a starting point; you are expected to edit prompts, tool definitions, policies, and deployment wiring.
- The command will not overwrite existing files unless you pass `--force`.

## Contract surface

### Usage

```bash
penguiflow new [OPTIONS] NAME
```

Key options:

- `--template/-t`: which template to use (default `react`)
- `--output-dir`: create the project under a specific directory
- `--dry-run`: print what would be created
- `--force`: overwrite if files exist
- feature flags:
  - `--with-streaming`
  - `--with-hitl`
  - `--with-a2a`
  - `--with-rich-output`
  - `--with-background-tasks`
  - `--no-memory`

### Dependencies / extras

`penguiflow new` requires **Jinja2**. Install either:

- `pip install "penguiflow[cli]"`, or
- `pip install jinja2`

## Templates (what you get)

Templates currently supported by the CLI:

- `minimal`: smallest runnable skeleton (good for learning)
- `react`: `ReactPlanner` baseline (default)
- `parallel`: parallel fan-out + join patterns
- `flow`: runtime-first project emphasizing DAGs (no planner by default)
- `controller`: controller-style flow wiring and contracts
- `rag_server`: retrieval-oriented service skeleton
- `wayfinder`: navigation/wayfinding pattern scaffold
- `analyst`: analysis/reporting pattern scaffold
- `enterprise`: multi-tenant oriented skeleton (HITL, policies, stronger defaults)

Choose based on how you will operate the system:

- planner-driven agents: `react` / `enterprise`
- deterministic pipelines: `flow` / `controller`
- parallel tool workloads: `parallel`

## Operational defaults (recommended)

- Use `--dry-run` in repos to avoid accidental writes.
- Prefer `react` unless you already know you’re building a pure runtime DAG.
- Prefer envelope-style messaging and `trace_id` scoping in production systems (see **[Messages & envelopes](../core/messages-and-envelopes.md)**).
- Generated templates include Playground fixed-session env keys in `.env.example`:
  - `PLAYGROUND_FIXED_SESSION_ID`
  - `PLAYGROUND_REWRITE_AGUI`

## Runnable example (typical usage)

```bash
uv run penguiflow new my-agent --template react --with-streaming
cd my-agent
uv sync
uv run penguiflow dev --project-root .
```

## Failure modes & recovery

- **“Jinja2 is required for `penguiflow new`”**: install `penguiflow[cli]` (or `jinja2`).
- **Unknown template**: run `penguiflow new --help` to see valid templates.
- **Files skipped**: the target already exists; re-run with `--force` if overwriting is intended.
- **Permission errors**: choose a writable `--output-dir` or fix filesystem permissions.

## Observability

This command is pure file I/O. Operational observability starts when you run the generated project via `penguiflow dev` or your own service runner.

## Security / multi-tenancy notes

- Treat the scaffold as code you must review; do not assume it is safe for your org by default.
- Keep secrets out of generated config files; prefer `.env` (uncommitted) and secret managers.

## Troubleshooting checklist

- Run `penguiflow new --help` and confirm the template/flag names you expect exist.
- If scaffolding works for one developer but not another, confirm you’re using the same venv and that Jinja2 is installed.
