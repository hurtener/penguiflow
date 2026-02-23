# PenguiFlow

<p align="center">
  <img src="asset/Penguiflow.png" alt="PenguiFlow logo" width="220">
</p>

<p align="center">
  <a href="https://github.com/hurtener/penguiflow/actions/workflows/ci.yml"><img src="https://github.com/hurtener/penguiflow/actions/workflows/ci.yml/badge.svg" alt="CI Status"></a>
  <a href="https://pypi.org/project/penguiflow/"><img src="https://img.shields.io/pypi/v/penguiflow.svg" alt="PyPI version"></a>
  <a href="https://hurtener.github.io/penguiflow/"><img src="https://img.shields.io/badge/docs-mkdocs%20material-teal" alt="Docs"></a>
  <a href="https://nightly.link/hurtener/penguiflow/workflows/benchmarks/main/benchmarks.json.zip"><img src="https://img.shields.io/badge/benchmarks-latest-orange" alt="Benchmarks"></a>
  <a href="https://github.com/hurtener/penguiflow/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License"></a>
</p>

Async-first orchestration library for **typed, reliable, concurrent** workflows — from deterministic data pipelines to LLM agents.

## Why PenguiFlow

- **Graph runtime**: run async node graphs with bounded queues (backpressure).
- **Reliability controls**: per-node timeouts + retries, plus per-trace cancellation and deadlines (envelope mode).
- **Streaming**: emit partial output (`StreamChunk`) and a final answer with deterministic correlation.
- **Planner (ReactPlanner)**: JSON-first tool orchestration with pause/resume (HITL), parallel fan-out + joins, and trajectory logging.
- **Tool integrations**: native + ToolNode (MCP / UTCP / HTTP) with auth and resilience patterns.

## Concepts at a glance

- **Flow**: a directed graph (runtime) you `run()`, `emit()` into, and `fetch()` results from.
- **Node**: an async function + `NodePolicy` (validation, retries, timeout).
- **Message** *(recommended for production)*: `Message(payload=..., headers=Headers(tenant=...), trace_id=...)` enabling trace correlation, cancellation, deadlines, and streaming.
- **StateStore** *(optional)*: durability/audit/event persistence for distributed and “ops-ready” deployments.

## Install

Requirements: Python **3.11+**

```bash
pip install penguiflow
```

Common extras:

```bash
pip install "penguiflow[planner]"      # ReactPlanner + ToolNode integrations
pip install "penguiflow[a2a-server]"   # A2A HTTP+JSON server bindings
pip install "penguiflow[a2a-client]"   # A2A client bindings
```

If you use `uv`:

```bash
uv pip install penguiflow
```

## Quickstart

### 1) Minimal typed flow (runtime)

```python
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from penguiflow import ModelRegistry, Node, NodePolicy, create


class In(BaseModel):
    text: str


class Out(BaseModel):
    upper: str


async def to_upper(msg: In, _ctx) -> Out:
    return Out(upper=msg.text.upper())


async def main() -> None:
    node = Node(to_upper, name="to_upper", policy=NodePolicy(validate="both"))

    registry = ModelRegistry()
    registry.register("to_upper", In, Out)

    flow = create(node.to())
    flow.run(registry=registry)

    await flow.emit(In(text="hello"))
    result: Out = await flow.fetch()
    await flow.stop()

    print(result.upper)


if __name__ == "__main__":
    asyncio.run(main())
```

### 2) ReactPlanner via CLI (fastest path)

```bash
uv run penguiflow new my-agent --template react
cd my-agent
uv sync
uv run penguiflow dev --project-root .
```

## Documentation (canonical)

- Docs site (MkDocs): https://hurtener.github.io/penguiflow/
- Source docs in repo: [docs/](docs/)

Suggested starting points (in-repo sources):

- Getting started: [docs/getting-started/quickstart.md](docs/getting-started/quickstart.md)
- Core runtime: [docs/core/flows-and-nodes.md](docs/core/flows-and-nodes.md), [docs/core/messages-and-envelopes.md](docs/core/messages-and-envelopes.md)
- Planner: [docs/planner/overview.md](docs/planner/overview.md)
- Tool integrations: [docs/tools/configuration.md](docs/tools/configuration.md)
- Deployment runbooks: [docs/deployment/production-deployment.md](docs/deployment/production-deployment.md)
- Observability runbooks: [docs/observability/metrics-and-alerts.md](docs/observability/metrics-and-alerts.md)
- CLI: [docs/cli/overview.md](docs/cli/overview.md)

## Stability, versioning, and public API

PenguiFlow follows a **2.x** line and aims to follow SemVer with a clear public surface.

- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Versioning & deprecations: [VERSIONING.md](VERSIONING.md)
- Public API surface: [docs/reference/public-api.md](docs/reference/public-api.md)

## Contributing, security, and support

- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Code of Conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- Security: [SECURITY.md](SECURITY.md)
- Support: [SUPPORT.md](SUPPORT.md)

## License

MIT — see [LICENSE](LICENSE).
