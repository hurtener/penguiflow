# PenguiFlow CLI Feature Specification

**Status**: Planned for v2.4+
**Priority**: Medium
**Effort**: 2-3 hours

---

## Overview

Add a command-line interface to PenguiFlow that helps users bootstrap projects and set up development environments. Unlike a VS Code extension, the CLI travels with the pip/uv package, requiring no separate installation.

---

## Motivation

### Problem

When users install PenguiFlow via `pip install penguiflow` or `uv add penguiflow`:
- They get the library code only
- No VS Code snippets, launch configs, or task definitions
- No project scaffolding or best-practice templates
- Must read docs and manually set up everything

### Solution

A CLI that:
- **Travels with the package** (it's Python code in the installed package)
- **User explicitly opts in** (no magic, runs only when invoked)
- **Evolves incrementally** (start simple, add features over time)
- **No external accounts needed** (no VS Code marketplace, no npm)

---

## User Experience

### Command 1: `penguiflow init`

Sets up development environment in current project.

```bash
# User has an existing project, wants PenguiFlow dev tooling
cd my-project
uv add penguiflow
uv run penguiflow init

# Output:
# ✓ Created .vscode/penguiflow.code-snippets
# ✓ Created .vscode/launch.json
# ✓ Created .vscode/tasks.json
# ✓ Created .vscode/settings.json
#
# PenguiFlow development environment ready!
# Open this folder in VS Code for snippets and debugging support.
```

**What it creates:**

```
.vscode/
├── penguiflow.code-snippets   # Tool, orchestrator, model snippets
├── launch.json                 # Debug configurations
├── tasks.json                  # Test, lint, type-check tasks
└── settings.json               # Python/Pylance settings (optional)
```

**Flags:**

```bash
penguiflow init --force          # Overwrite existing files
penguiflow init --no-launch      # Skip launch.json
penguiflow init --no-tasks       # Skip tasks.json
penguiflow init --dry-run        # Show what would be created
```

### Command 2: `penguiflow new <name>` (Future)

Scaffolds a complete agent project.

```bash
# User wants to start a new agent project from scratch
uv run penguiflow new my-agent

# Output:
# Creating new PenguiFlow agent project: my-agent
#
# ✓ Created my-agent/
# ✓ Created my-agent/pyproject.toml
# ✓ Created my-agent/src/my_agent/__init__.py
# ✓ Created my-agent/src/my_agent/orchestrator.py
# ✓ Created my-agent/src/my_agent/tools.py
# ✓ Created my-agent/src/my_agent/config.py
# ✓ Created my-agent/tests/test_tools.py
# ✓ Created my-agent/.vscode/ (dev environment)
# ✓ Created my-agent/.env.example
# ✓ Created my-agent/README.md
#
# Next steps:
#   cd my-agent
#   uv sync
#   cp .env.example .env
#   # Edit .env with your API keys
#   uv run python -m my_agent
```

**What it creates:**

```
my-agent/
├── pyproject.toml              # With penguiflow dependency
├── src/
│   └── my_agent/
│       ├── __init__.py
│       ├── orchestrator.py     # Orchestrator template
│       ├── tools.py            # Example tool definitions
│       ├── config.py           # Environment-based config
│       └── models.py           # Pydantic models
├── tests/
│   └── test_tools.py           # Example test
├── .vscode/                    # Dev environment (from init)
├── .env.example                # Environment template
├── .gitignore
└── README.md
```

**Flags:**

```bash
penguiflow new my-agent --minimal        # Just orchestrator, no examples
penguiflow new my-agent --with-parallel  # Include parallel execution example
penguiflow new my-agent --with-pause     # Include human-in-the-loop example
penguiflow new my-agent --template=enterprise  # Use enterprise template
```

### Command 3: `penguiflow doctor` (Future)

Validates project setup and dependencies.

```bash
uv run penguiflow doctor

# Output:
# Checking PenguiFlow installation...
#
# ✓ penguiflow 2.4.0 installed
# ✓ Python 3.12.0 (compatible)
# ✓ pydantic 2.5.0 (compatible)
# ✓ litellm 1.0.0 (optional, installed)
# ⚠ dspy not installed (optional, for structured output)
#
# Checking project setup...
#
# ✓ .vscode/penguiflow.code-snippets found
# ✗ .vscode/launch.json missing (run: penguiflow init)
# ✓ pyproject.toml found
# ⚠ No tools found in src/ (expected @tool decorators)
#
# Run 'penguiflow init' to fix missing VS Code configuration.
```

---

## Implementation

### Package Structure

```
penguiflow/
├── __init__.py
├── cli/
│   ├── __init__.py
│   ├── main.py           # Click app entry point
│   ├── init.py           # 'init' command
│   ├── new.py            # 'new' command (future)
│   └── doctor.py         # 'doctor' command (future)
├── templates/
│   ├── vscode/
│   │   ├── penguiflow.code-snippets
│   │   ├── launch.json
│   │   ├── tasks.json
│   │   └── settings.json
│   └── project/
│       ├── orchestrator.py.jinja
│       ├── tools.py.jinja
│       ├── config.py.jinja
│       └── pyproject.toml.jinja
└── ...
```

### Entry Point (pyproject.toml)

```toml
[project.scripts]
penguiflow = "penguiflow.cli.main:app"
```

### Core Implementation

```python
# penguiflow/cli/main.py
import click

@click.group()
@click.version_option()
def app():
    """PenguiFlow CLI - Bootstrap and manage agent projects."""
    pass

@app.command()
@click.option('--force', is_flag=True, help='Overwrite existing files')
@click.option('--dry-run', is_flag=True, help='Show what would be created')
def init(force: bool, dry_run: bool):
    """Initialize PenguiFlow development environment in current directory."""
    from penguiflow.cli.init import run_init
    run_init(force=force, dry_run=dry_run)

@app.command()
@click.argument('name')
@click.option('--minimal', is_flag=True, help='Minimal project structure')
def new(name: str, minimal: bool):
    """Create a new PenguiFlow agent project."""
    from penguiflow.cli.new import run_new
    run_new(name=name, minimal=minimal)

if __name__ == '__main__':
    app()
```

```python
# penguiflow/cli/init.py
from pathlib import Path
import importlib.resources

def run_init(force: bool = False, dry_run: bool = False) -> None:
    """Create .vscode/ directory with PenguiFlow dev tools."""
    import click

    vscode_dir = Path('.vscode')
    templates = importlib.resources.files('penguiflow').joinpath('templates/vscode')

    if dry_run:
        click.echo("Would create:")
        for template in templates.iterdir():
            click.echo(f"  .vscode/{template.name}")
        return

    vscode_dir.mkdir(exist_ok=True)

    for template in templates.iterdir():
        target = vscode_dir / template.name
        if target.exists() and not force:
            click.echo(f"⚠ Skipping {target} (exists, use --force to overwrite)")
            continue

        target.write_text(template.read_text())
        click.echo(f"✓ Created {target}")

    click.echo("\nPenguiFlow development environment ready!")
    click.echo("Open this folder in VS Code for snippets and debugging support.")
```

### Dependencies

Add to `pyproject.toml`:

```toml
[project.optional-dependencies]
cli = ["click>=8.0"]

# Or make click a core dependency (it's lightweight)
dependencies = [
    "pydantic>=2.0",
    "click>=8.0",  # For CLI
]
```

---

## Template Files

### VS Code Snippets (penguiflow.code-snippets)

```json
{
  "PenguiFlow Tool": {
    "prefix": "pftool",
    "scope": "python",
    "body": [
      "class ${1:Name}Args(BaseModel):",
      "    \"\"\"Input for ${1:Name}.\"\"\"",
      "    ${2:field}: ${3:str}",
      "",
      "",
      "class ${1:Name}Result(BaseModel):",
      "    \"\"\"Output of ${1:Name}.\"\"\"",
      "    ${4:result}: ${5:str}",
      "",
      "",
      "@tool(desc=\"${6:description}\", side_effects=\"${7|pure,read,write,external|}\")",
      "async def ${8:tool_name}(args: ${1:Name}Args, ctx: ToolContext) -> ${1:Name}Result:",
      "    \"\"\"${6:description}\"\"\"",
      "    publisher = ctx.tool_context.get(\"status_publisher\")",
      "    if callable(publisher):",
      "        publisher(StatusUpdate(status=\"thinking\", message=\"${9:Working...}\"))",
      "    ",
      "    $0",
      "    ",
      "    return ${1:Name}Result(${4:result}=\"\")"
    ]
  },

  "PenguiFlow Status Update": {
    "prefix": "pfstatus",
    "scope": "python",
    "body": [
      "publisher = ctx.tool_context.get(\"status_publisher\")",
      "if callable(publisher):",
      "    publisher(StatusUpdate(status=\"${1|thinking,ok,error|}\", message=\"${2:message}\"))"
    ]
  },

  "PenguiFlow Orchestrator": {
    "prefix": "pforchestrator",
    "scope": "python",
    "body": [
      "class ${1:Agent}Orchestrator:",
      "    def __init__(self, config: AgentConfig) -> None:",
      "        self.config = config",
      "        self._planner = self._build_planner()",
      "",
      "    def _build_planner(self) -> ReactPlanner:",
      "        nodes = [",
      "            Node(${2:tool_func}, name=\"${3:tool_name}\"),",
      "        ]",
      "        registry = ModelRegistry()",
      "        registry.register(\"${3:tool_name}\", ${4:InputModel}, ${5:OutputModel})",
      "        ",
      "        return ReactPlanner(",
      "            llm=self.config.llm_model,",
      "            catalog=build_catalog(nodes, registry),",
      "        )",
      "",
      "    async def execute(self, query: str) -> FinalAnswer:",
      "        result = await self._planner.run(",
      "            query=query,",
      "            llm_context={},",
      "            tool_context={},",
      "        )",
      "        if isinstance(result, PlannerPause):",
      "            return FinalAnswer(text=\"Paused\", route=\"pause\")",
      "        return FinalAnswer.model_validate(result.payload)"
    ]
  },

  "PenguiFlow Pydantic Model": {
    "prefix": "pfmodel",
    "scope": "python",
    "body": [
      "class ${1:ModelName}(BaseModel):",
      "    \"\"\"${2:Description}.\"\"\"",
      "    ${3:field}: ${4:str} = Field(description=\"${5:field description}\")"
    ]
  },

  "PenguiFlow Parallel Join": {
    "prefix": "pfjoin",
    "scope": "python",
    "body": [
      "class ${1:Merge}Args(BaseModel):",
      "    \"\"\"Join node for parallel fan-out.\"\"\"",
      "    branch_outputs: list[${2:BranchResult}]  # injected via $results",
      "    total_requests: int = 0                   # injected via $expect",
      "    failures: list[dict] = Field(default_factory=list)  # injected via $failures",
      "",
      "",
      "@tool(desc=\"Merge parallel results\", side_effects=\"pure\")",
      "async def ${3:merge_results}(args: ${1:Merge}Args, ctx: ToolContext) -> ${4:FinalResult}:",
      "    \"\"\"Merge results from parallel branches.\"\"\"",
      "    $0",
      "    return ${4:FinalResult}(...)"
    ]
  }
}
```

### Launch Configuration (launch.json)

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Debug: Current File",
      "type": "debugpy",
      "request": "launch",
      "program": "${file}",
      "cwd": "${workspaceFolder}",
      "env": {"PYTHONPATH": "${workspaceFolder}"}
    },
    {
      "name": "Debug: Pytest Current File",
      "type": "debugpy",
      "request": "launch",
      "module": "pytest",
      "args": ["${file}", "-v", "-s"],
      "cwd": "${workspaceFolder}"
    },
    {
      "name": "Debug: All Tests",
      "type": "debugpy",
      "request": "launch",
      "module": "pytest",
      "args": ["tests/", "-v"],
      "cwd": "${workspaceFolder}"
    }
  ]
}
```

### Tasks (tasks.json)

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Test",
      "type": "shell",
      "command": "uv run pytest tests/ -v",
      "group": {"kind": "test", "isDefault": true}
    },
    {
      "label": "Test (current file)",
      "type": "shell",
      "command": "uv run pytest ${file} -v"
    },
    {
      "label": "Type Check",
      "type": "shell",
      "command": "uv run mypy .",
      "group": "build"
    },
    {
      "label": "Lint",
      "type": "shell",
      "command": "uv run ruff check .",
      "group": "build"
    },
    {
      "label": "Lint (fix)",
      "type": "shell",
      "command": "uv run ruff check . --fix"
    },
    {
      "label": "Format",
      "type": "shell",
      "command": "uv run ruff format ."
    }
  ]
}
```

