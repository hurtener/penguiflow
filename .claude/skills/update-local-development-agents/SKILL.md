---
name: update-local-development-agents
description: Workflow for propagating penguiflow library changes to test agents. Use when modifying library code and need to test changes in test_generation agents.
---

# Updating Local Development Agents

## Overview

When developing the penguiflow library, changes need to be propagated to test agents in `test_generation/`. This skill documents the workflow.

## Directory Structure

```
penguiflow/
├── penguiflow/           # Library source code
│   ├── tools/
│   │   ├── node.py       # ToolNode implementation
│   │   └── config.py     # ExternalToolConfig, AuthType
│   ├── planner/
│   └── ...
├── test_generation/
│   └── reporting-agent/  # Test agent using local penguiflow
│       ├── src/reporting_agent/
│       ├── pyproject.toml
│       └── .env
└── .claude/skills/
```

## Workflow: After Library Changes

### 1. Make changes to library

Edit files in `penguiflow/` (e.g., `penguiflow/tools/node.py`)

### 2. Reinstall library in test agent venv

```bash
cd test_generation/reporting-agent
uv sync --reinstall-package penguiflow
```

This rebuilds and reinstalls the local penguiflow into the agent's venv.

### 3. Test the changes

```bash
# Quick test
uv run python -c "from penguiflow.tools import ToolNode; print('OK')"

# Run agent tests
uv run pytest tests/

# Or run the agent directly
uv run penguiflow dev
```

## Common Issues

### "VIRTUAL_ENV does not match" Warning

When running from the main penguiflow directory:
```
warning: `VIRTUAL_ENV=/path/to/penguiflow/.venv` does not match the project environment
```

This is harmless - uv uses the correct venv based on the project.

### Import Errors After Changes

If imports fail after library changes:
1. Ensure you ran `uv sync --reinstall-package penguiflow`
2. Check for syntax errors in the library code
3. Verify the library installs: `uv run python -c "import penguiflow"`

### Type Errors

Run mypy after changes:
```bash
cd /path/to/penguiflow
uv run mypy penguiflow
```

## Agent Configuration

### pyproject.toml Reference

```toml
[project]
dependencies = [
    "penguiflow[planner] @ file:///absolute/path/to/penguiflow",
    # other deps...
]
```

### .env Configuration

Test agents use `.env` for configuration:
```bash
# MCP Server URLs
TABLEAU_MCP_SERVER_URL=https://...
CHARTING_MCP_SERVER_URL=https://...

# Auth credentials
TABLEAU_MCP_COOKIE=<session_cookie>
DATABRICKS_CLIENT_ID=...
DATABRICKS_CLIENT_SECRET=...
```

## Quick Reference Commands

```bash
# From test agent directory
cd test_generation/reporting-agent

# Reinstall library
uv sync --reinstall-package penguiflow

# Run tests
uv run pytest tests/ -v

# Run specific test
uv run pytest tests/test_orchestrator.py -k "test_name"

# Interactive testing
uv run python -c "
import asyncio
from reporting_agent.orchestrator import ReportingAgentOrchestrator
# ... test code
"

# Start dev server
uv run penguiflow dev
```

## CI Pipeline

Before committing library changes:
```bash
cd /path/to/penguiflow
uv run ruff check penguiflow
uv run mypy penguiflow
uv run pytest --cov=penguiflow
```
