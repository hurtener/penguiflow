# LLM Interface Architecture

## Overview

The LLM Interface subsystem provides a standardized abstraction layer for interacting with Large Language Models in the Penguiflow system. It handles prompt generation, response parsing, JSON schema enforcement, error classification, and integration with the planning process. The system supports both LiteLLM-based clients and native provider implementations through the NativeLLMAdapter, providing flexibility and performance optimization. The architecture is built around a typed request/response model that eliminates raw dict plumbing throughout the stack.

## Core Components

### 1. JSONLLMClient Protocol
- **Location**: `penguiflow/planner/models.py`
- **Purpose**: Defines the interface contract for LLM interactions
- **Key Responsibilities**:
  - Abstract LLM provider differences
  - Handle JSON schema enforcement
  - Manage response parsing and validation
  - Support streaming responses and callbacks

### 2. _LiteLLMJSONClient Implementation
- **Location**: `penguiflow/planner/llm.py`
- **Purpose**: Concrete implementation using LiteLLM for provider compatibility
- **Key Responsibilities**:
  - Execute LLM calls with proper error handling
  - Manage retries and timeouts
  - Handle streaming responses with callbacks
  - Support native reasoning capabilities for advanced models

### 3. NativeLLMAdapter
- **Location**: `penguiflow/llm/protocol.py`
- **Purpose**: Adapter that implements JSONLLMClient protocol using native LLM providers
- **Key Responsibilities**:
  - Bridge between native LLM layer and JSONLLMClient protocol
  - Provide backward compatibility with existing planner infrastructure
  - Enable direct provider integrations without LiteLLM overhead
  - Support advanced provider-specific features

### 4. Native Provider Layer
- **Location**: `penguiflow/llm/providers/`
- **Purpose**: Native provider implementations with direct API integrations
- **Key Responsibilities**:
  - Direct API integrations with LLM providers (OpenAI, Anthropic, Google, etc.)
  - Provider-specific optimizations and features
  - Native streaming and reasoning support
  - Cost calculation and usage tracking
  - Typed request/response normalization

### 5. Core Types System
- **Location**: `penguiflow/llm/types.py`
- **Purpose**: Defines the typed request/response model for all providers
- **Key Responsibilities**:
  - Eliminate raw dict plumbing throughout the stack
  - Provide normalized message format (LLMMessage)
  - Define structured content parts (TextPart, ToolCallPart, etc.)
  - Specify tool and structured output schemas
  - Handle usage and cost tracking

### 6. Provider Factory
- **Location**: `penguiflow/llm/providers/__init__.py`
- **Purpose**: Creates appropriate provider instances based on model identifiers
- **Key Responsibilities**:
  - Route model identifiers to appropriate providers
  - Handle provider-specific configuration
  - Support lazy loading of provider SDKs
  - Manage provider-specific authentication

### 7. Prompt Management System
- **Location**: `penguiflow/planner/prompts.py` and `penguiflow/planner/llm.py`
- **Purpose**: Generate and manage prompts for the planning process
- **Key Responsibilities**:
  - Build structured prompts for LLMs with context injection
  - Format tool specifications and action schemas
  - Handle system and user messages
  - Support for different prompt templates and dynamic content
  - Adaptive guidance injection based on historical performance

### 8. Response Processing Engine
- **Location**: `penguiflow/planner/llm.py`
- **Purpose**: Parse, validate, and process LLM responses
- **Key Responsibilities**:
  - Parse JSON responses with fallback mechanisms
  - Validate against Pydantic models and JSON schemas
  - Handle malformed responses and recovery
  - Extract structured data and action plans
  - Manage artifact redaction for context safety

### 9. LLM Integration Layer
- **Location**: `penguiflow/planner/react_step.py` and `penguiflow/planner/llm.py`
- **Purpose**: Integrate LLM calls with planning process
- **Key Responsibilities**:
  - Execute LLM calls in planning context
  - Handle streaming responses and real-time updates
  - Manage context windows and token budgets
  - Process LLM outputs into planner actions

