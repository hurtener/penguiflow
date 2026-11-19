# Remote execution

Protocols and types for calling remote agents and tracking remote task
lifecycles, including streaming events, pagination, and terminal-state
constants. `penguiflow_a2a.A2AHttpTransport` implements them for A2A.

## `penguiflow.remote`

- `RemoteTransport`
- `RemoteCallRequest`
- `RemoteCallResult`
- `RemotePushNotificationBinding`
- `RemoteStreamEvent`
- `RemoteTaskEvent`
- `RemoteTaskAuthRequired`
- `RemoteTaskInputRequired`
- `RemoteTaskPage`
- `RemoteTaskSnapshot`
- `RemoteTaskState`
- `RemoteTaskStatus`
- `SupportsRemoteTasks`
- `REMOTE_TERMINAL_TASK_STATES`
