# ReactPlanner Core Architecture

## Overview

The ReactPlanner is the central orchestrator of the Penguiflow system, implementing the ReAct (Reasoning + Acting) paradigm for autonomous multi-step workflows. It manages the core planning loop where an LLM selects and sequences PenguiFlow nodes/tools based on structured JSON contracts. The planner supports pause/resume for approvals, adaptive re-planning on failures, parallel execution, and trajectory compression for long-running sessions.

## Core Components

### 1. ReactPlanner Class
- **Location**: `penguiflow/planner/react.py`
- **Purpose**: Central orchestrator for the ReAct planning loop
- **Key Responsibilities**:
  - Execute the ReAct (Reasoning + Acting) loop for planning tasks
  - Manage trajectory and state across planning steps
  - Handle tool selection and execution orchestration
  - Coordinate with memory, constraints, and validation systems
  - Support pause/resume functionality for human approvals
  - Handle parallel execution and background task spawning
  - Manage reflection and critique capabilities

### 2. Trajectory Management
- **Location**: `penguiflow/planner/trajectory.py`
- **Purpose**: Track the complete planning history and state
- **Key Responsibilities**:
  - Maintain step-by-step execution history
  - Track planning context and state
  - Handle trajectory serialization and compression
  - Support trajectory summarization for long-running sessions
  - Provide history for context window management

### 3. PlannerAction & Models
- **Location**: `penguiflow/planner/models.py`
- **Purpose**: Define the structured action format for LLM interactions
- **Key Responsibilities**:
  - Define the JSON schema for LLM responses
  - Handle action validation and normalization
  - Support different action types (tool calls, parallel execution, final response)
  - Enable structured data exchange with LLMs

### 4. LLM Integration Layer
- **Location**: `penguiflow/planner/llm.py` and `penguiflow/planner/react_step.py`
- **Purpose**: Interface with LLM providers for planning decisions
- **Key Responsibilities**:
  - Generate structured prompts for LLMs
  - Parse and validate LLM responses
  - Handle JSON schema enforcement
  - Manage streaming responses and callbacks
  - Support different LLM providers via LiteLLM

### 5. Tool Execution Engine
- **Location**: `penguiflow/planner/tool_calls.py`
- **Purpose**: Execute tools/nodes with proper validation and error handling
- **Key Responsibilities**:
  - Validate tool arguments against schemas
  - Execute tool functions with proper context
  - Handle tool results and errors
  - Emit execution telemetry
  - Support streaming tool responses

### 6. Memory Integration
- **Location**: `penguiflow/planner/memory_integration.py`
- **Purpose**: Integrate short-term memory with planning process
- **Key Responsibilities**:
  - Apply memory context to planning context
  - Record conversation turns after planning completion
  - Resolve memory keys from tool context
  - Handle memory hydration and persistence

### 7. Constraint & Budget Management
- **Location**: `penguiflow/planner/constraints.py`
- **Purpose**: Track and enforce planning constraints and budgets
- **Key Responsibilities**:
  - Track hop budgets (tool invocation limits)
  - Monitor deadline constraints
  - Track cost accumulation
  - Enforce resource limits
  - Handle budget violation responses

### 8. Error Recovery System
- **Location**: `penguiflow/planner/error_recovery.py`
- **Purpose**: Handle and recover from planning errors
- **Key Responsibilities**:
  - Detect validation and execution errors
  - Attempt automatic repairs for malformed responses
  - Handle argument filling for missing fields
  - Manage graceful failure scenarios
  - Support reflection-based error correction

### 9. Parallel Execution Engine
- **Location**: `penguiflow/planner/parallel.py`
- **Purpose**: Execute multiple tools in parallel with join strategies
- **Key Responsibilities**:
  - Execute parallel tool plans
  - Manage concurrent tool execution
  - Handle result aggregation and joining
  - Support different join strategies (append, replace, human-gated)
  - Enforce parallel execution limits

### 10. Pause Management
- **Location**: `penguiflow/planner/pause_management.py`
- **Purpose**: Handle planner pauses for human intervention
- **Key Responsibilities**:
  - Create pause records with context
  - Generate resume tokens
  - Handle pause serialization and persistence
  - Support different pause reasons and payloads
  - Manage pause/resume lifecycle