### 10. Model Profiles & Schema Transformers
- **Location**: `penguiflow/llm/profiles/` and `penguiflow/llm/schema/`
- **Purpose**: Provider-specific capabilities and schema transformations
- **Key Responsibilities**:
  - Capture provider-specific capabilities and quirks
  - Transform schemas for provider compatibility
  - Handle provider-specific output modes (native, tools, prompted)
  - Manage schema sanitization for different providers

## Key Features

### 1. JSON Schema Enforcement & Compatibility
- **Strict Mode**: Enforce strict JSON schema compliance for providers like OpenAI
- **Flexible Mode**: Adapt schema requirements based on provider capabilities
- **Schema Sanitization**: Remove advanced constraints for broader provider compatibility
- **Automatic Generation**: Generate schemas from Pydantic models with proper sanitization
- **Conditional Validation**: Support for conditional schema requirements
- **Provider-Specific Transformers**: Transform schemas for provider-specific requirements (Bedrock, Databricks, etc.)

### 2. Advanced Prompt Engineering
- **Dynamic Context Injection**: Inject relevant context based on planning state
- **Adaptive Guidance**: Dynamically inject guidance based on historical performance
- **Multi-turn Support**: Handle complex multi-turn conversations
- **Template System**: Flexible prompt templates with variable injection
- **System Prompt Enhancement**: Add contextual instructions and constraints

### 3. Sophisticated Response Processing
- **Robust JSON Parsing**: Handle various response formats and edge cases
- **Fenced Code Block Extraction**: Extract JSON from ```json ``` blocks
- **Fallback Parsing**: Multiple strategies for extracting structured data
- **Schema Validation**: Validate responses against expected schemas
- **Error Recovery**: Attempt repair of malformed responses

### 4. Streaming & Real-time Support
- **Chunk Processing**: Process streaming response chunks in real-time
- **Real-time Updates**: Provide real-time updates to UI and monitoring
- **Progress Tracking**: Track response progress and intermediate results
- **Reasoning Content**: Handle native reasoning capabilities in advanced models
- **Cancellation Support**: Support for interrupting streaming responses

### 5. Error Classification & Recovery
- **LLMErrorType**: Comprehensive error classification system
- **Context Length Detection**: Identify and handle context length exceeded errors
- **Rate Limit Handling**: Manage rate limiting and backoff strategies
- **Service Availability**: Handle service unavailability and timeouts
- **Adaptive Recovery**: Different recovery strategies based on error type

### 6. Advanced Model Capabilities
- **Native Reasoning**: Support for models with native reasoning (OpenAI o1, DeepSeek-R1, etc.)
- **Reasoning Effort Control**: Configure reasoning effort levels for capable models
- **Response Format Policies**: Adaptive response format selection based on model capabilities
- **Cost Tracking**: Accurate cost calculation for billing and monitoring

#### NIM Native Reasoning Ergonomics
- Canonical caller surface remains unchanged: use `use_native_reasoning=True` and `reasoning_effort` via `NativeLLMAdapter`.
- For NIM models (`nim/...` and `nvidia/...`), the provider maps `reasoning_effort` to `extra_body.chat_template_kwargs.thinking` automatically.
- Downstream teams do not need to pass NIM-specific payloads such as `chat_template_kwargs` manually.
- Budget-like reasoning controls are currently unsupported by NIM in native mode; they are ignored with warnings instead of failing requests.

### 7. Native Provider Integration
- **Direct API Integration**: Native integrations without LiteLLM overhead
- **Provider-Specific Features**: Access to provider-specific capabilities
- **Performance Optimization**: Reduced latency and overhead
- **Advanced Streaming**: Native streaming with provider-specific features
- **Native Reasoning**: Direct access to reasoning capabilities

### 8. Typed Request/Response Model
- **Eliminate Dict Plumbing**: All requests/responses use typed dataclasses
- **Normalized Format**: Consistent message format across all providers
- **Content Parts**: Structured content parts (text, tool calls, images, etc.)
- **Usage Tracking**: Consistent usage statistics across providers
- **Cancellation Protocol**: Standardized cancellation contract

