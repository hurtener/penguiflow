# API Reference

This is the complete reference for PenguiFlow's public API. Every symbol
documented here is re-exported from `penguiflow` (or one of its public
sub-packages `penguiflow.planner`, `penguiflow.llm`, `penguiflow.tools`) or from
`penguiflow_a2a`, and is covered by the library's backward-compatibility
guarantees.

Symbols are grouped by area below. Internal modules and private helpers are
intentionally omitted — only the curated public surface appears here.

Each page lists the public names per module; their docstrings in the source are
the detailed reference.

- [Planner](planner.md) — `ReactPlanner` and planner configuration
- [Tool definitions](catalog.md) — nodes, model registry, catalog helpers
- [LLM layer](llm.md) — native LLM client, providers, request/result types
- [Tools](tools.md) — `ToolNode`, OAuth, and presets
- [Skills](skills.md) — skill providers and configuration
- [Steering](steering.md) — steering inbox and events
- [Sessions & scheduling](sessions.md) — session manager, transports, job scheduler
- [State stores](state.md) — state persistence protocols and adapters
- [Artifacts](artifacts.md) — artifact stores and references
- [A2A](a2a.md) — serve a planner over A2A and call remote A2A agents
- [Remote execution](remote.md) — remote transport protocols and task types
- [Messages](messages.md) — `Message` and `Headers`
- [Logging](logging.md) — logging configuration helpers