### 11. Background Task Orchestration
- **Location**: `penguiflow/planner/react.py` and `penguiflow/sessions/task_service.py`
- **Purpose**: Spawn and manage background tasks (subagents or tool jobs)
- **Key Responsibilities**:
  - Fork planner instances for background execution
  - Manage background task lifecycles
  - Handle result merging strategies (human-gated, append, replace)
  - Support task grouping and coordination
  - Handle cancellation propagation

### 12. Reflection & Critique System
- **Location**: `penguiflow/planner/llm.py` and `penguiflow/planner/prompts.py`
- **Purpose**: Self-evaluate and improve planning quality
- **Key Responsibilities**:
  - Critique answers before finalization
  - Request revisions based on feedback
  - Generate clarifications for unclear queries
  - Support multi-round quality improvement
  - Track reflection metrics and history

## Key Features

### 1. ReAct Loop Implementation
- **Reasoning + Acting**: Alternates between reasoning (thought) and acting (tool calls)
- **Structured JSON**: Uses structured JSON contracts for reliable LLM interactions
- **Validation**: Strong validation of LLM responses against expected schemas
- **Iteration Control**: Configurable maximum iterations to prevent infinite loops
- **Adaptive Behavior**: Adjusts behavior based on historical performance

### 2. Memory Integration
- **Short-term Memory**: Maintain conversation history and context
- **Memory Strategies**: Support for truncation, rolling summaries, or no memory
- **Isolation**: Multi-tenant safe memory isolation with explicit keys
- **Budget Management**: Token-based memory budgets with overflow policies
- **Context Injection**: Inject memory context into LLM prompts

### 3. Tool Policy Enforcement
- **Whitelist/Blacklist**: Filter available tools based on policy
- **Tag-based Access**: Control access using tool tags
- **Runtime Filtering**: Dynamic tool availability based on context
- **Safety Controls**: Prevent unsafe tool combinations
- **Multi-tenant Isolation**: Tenant-specific tool availability

### 4. Parallel Execution
- **Concurrent Tool Calls**: Execute multiple tools simultaneously
- **Join Strategies**: Different strategies for aggregating parallel results
- **Resource Limits**: Concurrency limits to prevent resource exhaustion
- **Dependency Management**: Handle tool dependencies in parallel execution
- **Error Handling**: Isolate failures in parallel execution branches

### 5. Error Handling & Recovery
- **Validation Repair**: Attempt to repair malformed LLM responses
- **Argument Filling**: Fill missing arguments with additional LLM calls
- **Graceful Degradation**: Continue operation when possible despite failures
- **Budget Enforcement**: Handle hop, deadline, and cost budget violations
- **Retry Mechanisms**: Automatic retries with exponential backoff

### 6. Pause & Resume
- **Human Intervention**: Pause for human approval or input
- **Context Preservation**: Maintain full context across pause/resume
- **Token-based Resumption**: Secure resume with unique tokens
- **State Persistence**: Persist pause state for durability
- **Steering Integration**: Support for external steering during paused state

### 7. Background Task Orchestration
- **Subagent Spawning**: Fork planner instances for background work
- **Tool Job Execution**: Execute single tools in background
- **Result Merging**: Different strategies for merging background results
- **Task Groups**: Coordinate multiple related background tasks
- **Cancellation Propagation**: Propagate cancellations to background tasks

### 8. Reflection & Quality Assurance
- **Self-Critique**: Automatically evaluate answer quality
- **Revision Requests**: Request improvements based on critique
- **Quality Thresholds**: Configurable quality standards
- **Multi-round Improvement**: Iterative answer refinement
- **Confidence Scoring**: Assess answer confidence levels

## Architecture Layers

### 1. Planning Interface Layer
- **ReactPlanner**: Public interface for planning operations
- **run() method**: Execute planning until completion or pause
- **resume() method**: Resume from paused state
- **pause() method**: Initiate pause for human intervention
- **fork() method**: Create planner instances for background tasks

### 2. Core Loop Layer
- **run_loop()**: Main planning execution loop
- **step()**: Execute single planning step
- **Action Processing**: Process LLM-generated actions
- **Constraint Checking**: Validate against planning constraints
- **Budget Management**: Track and enforce resource budgets

