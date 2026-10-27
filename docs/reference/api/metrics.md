# Metrics & middleware

Observability primitives: the structured flow event, the pluggable middleware
hook, and helpers for formatting events and configuring logging.

::: penguiflow.metrics
    options:
      members:
        - FlowEvent

::: penguiflow.middlewares
    options:
      members:
        - Middleware
        - log_flow_events
        - LatencyCallback

::: penguiflow.debug
    options:
      members:
        - format_flow_event

::: penguiflow.logging
    options:
      members:
        - configure_logging
        - ExtraFormatter
        - StructuredFormatter