## Architecture Layers

### 1. Client Abstraction Layer
- **JSONLLMClient Protocol**: Define LLM interface contract
- **_LiteLLMJSONClient**: LiteLLM-based implementation with provider compatibility
- **NativeLLMAdapter**: Adapter for native provider implementations
- **Client Factory**: Create appropriate clients with configuration
- **Configuration Management**: Handle client-specific settings and parameters

### 2. Native Provider Layer
- **Provider Registry**: Registry of available native providers
- **Provider Implementations**: Direct API integrations (OpenAI, Anthropic, Google, etc.)
- **Request/Response Models**: Native request/response data structures
- **Authentication Management**: Provider-specific authentication handling
- **Error Handling**: Provider-specific error handling and retry logic

### 3. Core Types Layer
- **LLMMessage**: Typed message format with content parts
- **ContentParts**: Structured content (TextPart, ToolCallPart, ToolResultPart, ImagePart)
- **LLMRequest**: Typed request with messages, tools, and structured output specs
- **CompletionResponse**: Normalized response format
- **StreamEvent**: Streaming event format for real-time updates

### 4. Provider Profiles & Schema Layer
- **ModelProfile**: Provider-specific capabilities and quirks
- **Schema Transformers**: Provider-specific schema transformations
- **Output Modes**: Different output strategies (native, tools, prompted)
- **Capability Detection**: Automatic detection of provider capabilities

### 5. Prompt Generation Layer
- **Prompt Builders**: Construct prompts dynamically based on context
- **Template Engine**: Handle prompt templates with variable injection
- **Context Injection**: Inject relevant context into prompts
- **Message Formatting**: Format messages for LLM consumption
- **Adaptive Guidance**: Inject guidance based on historical performance

### 6. Response Processing Layer
- **Parser Engine**: Parse LLM responses with multiple strategies
- **Validator**: Validate parsed responses against schemas
- **Error Handler**: Handle parsing and validation errors
- **Extractor**: Extract structured data from responses
- **Sanitizer**: Sanitize and redact sensitive information

### 7. Integration Layer
- **Planning Integration**: Connect to planning process
- **Streaming Handlers**: Handle streaming responses
- **Context Management**: Manage context windows and budgets
- **Error Recovery**: Handle LLM errors and recovery
- **Cost Tracking**: Track and report usage costs

## Data Flow

### 1. LLM Call Flow (LiteLLM Path)
```
Planning Context
    ↓
Trajectory Analysis
    ↓
Prompt Generation (build_messages)
    ↓
Context Window Management
    ↓
Schema Preparation
    ↓
LLM Request (_LiteLLMJSONClient.complete)
    ↓
Response Reception
    ↓
JSON Parsing (_extract_json_from_text)
    ↓
Schema Validation
    ↓
Action Normalization (normalize_action)
    ↓
Parsed PlannerAction
    ↓
Planning Process Continuation
```

### 2. LLM Call Flow (Native Path)
```
Planning Context
    ↓
Trajectory Analysis
    ↓
Prompt Generation (build_messages)
    ↓
Context Window Management
    ↓
Schema Preparation
    ↓
LLM Request (NativeLLMAdapter.complete)
    ↓
Native Provider Request (create_provider -> Provider.complete)
    ↓
Typed Request Conversion (LLMRequest -> Provider-specific format)
    ↓
Response Reception
    ↓
Typed Response Conversion (Provider-specific -> CompletionResponse)
    ↓
JSON Parsing (native response processing)
    ↓
Schema Validation
    ↓
Action Normalization (normalize_action)
    ↓
Parsed PlannerAction
    ↓
Planning Process Continuation
```

### 3. Streaming Response Flow
```
LLM Request with Streaming Enabled
    ↓
LiteLLM/Native Streaming Response
    ↓
Chunk Processing Loop
    ↓
Real-time Callback Execution
    ↓
Partial JSON Accumulation
    ↓
Progress Updates to UI
    ↓
Complete Response Assembly
    ↓
Final Validation
    ↓
Structured Action Output
```