### 3. LLM Integration Layer
- **Message Building**: Construct LLM prompts with context
- **Response Parsing**: Parse and validate LLM responses
- **Schema Enforcement**: Ensure responses match expected schemas
- **Streaming Support**: Handle streaming LLM responses
- **Client Abstraction**: Abstract LLM provider differences

### 4. Tool Execution Layer
- **Tool Resolution**: Find and validate tools from catalog
- **Argument Validation**: Validate tool arguments against schemas
- **Execution Orchestration**: Execute tools with proper context
- **Result Processing**: Process and validate tool results
- **Error Handling**: Handle tool execution errors gracefully

### 5. Memory Management Layer
- **Context Application**: Apply memory context to planning
- **Turn Recording**: Record planning results as memory turns
- **Budget Enforcement**: Enforce memory token budgets
- **Summarization**: Background summarization of older turns
- **Persistence**: Save and restore memory state

### 6. Constraint Management Layer
- **Budget Tracking**: Track hop, time, and cost budgets
- **Violation Detection**: Detect budget and constraint violations
- **Policy Enforcement**: Enforce planning policies and constraints
- **Recovery Handling**: Handle constraint violation responses
- **Reporting**: Track constraint violation statistics

### 7. Parallel Execution Layer
- **Plan Execution**: Execute parallel tool plans
- **Concurrency Control**: Manage concurrent tool execution
- **Result Aggregation**: Collect and join parallel results
- **Dependency Resolution**: Handle tool dependencies
- **Error Isolation**: Isolate failures in parallel branches

### 8. Error Recovery Layer
- **Validation Repair**: Repair malformed responses
- **Argument Filling**: Fill missing arguments automatically
- **Fallback Strategies**: Alternative approaches when primary fails
- **Error Classification**: Categorize different error types
- **Recovery Policies**: Configurable recovery strategies

## Data Flow

### 1. Planning Loop Flow
```
Query Input
    ↓
Trajectory Initialization
    ↓
Context Assembly (memory, tools, constraints)
    ↓
LLM Prompt Construction
    ↓
LLM Request & Response
    ↓
Response Validation & Parsing
    ↓
Action Type Determination
    ↓
Action Execution (tool call, parallel, finish, etc.)
    ↓
Observation Processing
    ↓
Constraint Checking
    ↓
Iteration Decision (continue or finish)
    ↓
Loop Back or Return Result
```

### 2. Tool Execution Flow
```
PlannerAction with Tool Call
    ↓
Tool Resolution from Catalog
    ↓
Argument Validation against Schema
    ↓
Tool Context Preparation
    ↓
Tool Execution with Error Handling
    ↓
Result Validation
    ↓
Observation Formatting
    ↓
Trajectory Update
    ↓
LLM Context for Next Step
```

### 3. Parallel Execution Flow
```
PlannerAction with Parallel Plan
    ↓
Plan Validation & Tool Resolution
    ↓
Concurrent Tool Execution
    ↓
Result Collection with Semaphores
    ↓
Join Strategy Application
    ↓
Aggregated Result Formatting
    ↓
Trajectory Update
    ↓
LLM Context for Next Step
```

### 4. Background Task Flow
```
PlannerAction with task.subagent/task.tool
    ↓
Planner Forking with Context Snapshot
    ↓
Background Task Spawning
    ↓
Independent Execution
    ↓
Result Generation
    ↓
Merge Strategy Application
    ↓
Context Update in Main Planner
    ↓
Continued Planning
```

### 5. Memory Integration Flow
```
Planning Context Request
    ↓
Memory Key Resolution
    ↓
Memory Retrieval & Hydration
    ↓
Context Application to LLM
    ↓
Planning Execution
    ↓
Turn Recording after Completion
    ↓
Memory Persistence
    ↓
Continued Planning with Updated Context
```

### 6. Error Recovery Flow
```
LLM Response Received
    ↓
JSON Parsing Attempt
    ↓
Schema Validation
    ↓
Validation Failure Detected
    ↓
Error Classification
    ↓
Recovery Strategy Selection
    ↓
Repair Attempt (if applicable)
    ↓
Retry with Backoff (if applicable)
    ↓
Fallback Strategy (if needed)
    ↓
Continue Planning or Finish with Error
```

## Configuration Options

