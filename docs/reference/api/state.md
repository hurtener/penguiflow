# State stores

Persistence protocols and adapters for durable session state, stored events, and
conversation bindings.

The `ContextPatch` object is documented under [Routing policies](routing.md).

::: penguiflow.state
    options:
      members:
        - StateStore
        - StoredEvent
        - SupportsConversationBindings
        - RemoteBinding

::: penguiflow.sessions.persistence
    options:
      members:
        - SessionStateStore
        - InMemorySessionStateStore
        - StateStoreSessionAdapter

::: penguiflow.sessions.models
    options:
      members:
        - StateUpdate
        - UpdateType
