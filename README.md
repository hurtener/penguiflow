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

A Python-native runtime for **typed, steerable, bounded** AI agents — and the deterministic pipelines under them.

PenguiFlow runs async node graphs where every hop validates its data, every run stays inside a budget, and the same core powers both a deterministic data pipeline and a tool-using agent. It is asyncio-only and built on Pydantic v2, with no heavy runtime dependencies.

## Why PenguiFlow

Many agent and pipeline frameworks are loosely-typed loops: a node returns the wrong shape and you find out several hops later, a planner runs past its budget with no ceiling, a crash loses the run's state, and approving a risky step means not automating it. PenguiFlow treats those as the framework's responsibility, not yours:

- **Typed at every boundary.** Each node validates its input and output against Pydantic models, so malformed data is caught at its source instead of downstream.
- **Bounded by design.** Bounded queues apply real backpressure; per-trace deadlines, hop budgets, and cancellation keep loops and fan-outs from running away.
- **Steerable mid-run.** Pause for human approval (HITL), inject steering events, and resume — without losing the trajectory so far.
- **Durable and observable.** An optional `StateStore` persists events for audit and recovery; every run carries a `trace_id`, can stream partial output, and records its trajectory.
- **One runtime for agents and pipelines.** The `ReactPlanner` (JSON-first tool orchestration, parallel fan-out and joins, pause/resume) runs on the exact same typed, bounded core as a plain data flow.

## Architecture at a glance

```
┌─────────────────────────────────────────────────────────────┐
│  Agents      ReactPlanner · ToolNode (MCP / UTCP / HTTP)      │
│              JSON tool loop · HITL pause/resume · fan-out/join│
├─────────────────────────────────────────────────────────────┤
│  Flow        async node graph · bounded queues (backpressure)│
│  runtime     routers · subflows · streaming                  │
├─────────────────────────────────────────────────────────────┤
│  Envelope    Message: trace_id · deadline · hop budget · meta │
│  Reliability per-node retries / timeouts · per-trace cancel   │
├─────────────────────────────────────────────────────────────┤
│  Ops         StateStore (durable events) · metrics / hooks    │
└─────────────────────────────────────────────────────────────┘
     emit()  ──►   typed in/out validated at every node   ──►  fetch()
```

## Concepts at a glance

- **Flow**: a directed graph (runtime) you `run()`, `emit()` into, and `fetch()` results from.
- **Node**: an async function plus a `NodePolicy` (validation, retries, timeout).
- **Message** *(recommended for production)*: `Message(payload=..., headers=Headers(tenant=...), trace_id=...)` enabling trace correlation, cancellation, deadlines, and streaming.
- **ReactPlanner** *(agents)*: a JSON-first planning loop over your tools, with pause/resume, parallel calls, and trajectory logging.
- **StateStore** *(optional)*: durability, audit, and event persistence for distributed, ops-ready deployments.

## Install

Requirements: Python **3.11+**

```bash
pip install penguiflow
```

Common extras:

```bash
pip install "penguiflow[planner]"      # ReactPlanner + ToolNode integrations
pip install "penguiflow[llm]"          # native LLM provider SDKs
pip install "penguiflow[a2a-server]"   # A2A HTTP+JSON server bindings
pip install "penguiflow[a2a-client]"   # A2A client bindings
```

If you use `uv`:

```bash
uv pip install penguiflow
```

## Quickstart

PenguiFlow has two entry points that share the same runtime: a **typed pipeline** you wire yourself, and an **agent** scaffolded from a template.

### 1) Typed pipeline (runtime)

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

### 2) Agent (ReactPlanner via CLI — fastest path)

```bash
uv run penguiflow new my-agent --template react
cd my-agent
uv sync
uv run penguiflow dev --project-root .
```

## Documentation

- Docs site (MkDocs): https://hurtener.github.io/penguiflow/
- API reference (every public symbol): https://hurtener.github.io/penguiflow/reference/api/
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

PenguiFlow is on the **3.x** line and follows SemVer with a documented public API surface — additions are additive, and breaking changes are called out in the changelog.

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
