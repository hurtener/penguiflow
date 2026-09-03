# Flow runtime

The core runtime: the flow orchestrator, execution context, nodes, the model
registry, and the built-in pattern helpers for routing, fan-out/fan-in, and
subflows.

::: penguiflow.core
    options:
      members:
        - PenguiFlow
        - Context
        - create
        - call_playbook
        - DEFAULT_QUEUE_MAXSIZE

::: penguiflow.node
    options:
      members:
        - Node
        - NodePolicy

::: penguiflow.registry
    options:
      members:
        - ModelRegistry

::: penguiflow.patterns
    options:
      members:
        - predicate_router
        - union_router
        - join_k
        - map_concurrent

::: penguiflow.catalog
    options:
      members:
        - build_catalog
        - tool
        - NodeSpec
        - SideEffect
        - ToolInputExample
        - ToolLoadingMode

::: penguiflow.bus
    options:
      members:
        - MessageBus
        - BusEnvelope
