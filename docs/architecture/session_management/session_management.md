# Session Management Architecture

## Overview

The Session Management subsystem provides sophisticated orchestration for bidirectional streaming sessions with support for background tasks, real-time steering, and task grouping. It manages the lifecycle of planning sessions, coordinates multiple concurrent tasks, handles context management, and provides real-time communication between the planning system and external interfaces. The system is designed to be safe-by-default with explicit session keys for multi-tenant deployments.

The subsystem implements a layered architecture that separates concerns between session orchestration, task execution, communication protocols, persistence, and external integration. It provides a robust foundation for complex multi-step workflows while maintaining clear boundaries between different components and ensuring proper resource management and isolation.

## Core Components

### 1. StreamingSession
- **Location**: `penguiflow/sessions/session.py`
- **Purpose**: Central orchestrator for bidirectional communication and task lifecycle management
- **Key Responsibilities**:
  - **Session State Management**: The StreamingSession maintains the complete state of a planning session including the current context, active tasks, pending updates, and session metadata. It tracks the version and hash of the session context to detect changes and prevent conflicts when applying context patches. The session maintains a registry of all tasks spawned within it, tracking their status, priority, and execution state.

  - **Task Orchestration**: The session manages the spawning, execution, and lifecycle of both foreground and background tasks. It enforces concurrency limits through semaphores, validates task creation against configured limits, and manages task dependencies. The session handles task grouping by maintaining group membership and coordinating group-level operations like sealing and completion tracking.

  - **Communication Hub**: Acts as the central hub for all communication within the session. It manages the UpdateBroker for publishing state changes, maintains steering inboxes for each task, and handles the subscription mechanism for external consumers. The session ensures that updates are published consistently and that subscribers receive only the updates they're interested in.

  - **Context Management**: Maintains session-level context including LLM context, tool context, and memory. It handles context versioning and hashing for change detection, manages context snapshots for task isolation, and applies context patches using different merge strategies (APPEND, REPLACE, HUMAN_GATED). The session also manages context divergence detection and handles cases where background tasks complete with outdated context.

  - **Resource Management**: Enforces resource limits including maximum tasks per session, concurrent task limits, and task runtime limits. It manages memory usage through token estimation and budget enforcement, implements backpressure mechanisms when resources are constrained, and handles cleanup of completed tasks to prevent resource leaks.

  - **Error Handling & Recovery**: Implements comprehensive error handling for task execution failures, context patch application failures, and session-level errors. It provides retry mechanisms with exponential backoff, handles cancellation propagation between tasks, and maintains session health metrics for monitoring and alerting.

### 2. SessionContext
- **Location**: `penguiflow/sessions/session.py`
- **Purpose**: Maintain session-level context including LLM context, tool context, and memory
- **Key Responsibilities**:
  - **Context Versioning**: The SessionContext maintains a version counter that increments whenever the context changes. This allows the system to detect when context patches are applied to outdated versions and handle conflicts appropriately. The version is used in conjunction with the context hash to provide optimistic locking semantics for context updates.

  - **Hash Validation**: Computes SHA-256 hashes of the LLM context to detect changes between context patch applications. This is crucial for identifying when background tasks complete with context that has changed since they started, allowing the system to flag results as potentially divergent and handle them appropriately.

  - **Context Isolation**: Provides isolated copies of context to different tasks to prevent cross-task contamination. For background tasks, it creates deep copies of the current context snapshot, while for foreground tasks it may share context references for efficiency. The context includes LLM context (for LLM consumption), tool context (for external integrations), memory (for conversation history), and artifacts (for file-like data).

  - **Memory Integration**: Integrates with the memory management system to maintain conversation history and context across planning steps. It tracks memory state and ensures that memory updates are properly synchronized with the session context.

  - **Token Estimation**: Provides token estimation for the current context to help enforce token budgets and prevent context window overflow. Uses a conservative character-based heuristic (length divided by 4) that approximates token count for most models.

