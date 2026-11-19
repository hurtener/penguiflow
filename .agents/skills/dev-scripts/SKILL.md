---
name: dev-scripts
description: Use when adding, rewriting or moving a development script in this repo (scripts/, a shell script rewritten in Python) for building, checking, releasing, codegen, live provider tests or seeding data. Make it a named command (`uv run <name>`) of the dev-only uv workspace member `penguiflow-scripts` instead of a file run by its path.
---

# Dev scripts as workspace commands

Write a Python dev tool that lives outside the package as a named command of the dev-only uv workspace member `penguiflow-scripts` in `scripts/`. `uv run <name>` then works from anywhere in the repo, `uv sync` installs it, and it stays out of the published `penguiflow-lite` wheel. Never add it to the root project's `[project.scripts]`: that ships it to every user of the package.

## Rules

- Give every dev script a `main()` and register it in `scripts/pyproject.toml` under `[project.scripts]`.
- Run it as `uv run <name>` in docs and CI. Never by path (`python scripts/<name>.py`, `uv run scripts/<name>.py`).
- A shell dev script rewritten in Python becomes a workspace command too.
- Never use `sys.path.insert(...)` or `sys.path.append(...)` to import `penguiflow` or a sibling script. Both are installed in the environment, so imports work without it.
- Add the member to `[dependency-groups] dev` only. Never to `[project.optional-dependencies]`: those are published extras.
- Add `scripts/src` to `files` in the root `[tool.mypy]`, so `uv run mypy` checks it.

## Setup (first script only)

Put the tool in `scripts/src/penguiflow_scripts/<module>.py`, next to an empty `__init__.py`, and give `scripts/` its own `pyproject.toml`. Copy the `[build-system]` table from the root `pyproject.toml`:

```toml
# scripts/pyproject.toml
[project]
name = "penguiflow-scripts"
version = "0.0.0"
requires-python = ">=3.11"

[project.scripts]
<command> = "penguiflow_scripts.<module>:main"

[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"
```

Declare the member in the root `pyproject.toml`, add it to the dev group and to mypy:

```toml
# pyproject.toml
[dependency-groups]
dev = [
    # ...existing entries...
    "penguiflow-scripts",
]

[tool.uv.workspace]
members = ["scripts"]

[tool.uv.sources]
penguiflow-scripts = { workspace = true }

[tool.mypy]
# ...existing settings; only `files` changes
files = ["penguiflow", "penguiflow_a2a", "scripts/src"]
```

Then run `uv sync` and `uv run <command>`. Each later script is one module in `scripts/src/penguiflow_scripts/` plus one line in `[project.scripts]`.
