# PenguiFlow

PenguiFlow is an **async-first library for LLM tool-using agents** built on
`ReactPlanner`:

- **Typed tools** (Pydantic input/output contracts, MCP/UTCP/HTTP tool sources)
- **Planner runtime** (parallel tool calls, pause/resume, steering, memory, guardrails, reflection)
- **Production surfaces** (streaming sessions, background tasks, state stores, A2A serving and delegation)

## Start here

- New to PenguiFlow? Read **[Concepts](getting-started/concepts.md)**.
- Want to run something today? Follow **[Quickstart](getting-started/quickstart.md)**.
- Building an LLM tool-using agent? Start with **[ReactPlanner overview](planner/overview.md)**.

## What’s in scope for these docs

These pages are the curated, user-facing documentation.

The repository also contains:

- RFCs and proposals under `docs/RFC/` and `docs/proposals/` (implementation notes)
- deeper architecture notes under `docs/architecture/`

Those are valuable for contributors, but they are not part of the curated docs and may assume internal context.

## Docs map

| If you want to… | Start here |
|---|---|
| Build an LLM-driven agent | [ReactPlanner](planner/overview.md) |
| Run tools in parallel / join results | [Parallel & joins](planner/parallel-and-joins.md) |
| Stream partial output (tokens/status) | [Observability](planner/observability.md) |
| Cancel or steer in-flight work | [Steering](planner/steering.md) |
| Connect MCP/UTCP/HTTP tools | [Tooling](planner/tooling.md) |
| Ship pause/resume (OAuth/HITL) | [Pause/resume](planner/pause-resume-hitl.md) |
| Persist/replay/operate at scale | [State store](tools/statestore.md) |