### 3. SessionLimits
- **Location**: `penguiflow/sessions/session.py`
- **Purpose**: Define resource limits and constraints for session management
- **Key Responsibilities**:
  - **Task Limits**: Enforces maximum number of tasks per session to prevent resource exhaustion. This includes both total task limits and background task limits to ensure that sessions don't spawn unlimited numbers of concurrent operations. The limits are checked during task spawning and can be configured differently for different deployment scenarios.

  - **Concurrency Control**: Manages maximum concurrent tasks through asyncio semaphores to prevent overwhelming the system. The semaphore ensures that only a configured number of tasks execute simultaneously, preventing resource contention and ensuring fair scheduling.

  - **Queue Management**: Sets maximum sizes for update queues and steering event queues to prevent memory bloat. When queues reach their limits, the system implements appropriate backpressure mechanisms to slow down producers or drop oldest items.

  - **Runtime Constraints**: Sets maximum runtime limits for individual tasks to prevent long-running operations from blocking other work. Tasks that exceed their runtime limits are automatically cancelled and marked as failed.

  - **Security Boundaries**: Provides security boundaries to prevent denial-of-service attacks through resource exhaustion. These limits are enforced at multiple levels to ensure that no single session can overwhelm the system.

### 4. TaskRuntime
- **Location**: `penguiflow/sessions/session.py`
- **Purpose**: Runtime helpers exposed to task pipelines
- **Key Responsibilities**:
  - **Update Emission**: Provides a standardized interface for tasks to emit updates to the session. The emit_update method allows tasks to publish different types of updates (progress, status changes, results, errors) with proper metadata including trace IDs and step indices. This ensures that all task updates follow a consistent format and can be properly processed by subscribers.

  - **Notification System**: Enables tasks to send notifications to users through the notify method. Notifications can include severity levels, titles, bodies, and actionable buttons that allow users to interact with task results. The notification system integrates with the UI to provide timely alerts about task completion or important events.

  - **Steering Integration**: Provides access to the steering inbox for the current task, allowing tasks to receive real-time control signals. This enables tasks to be paused, cancelled, or redirected while they're running, providing a responsive user experience.

  - **Context Access**: Gives tasks access to their initial context snapshot, including the LLM context, tool context, and memory state that was current when the task was spawned. This ensures that tasks operate with consistent context even if the session context changes during execution.

  - **State Management**: Maintains the task's current state and progress information, allowing tasks to update their status and provide feedback about their execution progress. This information is used for monitoring and user interface updates.

### 5. TaskRegistry
- **Location**: `penguiflow/sessions/registry.py`
- **Purpose**: Track and manage task lifecycle across sessions
- **Key Responsibilities**:
  - **Task Creation & Tracking**: Manages the complete lifecycle of tasks from creation through completion or failure. When a task is created, the registry assigns it a unique ID, sets its initial status to PENDING, and stores its metadata including description, priority, and spawn reason. The registry maintains indexes for fast lookup by status, type, and parent-child relationships.

  - **Status Management**: Tracks and updates task statuses (PENDING, RUNNING, PAUSED, COMPLETE, FAILED, CANCELLED) with proper state transition validation. It ensures that tasks can only transition between valid states and maintains timestamps for each status change to enable accurate monitoring and analytics.

  - **Child Relationship Management**: Maintains parent-child relationships between tasks to enable hierarchical task structures and proper cancellation propagation. When a task spawns child tasks, the registry tracks these relationships and can efficiently query for all children of a particular task.

  - **Persistence Integration**: Integrates with the session state store to persist task state changes durably. Each status update, priority change, or metadata update is saved to the persistence layer to ensure that task state survives system restarts.

  - **Query & Filtering**: Provides efficient querying capabilities to list tasks by session, status, type, or other criteria. This enables monitoring dashboards, administrative tools, and debugging utilities to inspect task state across the system.

### 6. UpdateBroker
- **Location**: `penguiflow/sessions/broker.py`
- **Purpose**: Publish-subscribe system for session updates
- **Key Responsibilities**:
  - **Event Distribution**: Implements a publish-subscribe pattern to distribute state updates from the session to multiple subscribers. When the session publishes an update, the broker efficiently distributes it to all interested subscribers based on their subscription criteria (task IDs, update types, etc.).

  - **Subscription Management**: Manages the lifecycle of subscriptions including creation, filtering, and cleanup. Subscribers can specify which tasks and update types they're interested in, and the broker ensures that only relevant updates are delivered to each subscriber.

  - **Queue Management**: Maintains per-subscriber queues with configurable size limits to prevent memory exhaustion. When queues reach their limits, the broker implements appropriate strategies such as dropping oldest items or applying backpressure to publishers.

  - **Filtering & Routing**: Implements sophisticated filtering logic to ensure that subscribers only receive updates they're interested in. This includes filtering by task ID, update type, and potentially other metadata fields to optimize network usage and processing overhead.

  - **Async Iterator Interface**: Provides an async iterator interface that allows subscribers to consume updates in a streaming fashion. This enables real-time processing of updates without blocking other operations and provides a natural interface for async/await programming patterns.