### 4. Error Recovery Flow
```
LLM Response Received
    ↓
JSON Parsing Attempt
    ↓
Schema Validation
    ↓
Parse/Validation Failure
    ↓
Error Classification (classify_llm_error)
    ↓
Recovery Strategy Selection
    ↓
Retry with Backoff (if applicable)
    ↓
Fallback Processing
    ↓
Structured Error Response
    ↓
Planning Continuation Decision
```

### 5. Context Management Flow
```
Trajectory Query & Context
    ↓
Token Budget Check
    ↓
History Compression (if needed)
    ↓
Memory Context Injection
    ↓
Tool Specifications Formatting
    ↓
Current Step Context
    ↓
Steering Inputs Integration
    ↓
Final Prompt Assembly
    ↓
LLM Request
```

## Integration Points

### 1. With ReactPlanner
- **Action Generation**: Generate next actions using PlannerAction schema
- **Context Provision**: Provide trajectory context to LLM
- **Response Processing**: Process LLM responses into planner actions
- **Error Handling**: Handle LLM errors and recovery strategies
- **Cost Tracking**: Integrate cost tracking with planner budgeting

### 2. With Tool System
- **Tool Specification**: Format tool specs for LLM consumption
- **Schema Generation**: Generate JSON schemas from Pydantic models
- **Response Validation**: Validate tool response schemas
- **Context Integration**: Integrate tool context with LLM context

### 3. With Memory System
- **Context Injection**: Inject memory context into prompts
- **History Provision**: Provide conversation history
- **State Management**: Manage LLM state with memory
- **Summarization**: Trigger summarization based on context length

### 4. With External Systems
- **LLM Providers**: Connect to various LLM providers via LiteLLM or native
- **API Integration**: Handle different API formats and requirements
- **Authentication**: Manage provider authentication and API keys
- **Billing Integration**: Track and report usage costs

## Configuration Options

### 1. Client Configuration
- `llm`: LLM identifier or configuration dictionary
- `temperature`: Sampling temperature for generation diversity
- `json_schema_mode`: Enable strict JSON schema enforcement
- `system_prompt_extra`: Additional system instructions and constraints

### 2. Response Handling
- `llm_timeout_s`: Timeout for LLM calls (default: 60.0s)
- `llm_max_retries`: Maximum retry attempts (default: 3)
- `stream_final_response`: Enable streaming for final responses
- `use_native_reasoning`: Enable native reasoning for capable models

### 3. Reasoning Configuration
- `reasoning_effort`: Reasoning effort level for advanced models
- `use_native_reasoning`: Whether to use native reasoning capabilities
- `reasoning_effort`: Level of reasoning effort (low/medium/high)

### 4. Prompt Configuration
- `system_prompt_extra`: Additional system instructions
- `token_budget`: Context window management and summarization trigger
- `context_window_size`: Maximum context window considerations

### 5. Schema Enforcement
- `json_schema_mode`: Strict vs flexible schema enforcement
- `response_format_policy`: Adaptive response format selection
- `schema_sanitization`: Sanitize schemas for provider compatibility

### 6. Native Provider Configuration
- `streaming_enabled`: Enable streaming support for native providers
- `provider_kwargs`: Provider-specific configuration options
- `base_url`: Override base URL for provider
- `api_key`: Provider API key
- `model_profile`: Provider-specific capabilities and quirks
- NIM model prefixes: `nim/...` (preferred) and `nvidia/...` (alias)
- NIM API key env vars: `NIM_API_KEY` (preferred), `NVIDIA_API_KEY` (fallback)

## Error Handling

### 1. LLM Errors
- **API Errors**: Handle provider API errors with classification
- **Timeout Errors**: Handle request timeouts with retry logic
- **Rate Limiting**: Handle rate limiting with exponential backoff
- **Authentication**: Handle authentication failures gracefully
- **Context Length**: Detect and handle context length exceeded errors

