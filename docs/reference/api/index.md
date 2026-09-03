# API Reference

This is the complete reference for PenguiFlow's public API. Every symbol
documented here is re-exported from `penguiflow` (or one of its public
sub-packages `penguiflow.planner`, `penguiflow.llm`, `penguiflow.tools`) and is
covered by the library's backward-compatibility guarantees.

Symbols are grouped by area below. Internal modules and private helpers are
intentionally omitted — only the curated public surface appears here.

- [Flow runtime](flow.md) — `PenguiFlow`, `Context`, nodes, registry, pattern helpers
- [Messages & types](messages.md) — `Message`, `Headers`, `WM`, `StreamChunk`, and related types
- [Errors](errors.md) — `FlowError`, `FlowErrorCode`, `CycleError`
- [Metrics & middleware](metrics.md) — `FlowEvent`, middleware hooks, logging helpers
- [Routing policies](routing.md) — config-driven routing types
- [Artifacts](artifacts.md) — artifact stores and references
- [Streaming](streaming.md) — stream helpers and SSE/WebSocket adapters
- [Visualization](viz.md) — Mermaid/DOT exporters
- [Remote execution](remote.md) — remote node and task types
- [Sessions & scheduling](sessions.md) — session manager, transports, job scheduler
- [State stores](state.md) — state persistence protocols and adapters
- [Skills](skills.md) — skill providers and configuration
- [Steering](steering.md) — steering inbox and events
- [Testing kit](testkit.md) — `FlowTestKit` helpers
- [Planner](planner.md) — `ReactPlanner` and planner configuration
- [LLM layer](llm.md) — native LLM client, providers, request/result types
- [Tools](tools.md) — `ToolNode`, OAuth, and presets