### 1. Core Planning Configuration
- `llm`: LLM identifier or configuration dictionary
- `max_iters`: Maximum planning iterations before termination (default: 8)
- `temperature`: LLM sampling temperature for diversity (default: 0.0)
- `json_schema_mode`: Enable strict JSON schema enforcement (default: True)
- `system_prompt_extra`: Additional system instructions and constraints

### 2. Memory Configuration
- `short_term_memory`: Memory strategy and configuration
- `token_budget`: Context window management and summarization trigger
- `memory_key`: Explicit memory key for isolation (optional)
- `memory_isolation`: Tenant/user/session isolation configuration

### 3. Constraint Management
- `deadline_s`: Wall-clock deadline for planning session
- `hop_budget`: Maximum tool invocations allowed
- `cost_budget`: Maximum cost allowed for planning
- `time_source`: Override time source for testing

### 4. Tool Policy Configuration
- `tool_policy`: Runtime filtering of available tools
- `planning_hints`: Structured constraints and preferences
- `max_concurrent_parallel`: Maximum parallel tool executions
- `absolute_max_parallel`: System-level parallel execution limit

### 5. Error Handling Configuration
- `repair_attempts`: Max attempts to repair invalid LLM responses (default: 3)
- `max_consecutive_arg_failures`: Threshold for graceful failure (default: 3)
- `arg_fill_enabled`: Enable automatic argument filling (default: True)
- `error_recovery_config`: Configuration for error recovery strategies

### 6. Background Task Configuration
- `background_tasks`: Configuration for background task spawning
- `default_merge_strategy`: Default strategy for merging background results
- `max_concurrent_tasks`: Maximum concurrent background tasks
- `task_timeout_s`: Timeout for background tasks

### 7. Reflection Configuration
- `reflection_config`: Quality assurance configuration
- `reflection_llm`: Separate LLM for critique (optional)
- `reflection_criteria`: Quality criteria for evaluation
- `quality_threshold`: Minimum quality score for acceptance

### 8. Pause & Resume Configuration
- `pause_enabled`: Enable pause/resume functionality (default: True)
- `state_store`: Persistence store for pause records
- `pause_token_ttl_s`: Resume token time-to-live
- `pause_reasons`: Allowed pause reasons and handling

## Integration Points

### 1. With Tool System
- **Catalog Integration**: Build tool catalogs from node specifications
- **Schema Generation**: Generate JSON schemas from Pydantic models
- **Execution Context**: Pass execution context to tools
- **Result Validation**: Validate tool outputs against schemas
- **Artifact Handling**: Manage tool-generated artifacts

### 2. With Memory System
- **Context Injection**: Inject memory context into LLM prompts
- **Turn Recording**: Record planning results as conversation turns
- **Key Resolution**: Resolve memory keys from tool context
- **State Persistence**: Persist memory state across sessions
- **Budget Management**: Track memory token usage

### 3. With LLM System
- **Prompt Generation**: Build structured prompts for LLMs
- **Response Processing**: Parse and validate LLM responses
- **Schema Enforcement**: Ensure responses match expected schemas
- **Streaming Support**: Handle streaming LLM responses
- **Cost Tracking**: Track LLM usage costs

### 4. With Session Management
- **Task Orchestration**: Coordinate with background task management
- **State Management**: Maintain planning state across steps
- **Event Broadcasting**: Emit planning events for observability
- **Resource Management**: Track resource usage and limits
- **Cancellation Propagation**: Handle session-wide cancellations

### 5. With External Systems
- **Steering Integration**: Accept external control signals
- **Monitoring**: Emit telemetry events for monitoring
- **API Integration**: Expose planning capabilities via APIs
- **Authentication**: Integrate with authentication systems
- **Billing**: Track usage for billing purposes

## Error Handling & Recovery

### 1. LLM Errors
- **API Failures**: Handle provider API errors with retries
- **Timeouts**: Handle request timeouts with exponential backoff
- **Rate Limiting**: Handle rate limiting with appropriate delays
- **Authentication**: Handle authentication failures gracefully
- **Context Length**: Detect and handle context length exceeded errors

### 2. Response Errors
- **Malformed JSON**: Handle invalid JSON responses with parsing strategies
- **Schema Violations**: Handle responses that don't match schemas
- **Validation Failures**: Handle argument validation failures
- **Parsing Errors**: Handle response parsing failures
- **Type Mismatches**: Handle type mismatches in responses