---

## Rollout Plan

### Phase 1: `penguiflow init` (v2.4)

- Implement `init` command
- Bundle VS Code templates
- Add to pyproject.toml scripts
- Document in README

### Phase 2: `penguiflow new` (v2.5)

- Implement `new` command with Jinja templates
- Add project templates (minimal, standard, enterprise)
- Add `--with-*` flags for optional features

### Phase 3: `penguiflow doctor` (v2.5+)

- Implement `doctor` command
- Check dependencies, versions, project structure
- Suggest fixes for common issues

---

## Alternatives Considered

| Approach | Pros | Cons |
|----------|------|------|
| **CLI (chosen)** | Travels with package, no external deps | Users must run command |
| VS Code Extension | Auto-discovery, marketplace | Maintenance, separate account |
| Cookiecutter/Copier | Rich templating | Separate tool to install |
| Documentation only | No code | Manual copy-paste |

---

## Success Metrics

- Users can set up dev environment in < 1 minute
- No support questions about "how to set up VS Code"
- Templates stay in sync with best practices (single source of truth)
- CLI is discoverable (`penguiflow --help`)

---

## Open Questions

1. Should `click` be a core dependency or optional?
   - Recommendation: Core (it's small, widely used)

2. Should templates use Jinja or simple string replacement?
   - Recommendation: Simple replacement for v1, Jinja if complexity grows

3. Should we support other IDEs (PyCharm, etc.)?
   - Recommendation: VS Code first, add others based on demand

4. Where should templates live?
   - Recommendation: `penguiflow/templates/` in the package
