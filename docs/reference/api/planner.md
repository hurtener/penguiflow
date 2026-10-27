# Planner

The `ReactPlanner` and its supporting configuration: tool context, reflection and
tool-selection policies, parallel-call/join primitives, and trajectory objects.

Skill provider and configuration types re-exported by the planner are documented
under [Skills](skills.md).

::: penguiflow.planner
    options:
      members:
        - ReactPlanner
        - ToolContext
        - AnyContext
        - PlannerAction
        - PlannerFinish
        - PlannerPause
        - PlannerEvent
        - PlannerEventCallback
        - ReflectionConfig
        - ReflectionCriteria
        - ReflectionCritique
        - ErrorRecoveryConfig
        - LLMContextHook
        - LLMContextHookInput
        - LLMErrorType
        - JoinInjection
        - ParallelCall
        - ParallelJoin
        - ToolPolicy
        - ToolVisibilityPolicy
        - ToolSearchConfig
        - ToolExamplesConfig
        - ToolDirectoryConfig
        - ToolGroupConfig
        - ToolHintsConfig
        - BackgroundTasksConfig
        - BackgroundTaskHandle
        - BackgroundTaskResult
        - DSPyLLMClient
        - Trajectory
        - TrajectoryStep
        - TrajectorySummary