### 3. Tool Execution Errors
- **Validation Errors**: Handle tool argument validation failures
- **Execution Failures**: Handle tool runtime failures
- **Resource Errors**: Handle resource exhaustion during tool execution
- **Timeout Errors**: Handle tool execution timeouts
- **Dependency Errors**: Handle tool dependency failures

### 4. Recovery Strategies
- **Retry Logic**: Exponential backoff for transient failures
- **Response Repair**: Attempt to repair malformed responses
- **Argument Filling**: Use additional LLM calls to fill missing arguments
- **Fallback Actions**: Use alternative approaches when primary fails
- **Graceful Degradation**: Continue with reduced functionality

## Performance Considerations

### 1. Response Efficiency
- **Prompt Optimization**: Optimize prompts for token efficiency
- **Schema Caching**: Cache compiled JSON schemas
- **Validation Optimization**: Optimize validation for hot paths
- **Memory Management**: Efficient memory usage during planning

### 2. Concurrency Management
- **Parallel Execution**: Optimize parallel tool execution
- **Resource Limits**: Set appropriate concurrency limits
- **Backpressure**: Implement backpressure mechanisms
- **Task Scheduling**: Efficient task scheduling algorithms

### 3. Context Window Management
- **Token Estimation**: Efficient token counting for context windows
- **History Compression**: Compress history when approaching limits
- **Relevance Filtering**: Filter irrelevant context to reduce tokens
- **Summarization**: Background summarization for long sessions

### 4. Memory Efficiency
- **Efficient Serialization**: Optimize serialization of trajectory data
- **Caching**: Cache frequently accessed data
- **Indexing**: Index trajectory data for fast access
- **Compression**: Compress data when appropriate

## Security Considerations

### 1. Multi-tenant Isolation
- **Memory Keys**: Explicit memory keys prevent context leakage
- **Tool Policies**: Runtime tool filtering for safety
- **Context Validation**: Validate injected context for safety
- **Resource Limits**: Prevent resource exhaustion attacks

### 2. Input Validation
- **Prompt Injection**: Prevent prompt injection attacks
- **Context Sanitization**: Sanitize injected context
- **Schema Validation**: Validate all inputs against schemas
- **Output Sanitization**: Sanitize LLM outputs for safety

### 3. Access Control
- **Authentication**: Integrate with authentication systems
- **Authorization**: Control access to planning capabilities
- **Rate Limiting**: Implement rate limiting for API protection
- **Audit Logging**: Log all planning activities for auditing

### 4. Data Privacy
- **Context Redaction**: Redact sensitive information from context
- **Artifact Handling**: Secure handling of tool artifacts
- **Memory Isolation**: Prevent cross-session data leakage
- **Encryption**: Encrypt sensitive data in transit and at rest

## Extension Points

### 1. Custom LLM Clients
- **JSONLLMClient Protocol**: Implement custom LLM client protocols
- **Provider Integration**: Add support for new LLM providers
- **Response Processing**: Customize response parsing and validation
- **Streaming Handlers**: Add custom streaming response handlers

### 2. Memory Strategies
- **Custom Memory**: Implement custom memory strategies
- **Storage Backends**: Add support for different storage backends
- **Summarization Algorithms**: Implement custom summarization
- **Budget Policies**: Define custom budget enforcement policies

### 3. Tool Policies
- **Custom Filters**: Implement custom tool filtering policies
- **Authorization**: Add custom authorization mechanisms
- **Rate Limiting**: Implement tool-specific rate limiting
- **Safety Checks**: Add custom safety validation

### 4. Error Recovery
- **Custom Strategies**: Implement custom error recovery strategies
- **Repair Algorithms**: Add custom response repair algorithms
- **Fallback Mechanisms**: Implement custom fallback approaches
- **Monitoring**: Add custom monitoring and alerting

### 5. Planning Hints
- **Custom Constraints**: Implement domain-specific constraints
- **Preference Systems**: Add custom preference handling
- **Ordering Hints**: Implement custom execution ordering
- **Budget Policies**: Define custom budget management

## Best Practices

### 1. Planning Design
- Use clear, unambiguous queries for better LLM performance
- Provide sufficient context without exceeding token limits
- Implement proper error handling and recovery
- Follow consistent naming conventions for tools

