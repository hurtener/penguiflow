# Installation

## What it is / when to use it

This page explains how to install PenguiFlow and which extras to choose.

## Requirements

- Python **3.11+**

## Non-goals / boundaries

- This page does not cover dependency pinning and production packaging strategies (use your org’s standard).
- PenguiFlow does not require `uv`, but the repo and templates default to it.

## Install (base)

```bash
pip install penguiflow
```

Or with `uv`:

```bash
uv pip install penguiflow
```

## Optional extras

PenguiFlow is modular. Install only what you need:

- Planner: `penguiflow[planner]` (LiteLLM + dspy adapters + FastMCP + UTCP)
- A2A server bindings: `penguiflow[a2a-server]`, gRPC add-on: `penguiflow[a2a-grpc]`
- A2A client: `penguiflow[a2a-client]`
- Native LLM SDKs (for the native LLM layer tests/integrations): `penguiflow[llm]`
- Docs (contributors): `penguiflow[docs]` (MkDocs Material + mkdocstrings)

Example:

```bash
pip install "penguiflow[planner,a2a-server]"
```

## Contract surface (what changes when you add extras)

- `penguiflow[planner]` adds LiteLLM + FastMCP + UTCP and enables `ReactPlanner` + ToolNode integrations.
- `penguiflow[docs]` adds MkDocs tooling (contributors).
- A2A extras add server/client bindings.

## Operational defaults

- For library usage: start with base install, then add `planner` when you adopt `ReactPlanner` or ToolNode.
- For contributors: `uv pip install -e ".[dev,docs]"`.

## Failure modes & recovery

### Import errors for planner/tooling

If you see errors about `fastmcp`, `utcp`, or `litellm`, you likely need:

```bash
pip install "penguiflow[planner]"
```

### Mixed environments

If `python -c ...` imports a different environment than your app:

- confirm your venv is activated
- prefer `uv run ...` to ensure commands use the project environment

## Observability

Installation itself has no observability surface; verification is via import/version checks.

## Security / multi-tenancy notes

- Treat tool integration credentials as secrets; use environment variables and secret managers.
- Never commit tokens to `ExternalToolConfig`.

## Runnable example: verify install

```bash
python -c "import penguiflow; print(penguiflow.__version__)"
```

## Troubleshooting checklist

- **`ModuleNotFoundError`**: install the right extras for the feature you’re using.
- **Wrong Python version**: ensure Python 3.11+ is used by your environment.
- **CLI not found**: confirm the environment where you installed `penguiflow` is on your PATH (or run via `uv run penguiflow ...`).

## Install from source (contributors)

```bash
git clone git@github.com:hurtener/penguiflow.git
cd penguiflow
uv venv
uv pip install -e ".[dev,docs]"
```

## Verify

```bash
python -c "import penguiflow; print(penguiflow.__version__)"
```
