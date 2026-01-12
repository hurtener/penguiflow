# RFC: Native LLM Layer

> **Status**: Proposed
> **Created**: 2026-01-12
> **Author**: PenguiFlow Team

## Summary

This RFC proposes replacing the LiteLLM dependency with a native, penguiflow-owned LLM abstraction layer. The design extracts proven patterns from PydanticAI (schema transformers, output modes, retry mechanisms) while maintaining penguiflow's existing strengths (cost tracking, streaming callbacks, error recovery).

## Motivation

### Current State

PenguiFlow currently uses LiteLLM (`litellm>=1.80.15`) for LLM provider abstraction. While LiteLLM provides broad provider support, it introduces several challenges:

1. **Heavy dependency**: LiteLLM brings significant transitive dependencies and abstractions
2. **Limited control**: Provider-specific quirks require workarounds in our code (see `llm.py` lines 364-398)
3. **Schema handling complexity**: Manual per-provider JSON schema sanitization logic
4. **Error message extraction**: Nested JSON parsing for Databricks-style errors
5. **Parameter safety**: `reasoning_effort` crashes on Databricks even with `drop_params=True`

### PydanticAI Analysis

PydanticAI (MIT licensed, maintained by Pydantic team) has solved many of these problems elegantly:

| Component | What They Got Right |
|-----------|---------------------|
| `ModelProfile` | Declarative capability flags per model |
| `JsonSchemaTransformer` | Recursive schema adaptation per provider |
| Output Modes | Strategy pattern: Tool / Native / Prompted |
| `ModelRetry` | Exception-based retry with LLM feedback |
| Provider implementations | Direct SDK usage, no intermediate layer |

However, PydanticAI is a full agent framework—we only need the provider layer.

### Proposed Solution

Extract PydanticAI's provider patterns and build a penguiflow-native implementation that:

- Uses native SDKs directly (openai, anthropic, google-genai, boto3)
- Implements PydanticAI's schema transformers and output modes
- Keeps penguiflow's streaming callbacks and cost tracking
- Adds Databricks support (not in PydanticAI)
- Removes LiteLLM as a dependency

## Design

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            LLMClient                                     │
│  (Orchestrates: provider selection, output mode, retry, streaming)       │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
         ┌───────────────────────────┼───────────────────────────┐
         │                           │                           │
         v                           v                           v
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│   OutputMode    │       │  ModelProfile   │       │     Retry       │
│ Tool/Native/    │       │ Capabilities +  │       │ ValidationRetry │
│   Prompted      │       │  Transformer    │       │ + Backoff       │
└─────────────────┘       └─────────────────┘       └─────────────────┘
                                     │
                                     v
                          ┌─────────────────┐
                          │    Provider     │
                          │ (Direct SDK)    │
                          └────────┬────────┘
                                   │
    ┌──────────┬──────────┬────────┼────────┬──────────┬──────────┐
    v          v          v        v        v          v          v
┌───────┐ ┌────────┐ ┌────────┐ ┌───────┐ ┌────────┐ ┌────────┐ ┌─────┐
│OpenAI │ │Anthropic│ │ Google │ │Bedrock│ │Databricks│ │OpenRouter│ │...│
└───────┘ └────────┘ └────────┘ └───────┘ └────────┘ └────────┘ └─────┘
```

### Module Structure

```
penguiflow/llm/
├── __init__.py              # Public API: create_client(), LLMClient
├── protocol.py              # JSONLLMClient protocol (existing interface)
├── client.py                # LLMClient implementation
│
├── profiles/
│   ├── __init__.py          # ModelProfile dataclass, PROFILES registry
│   ├── openai.py            # OpenAI/GPT model profiles
│   ├── anthropic.py         # Claude model profiles
│   ├── google.py            # Gemini model profiles
│   ├── bedrock.py           # AWS Bedrock model profiles
│   ├── databricks.py        # Databricks model profiles
│   └── openrouter.py        # OpenRouter routing + profile mapping
│
├── schema/
│   ├── __init__.py          # JsonSchemaTransformer ABC
│   ├── transformer.py       # Base transformer with recursive walking
│   ├── openai.py            # OpenAI: additionalProperties, strict mode
│   ├── anthropic.py         # Anthropic: constraint relocation
│   ├── google.py            # Google: const→enum, format in description
│   └── bedrock.py           # Bedrock: inline defs transformer
│
├── output/
│   ├── __init__.py          # OutputMode enum, select_output_mode()
│   ├── tool.py              # Tool-based structured output
│   ├── native.py            # response_format with JSON schema
│   └── prompted.py          # Schema in prompt + parse/retry
│
├── providers/
│   ├── __init__.py          # Provider registry, create_provider()
│   ├── base.py              # Provider ABC
│   ├── openai.py            # AsyncOpenAI direct
│   ├── anthropic.py         # AsyncAnthropic direct
│   ├── google.py            # google-genai direct
│   ├── bedrock.py           # boto3 bedrock-runtime direct
│   ├── databricks.py        # OpenAI-compatible + Databricks quirks
│   └── openrouter.py        # OpenAI-compatible + model routing
│
├── retry.py                 # ModelRetry, ValidationRetry, retry loop
├── cost.py                  # Pricing tables, token counting
├── streaming.py             # StreamChunk, callback handling
└── errors.py                # LLMError hierarchy
```

### Core Components

#### 1. ModelProfile

Declarative capability description per model, adapted from PydanticAI:

```python
from dataclasses import dataclass, field
from typing import Callable, Literal

@dataclass
class ModelProfile:
    """Describes capabilities and configuration for a specific model."""

    # Output capabilities
    supports_json_schema_output: bool = False  # Native response_format with schema
    supports_json_object_output: bool = True   # response_format: {"type": "json_object"}
    supports_tools: bool = True                # Tool/function calling
    supports_reasoning: bool = False           # Native reasoning (o1, o3, deepseek-r1)

    # Output mode selection
    default_output_mode: Literal["tool", "native", "prompted"] = "tool"

    # Schema transformation
    schema_transformer_class: type["JsonSchemaTransformer"] | None = None

    # Reasoning configuration
    reasoning_effort_param: str | None = None  # Parameter name if supported
    thinking_tags: tuple[str, str] | None = None  # e.g., ("<think>", "</think>")

    # Cost tracking (per 1K tokens)
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0

    # Provider quirks
    strict_mode_default: bool = True           # Default for strict JSON schema
    supports_system_role: bool = True          # Some models need user role for system
    drop_unsupported_params: bool = True       # Silently drop unknown params