### 2. Response Errors
- **Malformed JSON**: Handle invalid JSON responses with fallback parsing
- **Schema Violations**: Handle schema violations with validation feedback
- **Parsing Errors**: Handle parsing failures with multiple strategies
- **Validation Errors**: Handle validation failures with detailed feedback

### 3. Recovery Mechanisms
- **Retry Logic**: Exponential backoff for transient failures
- **Response Repair**: Attempt to repair malformed responses
- **Fallback Models**: Use alternative approaches when primary fails
- **Error Reporting**: Provide detailed error information for debugging

## Performance Considerations

### 1. Response Efficiency
- **Caching**: Cache frequently used prompt components
- **Optimization**: Optimize prompt construction for token efficiency
- **Compression**: Compress large contexts when approaching limits
- **Batching**: Optimize for provider-specific batching capabilities

### 2. Parsing Performance
- **Efficient Parsing**: Use optimized JSON parsing strategies
- **Schema Caching**: Cache compiled and sanitized schemas
- **Validation Optimization**: Optimize validation for common patterns
- **Streaming Processing**: Process responses incrementally

### 3. Context Management
- **Window Optimization**: Optimize context window usage with summarization
- **Relevance Filtering**: Filter irrelevant context to reduce token usage
- **Compression**: Compress context when approaching budget limits
- **Caching**: Cache processed and compressed contexts

### 4. Native Provider Benefits
- **Reduced Latency**: Direct API calls without LiteLLM overhead
- **Provider Optimization**: Access to provider-specific optimizations
- **Feature Access**: Direct access to advanced provider features
- **Cost Efficiency**: More accurate cost calculation and tracking

### 5. Typed Model Benefits
- **Type Safety**: Eliminate runtime errors from incorrect dict structures
- **Performance**: Faster serialization/deserialization with typed models
- **Maintainability**: Clear data contracts between components
- **Debugging**: Better error messages and IDE support

### 6. Cost Optimization
- **Token Efficiency**: Optimize prompts for token efficiency
- **Model Selection**: Use appropriate models for different tasks
- **Caching**: Cache expensive operations when possible
- **Monitoring**: Track and optimize cost patterns

## Security Considerations

### 1. Provider Security
- **Authentication**: Secure provider authentication with key rotation
- **API Keys**: Protect API keys with secure storage
- **Rate Limiting**: Implement client-side rate limiting
- **Access Control**: Control access to different providers

### 2. Prompt Security
- **Injection Prevention**: Prevent prompt injection attacks
- **Context Validation**: Validate injected context for safety
- **Sanitization**: Sanitize prompt content for safety
- **Monitoring**: Monitor prompt content for anomalies

### 3. Response Security
- **Content Validation**: Validate response content for safety
- **Injection Prevention**: Prevent response injection attacks
- **Sanitization**: Sanitize response content for safety
- **Monitoring**: Monitor response content for anomalies

### 4. Data Privacy
- **Context Redaction**: Redact sensitive information from context
- **Artifact Handling**: Properly handle artifact references
- **Encryption**: Encrypt sensitive data in transit and at rest
- **Compliance**: Ensure compliance with privacy regulations

## Extension Points

### 1. LLM Providers
- **New Providers**: Add support for new LLM providers via LiteLLM or native
- **Custom Clients**: Implement custom JSONLLMClient implementations
- **Provider Configuration**: Extend provider-specific options
- **Authentication Methods**: Add new authentication methods

### 2. Native Providers
- **Provider Registry**: Add new native provider implementations
- **Request Models**: Extend native request/response models
- **Authentication**: Add new authentication schemes
- **Features**: Implement provider-specific features

### 3. Core Types
- **Content Parts**: Add new content part types (audio, video, etc.)
- **Request Extensions**: Extend LLMRequest with new fields
- **Response Extensions**: Extend CompletionResponse with new fields
- **Streaming Events**: Add new streaming event types

### 4. Model Profiles
- **Provider Profiles**: Add profiles for new providers
- **Capabilities**: Define provider-specific capabilities
- **Schema Transformers**: Add transformers for new providers
- **Output Modes**: Define new output strategies

