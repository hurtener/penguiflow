# Memory Management Architecture

## Overview

The Memory Management subsystem provides sophisticated short-term memory capabilities for the Penguiflow system, enabling conversation continuity, context preservation, and isolation between different planning sessions. It implements a safe-by-default, opt-in memory system with explicit session keys for multi-tenant deployments. The system supports multiple memory strategies including truncation, rolling summaries, and no memory, with configurable budgets and overflow policies.

## Core Components

### 1. MemoryKey
- **Location**: `penguiflow/planner/memory.py`
- **Purpose**: Composite key for memory isolation and multi-tenant safety
- **Key Responsibilities**:
  - Provide tenant, user, and session isolation
  - Enable explicit memory key resolution from tool context
  - Support ephemeral key generation when explicit keys are not provided
  - Prevent context leakage between sessions

### 2. ShortTermMemory Protocol
- **Location**: `penguiflow/planner/memory.py`
- **Purpose**: Defines the minimal required protocol for short-term memory implementations
- **Key Responsibilities**:
  - Abstract memory operations (add_turn, get_llm_context, estimate_tokens, flush)
  - Enable pluggable memory implementations
  - Support health monitoring and state management

### 3. ShortTermMemoryConfig
- **Location**: `penguiflow/planner/memory.py`
- **Purpose**: Configuration for opt-in short-term memory with comprehensive settings
- **Key Responsibilities**:
  - Define memory strategy (truncation, rolling_summary, none)
  - Configure memory budgets and overflow policies
  - Set up memory isolation parameters
  - Configure summarization and retry settings
  - Define token estimation and callback functions

### 4. DefaultShortTermMemory Implementation
- **Location**: `penguiflow/planner/memory.py`
- **Purpose**: In-memory short-term memory with optional background summarization
- **Key Responsibilities**:
  - Implement the ShortTermMemory protocol
  - Manage conversation turns and summaries
  - Handle background summarization tasks
  - Enforce memory budgets and overflow policies
  - Manage health states and recovery mechanisms

### 5. Memory Integration Layer
- **Location**: `penguiflow/planner/memory_integration.py`
- **Purpose**: Integrate memory management with the planning process
- **Key Responsibilities**:
  - Resolve memory keys from tool context
  - Apply memory context to planning context
  - Build conversation turns from planning results
  - Record memory turns after planning completion
  - Handle memory hydration and persistence

### 6. ConversationTurn
- **Location**: `penguiflow/planner/memory.py`
- **Purpose**: Atomic user-assistant exchange stored in short-term memory
- **Key Responsibilities**:
  - Represent a single conversation turn with user message and assistant response
  - Store trajectory digest for context preservation
  - Track artifacts shown and hidden references
  - Maintain timestamp for temporal ordering

### 7. TrajectoryDigest
- **Location**: `penguiflow/planner/memory.py`
- **Purpose**: Compressed trajectory representation for memory persistence
- **Key Responsibilities**:
  - Store invoked tools and observation summaries
  - Preserve reasoning summaries when available
  - Track artifact references for context reconstruction

### 8. Memory Budget & Isolation
- **Location**: `penguiflow/planner/memory.py`
- **Purpose**: Configure memory limits and isolation policies
- **Key Responsibilities**:
  - Define token budgets for memory components
  - Set overflow policies (truncate_summary, truncate_oldest, error)
  - Configure isolation parameters for multi-tenant safety
  - Manage recovery and retry mechanisms

## Memory Strategies

### 1. Truncation Strategy
- **Purpose**: Keep only the most recent turns up to the full zone limit
- **Mechanism**: Automatically truncate memory to `full_zone_turns` limit
- **Use Cases**: Simple conversation history with predictable token usage
- **Benefits**: Predictable performance and memory usage
- **Trade-offs**: Loss of historical context beyond the limit

### 2. Rolling Summary Strategy
- **Purpose**: Maintain recent turns plus a rolling summary of older turns
- **Mechanism**: Evict older turns to pending buffer and summarize in background
- **Use Cases**: Long-running conversations requiring historical context
- **Benefits**: Preserves historical context while managing token usage
- **Trade-offs**: Requires background summarization and more complex management

### 3. None Strategy
- **Purpose**: Disable memory functionality entirely
- **Mechanism**: No memory operations performed
- **Use Cases**: Stateless operations or when memory is not needed
- **Benefits**: Minimal overhead and complexity
- **Trade-offs**: No conversation continuity

