# `penguiflow dev`

## What it is / when to use it

`penguiflow dev` launches the local playground (backend + UI) for an agent project. It is intended for:

- rapid iteration on planner prompts/tools,
- validating streaming / HITL / tool wiring,
- smoke-testing your agent workspace without building deployment infra.

## Non-goals / boundaries

- This is not a production server. Use it for local development only.
- The playground is not a dependency isolation mechanism (see environment notes below).
- Hot reload is not bundled; refresh the browser to pick up code changes.

## Contract surface

```bash
penguiflow dev [--project-root PATH] [--host HOST] [--port PORT] [--no-browser]
```

Behavior:

- serves UI at `http://{host}:{port}`
- serves health at `http://{host}:{port}/health`
- loads `.env` from `--project-root` if present **without overriding** already-set environment variables

## IMPORTANT: which Python environment runs the agent?

The playground runs in **penguiflow’s Python environment**, not the agent project’s venv.

This is the #1 cause of “it imports locally but not in the playground”.

Recommended workflows:

### Option A: install the agent project into penguiflow (recommended)

```bash
cd <agent_project> && uv sync
cd <where_penguiflow_is_installed_or_checked_out>
uv pip install -e <agent_project>
uv run penguiflow dev --project-root <agent_project>
```

### Option B: ensure planner deps exist in the penguiflow environment

If you are using `ReactPlanner`/ToolNode:

```bash
uv pip install "penguiflow[planner]"
```

## Operational defaults (recommended)

- Keep the playground bound to `127.0.0.1` unless you explicitly need LAN access.
- Put API keys in `<project_root>/.env` (uncommitted), and rely on `.env` precedence rules:
  - process env wins; `.env` fills only missing values.

## Failure modes & recovery

- **UI assets missing (`playground_ui/dist`)** (repo checkout): build the UI:
  - `cd penguiflow/cli/playground_ui`
  - `npm install`
  - `npm run build`
- **Planner imports fail (`litellm`, `fastmcp`, `utcp`)**: install `penguiflow[planner]` in the environment running `penguiflow dev`.
- **Port already in use**: pass `--port 8002` (or free the port).
- **`.env` not applied**: confirm the file is at `<project_root>/.env` and variables are not already set in the shell.

## Observability

- Use server logs (uvicorn) for request traces.
- For runtime/planner behavior, ensure your agent project attaches:
  - structured logging (`configure_logging(structured=True)`), and/or
  - event capture (runtime `FlowEvent`, planner event callbacks).

See:

- **[Logging](../observability/logging.md)**
- **[Telemetry patterns](../observability/telemetry-patterns.md)**

## Security / multi-tenancy notes

- Treat `.env` as sensitive and do not commit it.
- If you bind to `0.0.0.0`, you may expose local credentials and debugging surfaces to your network.

## Troubleshooting checklist

- Confirm which Python environment runs `penguiflow dev` (`which penguiflow`, `python -c "import penguiflow; print(penguiflow.__version__)"`).
- Confirm the agent project is importable in that environment (editable install is the simplest).