### 2. Memory Management
- Use appropriate memory strategies for your use case
- Set realistic token budgets based on expected usage
- Implement proper memory key resolution for multi-tenant safety
- Monitor memory usage patterns regularly

### 3. Tool Design
- Define clear input/output schemas for tools
- Implement proper error handling in tools
- Document tool capabilities and limitations
- Follow consistent interface patterns

### 4. Performance
- Optimize prompts for token efficiency
- Use appropriate concurrency limits
- Implement efficient context window management
- Monitor and optimize planning performance

### 5. Security
- Implement proper multi-tenant isolation
- Validate all inputs and outputs
- Protect sensitive context information
- Monitor for security issues

## Advanced Capabilities

### 1. Multi-modal Support
- **Image Processing**: Handle image inputs in planning context
- **Content Parts**: Support for different content modalities
- **Context Injection**: Inject multi-modal context into prompts
- **Response Handling**: Process multi-modal LLM responses

### 2. Adaptive Planning
- **Dynamic Tool Selection**: Adjust available tools based on context
- **Context-Aware Reasoning**: Adapt reasoning based on conversation history
- **Learning from History**: Improve planning based on past performance
- **Feedback Integration**: Incorporate user feedback into planning

### 3. Distributed Execution
- **Background Tasks**: Execute long-running tasks in background
- **Task Coordination**: Coordinate distributed task execution
- **Result Aggregation**: Aggregate results from distributed execution
- **Fault Tolerance**: Handle failures in distributed execution

### 4. Real-time Steering
- **External Control**: Accept real-time control signals
- **Dynamic Repositioning**: Change planning direction mid-execution
- **Context Injection**: Inject new context during execution
- **Priority Adjustment**: Adjust task priorities dynamically

## Architecture Patterns

### 1. ReAct Pattern Implementation
- **Reasoning-Action Loop**: Alternating reasoning and action phases
- **Structured Communication**: JSON-based communication with LLMs
- **State Management**: Maintain state across loop iterations
- **Termination Conditions**: Clear conditions for loop termination

### 2. Event-Driven Architecture
- **Planning Events**: Emit events for each planning step
- **Observability**: Rich telemetry for monitoring and debugging
- **External Integration**: Enable external systems to react to events
- **Streaming Updates**: Real-time updates for UI and monitoring

### 3. Context Isolation
- **Explicit Keys**: Require explicit session keys for safety
- **Tenant Isolation**: Prevent cross-tenant context leakage
- **User Isolation**: Maintain user-level context separation
- **Session Boundaries**: Clear boundaries between planning sessions

### 4. Resource Management
- **Budget Enforcement**: Enforce resource budgets automatically
- **Backpressure**: Apply backpressure when resources are constrained
- **Efficiency Optimization**: Optimize resource usage patterns
- **Monitoring**: Track resource usage for optimization

## Implementation Details

### 1. Trajectory Structure
The trajectory maintains the complete history of the planning process:
- **Steps**: Sequential list of action-observation pairs
- **Metadata**: Additional information about the planning process
- **Context**: Current planning context and state
- **Artifacts**: Generated artifacts during planning

### 2. Action Processing Pipeline
Each LLM response goes through a validation and processing pipeline:
- **Schema Validation**: Validate against PlannerAction schema
- **Action Type Detection**: Determine if tool call, parallel, or finish
- **Tool Resolution**: Find the appropriate tool in the catalog
- **Argument Validation**: Validate arguments against tool schema
- **Execution**: Execute the tool with proper error handling

### 3. Memory Context Injection
Memory context is injected into LLM prompts to maintain continuity:
- **Recent Turns**: Include recent conversation history
- **Summaries**: Include rolling summaries of older turns
- **Artifacts**: Reference relevant artifacts from memory
- **State Preservation**: Maintain state across planning sessions

### 4. Constraint Tracking
The system tracks multiple types of constraints simultaneously:
- **Hop Budget**: Track tool invocation counts
- **Time Budget**: Monitor elapsed time against deadlines
- **Cost Budget**: Track accumulated costs
- **Resource Limits**: Monitor various resource constraints

## Testing & Validation

### 1. Unit Testing
- **Action Processing**: Test individual action types
- **Tool Execution**: Test tool execution with various inputs
- **Error Handling**: Test error scenarios and recovery
- **Memory Operations**: Test memory integration scenarios

