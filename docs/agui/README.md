# AG-UI Adapter and Protocol Diagrams

This folder documents how the AG-UI adapter and protocol work in PenguiFlow.
Each file focuses on a single flow so it is easy to map the request/response
path to real code.

Files:
- flow-end-to-end.md: UI -> backend -> planner -> UI event loop.
- flow-context-mapping.md: How llm_context and tool_context move through AG-UI.
- flow-event-mapping.md: PlannerEvent types mapped to AG-UI events.
- flow-tool-calls.md: Tool call lifecycle event flow.
- flow-artifacts-resources.md: Artifact and MCP resource events.
- flow-pause.md: Pause and resume signaling.

Related docs:
- docs/PLAYGROUND_BACKEND_CONTRACTS.md
- docs/RFC_AGUI_INTEGRATION.md