```

**Profile Registry:**

```python
PROFILES: dict[str, ModelProfile] = {
    # OpenAI
    "gpt-4o": ModelProfile(
        supports_json_schema_output=True,
        schema_transformer_class=OpenAIJsonSchemaTransformer,
        cost_per_1k_input=0.0025,
        cost_per_1k_output=0.01,
    ),
    "gpt-4o-mini": ModelProfile(
        supports_json_schema_output=True,
        schema_transformer_class=OpenAIJsonSchemaTransformer,
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.0006,
    ),
    "o1": ModelProfile(
        supports_json_schema_output=True,
        supports_reasoning=True,
        reasoning_effort_param="reasoning_effort",
        schema_transformer_class=OpenAIJsonSchemaTransformer,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.06,
    ),

    # Anthropic
    "claude-3-5-sonnet": ModelProfile(
        supports_json_schema_output=True,
        schema_transformer_class=AnthropicJsonSchemaTransformer,
        strict_mode_default=False,  # Lossy transformation
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
    ),
    "claude-3-5-haiku": ModelProfile(
        supports_json_schema_output=True,
        schema_transformer_class=AnthropicJsonSchemaTransformer,
        strict_mode_default=False,
        cost_per_1k_input=0.0008,
        cost_per_1k_output=0.004,
    ),

    # Google
    "gemini-2.0-flash": ModelProfile(
        supports_json_schema_output=True,
        schema_transformer_class=GoogleJsonSchemaTransformer,
        cost_per_1k_input=0.0,  # Free tier
        cost_per_1k_output=0.0,
    ),

    # Bedrock (via Anthropic)
    "anthropic.claude-3-5-sonnet": ModelProfile(
        supports_json_schema_output=True,
        schema_transformer_class=BedrockJsonSchemaTransformer,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
    ),

    # Databricks Foundation Model APIs
    # Supports structured outputs via constrained decoding
    # Limitations: no anyOf/oneOf/allOf/$ref/pattern, max 64 keys
    # Reference: https://docs.databricks.com/aws/en/machine-learning/model-serving/structured-outputs
    "databricks-meta-llama-3-1-70b-instruct": ModelProfile(
        supports_json_schema_output=True,
        supports_json_object_output=True,
        supports_tools=True,  # Public Preview, max 32 tools
        schema_transformer_class=DatabricksJsonSchemaTransformer,
        default_output_mode="native",
    ),
    "databricks-meta-llama-3-3-70b-instruct": ModelProfile(
        supports_json_schema_output=True,
        supports_json_object_output=True,
        supports_tools=True,
        schema_transformer_class=DatabricksJsonSchemaTransformer,
        default_output_mode="native",
    ),
    "databricks-dbrx-instruct": ModelProfile(
        supports_json_schema_output=True,
        supports_json_object_output=True,
        supports_tools=True,
        schema_transformer_class=DatabricksJsonSchemaTransformer,
        default_output_mode="native",
    ),
    # Anthropic on Databricks - only supports json_schema, NOT json_object
    "databricks-claude-3-5-sonnet": ModelProfile(
        supports_json_schema_output=True,
        supports_json_object_output=False,  # Claude on Databricks only supports json_schema
        supports_tools=True,
        schema_transformer_class=DatabricksJsonSchemaTransformer,
        default_output_mode="native",
    ),
}

def get_profile(model: str) -> ModelProfile:
    """Get profile for a model, with fallback to defaults."""
    # Exact match
    if model in PROFILES:
        return PROFILES[model]

    # Prefix matching for versioned models
    for key, profile in PROFILES.items():
        if model.startswith(key):
            return profile

    # Default fallback
    return ModelProfile()
```

#### 2. JsonSchemaTransformer

Base class for recursive schema transformation, extracted from PydanticAI:

```python
from abc import ABC, abstractmethod
from typing import Any

