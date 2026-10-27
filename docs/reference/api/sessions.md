# Sessions & scheduling

Session management, transports, task lifecycle objects, and the job scheduler for
running background and recurring work.

::: penguiflow.sessions.session
    options:
      members:
        - SessionManager
        - SessionLimits
        - StreamingSession
        - TaskResult
        - TaskRuntime

::: penguiflow.sessions.transport
    options:
      members:
        - SessionConnection
        - Transport

::: penguiflow.sessions.scheduler
    options:
      members:
        - JobScheduler
        - JobSchedulerRunner
        - JobDefinition
        - ScheduleConfig

::: penguiflow.sessions.models
    options:
      members:
        - TaskContextSnapshot
        - TaskStatus
        - TaskType
        - NotificationAction

::: penguiflow.sessions.planner
    options:
      members:
        - PlannerTaskPipeline
