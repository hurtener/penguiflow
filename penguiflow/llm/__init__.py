"""Native LLM layer for penguiflow.

This module provides a native, type-safe LLM abstraction layer with:
- Typed request/response models
- Provider-specific adapters (OpenAI, Anthropic, Google, Bedrock, Databricks, OpenRouter)
- Automatic output mode selection (native, tools, prompted)
- Schema transformation for provider compatibility
- Retry with LLM feedback
- Cost tracking and telemetry
- Streaming support

Quick Start:
    from penguiflow.llm import LLMClient, LLMMessage, TextPart
    from pydantic import BaseModel

    class Answer(BaseModel):
        text: str
        confidence: float

    client = LLMClient("gpt-4o")
    result = await client.generate(
        messages=[LLMMessage(role="user", parts=[TextPart(text="What is 2+2?")])],
        response_model=Answer,
    )
    print(result.data.text)

For backward compatibility with existing planner code:
    from penguiflow.llm.protocol import NativeLLMAdapter

    # Drop-in replacement for _LiteLLMJSONClient
    client = NativeLLMAdapter("gpt-4o")
    content, cost = await client.complete(
        messages=[{"role": "user", "content": "Hello"}],
        response_format={"type": "json_object"},
    )
"""

from __future__ import annotations

# Client
from .client import (
    LLMClient,
    LLMClientConfig,
    LLMResult,
    generate_structured,
)

# Errors
from .errors import (
    LLMAuthError,
    LLMCancelledError,
    LLMContextLengthError,
    LLMError,
    LLMInvalidRequestError,
    LLMParseError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    LLMValidationError,
    is_context_length_error,
    is_retryable,
    map_status_to_error,
)

# Pricing
from .pricing import (
    calculate_cost,
    calculate_cost_from_usage,
    get_pricing,
    register_pricing,
)

# Profiles
from .profiles import (
    ModelProfile,
    get_profile,
    register_profile,
)

# Protocol adapter
from .protocol import (
    NativeLLMAdapter,
    create_native_adapter,
)

# Providers
from .providers import (
    AnthropicProvider,
    BedrockProvider,
    DatabricksProvider,
    GoogleProvider,
    NIMProvider,
    OpenAIProvider,
    OpenRouterProvider,
    Provider,
    create_provider,
)

# Retry
from .retry import (
    ModelRetry,
    RetryConfig,
    RetryState,
    ValidationRetry,
    call_with_retry,
)

# Routing
from .routing import (
    ParsedModel,
    ProviderType,
    build_model_string,
    estimate_context_window,
    get_provider_for_model,
    is_reasoning_model,
    is_vision_model,
    normalize_model_id,
    parse_model_string,
)

# Schema
from .schema.plan import (
    OutputMode,
    SchemaPlan,
    choose_output_mode,
    plan_schema,
)

# Telemetry
from .telemetry import (
    LLMEvent,
    TelemetryCallback,
    TelemetryHooks,
    TimingContext,
    create_mlflow_callback,
    create_prometheus_callback,
    get_telemetry_hooks,
    set_telemetry_hooks,
)

# Core types
from .types import (
    CancelToken,
    CompletionResponse,
    ContentPart,
    Cost,
    ImagePart,
    LLMMessage,
    LLMRequest,
    Role,
    StreamCallback,
    StreamEvent,
    StructuredOutputSpec,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
    Usage,
    extract_single_tool_call,
    extract_text,
    strip_markdown_fences,
)

__all__ = [
    # Types
    "CancelToken",
    "CompletionResponse",
    "ContentPart",
    "Cost",
    "ImagePart",
    "LLMMessage",
    "LLMRequest",
    "Role",
    "StreamCallback",
    "StreamEvent",
    "StructuredOutputSpec",
    "TextPart",
    "ToolCallPart",
    "ToolResultPart",
    "ToolSpec",
    "Usage",
    "extract_single_tool_call",
    "extract_text",
    "strip_markdown_fences",
    # Errors
    "LLMAuthError",
    "LLMCancelledError",
    "LLMContextLengthError",
    "LLMError",
    "LLMInvalidRequestError",
    "LLMParseError",
    "LLMRateLimitError",
    "LLMServerError",
    "LLMTimeoutError",
    "LLMValidationError",
    "is_context_length_error",
    "is_retryable",
    "map_status_to_error",
    # Profiles
    "ModelProfile",
    "get_profile",
    "register_profile",
    # Providers
    "AnthropicProvider",
    "BedrockProvider",
    "DatabricksProvider",
    "GoogleProvider",
    "NIMProvider",
    "OpenAIProvider",
    "OpenRouterProvider",
    "Provider",
    "create_provider",
    # Schema
    "OutputMode",
    "SchemaPlan",
    "choose_output_mode",
    "plan_schema",
    # Client
    "LLMClient",
    "LLMClientConfig",
    "LLMResult",
    "generate_structured",
    # Protocol
    "NativeLLMAdapter",
    "create_native_adapter",
    # Routing
    "ParsedModel",
    "ProviderType",
    "build_model_string",
    "estimate_context_window",
    "get_provider_for_model",
    "is_reasoning_model",
    "is_vision_model",
    "normalize_model_id",
    "parse_model_string",
    # Pricing
    "calculate_cost",
    "calculate_cost_from_usage",
    "get_pricing",
    "register_pricing",
    # Telemetry
    "LLMEvent",
    "TelemetryCallback",
    "TelemetryHooks",
    "TimingContext",
    "create_mlflow_callback",
    "create_prometheus_callback",
    "get_telemetry_hooks",
    "set_telemetry_hooks",
    # Retry
    "ModelRetry",
    "RetryConfig",
    "RetryState",
    "ValidationRetry",
    "call_with_retry",
]