class JsonSchemaTransformer(ABC):
    """Base class for provider-specific JSON schema transformations.

    Walks the schema recursively, applying transformations at each level.
    Handles $ref resolution, recursive schemas, and keyword stripping.
    """

    def __init__(self, schema: dict[str, Any], *, strict: bool = True):
        self.original_schema = schema
        self.strict = strict
        self.is_strict_compatible = True
        self._refs_stack: list[str] = []
        self._recursive_refs: set[str] = set()

    def transform(self) -> dict[str, Any]:
        """Transform the schema for this provider."""
        result = self._walk(self.original_schema)
        return self._finalize(result)

    def _walk(self, node: dict[str, Any]) -> dict[str, Any]:
        """Recursively walk and transform schema nodes."""
        if "$ref" in node:
            return self._handle_ref(node)

        result = {}
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                result[key] = {k: self._walk(v) for k, v in value.items()}
            elif key == "items" and isinstance(value, dict):
                result[key] = self._walk(value)
            elif key in ("anyOf", "oneOf", "allOf") and isinstance(value, list):
                result[key] = [self._walk(v) for v in value]
            elif key == "$defs" and isinstance(value, dict):
                result[key] = {k: self._walk(v) for k, v in value.items()}
            else:
                result[key] = value

        return self._transform_node(result)

    @abstractmethod
    def _transform_node(self, node: dict[str, Any]) -> dict[str, Any]:
        """Apply provider-specific transformations to a node."""
        ...

    def _handle_ref(self, node: dict[str, Any]) -> dict[str, Any]:
        """Handle $ref, detecting recursive references."""
        ref = node["$ref"]
        if ref in self._refs_stack:
            self._recursive_refs.add(ref)
            return node  # Keep recursive ref as-is
        # Inline non-recursive refs if preferred
        return node

    def _finalize(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Final cleanup after transformation."""
        return schema
```

**OpenAI Transformer:**

```python
class OpenAIJsonSchemaTransformer(JsonSchemaTransformer):
    """OpenAI strict mode schema transformer.

    Transformations:
    - Add additionalProperties: false to all objects
    - Mark all properties as required
    - Remove unsupported keywords (minLength, maxLength, pattern, etc.)
    - Convert oneOf to anyOf
    """

    UNSUPPORTED_KEYWORDS = {
        "minLength", "maxLength", "pattern", "format",
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
        "minItems", "maxItems", "uniqueItems",
        "minProperties", "maxProperties", "patternProperties",
    }

    def _transform_node(self, node: dict[str, Any]) -> dict[str, Any]:
        # Remove unsupported keywords
        for keyword in self.UNSUPPORTED_KEYWORDS:
            if keyword in node:
                del node[keyword]
                self.is_strict_compatible = False

        # Handle object types
        if node.get("type") == "object":
            if self.strict:
                node["additionalProperties"] = False
            if "properties" in node:
                node["required"] = list(node["properties"].keys())

        # Convert oneOf to anyOf (OpenAI preference)
        if "oneOf" in node:
            node["anyOf"] = node.pop("oneOf")

        return node
```

**Anthropic Transformer:**

```python
class AnthropicJsonSchemaTransformer(JsonSchemaTransformer):
    """Anthropic schema transformer.

    Transformations:
    - Remove constraints and relocate to description
    - Add additionalProperties: false when strict
    - Strip title and $schema fields
    """

    RELOCATE_TO_DESCRIPTION = {
        "minLength", "maxLength", "pattern", "format",
        "minimum", "maximum",
    }

    def __init__(self, schema: dict[str, Any], *, strict: bool = False):
        # Anthropic defaults to strict=False due to lossy transformation
        super().__init__(schema, strict=strict)

    def _transform_node(self, node: dict[str, Any]) -> dict[str, Any]:
        # Relocate constraints to description
        constraints = []
        for keyword in self.RELOCATE_TO_DESCRIPTION:
            if keyword in node:
                constraints.append(f"{keyword}: {node.pop(keyword)}")

        if constraints:
            desc = node.get("description", "")
            constraint_str = " | ".join(constraints)
            node["description"] = f"{desc} [{constraint_str}]".strip()

        # Remove title and $schema
        node.pop("title", None)
        node.pop("$schema", None)

        # Handle object types with strict mode
        if node.get("type") == "object" and self.strict:
            node["additionalProperties"] = False

        return node
```

**Google Transformer:**

```python
class GoogleJsonSchemaTransformer(JsonSchemaTransformer):
    """Google/Gemini schema transformer.

    Transformations:
    - Convert const to enum with single value
    - Append format to description
    - Remove exclusiveMinimum/exclusiveMaximum
    - Remove $schema, discriminator, examples, title
    """

    def _transform_node(self, node: dict[str, Any]) -> dict[str, Any]:
        # Convert const to enum (Gemini doesn't support const)
        if "const" in node:
            const_val = node.pop("const")
            node["enum"] = [const_val]
            if "type" not in node:
                node["type"] = self._infer_type(const_val)

        # Append format to description
        if "format" in node:
            fmt = node.pop("format")
            desc = node.get("description", "")
            node["description"] = f"{desc} (format: {fmt})".strip()

        # Remove unsupported keywords
        for keyword in ("$schema", "discriminator", "examples", "title",
                        "exclusiveMinimum", "exclusiveMaximum"):
            node.pop(keyword, None)

        return node

    def _infer_type(self, value: Any) -> str:
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        return "string"
```

**Bedrock Transformer:**

```python
class BedrockJsonSchemaTransformer(JsonSchemaTransformer):
    """AWS Bedrock schema transformer.

    Uses inline definitions strategy - inlines all $defs except recursive ones.
    """

    def __init__(self, schema: dict[str, Any], *, strict: bool = True):
        super().__init__(schema, strict=strict)
        self._prefer_inlined = True

    def _handle_ref(self, node: dict[str, Any]) -> dict[str, Any]:
        ref = node["$ref"]
        if ref in self._refs_stack:
            self._recursive_refs.add(ref)
            return node

        # Inline the reference
        if self._prefer_inlined and ref.startswith("#/$defs/"):
            def_name = ref[8:]  # Remove "#/$defs/"
            if "$defs" in self.original_schema and def_name in self.original_schema["$defs"]:
                self._refs_stack.append(ref)
                inlined = self._walk(self.original_schema["$defs"][def_name].copy())
                self._refs_stack.pop()
                return inlined

        return node
```

#### 3. Output Modes

Strategy pattern for structured output extraction:

```python
from enum import Enum
from typing import Any
from pydantic import BaseModel

class OutputMode(Enum):
    TOOL = "tool"        # Force structured output via tool calling
    NATIVE = "native"    # Use provider's response_format with JSON schema
    PROMPTED = "prompted"  # Schema in system prompt + parse/retry


def select_output_mode(profile: ModelProfile, schema: dict[str, Any]) -> OutputMode:
    """Select the best output mode for the given profile and schema."""
    if profile.supports_json_schema_output:
        return OutputMode.NATIVE
    if profile.supports_tools:
        return OutputMode.TOOL
    return OutputMode.PROMPTED


class ToolOutputStrategy:
    """Structured output via tool calling."""

    def build_request(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        profile: ModelProfile,
    ) -> dict[str, Any]:
        """Build request with tool definition."""
        tool_schema = schema.model_json_schema()

        if profile.schema_transformer_class:
            transformer = profile.schema_transformer_class(
                tool_schema,
                strict=profile.strict_mode_default,
            )
            tool_schema = transformer.transform()

        return {
            "messages": messages,
            "tools": [{
                "type": "function",
                "function": {
                    "name": "structured_output",
                    "description": "Return structured data",
                    "parameters": tool_schema,
                },
            }],
            "tool_choice": {"type": "function", "function": {"name": "structured_output"}},
        }

    def parse_response(self, response: Any, schema: type[BaseModel]) -> BaseModel:
        """Parse tool call response."""
        tool_call = response.choices[0].message.tool_calls[0]
        return schema.model_validate_json(tool_call.function.arguments)


class NativeOutputStrategy:
    """Structured output via response_format."""

    def build_request(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        profile: ModelProfile,
    ) -> dict[str, Any]:
        """Build request with response_format."""
        json_schema = schema.model_json_schema()

        if profile.schema_transformer_class:
            transformer = profile.schema_transformer_class(
                json_schema,
                strict=profile.strict_mode_default,
            )
            json_schema = transformer.transform()

        return {
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": json_schema,
                    "strict": profile.strict_mode_default,
                },
            },
        }

    def parse_response(self, response: Any, schema: type[BaseModel]) -> BaseModel:
        """Parse JSON response."""
        content = response.choices[0].message.content
        return schema.model_validate_json(content)


class PromptedOutputStrategy:
    """Structured output via prompt injection + parse/retry."""

    TEMPLATE = """You must respond with a valid JSON object matching this schema:

{schema}

Do not include any text before or after the JSON. Only output the JSON object."""

    def build_request(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        profile: ModelProfile,
    ) -> dict[str, Any]:
        """Build request with schema in system prompt."""
        json_schema = schema.model_json_schema()
        schema_str = json.dumps(json_schema, indent=2)

        # Inject schema into system message
        system_content = self.TEMPLATE.format(schema=schema_str)

        augmented_messages = messages.copy()
        if augmented_messages and augmented_messages[0].get("role") == "system":
            augmented_messages[0]["content"] += "\n\n" + system_content
        else:
            augmented_messages.insert(0, {"role": "system", "content": system_content})

        return {
            "messages": augmented_messages,
            "response_format": {"type": "json_object"},
        }

    def parse_response(self, response: Any, schema: type[BaseModel]) -> BaseModel:
        """Parse JSON response, stripping markdown if present."""
        content = response.choices[0].message.content

        # Strip markdown code blocks
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1]) if lines[-1] == "```" else "\n".join(lines[1:])

        return schema.model_validate_json(content)
```

#### 4. Retry Mechanism

Exception-based retry with LLM feedback, adapted from PydanticAI:

```python
from dataclasses import dataclass
from pydantic import ValidationError

class ModelRetry(Exception):
    """Raise to retry the LLM call with feedback."""

    def __init__(self, message: str, validation_errors: list[dict] | None = None):
        self.message = message
        self.validation_errors = validation_errors
        super().__init__(message)


class ValidationRetry(Exception):
    """Raise when Pydantic validation fails."""

    def __init__(self, errors: list[dict], raw_content: str):
        self.errors = errors
        self.raw_content = raw_content
        super().__init__(f"Validation failed: {errors}")


def format_retry_message(error: ValidationError | ValidationRetry) -> dict:
    """Format validation error as a message for the LLM."""
    if isinstance(error, ValidationError):
        errors = error.errors()
    else:
        errors = error.errors

    error_details = []
    for err in errors:
        loc = " -> ".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "Unknown error")
        error_details.append(f"- {loc}: {msg}")

    content = (
        "The previous response failed validation. Please fix these errors:\n"
        + "\n".join(error_details)
        + "\n\nProvide a corrected response."
    )

    return {"role": "user", "content": content}


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_retries: int = 3
    retry_on_validation: bool = True
    retry_on_parse: bool = True


async def call_with_retry(
    provider: "Provider",
    messages: list[dict],
    schema: type[BaseModel],
    output_strategy: "OutputStrategy",
    *,
    config: RetryConfig | None = None,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> BaseModel:
    """Execute LLM call with automatic retry on validation failure."""
    if config is None:
        config = RetryConfig()

    working_messages = messages.copy()

    for attempt in range(config.max_retries + 1):
        try:
            request = output_strategy.build_request(
                working_messages,
                schema,
                provider.profile,
            )
            response = await provider.complete(**request)
            return output_strategy.parse_response(response, schema)

        except ValidationError as e:
            if not config.retry_on_validation or attempt >= config.max_retries:
                raise

            if on_retry:
                on_retry(attempt + 1, e)

            working_messages.append(format_retry_message(e))

        except json.JSONDecodeError as e:
            if not config.retry_on_parse or attempt >= config.max_retries:
                raise

            if on_retry:
                on_retry(attempt + 1, e)

            working_messages.append({
                "role": "user",
                "content": f"Invalid JSON: {e}. Please provide valid JSON.",
            })

    raise RuntimeError("Retry loop exited unexpectedly")
```

#### 5. Provider Base Class

Abstract base for all providers:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

@dataclass
class Usage:
    """Token usage statistics."""
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass
class StreamChunk:
    """A chunk of streamed content."""
    content: str
    done: bool = False
    usage: Usage | None = None


@dataclass
class CompletionResponse:
    """Response from a completion call."""
    content: str
    usage: Usage
    raw_response: Any
    cost: float = 0.0
    reasoning_content: str | None = None


class Provider(ABC):
    """Abstract base class for LLM providers."""

    profile: ModelProfile

    @abstractmethod
    async def complete(
        self,
        messages: list[dict],
        *,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[StreamChunk], None] | None = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        """Execute a completion request."""
        ...

    def calculate_cost(self, usage: Usage) -> float:
        """Calculate cost based on token usage."""
        return (
            usage.input_tokens * self.profile.cost_per_1k_input / 1000 +
            usage.output_tokens * self.profile.cost_per_1k_output / 1000
        )
```

#### 6. Provider Implementations

**OpenAI Provider:**

```python
from openai import AsyncOpenAI

class OpenAIProvider(Provider):
    """OpenAI provider using native SDK."""

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        profile: ModelProfile | None = None,
    ):
        self.model = model
        self.profile = profile or get_profile(model)
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def complete(
        self,
        messages: list[dict],
        *,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[StreamChunk], None] | None = None,
        reasoning_effort: str | None = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }

        if response_format:
            params["response_format"] = response_format
        if tools:
            params["tools"] = tools
        if tool_choice:
            params["tool_choice"] = tool_choice
        if max_tokens:
            params["max_tokens"] = max_tokens

        # Reasoning models
        if self.profile.supports_reasoning and reasoning_effort:
            params["reasoning_effort"] = reasoning_effort

        if stream and on_stream_chunk:
            return await self._stream_completion(params, on_stream_chunk)

        response = await self._client.chat.completions.create(**params)

        usage = Usage(
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
        )

        content = response.choices[0].message.content or ""
        reasoning = getattr(response.choices[0].message, "reasoning_content", None)

        return CompletionResponse(
            content=content,
            usage=usage,
            raw_response=response,
            cost=self.calculate_cost(usage),
            reasoning_content=reasoning,
        )

    async def _stream_completion(
        self,
        params: dict,
        on_stream_chunk: Callable[[StreamChunk], None],
    ) -> CompletionResponse:
        params["stream"] = True
        params["stream_options"] = {"include_usage": True}

        content_parts: list[str] = []
        usage: Usage | None = None

        async with await self._client.chat.completions.create(**params) as stream:
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    content_parts.append(text)
                    on_stream_chunk(StreamChunk(content=text, done=False))

                if chunk.usage:
                    usage = Usage(
                        input_tokens=chunk.usage.prompt_tokens,
                        output_tokens=chunk.usage.completion_tokens,
                        total_tokens=chunk.usage.total_tokens,
                    )

        full_content = "".join(content_parts)
        on_stream_chunk(StreamChunk(content="", done=True, usage=usage))

        if usage is None:
            usage = Usage(0, 0, 0)

        return CompletionResponse(
            content=full_content,
            usage=usage,
            raw_response=None,
            cost=self.calculate_cost(usage),
        )
```

**Anthropic Provider:**

```python
from anthropic import AsyncAnthropic

class AnthropicProvider(Provider):
    """Anthropic provider using native SDK."""

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        profile: ModelProfile | None = None,
    ):
        self.model = model
        self.profile = profile or get_profile(model)
        self._client = AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        messages: list[dict],
        *,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[StreamChunk], None] | None = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        # Convert OpenAI message format to Anthropic format
        system_content, anthropic_messages = self._convert_messages(messages)

        params: dict[str, Any] = {
            "model": self.model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens or 4096,
        }

        if system_content:
            params["system"] = system_content
        if temperature > 0:
            params["temperature"] = temperature
        if tools:
            params["tools"] = self._convert_tools(tools)

        if stream and on_stream_chunk:
            return await self._stream_completion(params, on_stream_chunk)

        response = await self._client.messages.create(**params)

        usage = Usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
        )

        content = self._extract_content(response)

        return CompletionResponse(
            content=content,
            usage=usage,
            raw_response=response,
            cost=self.calculate_cost(usage),
        )

    def _convert_messages(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """Convert OpenAI messages to Anthropic format."""
        system_parts = []
        anthropic_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system_parts.append(msg["content"])
            else:
                anthropic_messages.append(msg)

        return "\n\n".join(system_parts), anthropic_messages

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convert OpenAI tool format to Anthropic format."""
        return [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"]["parameters"],
            }
            for t in tools
            if t.get("type") == "function"
        ]

    def _extract_content(self, response: Any) -> str:
        """Extract text content from Anthropic response."""
        for block in response.content:
            if block.type == "text":
                return block.text
        return ""
