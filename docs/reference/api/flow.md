# Flow runtime

The core runtime: the flow orchestrator, execution context, nodes, the model
registry, and the built-in pattern helpers for routing, fan-out/fan-in, and
subflows.

## `penguiflow.core`

- `PenguiFlow`
- `Context`
- `create`
- `call_playbook`
- `DEFAULT_QUEUE_MAXSIZE`

## `penguiflow.node`

- `Node`
- `NodePolicy`

## `penguiflow.registry`

- `ModelRegistry`

## `penguiflow.patterns`

- `predicate_router`
- `union_router`
- `join_k`
- `map_concurrent`

## `penguiflow.catalog`

- `build_catalog`
- `tool`
- `NodeSpec`
- `SideEffect`
- `ToolInputExample`
- `ToolLoadingMode`

## `penguiflow.bus`

- `MessageBus`
- `BusEnvelope`
