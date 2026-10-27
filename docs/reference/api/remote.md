# Remote execution

Types for invoking remote nodes and tracking remote task lifecycles, including
streaming events, pagination, and terminal-state constants.

::: penguiflow.remote
    options:
      members:
        - RemoteNode
        - RemoteTransport
        - RemoteCallRequest
        - RemoteCallResult
        - RemotePushNotificationBinding
        - RemoteStreamEvent
        - RemoteTaskEvent
        - RemoteTaskAuthRequired
        - RemoteTaskInputRequired
        - RemoteTaskPage
        - RemoteTaskSnapshot
        - RemoteTaskState
        - RemoteTaskStatus
        - SupportsRemoteTasks
        - REMOTE_TERMINAL_TASK_STATES