```

**Bedrock Provider:**

```python
import json
import boto3
from botocore.config import Config

class BedrockProvider(Provider):
    """AWS Bedrock provider using boto3."""

    def __init__(
        self,
        model: str,
        *,
        region_name: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        profile: ModelProfile | None = None,
    ):
        self.model = model
        self.profile = profile or get_profile(model)

        config = Config(
            read_timeout=300,
            connect_timeout=60,
            retries={"max_attempts": 3},
        )

        session = boto3.Session(
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=region_name,
        )
        self._client = session.client("bedrock-runtime", config=config)

    async def complete(
        self,
        messages: list[dict],
        *,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[StreamChunk], None] | None = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        # Bedrock uses converse API
        system_content, bedrock_messages = self._convert_messages(messages)

        params: dict[str, Any] = {
            "modelId": self.model,
            "messages": bedrock_messages,
            "inferenceConfig": {
                "maxTokens": max_tokens or 4096,
                "temperature": temperature,
            },
        }

        if system_content:
            params["system"] = [{"text": system_content}]

        if tools:
            params["toolConfig"] = {
                "tools": self._convert_tools(tools),
            }

        # Run sync boto3 call in executor
        import asyncio
        loop = asyncio.get_event_loop()

        if stream and on_stream_chunk:
            response = await loop.run_in_executor(
                None,
                lambda: self._client.converse_stream(**params),
            )
            return await self._process_stream(response, on_stream_chunk)

        response = await loop.run_in_executor(
            None,
            lambda: self._client.converse(**params),
        )

        usage = Usage(
            input_tokens=response["usage"]["inputTokens"],
            output_tokens=response["usage"]["outputTokens"],
            total_tokens=response["usage"]["totalTokens"],
        )

        content = self._extract_content(response)

        return CompletionResponse(
            content=content,
            usage=usage,
            raw_response=response,
            cost=self.calculate_cost(usage),
        )

    def _convert_messages(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """Convert to Bedrock message format."""
        system_parts = []
        bedrock_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system_parts.append(msg["content"])
            else:
                bedrock_messages.append({
                    "role": msg["role"],
                    "content": [{"text": msg["content"]}],
                })

        return "\n\n".join(system_parts), bedrock_messages

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convert to Bedrock tool format."""
        return [
            {
                "toolSpec": {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "inputSchema": {"json": t["function"]["parameters"]},
                }
            }
            for t in tools
            if t.get("type") == "function"
        ]

    def _extract_content(self, response: dict) -> str:
        """Extract text from Bedrock response."""
        for block in response.get("output", {}).get("message", {}).get("content", []):
            if "text" in block:
                return block["text"]
        return ""
```

**Databricks Provider:**

Databricks provides an OpenAI-compatible API for Foundation Model APIs. Key capabilities and limitations from the [official documentation](https://docs.databricks.com/aws/en/machine-learning/model-serving/structured-outputs):

**Structured Outputs Support:**
- `json_schema` response_format is supported via constrained decoding
- `json_object` response_format for unstructured JSON
- Anthropic Claude models on Databricks **only** support `json_schema` (not `json_object`)

**JSON Schema Limitations:**
- Maximum 64 keys in schema
- No support for: `pattern`, `anyOf`, `oneOf`, `allOf`, `prefixItems`, `$ref`
- No constraint enforcement: `maxProperties`, `minProperties`, `maxLength`
- Heavily nested schemas reduce generation quality

**Function Calling (Public Preview):**
- Maximum 32 functions per request
- Maximum 16 keys per function schema
- Single-turn function calling only (no parallel calls)
- Same schema restrictions as structured outputs

```python
from openai import AsyncOpenAI

class DatabricksJsonSchemaTransformer(JsonSchemaTransformer):
    """Databricks schema transformer.

    Databricks uses constrained decoding and has specific limitations:
    - No anyOf, oneOf, allOf, $ref, pattern support
    - Maximum 64 keys
    - Flatten nested structures where possible

    Reference: https://docs.databricks.com/aws/en/machine-learning/model-serving/structured-outputs
    """

    UNSUPPORTED_KEYWORDS = {
        "pattern", "patternProperties",
        "minLength", "maxLength",
        "minProperties", "maxProperties",
        "minItems", "maxItems",
        "$ref", "$defs",
    }

    def __init__(self, schema: dict[str, Any], *, strict: bool = True):
        super().__init__(schema, strict=strict)
        self._key_count = 0

    def _transform_node(self, node: dict[str, Any]) -> dict[str, Any]:
        # Remove unsupported keywords
        for keyword in self.UNSUPPORTED_KEYWORDS:
            node.pop(keyword, None)

        # Databricks doesn't support anyOf/oneOf/allOf - flatten or fail
        for composition in ("anyOf", "oneOf", "allOf"):
            if composition in node:
                # Try to simplify: if it's just [type, null], make nullable
                options = node.pop(composition)
                if len(options) == 2:
                    types = [o.get("type") for o in options]
                    if "null" in types:
                        non_null = [o for o in options if o.get("type") != "null"][0]
                        node.update(non_null)
                        # Databricks uses nullable for optional fields
                        continue
                # Can't simplify - mark as incompatible
                self.is_strict_compatible = False

        # Track key count for validation
        if node.get("type") == "object" and "properties" in node:
            self._key_count += len(node["properties"])
            if self._key_count > 64:
                self.is_strict_compatible = False

        # Add additionalProperties: false for strict mode
        if node.get("type") == "object" and self.strict:
            node["additionalProperties"] = False

        return node


class DatabricksProvider(Provider):
    """Databricks provider using OpenAI-compatible API.

    Uses the Databricks Foundation Model APIs which provide an OpenAI-compatible
    interface. Supports structured outputs via constrained decoding and function
    calling (in Public Preview).

    Reference: https://docs.databricks.com/aws/en/machine-learning/model-serving/score-foundation-models

    Handles Databricks-specific quirks:
    - Nested JSON error messages (DatabricksException wrapper)
    - No reasoning_effort parameter support
    - Schema limitations (no anyOf/oneOf/allOf/$ref/pattern, max 64 keys)
    - Function calling limited to 32 functions, 16 keys per schema
    """

    # Maximum limits per Databricks docs
    MAX_SCHEMA_KEYS = 64
    MAX_TOOLS = 32
    MAX_TOOL_SCHEMA_KEYS = 16

    def __init__(
        self,
        model: str,
        *,
        host: str | None = None,
        token: str | None = None,
        profile: ModelProfile | None = None,
    ):
        import os
        self.model = model
        self.profile = profile or get_profile(model)

        host = host or os.environ.get("DATABRICKS_HOST")
        token = token or os.environ.get("DATABRICKS_TOKEN")

        if not host or not token:
            raise ValueError(
                "Databricks host and token required. Set DATABRICKS_HOST and "
                "DATABRICKS_TOKEN environment variables or pass explicitly."
            )

        # Normalize host (remove https:// if present)
        if host.startswith("https://"):
            host = host[8:]
        if host.startswith("http://"):
            host = host[7:]

        base_url = f"https://{host}/serving-endpoints"

        self._client = AsyncOpenAI(
            api_key=token,
            base_url=base_url,
        )

    async def complete(
        self,
        messages: list[dict],
        *,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[StreamChunk], None] | None = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        # Databricks doesn't support reasoning_effort - strip it
        kwargs.pop("reasoning_effort", None)

        params: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }

        # Databricks supports json_schema response_format via constrained decoding
        if response_format:
            params["response_format"] = response_format

        # Validate tool limits
        if tools:
            if len(tools) > self.MAX_TOOLS:
                raise ValueError(
                    f"Databricks supports max {self.MAX_TOOLS} tools, got {len(tools)}"
                )
            params["tools"] = tools
        if tool_choice:
            params["tool_choice"] = tool_choice
        if max_tokens:
            params["max_tokens"] = max_tokens

        try:
            if stream and on_stream_chunk:
                return await self._stream_completion(params, on_stream_chunk)

            response = await self._client.chat.completions.create(**params)
        except Exception as e:
            # Extract clean error message from nested Databricks format
            raise self._wrap_error(e) from e

        usage = Usage(
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
        )

        content = response.choices[0].message.content or ""

        return CompletionResponse(
            content=content,
            usage=usage,
            raw_response=response,
            cost=self.calculate_cost(usage),
        )

    async def _stream_completion(
        self,
        params: dict,
        on_stream_chunk: Callable[[StreamChunk], None],
    ) -> CompletionResponse:
        """Stream completion with callback."""
        params["stream"] = True

        content_parts: list[str] = []
        usage: Usage | None = None

        async with await self._client.chat.completions.create(**params) as stream:
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    content_parts.append(text)
                    on_stream_chunk(StreamChunk(content=text, done=False))

                if hasattr(chunk, "usage") and chunk.usage:
                    usage = Usage(
                        input_tokens=chunk.usage.prompt_tokens,
                        output_tokens=chunk.usage.completion_tokens,
                        total_tokens=chunk.usage.total_tokens,
                    )

        full_content = "".join(content_parts)
        on_stream_chunk(StreamChunk(content="", done=True, usage=usage))

        if usage is None:
            usage = Usage(0, 0, 0)

        return CompletionResponse(
            content=full_content,
            usage=usage,
            raw_response=None,
            cost=self.calculate_cost(usage),
        )

    def _wrap_error(self, exc: Exception) -> Exception:
        """Extract clean error from Databricks nested JSON format.

        Databricks wraps errors in a DatabricksException with nested JSON:
        {"error_code":"BAD_REQUEST","message":"{\\"message\\":\\"Input is too long.\\"}"}
        """
        import re

        error_str = str(exc)

        # Try to extract nested JSON message
        # Pattern: {"error_code":"BAD_REQUEST","message":"{"message":"..."}"}
        json_match = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', error_str)
        if json_match:
            inner_msg = json_match.group(1)
            inner_msg = inner_msg.replace('\\"', '"').replace("\\\\", "\\")

            # Check for double-nested JSON
            inner_json = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', inner_msg)
            if inner_json:
                return LLMError(inner_json.group(1).replace('\\"', '"'))

            return LLMError(inner_msg)

        return exc
```

**OpenRouter Provider:**

```python
from openai import AsyncOpenAI

class OpenRouterProvider(Provider):
    """OpenRouter provider with model routing.

    Parses model strings like "openrouter/anthropic/claude-3-5-sonnet"
    and routes to appropriate profile.
    """

    # Provider prefix to profile mapping
    PROVIDER_PROFILES = {
        "openai": "openai",
        "anthropic": "anthropic",
        "google": "google",
        "mistralai": "mistral",
        "meta-llama": "llama",
        "deepseek": "deepseek",
        "cohere": "cohere",
    }

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        profile: ModelProfile | None = None,
    ):
        import os

        # Parse model string: "openrouter/provider/model" or "provider/model"
        self.original_model = model
        self.model, provider_hint = self._parse_model(model)

        # Get profile based on underlying provider
        if profile:
            self.profile = profile
        else:
            self.profile = self._get_routed_profile(provider_hint)

        api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OpenRouter API key required")

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": os.environ.get("OPENROUTER_APP_URL", ""),
                "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "penguiflow"),
            },
        )

    def _parse_model(self, model: str) -> tuple[str, str | None]:
        """Parse model string and extract provider hint."""
        parts = model.split("/")

        # Remove "openrouter" prefix if present
        if parts[0] == "openrouter":
            parts = parts[1:]

        if len(parts) >= 2:
            provider_hint = parts[0]
            model_name = "/".join(parts)
            return model_name, provider_hint

        return model, None

    def _get_routed_profile(self, provider_hint: str | None) -> ModelProfile:
        """Get profile based on routed provider."""
        if provider_hint and provider_hint in self.PROVIDER_PROFILES:
            profile_type = self.PROVIDER_PROFILES[provider_hint]

            if profile_type == "anthropic":
                return ModelProfile(
                    supports_json_schema_output=True,
                    schema_transformer_class=OpenRouterAnthropicTransformer,
                    strict_mode_default=False,
                )
            elif profile_type == "google":
                return ModelProfile(
                    supports_json_schema_output=True,
                    schema_transformer_class=OpenRouterGoogleTransformer,
                )
            elif profile_type == "openai":
                return ModelProfile(
                    supports_json_schema_output=True,
                    schema_transformer_class=OpenAIJsonSchemaTransformer,
                )

        # Default profile for unknown providers
        return ModelProfile(
            supports_json_schema_output=False,
            supports_json_object_output=True,
            default_output_mode="prompted",
        )

    async def complete(
        self,
        messages: list[dict],
        *,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[StreamChunk], None] | None = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }

        if response_format:
            params["response_format"] = response_format
        if tools:
            params["tools"] = tools
        if tool_choice:
            params["tool_choice"] = tool_choice
        if max_tokens:
            params["max_tokens"] = max_tokens

        if stream and on_stream_chunk:
            return await self._stream_completion(params, on_stream_chunk)

        response = await self._client.chat.completions.create(**params)

        usage = Usage(
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
        )

        content = response.choices[0].message.content or ""

        return CompletionResponse(
            content=content,
            usage=usage,
            raw_response=response,
            cost=self.calculate_cost(usage),
        )


class OpenRouterGoogleTransformer(GoogleJsonSchemaTransformer):
    """Google transformer for OpenRouter with additional compatibility fixes.

    OpenRouter's Google compatibility layer has limitations with modern JSON Schema.
    """

    def __init__(self, schema: dict[str, Any], *, strict: bool = True):
        super().__init__(schema, strict=strict)
        self._prefer_inlined = True  # Inline all defs

    def _transform_node(self, node: dict[str, Any]) -> dict[str, Any]:
        node = super()._transform_node(node)

        # Additional OpenRouter-specific: simplify nullable unions
        if "anyOf" in node and len(node["anyOf"]) == 2:
            types = [n.get("type") for n in node["anyOf"]]
            if "null" in types:
                non_null = [n for n in node["anyOf"] if n.get("type") != "null"][0]
                node.clear()
                node.update(non_null)
                node["nullable"] = True

        return node
```

#### 7. LLMClient (Main Interface)

The unified client that orchestrates everything:

```python
from typing import Callable, Any
from pydantic import BaseModel

class LLMClient:
    """Main LLM client interface for PenguiFlow.

    Orchestrates provider selection, output mode, retry, and streaming.
    """

    def __init__(
        self,
        model: str,
        *,
        temperature: float = 0.0,
        max_retries: int = 3,
        timeout_s: float = 60.0,
        streaming_enabled: bool = False,
        on_stream_chunk: Callable[[StreamChunk], None] | None = None,
        # Provider-specific config
        api_key: str | None = None,
        base_url: str | None = None,
        **provider_kwargs: Any,
    ):
        self.model = model
        self.temperature = temperature
        self.max_retries = max_retries
        self.timeout_s = timeout_s
        self.streaming_enabled = streaming_enabled
        self.on_stream_chunk = on_stream_chunk

        # Create provider
        self._provider = create_provider(
            model,
            api_key=api_key,
            base_url=base_url,
            **provider_kwargs,
        )

        # Select output strategy based on profile
        self._output_mode = select_output_mode(
            self._provider.profile,
            {},  # Will be refined per-call
        )

    @property
    def profile(self) -> ModelProfile:
        return self._provider.profile

    async def call(
        self,
        messages: list[dict],
        response_model: type[BaseModel],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool | None = None,
        on_stream_chunk: Callable[[StreamChunk], None] | None = None,
    ) -> tuple[BaseModel, float]:
        """Execute a structured output call.

        Args:
            messages: The conversation messages.
            response_model: Pydantic model for the expected response.
            temperature: Override default temperature.
            max_tokens: Maximum tokens to generate.
            stream: Override default streaming setting.
            on_stream_chunk: Override default stream callback.

        Returns:
            Tuple of (parsed response, cost in USD).
        """
        # Select output strategy
        schema = response_model.model_json_schema()
        output_mode = select_output_mode(self._provider.profile, schema)
        strategy = self._get_strategy(output_mode)

        # Build retry config
        retry_config = RetryConfig(
            max_retries=self.max_retries,
            retry_on_validation=True,
            retry_on_parse=True,
        )

        # Determine streaming
        should_stream = stream if stream is not None else self.streaming_enabled
        chunk_callback = on_stream_chunk or self.on_stream_chunk

        # Execute with retry
        result = await call_with_retry(
            self._provider,
            messages,
            response_model,
            strategy,
            config=retry_config,
        )

        # Get last call cost (simplified - full impl would track across retries)
        cost = 0.0  # TODO: Accumulate cost across retries

        return result, cost

    async def call_raw(
        self,
        messages: list[dict],
        *,
        response_format: dict | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool | None = None,
        on_stream_chunk: Callable[[StreamChunk], None] | None = None,
    ) -> CompletionResponse:
        """Execute a raw completion call without structured output."""
        should_stream = stream if stream is not None else self.streaming_enabled
        chunk_callback = on_stream_chunk or self.on_stream_chunk

        return await self._provider.complete(
            messages,
            response_format=response_format,
            temperature=temperature or self.temperature,
            max_tokens=max_tokens,
            stream=should_stream,
            on_stream_chunk=chunk_callback,
        )

    def _get_strategy(self, mode: OutputMode) -> Any:
        """Get output strategy for the given mode."""
        if mode == OutputMode.TOOL:
            return ToolOutputStrategy()
        elif mode == OutputMode.NATIVE:
            return NativeOutputStrategy()
        else:
            return PromptedOutputStrategy()


def create_provider(
    model: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs: Any,
) -> Provider:
    """Create a provider instance based on model string."""
    # Detect provider from model prefix
    if model.startswith("openrouter/") or "/" in model and not model.startswith(("gpt", "claude", "gemini")):
        return OpenRouterProvider(model, api_key=api_key, **kwargs)

    if model.startswith("gpt") or model.startswith("o1") or model.startswith("o3"):
        return OpenAIProvider(model, api_key=api_key, base_url=base_url, **kwargs)

    if model.startswith("claude"):
        return AnthropicProvider(model, api_key=api_key, **kwargs)

    if model.startswith("gemini"):
        return GoogleProvider(model, api_key=api_key, **kwargs)

    if model.startswith("anthropic.") or model.startswith("amazon.") or model.startswith("meta."):
        return BedrockProvider(model, **kwargs)

    if model.startswith("databricks"):
        return DatabricksProvider(model, **kwargs)

    # Default to OpenAI-compatible
    return OpenAIProvider(model, api_key=api_key, base_url=base_url, **kwargs)


def create_client(
    model: str,
    *,
    temperature: float = 0.0,
    max_retries: int = 3,
    timeout_s: float = 60.0,
    streaming_enabled: bool = False,
    on_stream_chunk: Callable[[StreamChunk], None] | None = None,
    **provider_kwargs: Any,
) -> LLMClient:
    """Create an LLMClient instance.

    This is the main entry point for the LLM layer.

    Args:
        model: Model identifier (e.g., "gpt-4o", "claude-3-5-sonnet",
               "openrouter/anthropic/claude-3-5-sonnet").
        temperature: Sampling temperature (0.0 for deterministic).
        max_retries: Maximum retry attempts on validation failure.
        timeout_s: Request timeout in seconds.
        streaming_enabled: Enable streaming by default.
        on_stream_chunk: Default streaming callback.
        **provider_kwargs: Provider-specific configuration.

    Returns:
        Configured LLMClient instance.
    """
    return LLMClient(
        model,
        temperature=temperature,
        max_retries=max_retries,
        timeout_s=timeout_s,
        streaming_enabled=streaming_enabled,
        on_stream_chunk=on_stream_chunk,
        **provider_kwargs,
    )
```

### Cost Tracking

Maintain pricing tables for supported models:

```python
# penguiflow/llm/cost.py

PRICING: dict[str, tuple[float, float]] = {
    # OpenAI (per 1K tokens: input, output)
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4-turbo": (0.01, 0.03),
    "o1": (0.015, 0.06),
    "o1-mini": (0.003, 0.012),

    # Anthropic
    "claude-3-5-sonnet": (0.003, 0.015),
    "claude-3-5-haiku": (0.0008, 0.004),
    "claude-3-opus": (0.015, 0.075),

    # Google
    "gemini-2.0-flash": (0.0, 0.0),  # Free tier
    "gemini-1.5-pro": (0.00125, 0.005),
    "gemini-1.5-flash": (0.000075, 0.0003),

    # Bedrock (same as base models)
    "anthropic.claude-3-5-sonnet": (0.003, 0.015),
    "anthropic.claude-3-5-haiku": (0.0008, 0.004),
    "amazon.nova-pro": (0.0008, 0.0032),
    "amazon.nova-lite": (0.00006, 0.00024),

    # Databricks (varies by deployment)
    "databricks-meta-llama-3-1-70b-instruct": (0.001, 0.001),
    "databricks-dbrx-instruct": (0.00075, 0.00225),
}


def get_pricing(model: str) -> tuple[float, float]:
    """Get pricing for a model (input, output per 1K tokens)."""
    # Exact match
    if model in PRICING:
        return PRICING[model]

    # Prefix match for versioned models
    for key, price in PRICING.items():
        if model.startswith(key):
            return price

    # Default: unknown pricing
    return (0.0, 0.0)


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost for a completion."""
    input_price, output_price = get_pricing(model)
    return (input_tokens * input_price / 1000) + (output_tokens * output_price / 1000)
```

## Implementation Plan

### Phase 1: Core Infrastructure (Week 1-2)

| Task | Description | Files |
|------|-------------|-------|
| **1.1** | Create module structure | `penguiflow/llm/__init__.py`, etc. |
| **1.2** | Implement `ModelProfile` and registry | `profiles/__init__.py` |
| **1.3** | Implement base `JsonSchemaTransformer` | `schema/transformer.py` |
| **1.4** | Implement `Provider` ABC | `providers/base.py` |
| **1.5** | Implement `LLMClient` skeleton | `client.py` |
| **1.6** | Add error types | `errors.py` |

### Phase 2: First Providers (Week 2-3)

| Task | Description | Files |
|------|-------------|-------|
| **2.1** | OpenAI provider + transformer | `providers/openai.py`, `schema/openai.py`, `profiles/openai.py` |
| **2.2** | Anthropic provider + transformer | `providers/anthropic.py`, `schema/anthropic.py`, `profiles/anthropic.py` |
| **2.3** | Google provider + transformer | `providers/google.py`, `schema/google.py`, `profiles/google.py` |
| **2.4** | Bedrock provider + transformer | `providers/bedrock.py`, `schema/bedrock.py`, `profiles/bedrock.py` |
| **2.5** | Databricks provider | `providers/databricks.py`, `profiles/databricks.py` |
| **2.6** | OpenRouter provider + routing | `providers/openrouter.py`, `profiles/openrouter.py` |

### Phase 3: Output Modes & Retry (Week 3-4)

| Task | Description | Files |
|------|-------------|-------|
| **3.1** | Tool output strategy | `output/tool.py` |
| **3.2** | Native output strategy | `output/native.py` |
| **3.3** | Prompted output strategy | `output/prompted.py` |
| **3.4** | Retry mechanism | `retry.py` |
| **3.5** | Output mode selection | `output/__init__.py` |

### Phase 4: Streaming & Cost (Week 4)

| Task | Description | Files |
|------|-------------|-------|
| **4.1** | Streaming with callbacks | `streaming.py` |
| **4.2** | Cost tracking tables | `cost.py` |
| **4.3** | Integrate streaming into providers | All provider files |

### Phase 5: Integration & Migration (Week 5)

| Task | Description | Files |
|------|-------------|-------|
| **5.1** | Implement `JSONLLMClient` protocol compatibility | `protocol.py` |
| **5.2** | Update `_LiteLLMJSONClient` → `LLMClient` adapter | `planner/llm.py` |
| **5.3** | Update templates | `templates/*.jinja` |
| **5.4** | Update tests | `tests/test_llm_*.py` |
| **5.5** | Remove LiteLLM dependency | `pyproject.toml` |
| **5.6** | Migration documentation | `docs/MIGRATION_LLM.md` |

### Phase 6: Additional Providers (Future)

| Provider | Priority | Notes |
|----------|----------|-------|
| Azure OpenAI | Medium | OpenAI-compatible with Azure auth |
| Groq | Medium | Fast inference |
| Mistral | Low | Growing usage |
| Together | Low | Open-source models |
| Ollama | Low | Local development |

## Dependencies

### Required (Core)

```toml
[project.dependencies]
pydantic = ">=2.8.0"
httpx = ">=0.27.0"  # Async HTTP client
```

### Optional (Per Provider)

```toml
[project.optional-dependencies]
llm-openai = ["openai>=1.50.0"]
llm-anthropic = ["anthropic>=0.40.0"]
llm-google = ["google-genai>=1.0.0"]
llm-bedrock = ["boto3>=1.35.0"]
llm-all = [
    "openai>=1.50.0",
    "anthropic>=0.40.0",
    "google-genai>=1.0.0",
    "boto3>=1.35.0",
]
```

## Testing Strategy

### Unit Tests

```python
# tests/test_llm_schema_transformers.py
def test_openai_transformer_adds_additional_properties():
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    transformer = OpenAIJsonSchemaTransformer(schema, strict=True)
    result = transformer.transform()
    assert result["additionalProperties"] is False
    assert result["required"] == ["name"]

def test_anthropic_transformer_relocates_constraints():
    schema = {"type": "string", "minLength": 1, "maxLength": 100}
    transformer = AnthropicJsonSchemaTransformer(schema)
    result = transformer.transform()
    assert "minLength" not in result
    assert "minLength: 1" in result.get("description", "")

# tests/test_llm_providers.py
@pytest.mark.asyncio
async def test_openai_provider_completion():
    provider = OpenAIProvider("gpt-4o-mini")
    response = await provider.complete([{"role": "user", "content": "Hi"}])
    assert response.content
    assert response.usage.total_tokens > 0

# tests/test_llm_output_modes.py
@pytest.mark.asyncio
async def test_tool_output_strategy():
    strategy = ToolOutputStrategy()
    request = strategy.build_request(
        [{"role": "user", "content": "Extract name"}],
        PersonModel,
        get_profile("gpt-4o"),
    )
    assert "tools" in request
    assert request["tools"][0]["function"]["name"] == "structured_output"

# tests/test_llm_retry.py
@pytest.mark.asyncio
async def test_retry_on_validation_failure():
    # Mock provider that returns invalid JSON first, then valid
    ...
```

### Integration Tests

```python
# tests/integration/test_llm_providers.py
@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_structured_output():
    client = create_client("gpt-4o-mini")
    result, cost = await client.call(
        [{"role": "user", "content": "What is 2+2? Return as JSON."}],
        MathResult,
    )
    assert result.answer == 4

@pytest.mark.integration
@pytest.mark.asyncio
async def test_anthropic_structured_output():
    client = create_client("claude-3-5-haiku")
    result, cost = await client.call(
        [{"role": "user", "content": "What is 2+2? Return as JSON."}],
        MathResult,
    )
    assert result.answer == 4
```

## Migration Guide

### For Users

```python
# Before (LiteLLM)
from penguiflow.planner.llm import _LiteLLMJSONClient

client = _LiteLLMJSONClient(
    "openrouter/anthropic/claude-3-5-sonnet",
    temperature=0.0,
    json_schema_mode=True,
)

# After (Native)
from penguiflow.llm import create_client

client = create_client(
    "openrouter/anthropic/claude-3-5-sonnet",
    temperature=0.0,
)
```

### For Templates

Templates using `config.py.jinja` will be updated to import from `penguiflow.llm` instead of `penguiflow.planner.llm`.

## Alternatives Considered

### 1. Keep LiteLLM

**Pros:** Already working, broad provider support
**Cons:** Heavy dependency, limited control, schema handling in our code anyway

**Decision:** Rejected. The maintenance burden of working around LiteLLM quirks exceeds the cost of native implementation.

### 2. Use PydanticAI Directly

**Pros:** Well-maintained, Pydantic team support
**Cons:** Full agent framework (overkill), different API patterns, async iterator streaming

**Decision:** Rejected. Extract patterns, don't adopt the framework.

### 3. Use Instructor

**Pros:** Lightweight, Pydantic-focused
**Cons:** Still wraps other SDKs, limited provider control, no cost tracking

**Decision:** Rejected. We need lower-level control.

## References

- [PydanticAI Source](https://github.com/pydantic/pydantic-ai) (MIT License)
- [OpenAI Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs)
- [Anthropic Tool Use](https://docs.anthropic.com/en/docs/tool-use)
- [AWS Bedrock Converse API](https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html)
- [Databricks Foundation Model APIs](https://docs.databricks.com/en/machine-learning/foundation-models/index.html)

## Appendix: PydanticAI Code References

Key files studied from PydanticAI (MIT License):

| File | What We Extracted |
|------|-------------------|
| `profiles/__init__.py` | `ModelProfile` dataclass pattern |
| `profiles/openai.py` | OpenAI transformer, capability flags |
| `profiles/anthropic.py` | Anthropic strict mode handling |
| `profiles/google.py` | Google const→enum conversion |
| `profiles/amazon.py` | Bedrock inline defs pattern |
| `providers/openrouter.py` | Model routing, provider mapping |
| `providers/bedrock.py` | AWS auth patterns, converse API |
| `_json_schema.py` | Recursive transformer base |
| `_output.py` | Output mode strategies |
| `exceptions.py` | `ModelRetry` pattern |