### 5. Prompt Templates
- **Custom Templates**: Create domain-specific prompt templates
- **Template Variables**: Add new template variables and functions
- **Formatting Options**: Extend formatting and injection options
- **Context Injection**: Add new context injection points

### 6. Response Processing
- **Custom Parsers**: Implement custom response parsing strategies
- **Validation Rules**: Add new validation rules and schemas
- **Error Recovery**: Extend error recovery mechanisms
- **Streaming Handlers**: Add custom streaming response handlers

### 7. Schema Management
- **Custom Schemas**: Define custom JSON schemas for specific use cases
- **Validation Extensions**: Add custom validation logic
- **Schema Evolution**: Handle schema versioning and migration
- **Compatibility Layers**: Add provider-specific compatibility

## Best Practices

### 1. Prompt Design
- Use clear, unambiguous instructions with proper context
- Provide sufficient context without exceeding token limits
- Include examples when helpful for complex tasks
- Follow consistent formatting and structure

### 2. Error Handling
- Implement comprehensive error classification and handling
- Provide meaningful error messages for debugging
- Log errors with appropriate detail for monitoring
- Implement appropriate fallback mechanisms

### 3. Performance
- Optimize prompt construction for token efficiency
- Use efficient parsing and validation methods
- Manage context windows effectively to avoid token limits
- Monitor API usage and costs regularly

### 4. Security
- Protect API keys and credentials with secure storage
- Validate all inputs and outputs for safety
- Monitor for prompt injection and other attacks
- Implement proper rate limiting and access controls

### 5. Reliability
- Implement robust retry logic with exponential backoff
- Handle various error types appropriately
- Provide graceful degradation when services are unavailable
- Test thoroughly with various providers and scenarios

### 6. Native Provider Usage
- Use native providers for performance-critical applications
- Leverage provider-specific features when beneficial
- Monitor provider-specific metrics and costs
- Implement proper fallback strategies

### 7. Typed Models Usage
- Use typed models consistently throughout the stack
- Leverage type safety for better error prevention
- Follow the normalized request/response patterns
- Maintain compatibility with the core types system

## Advanced Capabilities

### 1. Native Reasoning Support
- **Model Detection**: Automatically detect reasoning-capable models
- **Effort Control**: Configure reasoning effort levels
- **Content Extraction**: Handle reasoning content separately from responses
- **Cost Optimization**: Optimize for reasoning model pricing models

### 2. Adaptive Schema Handling
- **Provider Detection**: Automatically detect provider capabilities
- **Schema Sanitization**: Remove incompatible schema elements
- **Format Selection**: Choose optimal response format per provider
- **Compatibility Management**: Handle provider-specific quirks

### 3. Context Optimization
- **Intelligent Summarization**: Summarize context when approaching limits
- **Relevance Scoring**: Prioritize most relevant context
- **Memory Integration**: Seamlessly integrate with memory systems
- **Budget Management**: Proactively manage token budgets

### 4. Native Provider Features
- **Direct Integration**: Access to provider-specific features
- **Performance Optimization**: Reduced latency and overhead
- **Advanced Streaming**: Native streaming with provider-specific capabilities
- **Native Reasoning**: Direct access to reasoning capabilities without abstraction layers

### 5. Provider-Specific Optimizations
- **Model Profiles**: Leverage provider-specific capabilities and quirks
- **Schema Transformers**: Apply provider-specific schema transformations
- **Output Modes**: Use appropriate output strategies (native, tools, prompted)
- **Capability Detection**: Automatically detect and utilize provider capabilities

This subsystem provides the essential interface between the Penguiflow system and Large Language Models, enabling the planning process to leverage LLM capabilities for reasoning and decision-making with sophisticated error handling, performance optimization, and security measures. The dual approach of supporting both LiteLLM compatibility and native provider implementations offers flexibility for different use cases and performance requirements, while the typed request/response model ensures type safety and maintainability throughout the stack.