### 2. Integration Testing
- **End-to-End Planning**: Test complete planning workflows
- **Background Tasks**: Test background task orchestration
- **Pause/Resume**: Test pause and resume functionality
- **Parallel Execution**: Test parallel tool execution

### 3. Performance Testing
- **Token Efficiency**: Measure token usage efficiency
- **Response Times**: Benchmark response times
- **Concurrency**: Test concurrent planning sessions
- **Memory Usage**: Monitor memory consumption patterns

### 4. Security Testing
- **Isolation**: Test multi-tenant isolation
- **Injection**: Test for prompt and context injection
- **Access Control**: Test tool and resource access controls
- **Privacy**: Test data privacy and redaction

## Monitoring & Observability

### 1. Planning Events
- **Step Events**: Track each planning step
- **Tool Events**: Monitor tool execution
- **LLM Events**: Track LLM interactions
- **Memory Events**: Monitor memory operations

### 2. Performance Metrics
- **Response Times**: Track planning response times
- **Token Usage**: Monitor token consumption
- **Cost Tracking**: Track planning costs
- **Resource Usage**: Monitor resource consumption

### 3. Error Monitoring
- **Failure Rates**: Track planning failure rates
- **Error Types**: Categorize different error types
- **Recovery Success**: Monitor recovery success rates
- **Budget Violations**: Track constraint violations

### 4. Business Metrics
- **Completion Rates**: Track planning completion rates
- **Quality Scores**: Monitor answer quality metrics
- **User Satisfaction**: Track user satisfaction indicators
- **Usage Patterns**: Analyze usage patterns and trends

## Diagrams

### ReactPlanner Architecture
```
┌─────────────────────────────────────────────────────────────────────────┐
│                        REACTPLANNER CORE                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    PLANNING INTERFACE                         │   │
│  │  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐   │   │
│  │  │     RUN()       │ │    RESUME()     │ │    PAUSE()      │   │   │
│  │  │   (public)      │ │   (public)      │ │   (public)      │   │   │
│  │  │ Execute planning│ │ Resume paused   │ │ Initiate pause  │   │   │
│  │  │ until completion│ │ planning session│ │ for human input │   │   │
│  │  └─────────────────┘ └─────────────────┘ └─────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    CORE PLANNING LOOP                         │   │
│  │  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐   │   │
│  │  │   TRAJECTORY    │ │   STEP()        │ │   RUN_LOOP()    │   │   │
│  │  │  MANAGEMENT     │ │ Execute single  │ │ Main planning   │   │   │
│  │  │ Maintain state  │ │ planning step   │ │ execution loop  │   │   │
│  │  │ & history       │ │ with LLM + tool │ │ with iteration  │   │   │
│  │  │                 │ │ execution       │ │ control         │   │   │
│  │  └─────────────────┘ └─────────────────┘ └─────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                   LLM INTEGRATION                           │   │
│  │  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐   │   │
│  │  │  PROMPT BUILD   │ │ RESPONSE        │ │  CLIENT         │   │   │
│  │  │   (build_msgs)  │ │   PARSING       │ │  ABSTRACTION    │   │   │
│  │  │ Build structured│ │ Parse & validate│ │ Abstract provider │   │   │
│  │  │ prompts for LLM │ │ LLM responses   │ │ differences     │   │   │
│  │  └─────────────────┘ └─────────────────┘ └─────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                  TOOL EXECUTION                             │   │
│  │  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐   │   │
│  │  │  CATALOG        │ │  VALIDATION     │ │  EXECUTION      │   │   │
│  │  │  MANAGEMENT     │ │  & ERROR        │ │  ENGINE         │   │   │
│  │  │ Build tool      │ │  HANDLING       │ │ Execute tools   │   │   │
│  │  │ catalogs from   │ │ Handle tool     │ │ with context &  │   │   │
│  │  │ nodes/specs     │ │ errors safely   │ │ proper result   │   │   │
│  │  └─────────────────┘ └─────────────────┘ │ processing      │   │   │
│  │                                         └─────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                 MEMORY INTEGRATION                            │   │
│  │  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐   │   │
│  │  │  KEY RESOLUTION │ │  CONTEXT        │ │  TURN           │   │   │
│  │  │  (resolve_key)  │ │  APPLICATION    │ │  RECORDING      │   │   │
│  │  │ Extract memory  │ │ Apply memory    │ │ Record planning │   │   │
│  │  │ keys from ctx   │ │ context to LLM  │ │ results as      │   │   │
│  │  │ for isolation   │ │ prompts         │ │ memory turns    │   │   │
│  │  └─────────────────┘ └─────────────────┘ └─────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                CONSTRAINT MANAGEMENT                          │   │
│  │  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐   │   │
│  │  │  BUDGET         │ │  POLICY         │ │  ENFORCEMENT    │   │   │
│  │  │  TRACKING       │ │  ENFORCEMENT    │ │  & VIOLATIONS   │   │   │
│  │  │ Track hop/time/ │ │ Enforce tool    │ │ Handle budget   │   │   │
│  │  │ cost budgets    │ │ policies &      │ │ violations &    │   │   │
│  │  │                 │ │ hints           │ │ recovery        │   │   │
│  │  └─────────────────┘ └─────────────────┘ └─────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                ERROR RECOVERY SYSTEM                          │   │
│  │  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐   │   │
│  │  │  VALIDATION     │ │  REPAIR         │ │  FALLBACK        │   │   │
│  │  │  REPAIR         │ │  ATTEMPTS       │ │  STRATEGIES      │   │   │
│  │  │ Attempt to fix  │ │ Multiple repair │ │ Alternative       │   │   │
│  │  │ malformed       │ │ attempts with   │ │ approaches when   │   │   │
│  │  │ responses       │ │ backoff         │ │ primary fails     │   │   │
│  │  └─────────────────┘ └─────────────────┘ └─────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Planning Loop Flow
```
    Start Planning
         │
         ▼
    Initialize Trajectory
         │
         ▼
    Build LLM Context (memory, tools, constraints)
         │
         ▼
    Generate Prompt for LLM
         │
         ▼
    Call LLM -> Receive Action JSON
         │
         ▼
    Validate Action Schema
         │
         ▼
    Parse Action Type (tool/parallel/final/pause)
         │
         ▼
    ┌─────────────────┐
    │   ACTION TYPE   │
    └─────────────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