## Memory Isolation & Security

### 1. Explicit Key Requirement
- **Requirement**: Memory keys must be explicitly provided or derivable from context
- **Safety**: Prevents context leakage between different tenants/users/sessions
- **Configuration**: Controlled via `require_explicit_key` in isolation settings
- **Fallback**: Ephemeral keys generated when explicit keys are not available

### 2. Multi-Tenant Isolation
- **Tenant Key**: Isolate by tenant identifier (default: "tenant_id")
- **User Key**: Isolate by user identifier (default: "user_id")
- **Session Key**: Isolate by session identifier (default: "session_id")
- **Composite Key**: Combined key prevents cross-contamination

### 3. Context Extraction
- **Path Resolution**: Extract memory keys from tool context using configurable paths
- **Validation**: Validate extracted values before creating keys
- **Default Values**: Use default values when context keys are missing
- **Security**: Prevent unauthorized access to other sessions' memory

## Memory Management Features

### 1. Budget Management
- **Full Zone Turns**: Number of recent turns to keep in full detail (default: 5)
- **Summary Budget**: Maximum tokens for summary component (default: 1000)
- **Total Budget**: Maximum total tokens for memory context (default: 10000)
- **Overflow Policy**: Strategy for handling budget exceedances (truncate_summary, truncate_oldest, error)

### 2. Health Monitoring & Recovery
- **Health States**: Healthy, Retry, Degraded, Recovering states for summarization
- **Automatic Recovery**: Background recovery when summarization is degraded
- **Retry Mechanisms**: Exponential backoff for failed summarization attempts
- **Degraded Mode**: Fallback to recent turns only when summarization fails