### 7. SessionStateStore
- **Location**: `penguiflow/sessions/persistence.py`
- **Purpose**: Persistence layer for session state and updates
- **Key Responsibilities**:
  - **State Persistence**: Provides durable storage for session state including task states, context snapshots, and update histories. The store ensures that session state survives system restarts and can be recovered when needed. It implements appropriate transaction semantics to ensure data consistency during state updates.

  - **Update History**: Maintains a complete history of all state updates for audit, debugging, and replay purposes. Each update is stored with sufficient metadata to reconstruct the session state at any point in time, enabling features like session replay and historical analysis.

  - **Steering Event Storage**: Persists steering events to ensure that control signals survive system restarts and can be reapplied to tasks when they resume. This is crucial for maintaining the integrity of the steering system across failures.

  - **Task State Management**: Manages the persistence of individual task states including their current status, progress, results, and error information. The store provides efficient indexing and querying capabilities to support task management operations.

  - **Recovery Mechanics**: Implements recovery mechanisms to restore session state from persistence when sessions are reactivated. This includes loading task states, rebuilding context, and reconnecting steering inboxes to ensure seamless continuation of operations.

### 8. TaskService
- **Location**: `penguiflow/sessions/task_service.py`
- **Purpose**: Planner-facing interface for task orchestration
- **Key Responsibilities**:
  - **Task Spawning Interface**: Provides a clean, planner-focused interface for spawning background tasks. The spawn method handles all the complexity of creating task contexts, validating spawn requests against policies, and managing task groups. It returns structured results that include task IDs, status information, and group completion results when applicable.

  - **Spawn Guard Integration**: Integrates with spawn guards to enforce policies around task creation. Spawn guards can implement business logic to determine whether particular tasks should be allowed based on factors like resource availability, cost considerations, or security policies.

  - **Context Depth Management**: Manages the depth of context provided to background tasks through the context_depth parameter. This allows callers to control how much of the current session context is provided to background tasks, balancing between context availability and security/privacy considerations.

  - **Group Management**: Handles the creation and management of task groups for coordinated execution. The service resolves group names to specific group instances, manages group sealing, and coordinates group-level operations like collective approval of results.

  - **Result Integration**: Manages the integration of background task results back into the main session context using different merge strategies. It handles the complexities of context patch application, including version/hashing validation and human-gated approval workflows.

### 9. TaskGroup
- **Location**: `penguiflow/sessions/models.py`
- **Purpose**: Group related background tasks for coordinated execution and reporting
- **Key Responsibilities**:
  - **Task Aggregation**: Groups related background tasks together for coordinated management. Each group maintains a list of task IDs that belong to it, allowing operations to be applied collectively to all tasks in the group. This enables features like group-level cancellation, prioritization, and status reporting.

  - **Lifecycle Management**: Manages the complete lifecycle of task groups from creation through completion. Groups start in an "open" state where new tasks can join, transition to "sealed" when no more tasks are expected to join, and finally reach "complete" or "failed" status when all constituent tasks have reached terminal states.

  - **Completion Tracking**: Tracks the completion status of all tasks within the group and determines when the group as a whole is complete. It maintains separate lists of completed and failed tasks to provide detailed status information and enable appropriate error handling.

  - **Reporting Coordination**: Coordinates the reporting of results from all tasks in the group. The group can implement different reporting strategies (report when all tasks complete, when any task completes, or never automatically) to match different use cases and user experience requirements.

  - **Context Integration**: Manages how results from multiple tasks in the group are integrated back into the session context. It can aggregate results from multiple tasks and apply them together using appropriate merge strategies, providing a cohesive user experience when multiple background tasks contribute to a single outcome.

