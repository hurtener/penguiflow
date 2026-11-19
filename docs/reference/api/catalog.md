# Tool definitions

Building blocks for exposing Python functions to the planner as tools: nodes,
the model registry that validates tool input and output, and the catalog
helpers that turn them into planner tool specs.

## `penguiflow.node`

- `Node`
- `NodePolicy`

## `penguiflow.registry`

- `ModelRegistry`

## `penguiflow.catalog`

- `build_catalog`
- `tool`
- `NodeSpec`
- `SideEffect`
- `ToolInputExample`
- `ToolLoadingMode`