### 3. Token Estimation
- **Conservative Estimation**: Character-based token estimation (len(text)//4 + 1)
- **Configurable Estimator**: Custom token estimation functions
- **Budget Enforcement**: Real-time budget checking and enforcement
- **Context Optimization**: Efficient context window management

### 4. Background Summarization
- **Asynchronous Operation**: Summarization runs in background tasks
- **Pending Buffer**: Evict older turns to pending buffer for summarization
- **Health-Aware**: Adjust behavior based on summarization health state
- **Retry Logic**: Automatic retry with exponential backoff for failed summarization

### 5. Persistence & Serialization
- **State Persistence**: Save and restore memory state to external stores
- **JSON Serialization**: Serialize memory state to JSON-friendly dictionaries
- **Hydration Support**: Load memory state from external stores
- **Artifact Access**: Optional access to hidden artifacts by reference

## Data Structures

### 1. ConversationTurn
```python
@dataclass(slots=True)
class ConversationTurn:
    user_message: str
    assistant_response: str
    trajectory_digest: TrajectoryDigest | None = None
    artifacts_shown: dict[str, Any] = field(default_factory=dict)
    artifacts_hidden_refs: list[str] = field(default_factory=list)
    ts: float = 0.0
```

### 2. TrajectoryDigest
```python
@dataclass(slots=True)
class TrajectoryDigest:
    tools_invoked: list[str]
    observations_summary: str
    reasoning_summary: str | None = None
    artifacts_refs: list[str] = field(default_factory=list)
```

### 3. MemoryBudget
```python
@dataclass(slots=True)
class MemoryBudget:
    full_zone_turns: int = 5
    summary_max_tokens: int = 1000
    total_max_tokens: int = 10000
    overflow_policy: Literal["truncate_summary", "truncate_oldest", "error"] = "truncate_oldest"
```

### 4. MemoryIsolation
```python
@dataclass(slots=True)
class MemoryIsolation:
    tenant_key: str = "tenant_id"
    user_key: str = "user_id"
    session_key: str = "session_id"
    require_explicit_key: bool = True
```

### 5. MemoryKey
```python
@dataclass(slots=True)
class MemoryKey:
    tenant_id: str
    user_id: str
    session_id: str
    
    def composite(self) -> str:
        return f"{self.tenant_id}:{self.user_id}:{self.session_id}"
```

## Memory Operations

### 1. Adding Conversation Turns
- **Method**: `add_turn()` in DefaultShortTermMemory
- **Process**: Add completed user-assistant exchanges to memory
- **Strategy Handling**: Different behavior based on memory strategy
- **Budget Enforcement**: Automatically enforce memory budgets after adding

### 2. Retrieving LLM Context
- **Method**: `get_llm_context()` in DefaultShortTermMemory
- **Output**: JSON-serializable patch to merge into `llm_context`
- **Content**: Recent turns, summary (for rolling_summary), pending turns
- **Format**: Structured format for LLM consumption

### 3. Token Estimation
- **Method**: `estimate_tokens()` in DefaultShortTermMemory
- **Algorithm**: Uses configured estimator or default character-based estimation
- **Purpose**: Track memory usage against budgets
- **Frequency**: Called during budget enforcement

### 4. Flushing Operations
- **Method**: `flush()` in DefaultShortTermMemory
- **Purpose**: Wait for in-flight summarization to finish
- **Scope**: Best-effort for rolling strategies when summarizer is not degraded
- **Cancellation**: Handles cancellation of ongoing summarization

## Integration with Planning Process

### 1. Memory Key Resolution
- **Explicit Keys**: Use memory keys provided explicitly to planner
- **Context Extraction**: Extract keys from tool context using configured paths
- **Ephemeral Keys**: Generate temporary keys when explicit keys are not available
- **Singleton Memory**: Use shared memory instance when configured

### 2. Context Application
- **Pre-Planning**: Apply memory context to LLM context before planning
- **Hydration**: Hydrate memory state from persistence store
- **Merging**: Merge memory context with existing LLM context
- **Serialization**: Ensure context remains JSON serializable

### 3. Turn Recording
- **Post-Planning**: Record completed planning results as conversation turns
- **Trajectory Digest**: Include trajectory information in memory
- **Artifact Tracking**: Track artifacts shown and hidden during planning
- **Persistence**: Persist memory state after recording turns

### 4. Memory Lifecycle
- **Initialization**: Set up memory based on configuration
- **Operation**: Add turns and manage context during planning
- **Cleanup**: Flush and persist memory at session end
- **Recovery**: Hydrate memory for resumed sessions

## Error Handling & Recovery

### 1. Budget Exceedances
- **Detection**: Real-time detection of token budget exceedances
- **Overflow Policies**: Different strategies for handling budget violations
- **Truncation**: Automatic truncation of memory to fit within budgets
- **Error Reporting**: Raise MemoryBudgetExceeded when policy is "error"

### 2. Summarization Failures
- **Health Transitions**: Move to RETRY or DEGRADED states on failures
- **Backlog Management**: Store failed items for later recovery
- **Retry Logic**: Exponential backoff for failed summarization attempts
- **Fallback Strategies**: Maintain recent turns when summarization fails

### 3. Context Resolution Errors
- **Missing Keys**: Handle missing memory keys appropriately
- **Invalid Keys**: Handle invalid memory keys with fallback mechanisms
- **Isolation Failures**: Prevent context leakage between sessions
- **Serialization Errors**: Handle serialization failures gracefully

### 4. Recovery Mechanisms
- **Exponential Backoff**: Retry failed operations with increasing delays
- **Health Monitoring**: Monitor and adjust behavior based on health states
- **State Persistence**: Maintain state across failures and restarts
- **Error Reporting**: Provide detailed error information for debugging

## Performance Considerations

### 1. Memory Efficiency
- **Token Estimation**: Efficient token counting for budget enforcement
- **Compression**: Compress memory content when approaching limits
- **Caching**: Cache processed memory contexts
- **Indexing**: Index memory for fast retrieval

### 2. Summarization Performance
- **Background Processing**: Run summarization in background tasks
- **Batch Processing**: Batch summarization operations when possible
- **Caching**: Cache summaries when appropriate
- **Optimization**: Optimize for quality and speed balance

### 3. Context Application
- **Fast Application**: Apply memory context quickly to planning
- **Selective Application**: Apply only relevant context portions
- **Memory Management**: Manage memory usage during application
- **Optimization**: Optimize context application performance

### 4. Concurrency Management
- **Thread Safety**: Use locks for thread-safe memory operations
- **Async Operations**: Perform I/O operations asynchronously
- **Task Management**: Manage background summarization tasks
- **Resource Limits**: Set appropriate resource limits

## Security Considerations

### 1. Access Control
- **Key Authorization**: Authorize access to memory keys
- **Isolation**: Maintain proper memory isolation
- **Permissions**: Control memory access permissions
- **Authentication**: Authenticate memory access

### 2. Data Privacy
- **Sensitive Data**: Protect sensitive conversation data
- **Encryption**: Encrypt memory data in transit and at rest
- **Anonymization**: Anonymize data when appropriate
- **Compliance**: Ensure compliance with privacy regulations

### 3. Resource Protection
- **Quota Management**: Enforce memory quotas
- **Denial of Service**: Prevent memory-based DoS attacks
- **Resource Limits**: Set appropriate resource limits
- **Monitoring**: Monitor memory usage patterns

### 4. Context Isolation
- **Multi-tenant Safety**: Prevent cross-tenant context leakage
- **Session Isolation**: Maintain session-level isolation
- **User Isolation**: Maintain user-level isolation
- **Data Segregation**: Segregate data appropriately

## Extension Points

### 1. Custom Memory Implementations
- **Memory Protocol**: Implement custom ShortTermMemory implementations
- **Storage Backends**: Implement custom storage backends
- **Caching Layers**: Add custom caching layers
- **Compression**: Implement custom compression algorithms

### 2. Memory Policies
- **Isolation Strategies**: Implement custom isolation strategies
- **Budget Policies**: Define custom budget policies
- **Retention Policies**: Implement custom retention policies
- **Security Policies**: Add custom security policies

### 3. Summarization
- **Summarization Algorithms**: Implement custom summarization algorithms
- **Quality Metrics**: Add custom quality metrics
- **Optimization**: Optimize for specific domains
- **Evaluation**: Add evaluation capabilities

### 4. Token Estimation
- **Custom Estimators**: Implement custom token estimation functions
- **Model-Specific**: Implement model-specific estimators
- **Accuracy Improvements**: Improve estimation accuracy
- **Performance**: Optimize estimation performance

### 5. Persistence
- **Storage Adapters**: Implement custom storage adapters
- **Serialization Formats**: Support different serialization formats
- **Sync/Async**: Support synchronous and asynchronous persistence
- **Replication**: Add replication capabilities

## Configuration Options

### 1. Memory Strategy Configuration
- `strategy`: Memory strategy ("truncation", "rolling_summary", "none")
- `include_trajectory_digest`: Include trajectory digest in memory (default: True)
- `summarizer_model`: Model identifier for summarization (optional)

### 2. Budget Management
- `full_zone_turns`: Number of recent turns to keep in full detail (default: 5)
- `summary_max_tokens`: Maximum tokens for summary component (default: 1000)
- `total_max_tokens`: Maximum total tokens for memory context (default: 10000)
- `overflow_policy`: Overflow policy ("truncate_summary", "truncate_oldest", "error")

### 3. Isolation Settings
- `tenant_key`: Path to extract tenant ID from tool context (default: "tenant_id")
- `user_key`: Path to extract user ID from tool context (default: "user_id")
- `session_key`: Path to extract session ID from tool context (default: "session_id")
- `require_explicit_key`: Require explicit memory keys (default: True)

### 4. Summarization Configuration
- `recovery_backlog_limit`: Maximum items in recovery backlog (default: 20)
- `retry_attempts`: Maximum retry attempts for summarization (default: 3)
- `retry_backoff_base_s`: Base backoff time in seconds (default: 2.0)
- `degraded_retry_interval_s`: Retry interval when degraded (default: 30.0)

### 5. Token Estimation
- `token_estimator`: Custom token estimation function (optional)
- `default_token_estimator`: Conservative character-based estimation (len(text)//4 + 1)

### 6. Callback Functions
- `on_turn_added`: Callback when turns are added (optional)
- `on_summary_updated`: Callback when summaries are updated (optional)
- `on_health_changed`: Callback when health states change (optional)

## Advanced Capabilities

### 1. Rolling Summary Strategy
- **Background Summarization**: Summarize older turns in background tasks
- **Health-Aware Operation**: Adjust behavior based on summarization health
- **Recovery Mechanisms**: Automatic recovery when summarization fails
- **Token Efficiency**: Optimize token usage with summaries

### 2. Trajectory Digest Integration
- **Tool Invocation Tracking**: Track tools invoked during planning
- **Observation Summaries**: Summarize tool observations for context
- **Reasoning Preservation**: Preserve reasoning summaries when available
- **Artifact References**: Track artifact references for context reconstruction

### 3. Context Isolation
- **Explicit Key Requirement**: Require explicit keys for multi-tenant safety
- **Context Path Extraction**: Extract keys from configurable context paths
- **Ephemeral Key Generation**: Generate temporary keys when needed
- **Tenant/User/Session Isolation**: Comprehensive isolation between entities

### 4. Budget Management
- **Token Estimation**: Accurate token estimation for budget enforcement
- **Overflow Policies**: Flexible policies for handling budget exceedances
- **Dynamic Adjustment**: Adjust budgets based on usage patterns
- **Monitoring**: Monitor and alert on budget usage

## Memory Integration Flow

### 1. Memory Initialization Flow
```
Query Input
    ↓
Memory Key Resolution (_resolve_memory_key)
    ↓
Memory Retrieval (_get_memory_for_key)
    ↓
Memory Hydration (_maybe_memory_hydrate)
    ↓
Context Application (_apply_memory_context)
    ↓
Planning Execution
```

### 2. Conversation Turn Flow
```
Planning Step Completion
    ↓
Turn Creation (_build_memory_turn)
    ↓
Memory Update (add_turn)
    ↓
Budget Check (_enforce_budget_locked)
    ↓
Summarization Check (_maybe_schedule_summarize_locked)
    ↓
Persistence (_maybe_memory_persist)
```

### 3. Summarization Flow
```
Turns Exceed Full Zone Limit
    ↓
Evict to Pending Buffer (_evict_to_pending_locked)
    ↓
Schedule Summarization (_maybe_schedule_summarize_locked)
    ↓
Background Summarization Task
    ↓
Generate Summary via LLM
    ↓
Update Memory State
    ↓
Apply Budget Enforcement
```

### 4. Memory Context Flow
```
Planning Context Request
    ↓
Memory State Check
    ↓
Recent Turns Collection
    ↓
Summary Inclusion (if available)
    ↓
Pending Turns Inclusion (if healthy)
    ↓
Trajectory Digest Inclusion (if configured)
    ↓
LLM Context Patch Generation
    ↓
Context Injection into Planning
```

### 5. Health Recovery Flow
```
Summarization Failure
    ↓
Health State Transition to DEGRADED
    ↓
Store Failed Turns in Backlog
    ↓
Wait for Recovery Interval
    ↓
Schedule Recovery Task
    ↓
Process Backlog Items
    ↓
Transition to RECOVERING State
    ↓
Successful Recovery → HEALTHY State
```

## Integration Points

### 1. With ReactPlanner
- **Context Application**: Apply memory to planning context before execution
- **Turn Recording**: Record planning results as conversation turns after completion
- **Key Resolution**: Resolve memory keys from tool context or explicit parameters
- **Budget Management**: Track memory usage against planner budgets

### 2. With Session Management
- **Session Isolation**: Maintain memory separation between sessions
- **Context Sharing**: Share memory across tasks within session
- **State Persistence**: Persist memory with session state

### 3. With Tool System
- **Context Propagation**: Pass memory context to tools
- **State Updates**: Update memory based on tool results
- **Isolation**: Maintain memory isolation between tools

### 4. With LLM System
- **Context Injection**: Inject memory context into LLM prompts
- **Summarization**: Use LLM for background summarization
- **Token Estimation**: Estimate token usage for memory content

### 5. With External Systems
- **Memory APIs**: Expose memory management APIs
- **Data Import**: Import external conversation data
- **Export Capabilities**: Export memory data
- **Persistence Stores**: Connect to external persistence systems

## Best Practices

### 1. Memory Design
- Use appropriate isolation strategies for multi-tenant deployments
- Set realistic memory budgets based on use case requirements
- Implement proper error handling for memory operations
- Follow consistent naming conventions for memory keys

### 2. Performance
- Optimize memory access patterns for performance
- Use efficient data structures for memory storage
- Implement appropriate caching strategies
- Monitor memory usage patterns regularly

### 3. Security
- Implement proper access controls for memory operations
- Protect sensitive conversation data
- Follow privacy regulations for data handling
- Monitor for security issues in memory usage

### 4. Reliability
- Implement robust error handling and recovery mechanisms
- Ensure data persistence for important memory content
- Handle edge cases properly in memory operations
- Test thoroughly with various memory configurations

### 5. Configuration
- Set appropriate memory budgets based on expected usage
- Configure isolation parameters for multi-tenant safety
- Set reasonable retry and timeout values
- Monitor and adjust configuration based on usage patterns

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           MEMORY MANAGEMENT SYSTEM                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                        MEMORY INTEGRATION LAYER                       │   │
│  │  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐         │   │
│  │  │ KEY RESOLUTION  │ │ CONTEXT         │ │ TURN            │         │   │
│  │  │ (_resolve_key)  │ │ APPLICATION     │ │ RECORDING       │         │   │
│  │  │                 │ │ (_apply_context)│ │ (_record_turn)  │         │   │
│  │  │ - Explicit keys │ │ - Memory        │ │ - Post-planning │         │   │
│  │  │ - Context       │ │   context       │ │ - Trajectory    │         │   │
│  │  │   extraction    │ │   injection     │ │   digest        │         │   │
│  │  │ - Ephemeral     │ │ - Serialization │ │ - Artifact      │         │   │
│  │  │   generation    │ │ - Validation    │ │   tracking      │         │   │
│  │  └─────────────────┘ └─────────────────┘ └─────────────────┘         │   │
│  │                                                                       │   │
│  │  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐         │   │
│  │  │ HYDRATION &     │ │ PERSISTENCE     │ │ CALLBACK        │         │   │
│  │  │ PERSISTENCE     │ │ MANAGEMENT      │ │ MANAGEMENT      │         │   │
│  │  │ (_hydrate)      │ │ (_persist)      │ │ (on_*)          │         │   │
│  │  │ - State loading │ │ - State saving  │ │ - Turn added    │         │   │
│  │  │ - Store         │ │ - Format        │ │ - Summary       │         │   │
│  │  │   integration   │ │   conversion    │ │   updated       │         │   │
│  │  │ - Validation    │ │ - Error         │ │ - Health        │         │   │
│  │  │                 │ │   handling      │ │   changed       │         │   │
│  │  └─────────────────┘ └─────────────────┘ └─────────────────┘         │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                        MEMORY IMPLEMENTATION                          │   │
│  │  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐         │   │
│  │  │ DEFAULT MEMORY  │ │ MEMORY          │ │ MEMORY          │         │   │
│  │  │ (DefaultSTM)    │ │ PROTOCOL        │ │ CONFIGURATION   │         │   │
│  │  │                 │ │ (STM)           │ │ (STMConfig)     │         │   │
│  │  │ - In-memory     │ │ - Interface     │ │ - Strategy      │         │   │
│  │  │ - Async         │ │   contract      │ │ - Budgets       │         │   │
│  │  │   operations    │ │ - Extensible    │ │ - Isolation     │         │   │
│  │  │ - Lock-based    │ │ - Serialization │ │ - Summarization │         │   │
│  │  │   synchronization│ │ - Persistence   │ │ - Callbacks     │         │   │
│  │  └─────────────────┘ └─────────────────┘ └─────────────────┘         │   │
│  │                                                                       │   │
│  │  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐         │   │
│  │  │ MEMORY BUDGET   │ │ MEMORY ISOLATION│ │ MEMORY KEY      │         │   │
│  │  │ (MemoryBudget)  │ │ (MemoryIsolation│ │ (MemoryKey)     │         │   │
│  │  │ - Token limits  │ │ )               │ │ - Composite     │         │   │
│  │  │ - Overflow      │ │ - Tenant/user/  │ │   key gen       │         │   │
│  │  │   policies      │ │   session       │ │ - Multi-tenant  │         │   │
│  │  │ - Enforcement   │ │   isolation     │ │   safety        │         │   │
│  │  │                 │ │ - Explicit req. │ │ - Context       │         │   │
│  │  │                 │ │                 │ │   extraction    │         │   │
│  │  └─────────────────┘ └─────────────────┘ └─────────────────┘         │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                        DATA STRUCTURES                                │   │
│  │  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐         │   │
│  │  │ CONVERSATION    │ │ TRAJECTORY      │ │ TOKEN           │         │   │
│  │  │ TURN            │ │ DIGEST          │ │ ESTIMATION      │         │   │
│  │  │ (Conversation   │ │ (Trajectory     │ │ (token_estimator│         │   │
│  │  │ Turn)           │ │ Digest)         │ │ )               │         │   │
│  │  │ - User message  │ │ - Tools invoked │ │ - Character     │         │   │
│  │  │ - Assistant     │ │ - Observations  │ │   heuristic     │         │   │
│  │  │   response      │ │   summary       │ │ - Custom        │         │   │
│  │  │ - Trajectory    │ │ - Reasoning     │ │   functions     │         │   │
│  │  │   digest        │ │   summary       │ │ - Budget        │         │   │
│  │  │ - Artifacts     │ │ - Artifacts     │ │   tracking      │         │   │
│  │  │   tracking      │ │   refs          │ │                 │         │   │
│  │  └─────────────────┘ └─────────────────┘ └─────────────────┘         │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                        STRATEGY MANAGEMENT                            │   │
│  │  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐         │   │
│  │  │ TRUNCATION      │ │ ROLLING SUMMARY │ │ NONE STRATEGY   │         │   │
│  │  │ STRATEGY        │ │ STRATEGY        │ │ (disabled)      │         │   │
│  │  │ - Fixed limit   │ │ - Background    │ │ - No memory     │         │   │
│  │  │ - Recent turns  │ │   summarization │ │ - Zero overhead │         │   │
│  │  │ - Predictable   │ │ - Summary +     │ │ - Stateless     │         │   │
│  │  │   behavior      │ │   recent turns  │ │ - Simple ops    │         │   │
│  │  │ - Simple        │ │ - Health        │ │                 │         │   │
│  │  │   management    │ │   monitoring    │ │                 │         │   │
│  │  └─────────────────┘ └─────────────────┘ └─────────────────┘         │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                        HEALTH & RECOVERY                              │   │
│  │  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐         │   │
│  │  │ HEALTH STATES   │ │ SUMMARIZATION   │ │ RECOVERY        │         │   │
│  │  │ (MemoryHealth)  │ │ MANAGEMENT      │ │ MECHANISMS      │         │   │
│  │  │ - HEALTHY       │ │ - Background    │ │ - Exponential   │         │   │
│  │  │ - RETRY         │ │   tasks         │ │   backoff       │         │   │
│  │  │ - DEGRADED      │ │ - Pending       │ │ - Backlog       │         │   │
│  │  │ - RECOVERING    │ │   buffer        │ │   management    │         │   │
│  │  │ - State         │ │ - Error         │ │ - Fallback      │         │   │
│  │  │   transitions   │ │   handling      │ │   strategies    │         │   │
│  │  └─────────────────┘ └─────────────────┘ └─────────────────┘         │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Memory Strategy Flow Diagrams

### Truncation Strategy Flow
```
Start Planning
    ↓
Add Turn to Memory
    ↓
Check Full Zone Limit
    ↓
If Exceeds Limit → Remove Oldest Turns
    ↓
Enforce Token Budgets
    ↓
Continue Planning
```

### Rolling Summary Strategy Flow
```
Start Planning
    ↓
Add Turn to Memory
    ↓
Check Full Zone Limit
    ↓
If Exceeds Limit → Move to Pending Buffer
    ↓
Schedule Background Summarization
    ↓
Maintain Recent Turns + Summary
    ↓
Enforce Token Budgets
    ↓
Continue Planning
```

### Health State Transitions
```
    ┌─────────────┐
    │   HEALTHY   │
    └──────┬──────┘
           │ Normal operation
    ┌──────▼──────┐
    │    RETRY    │ ←───┐ Summarization
    └──────┬──────┘     │ failure (retryable)
           │ Backoff    │
    ┌──────▼──────┐     │
    │  DEGRADED   │ ◄───┼── Retry attempts
    └──────┬──────┘     │ exceeded
           │ Fallback   │
    ┌──────▼──────┐     │
    │ RECOVERING  │ ────┘
    └─────────────┘
           │
    ┌──────▼──────┐
    │   HEALTHY   │ ←─── Recovery successful
    └─────────────┘
```

## Memory Context Injection

### LLM Context Structure
```json
{
  "conversation_memory": {
    "recent_turns": [
      {
        "user": "User message",
        "assistant": "Assistant response",
        "trajectory_digest": {
          "tools_invoked": ["tool1", "tool2"],
          "observations_summary": "Summary of observations",
          "reasoning_summary": "Summary of reasoning",
          "artifacts_refs": ["ref1", "ref2"]
        }
      }
    ],
    "summary": "Rolling summary of older conversation turns",
    "pending_turns": [
      {
        "user": "User message",
        "assistant": "Assistant response"
      }
    ]
  }
}
```

This comprehensive Memory Management subsystem provides essential capabilities for the Penguiflow system, enabling conversation continuity and proper isolation between different planning sessions with sophisticated budget management, health monitoring, and recovery mechanisms. The system is designed to be safe-by-default with explicit session keys for multi-tenant deployments, while providing flexible strategies for different use cases and performance requirements.