### 10. ContextPatch
- **Location**: `penguiflow/sessions/models.py`
- **Purpose**: Represent changes to session context for merging
- **Key Responsibilities**:
  - **Result Serialization**: Captures the results of completed tasks in a structured format that can be applied to the session context. The patch includes not just the raw result but also metadata like digests, facts, artifacts, and sources that provide context for the changes being made.

  - **Change Validation**: Includes mechanisms to validate that the patch is being applied to the correct context version. The patch contains source context version and hash information that is compared against the current session context to detect conflicts or divergence.

  - **Divergence Handling**: Flags when the context has changed since the task that generated the patch started executing. This allows the system to handle cases where background tasks complete with results that may be based on outdated context, ensuring that users are aware of potential inconsistencies.

  - **Merge Strategy Support**: Works with different merge strategies (APPEND, REPLACE, HUMAN_GATED) to determine how the patch should be applied to the session context. The patch structure supports all the information needed for different types of context integration.

  - **Metadata Preservation**: Preserves important metadata about the task that generated the patch including the task ID, spawn event ID, and completion timestamp. This enables proper attribution of changes and supports debugging and audit requirements.

## Key Features

### 1. Task Orchestration
- **Background Task Spawning**: Supports spawning background tasks that run concurrently with the main planning process. These tasks can perform long-running operations without blocking the main planner, enabling more responsive user experiences.
- **Foreground Task Management**: Manages the primary planning task that interacts directly with users and coordinates background tasks.
- **Task Lifecycle Management**: Comprehensive management of task lifecycles from creation through completion, including proper resource cleanup and state management.
- **Task Prioritization**: Supports task prioritization to ensure critical tasks receive appropriate resources and attention.

### 2. Real-time Steering
- **Bidirectional Control**: Provides real-time control over planning processes through the steering system, allowing external systems to pause, resume, redirect, or cancel ongoing operations.
- **Event-Driven Architecture**: Implements an event-driven architecture for steering that allows for responsive control without tight coupling between components.
- **Approval Workflows**: Supports approval workflows that allow human intervention in automated processes when needed.
- **Cancellation Propagation**: Ensures that cancellation requests propagate correctly through task hierarchies, preventing orphaned tasks and resource leaks.

### 3. Context Management
- **Versioned Context**: Maintains versioned session context to detect and handle concurrent modifications appropriately.
- **Hash Validation**: Uses cryptographic hashes to validate context consistency and detect changes between patch applications.
- **Deep Copying**: Implements proper deep copying of context for task isolation to prevent cross-task contamination.
- **Merge Strategies**: Supports multiple strategies for merging context changes including append, replace, and human-gated approaches.

### 4. Resource Management
- **Concurrency Limits**: Enforces configurable limits on concurrent tasks to prevent resource exhaustion.
- **Task Limits**: Controls the total number of tasks per session to prevent runaway operations.
- **Runtime Limits**: Implements runtime limits for individual tasks to prevent long-running operations from blocking other work.
- **Memory Management**: Efficiently manages memory usage through token estimation and context summarization.

### 5. Error Handling & Recovery
- **Task-Level Recovery**: Implements recovery mechanisms for individual task failures without affecting the entire session.
- **Context Patch Validation**: Validates context patches before application to prevent corruption of session state.
- **Graceful Degradation**: Provides graceful degradation when resources are constrained or errors occur.
- **Comprehensive Logging**: Maintains detailed logs of all session activities for debugging and monitoring.

## Data Flow Patterns

### 1. Task Spawning Flow
```
Planner Request → Session Validation → Context Snapshot → Task Creation → Execution Start
```
- The session validates the spawn request against configured limits
- Creates a context snapshot for the new task
- Registers the task with the registry
- Starts the task execution with appropriate resource controls

### 2. Context Update Flow
```
Task Completion → Context Patch → Validation → Merge Strategy → Session Context Update
```
- Completed tasks generate context patches with their results
- Patches are validated against current context version/hash
- Applied using the appropriate merge strategy
- Session context is updated and version incremented

### 3. Steering Event Flow
```
External Control → Event Validation → Session Processing → Task Steering → Status Update
```
- External systems send steering events to the session
- Events are validated and processed by the session
- Appropriate actions are taken on target tasks
- Status updates are published to subscribers

This architecture enables sophisticated session management with support for complex multi-step workflows, real-time control, and proper resource management while maintaining clear separation of concerns between different system components.