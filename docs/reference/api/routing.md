# Routing policies

Config-driven routing: the policy protocol, its dictionary-backed
implementation, the routing request, and the context-patch objects used to
mutate routing state.

::: penguiflow.policies
    options:
      members:
        - RoutingPolicy
        - DictRoutingPolicy
        - RoutingRequest

::: penguiflow.sessions.models
    options:
      members:
        - ContextPatch

::: penguiflow.sessions.session
    options:
      members:
        - PendingContextPatch