TOOL CALL   PARALLEL
    │         │
    ▼         ▼
Execute   Execute Plan
Tool      Concurrently
    │         │
    ▼         ▼
Get      Collect
Result   Results
    │         │
    ▼         ▼
Update   Join Results
Trajectory      │
    │         ▼
    │    Update Trajectory
    │         │
    │    ┌────┴────┐
    │    │         │
    │    ▼         ▼
    │FINAL_RSP   CHECK
    │            BUDGETS
    │              │
    │              ▼
    │         Within Budget?
    │              │
    │         ┌────┴────┐
    │         │         │
    │         ▼         ▼
    │      Continue   Finish
    │      Planning    with
    │         │       Result
    │         ▼
    └───> Next Iteration
```

### Memory Integration Flow
```
Planning Context Request
         │
         ▼
Resolve Memory Key (explicit/context-derived)
         │
         ▼
Get Memory Instance (singleton or keyed)
         │
         ▼
Hydrate Memory State (from persistence)
         │
         ▼
Apply Memory Context to LLM Context
         │
         ▼
Include in Prompt (recent turns, summary, pending)
         │
         ▼
LLM Generates Response with Memory Context
         │
         ▼
Planning Continues with Memory-Aware Decisions
         │
         ▼
After Step: Record Turn in Memory
         │
         ▼
Enforce Memory Budgets & Policies
         │
         ▼
Persist Memory State (if configured)
```

### Background Task Flow
```
LLM Action: task.subagent/task.tool
         │
         ▼
Detect Background Task Request
         │
         ▼
Fork Current Planner State
         │
         ▼
Create New Planner Instance
         │
         ▼
Spawn Background Task
         │
         ▼
Execute Independently
         │
         ▼
Background Task Completes
         │
         ▼
Apply Result via Merge Strategy
         │
         ▼
(HUMAN_GATED/APPEND/REPLACE)
         │
         ▼
Update Main Planner Context
         │
         ▼
Continue Main Planning Loop
```

This comprehensive ReactPlanner Core architecture provides the essential orchestration layer for the Penguiflow system, enabling sophisticated autonomous planning with memory, constraints, error recovery, and background task capabilities. The system is designed to be extensible, secure, and performant while maintaining clear separation of concerns between different architectural layers.