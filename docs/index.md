# PenguiFlow

PenguiFlow is an **async-first orchestration library** for building:

- **Typed agent workflows** (LLM planner + tools)
- **Concurrent pipelines** (fan-out/fan-in, joins, routers)
- **Production-ready runtime behavior** (timeouts, retries, backpressure, cancellation, telemetry hooks)

## Start here

- New to PenguiFlow? Read **[Concepts](getting-started/concepts.md)**.
- Want to run something today? Follow **[Quickstart](getting-started/quickstart.md)**.
- Building an LLM tool-using agent? Start with **[ReactPlanner overview](planner/overview.md)**.

## What’s in scope for these docs

This site is the curated, user-facing documentation.

The repository also contains:

- RFCs and proposals under `docs/RFC/` and `docs/proposals/` (implementation notes)
- deeper architecture notes under `docs/architecture/`

Those are valuable for contributors, but they are not part of the curated navigation and may assume internal context.

## Docs map

| If you want to… | Start here |
|---|---|
| Build a typed async pipeline | [Flows & nodes](core/flows-and-nodes.md) |
| Add parallel fan-out / joins | [Concurrency](core/concurrency.md) |
| Stream partial output (tokens/status) | [Streaming](core/streaming.md) |
| Cancel in-flight work | [Cancellation](core/cancellation.md) |
| Build an LLM-driven orchestrator | [ReactPlanner](planner/overview.md) |
| Connect MCP/UTCP/HTTP tools | [Tooling](planner/tooling.md) |
| Ship pause/resume (OAuth/HITL) | [Pause/resume](planner/pause-resume-hitl.md) |
| Persist/replay/operate at scale | [State store](tools/statestore.md) |
