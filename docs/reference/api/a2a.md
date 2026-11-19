# A2A

Serve a `ReactPlanner` as an A2A agent, and call remote A2A agents from a
planner as tools.

## `penguiflow_a2a` — server

- `A2AService` — runs one planner task per A2A task. It maps `PlannerFinish`
  to `completed`/`failed` and `PlannerPause` to `input-required`, which the next
  message resumes. It also handles `tasks/cancel`, and streams the answer for
  planners with `stream_final_response=True`.
- `A2AConfig`
- `create_a2a_http_app`
- `create_a2a_http_router`
- `install_a2a_http`
- `A2AGrpcServicer`, `add_a2a_grpc_service` (require the `a2a-grpc` extra)

## `penguiflow_a2a` — client

- `A2AHttpTransport`
- `A2AAgentToolset`
- `RemoteEventSink`
- `A2ARouterToolset`
- `AgentRegistry`
- `AgentRouteCandidate`
- `AgentRouteRequest`
- `RemoteAgentRecord`
- `RemoteSkillRecord`
- `RouterPolicy`
- `RouterDelegationArgs`
- `RouterDelegationResult`
- `fetch_agent_card`
- `load_agent_registry_config`
