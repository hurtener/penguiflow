# RFC: Native LLM Layer

> **Status**: Proposed
> **Created**: 2026-01-12
> **Author**: PenguiFlow Team

## Summary

This RFC proposes replacing the LiteLLM dependency with a native, penguiflow-owned LLM abstraction layer. The design extracts proven patterns from PydanticAI (schema transformers, output modes, retry mechanisms) while maintaining penguiflow's existing strengths (streaming callbacks, cost/usage tracking, cancellation propagation, and error recovery).

This revised design explicitly addresses key cross-provider realities:

- Structured output mechanisms are **provider-native** (not “OpenAI-shaped” everywhere)
- Requests and responses are normalized via **typed internal message/tool/event models** (no raw `dict` plumbing)
- Schema transformation produces a **schema plan** with compatibility signals and graceful degradation
- Retry/timeout/cancellation/cost are part of the **core contract**, not incidental implementation details

> Note: Code snippets in this RFC are illustrative pseudocode. Provider SDKs differ materially in payload shapes (tools, content blocks, streaming events, usage reporting), so real implementations must use provider-specific adapters.

## Scope (v1) and Non-Goals

### Goals

- Provide a stable internal LLM interface used by the planner/runtime (and a compatibility adapter for `JSONLLMClient`)
- Support structured outputs with automatic fallback across modes: provider-native → tools → prompted
- Preserve streaming callbacks, cancellation propagation, and trajectory logging hooks
- Standardize error taxonomy (retryability, user-facing messages, raw payload retention)
- Unify cost/usage accounting across attempts (including retries)

### Non-Goals (v1)

- Perfect feature parity across all providers for every capability (e.g., identical “native” structured output semantics)
- Comprehensive provider coverage beyond the initial set (OpenAI, Anthropic, Google, Bedrock, Databricks, OpenRouter)
- Implementing a full agent framework (planning/memory/tools orchestration remains in existing PenguiFlow layers)

### Target Support Matrix (v1)

This is the intended “works as designed” surface for v1; anything beyond this is best-effort:

- OpenAI: `native` (schema-guided), `tools`, `prompted`, streaming + usage
- Anthropic: `native` (tool-use), `tools`, `prompted`, streaming + usage
- Bedrock: `native` (tool-use via Converse), `tools`, `prompted`, usage (streaming best-effort)
- Databricks: `native` (constrained decoding `json_schema`), `tools` (preview limits), `prompted`, usage (streaming best-effort)
- OpenRouter: OpenAI-compatible best-effort (depends on routed provider)

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
│                               LLMClient                                  │
│  (Orchestrates: routing, mode selection, schema planning, retry,         │
│   streaming, cancellation, timeouts, cost/usage accounting)              │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
         ┌───────────────────────────┼───────────────────────────┐
         │                           │                           │
         v                           v                           v
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│   Mode Engine   │       │  Schema Plan    │       │    Retry +       │
│ Native/Tools/   │       │ Transform +     │       │ Error Taxonomy   │
│ Prompted        │       │ Compatibility   │       │ + Backoff        │
└─────────────────┘       └─────────────────┘       └─────────────────┘
                 │                 │                           │
                 └───────────┬─────┴───────────┬──────────────┘
                             v                 v
                    ┌─────────────────┐  ┌─────────────────┐
                    │ Typed Requests  │  │   Routing        │
                    │ Messages/Tools/ │  │ ProviderRegistry │
                    │ Stream Events   │  │ + ModelProfiles  │
                    └────────┬────────┘  └────────┬────────┘
                             │                    │
                             v                    v
                          ┌────────────────────────────────┐
                          │            Provider            │
                          │  (Direct SDK + per-provider    │
                          │   request/response adapters)   │
                          └───────────────┬────────────────┘
                                          │
              ┌──────────┬──────────┬─────┼─────┬──────────┬──────────┐
              v          v          v     v     v          v          v
          ┌───────┐  ┌────────┐  ┌───────┐ ┌───────┐  ┌────────┐  ┌─────┐
          │OpenAI │  │Anthropic│ │ Google│ │Bedrock│  │Databricks│ │OpenR│
          └───────┘  └────────┘  └───────┘ └───────┘  └────────┘  └─────┘
```

### Module Structure

```
penguiflow/llm/
├── __init__.py              # Public API: create_client(), LLMClient
├── protocol.py              # JSONLLMClient protocol (existing interface)
├── client.py                # LLMClient implementation
├── types.py                 # Typed requests/messages/tools/events
├── routing.py               # ProviderRegistry + model string parsing
├── pricing.py               # Pricing table + cost calculation
├── errors.py                # LLMError taxonomy + provider error mapping
├── telemetry.py             # Hooks: attempts, retries, usage, cost
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
│   ├── plan.py              # SchemaPlan: transformed schema + compatibility
│   ├── openai.py            # OpenAI: additionalProperties, strict mode
│   ├── anthropic.py         # Anthropic: constraint relocation
│   ├── google.py            # Google: const→enum, format in description
│   └── bedrock.py           # Bedrock: inline defs transformer
│
├── output/
│   ├── __init__.py          # OutputMode enum, choose_output_mode()
│   ├── tool.py              # Tool-based structured output (portable)
│   ├── native.py            # Provider-native structured output (adapter-driven)
│   └── prompted.py          # Schema in prompt + parse/retry (fallback)
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
└── errors.py                # LLMError taxonomy
```

### Core Components

#### 0. Typed Request/Response Model (No Raw `dict` Plumbing)

One root cause of “provider quirks leaking everywhere” is pushing raw request/response dictionaries through the stack. v1 should define a small typed core model that all providers adapt to/from.

Key properties:

- Provider adapters handle SDK-specific payload shapes (Anthropic content blocks, Google parts, Bedrock converse formats)
- Output strategies operate on typed messages/tools/events, not provider-specific JSON
- Streaming and cancellation can be standardized at the interface level

Illustrative types:

```python
from dataclasses import dataclass
from typing import Any, Callable, Literal

Role = Literal["system", "user", "assistant", "tool"]

@dataclass(frozen=True)
class TextPart:
    text: str

@dataclass(frozen=True)
class ToolCallPart:
    name: str
    arguments_json: str  # raw JSON string for faithful round-trip
    call_id: str | None = None

@dataclass(frozen=True)
class ToolResultPart:
    name: str
    result_json: str
    call_id: str | None = None

ContentPart = TextPart | ToolCallPart | ToolResultPart

@dataclass(frozen=True)
class LLMMessage:
    role: Role
    parts: list[ContentPart]

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    json_schema: dict[str, Any]

@dataclass(frozen=True)
class StructuredOutputSpec:
    name: str
    json_schema: dict[str, Any]
    strict: bool

@dataclass(frozen=True)
class LLMRequest:
    model: str
    messages: list[LLMMessage]
    tools: list[ToolSpec] | None = None
    tool_choice: str | None = None  # tool name or None
    structured_output: StructuredOutputSpec | None = None
    temperature: float = 0.0
    max_tokens: int | None = None
    extra: dict[str, Any] | None = None  # provider-specific passthrough (sanitized)

@dataclass(frozen=True)
class StreamEvent:
    # A minimal common denominator; providers can emit richer events internally.
    delta_text: str | None = None
    usage: "Usage" | None = None
    done: bool = False

StreamCallback = Callable[[StreamEvent], None]
```

#### 1. ModelProfile

Declarative capability description per model, adapted from PydanticAI:

```python
from dataclasses import dataclass
from typing import Literal

@dataclass
class ModelProfile:
    """Describes capabilities and configuration for a specific model."""

    # Output capabilities
    supports_schema_guided_output: bool = False  # Provider-native schema-guided structured output
    supports_json_only_output: bool = True       # Provider-native “JSON only” mode (if supported)
    supports_tools: bool = True                  # Tool/function calling
    supports_reasoning: bool = False             # Native reasoning (o1, o3, deepseek-r1)

    # Output mode selection
    default_output_mode: Literal["native", "tools", "prompted"] = "native"

    # Provider-native structured output mechanism (used by OutputMode.NATIVE)
    native_structured_kind: Literal[
        "openai_response_format",
        "databricks_constrained_decoding",
        "anthropic_tool_use",
        "google_response_schema",
        "bedrock_tool_use",
        "openai_compatible_tools",
        "unknown",
    ] = "unknown"

    # Schema transformation
    schema_transformer_class: type["JsonSchemaTransformer"] | None = None

    # Reasoning configuration
    reasoning_effort_param: str | None = None  # Parameter name if supported
    thinking_tags: tuple[str, str] | None = None  # e.g., ("<think>", "</think>")

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
        supports_schema_guided_output=True,
        schema_transformer_class=OpenAIJsonSchemaTransformer,
        native_structured_kind="openai_response_format",
    ),
    "gpt-4o-mini": ModelProfile(
        supports_schema_guided_output=True,
        schema_transformer_class=OpenAIJsonSchemaTransformer,
        native_structured_kind="openai_response_format",
    ),
    "o1": ModelProfile(
        supports_schema_guided_output=True,
        supports_reasoning=True,
        reasoning_effort_param="reasoning_effort",
        schema_transformer_class=OpenAIJsonSchemaTransformer,
        native_structured_kind="openai_response_format",
    ),

    # Anthropic
    "claude-3-5-sonnet": ModelProfile(
        supports_schema_guided_output=True,
        schema_transformer_class=AnthropicJsonSchemaTransformer,
        strict_mode_default=False,  # Lossy transformation
        native_structured_kind="anthropic_tool_use",
    ),
    "claude-3-5-haiku": ModelProfile(
        supports_schema_guided_output=True,
        schema_transformer_class=AnthropicJsonSchemaTransformer,
        strict_mode_default=False,
        native_structured_kind="anthropic_tool_use",
    ),

    # Google
    "gemini-2.0-flash": ModelProfile(
        supports_schema_guided_output=True,
        schema_transformer_class=GoogleJsonSchemaTransformer,
        native_structured_kind="google_response_schema",
    ),

    # Bedrock (via Anthropic)
    "anthropic.claude-3-5-sonnet": ModelProfile(
        supports_schema_guided_output=True,
        schema_transformer_class=BedrockJsonSchemaTransformer,
        native_structured_kind="bedrock_tool_use",
    ),

    # Databricks Foundation Model APIs
    # Supports structured outputs via constrained decoding
    # Limitations: no anyOf/oneOf/allOf/$ref/pattern, max 64 keys
    # Reference: https://docs.databricks.com/aws/en/machine-learning/model-serving/structured-outputs
    "databricks-meta-llama-3-1-70b-instruct": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,  # Public Preview, max 32 tools
        schema_transformer_class=DatabricksJsonSchemaTransformer,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
    ),
    "databricks-meta-llama-3-3-70b-instruct": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        schema_transformer_class=DatabricksJsonSchemaTransformer,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
    ),
    "databricks-dbrx-instruct": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        schema_transformer_class=DatabricksJsonSchemaTransformer,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
    ),
    # Anthropic on Databricks - only supports json_schema, NOT json_object
    "databricks-claude-3-5-sonnet": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=False,  # Claude on Databricks only supports schema-guided mode
        supports_tools=True,
        schema_transformer_class=DatabricksJsonSchemaTransformer,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
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

#### 2. Schema Planning + JsonSchemaTransformer

Provider constraints vary widely (e.g., Databricks forbids `$ref` and `anyOf`; some providers ignore constraints; others require `additionalProperties=false`). To avoid pushing these decisions into ad-hoc code paths, the LLM layer should compute a **SchemaPlan** for every structured call.

The plan is used to:

- Transform and (when safe) inline `$ref`/`$defs`
- Count/validate schema complexity against provider limits
- Decide whether “native structured output” is viable, or whether to degrade to tools/prompted

Illustrative plan type:

```python
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class SchemaPlan:
    requested_schema: dict[str, Any]
    transformed_schema: dict[str, Any]
    strict_requested: bool
    strict_applied: bool
    compatible_with_native: bool
    compatible_with_tools: bool
    reasons: list[str]
    estimated_total_keys: int | None = None
```

Base class for recursive schema transformation (adapted from PydanticAI). Note that `$ref` support is explicit and provider-tunable: some providers require inlining, some can keep `$ref`, and some must reject recursion.

```python
from abc import ABC, abstractmethod
from typing import Any

class JsonSchemaTransformer(ABC):
    """Base class for provider-specific JSON schema transformations.

    Walks the schema recursively, applying transformations at each level.
    Handles recursive walking, optional $ref inlining, and keyword stripping.
    """

    def __init__(self, schema: dict[str, Any], *, strict: bool = True):
        self.original_schema = schema
        self.strict = strict
        self.is_strict_compatible = True
        self._prefer_inline_refs = False
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

        if self._prefer_inline_refs and ref.startswith("#/$defs/"):
            def_name = ref.removeprefix("#/$defs/")
            defs = self.original_schema.get("$defs", {})
            if def_name in defs:
                self._refs_stack.append(ref)
                inlined = self._walk(defs[def_name].copy())
                self._refs_stack.pop()
                return inlined

        return node  # Keep as $ref when allowed by provider

    def _finalize(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Final cleanup after transformation."""
        return schema
```

Schema planning ties transformers + provider limits to mode selection:

```python
def estimate_object_key_count(schema: dict[str, Any]) -> int:
    """Best-effort count of object properties keys (heuristic)."""
    count = 0
    if schema.get("type") == "object" and isinstance(schema.get("properties"), dict):
        count += len(schema["properties"])
        for v in schema["properties"].values():
            if isinstance(v, dict):
                count += estimate_object_key_count(v)
    if isinstance(schema.get("items"), dict):
        count += estimate_object_key_count(schema["items"])
    for k in ("anyOf", "oneOf", "allOf"):
        if isinstance(schema.get(k), list):
            for v in schema[k]:
                if isinstance(v, dict):
                    count += estimate_object_key_count(v)
    if isinstance(schema.get("$defs"), dict):
        for v in schema["$defs"].values():
            if isinstance(v, dict):
                count += estimate_object_key_count(v)
    return count


def plan_schema(profile: ModelProfile, schema: dict[str, Any], *, mode: "OutputMode") -> SchemaPlan:
    strict_requested = profile.strict_mode_default and mode in (OutputMode.NATIVE, OutputMode.TOOLS)

    transformed = schema
    reasons: list[str] = []
    strict_applied = strict_requested

    if profile.schema_transformer_class:
        transformer = profile.schema_transformer_class(schema, strict=strict_requested)
        transformed = transformer.transform()
        if strict_requested and not transformer.is_strict_compatible:
            strict_applied = False
            reasons.append("Schema required lossy transformations; strict disabled.")

    estimated_keys = estimate_object_key_count(transformed)

    # Provider-specific viability checks (examples)
    compatible_with_native = True
    compatible_with_tools = True

    if profile.native_structured_kind == "databricks_constrained_decoding":
        if estimated_keys > 64:
            compatible_with_native = False
            reasons.append("Databricks: schema exceeds 64-key limit.")

    return SchemaPlan(
        requested_schema=schema,
        transformed_schema=transformed,
        strict_requested=strict_requested,
        strict_applied=strict_applied,
        compatible_with_native=compatible_with_native,
        compatible_with_tools=compatible_with_tools,
        reasons=reasons,
        estimated_total_keys=estimated_keys,
    )
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

#### 3. Structured Output Modes (Provider-Native, With Degradation)

Structured output is implemented as a three-mode ladder, with deterministic fallback:

1. `native`: provider’s best structured mechanism (schema-guided decoding where available, or provider-native tool use)
2. `tools`: force tool calling (portable, often higher fidelity than prompted JSON)
3. `prompted`: inject schema into prompt and validate/retry

Key principle: “native” is **not** a single wire format. It is expressed differently per provider via adapters, driven by `ModelProfile.native_structured_kind`.

Illustrative API:

```python
import json
from enum import Enum
from typing import Any
from pydantic import BaseModel


class OutputMode(Enum):
    NATIVE = "native"
    TOOLS = "tools"
    PROMPTED = "prompted"


def choose_output_mode(profile: ModelProfile, schema: dict[str, Any]) -> tuple[OutputMode, SchemaPlan]:
    """Choose mode by planning schema compatibility per mode."""
    preference: list[OutputMode] = [
        OutputMode(profile.default_output_mode),
        OutputMode.NATIVE,
        OutputMode.TOOLS,
        OutputMode.PROMPTED,
    ]

    seen: set[OutputMode] = set()
    ordered = [m for m in preference if not (m in seen or seen.add(m))]

    last_plan: SchemaPlan | None = None
    for mode in ordered:
        plan = plan_schema(profile, schema, mode=mode)  # returns SchemaPlan
        last_plan = plan
        if mode == OutputMode.NATIVE and profile.supports_schema_guided_output and plan.compatible_with_native:
            return mode, plan
        if mode == OutputMode.TOOLS and profile.supports_tools and plan.compatible_with_tools:
            return mode, plan
        if mode == OutputMode.PROMPTED:
            return mode, plan

    # Defensive fallback
    assert last_plan is not None
    return OutputMode.PROMPTED, last_plan


class NativeOutputStrategy:
    """Provider-native structured output (adapter-driven)."""

    def build_request(
        self,
        model: str,
        messages: list[LLMMessage],
        response_model: type[BaseModel],
        profile: ModelProfile,
        plan: SchemaPlan,
    ) -> LLMRequest:
        return LLMRequest(
            model=model,
            messages=messages,
            structured_output=StructuredOutputSpec(
                name=response_model.__name__,
                json_schema=plan.transformed_schema,
                strict=plan.strict_applied,
            ),
            temperature=0.0,
        )

    def parse_response(self, response: CompletionResponse, response_model: type[BaseModel]) -> BaseModel:
        text = extract_text(response.message)
        return response_model.model_validate_json(text)


class ToolsOutputStrategy:
    """Force tool calling to return structured output."""

    TOOL_NAME = "structured_output"

    def build_request(
        self,
        model: str,
        messages: list[LLMMessage],
        response_model: type[BaseModel],
        profile: ModelProfile,
        plan: SchemaPlan,
    ) -> LLMRequest:
        return LLMRequest(
            model=model,
            messages=messages,
            tools=[
                ToolSpec(
                    name=self.TOOL_NAME,
                    description="Return structured data",
                    json_schema=plan.transformed_schema,
                )
            ],
            tool_choice=self.TOOL_NAME,
            temperature=0.0,
        )

    def parse_response(self, response: CompletionResponse, response_model: type[BaseModel]) -> BaseModel:
        call = extract_single_tool_call(response.message, expected_name=self.TOOL_NAME)
        return response_model.model_validate_json(call.arguments_json)


class PromptedOutputStrategy:
    """Prompt injection + parse/retry (last resort)."""

    TEMPLATE = """You must respond with a valid JSON object matching this schema:

{schema}

Do not include any text before or after the JSON. Only output the JSON object."""

    def build_request(
        self,
        model: str,
        messages: list[LLMMessage],
        response_model: type[BaseModel],
        profile: ModelProfile,
        plan: SchemaPlan,
    ) -> LLMRequest:
        schema_str = json.dumps(plan.transformed_schema, indent=2)
        injected = LLMMessage(role="system", parts=[TextPart(text=self.TEMPLATE.format(schema=schema_str))])
        return LLMRequest(
            model=model,
            messages=[injected, *messages],
            temperature=0.0,
        )

    def parse_response(self, response: CompletionResponse, response_model: type[BaseModel]) -> BaseModel:
        text = strip_markdown_fences(extract_text(response.message))
        return response_model.model_validate_json(text)
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


def format_retry_message(error: ValidationError | ValidationRetry) -> LLMMessage:
    """Format validation error as a user message for the LLM."""
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

    return LLMMessage(role="user", parts=[TextPart(text=content)])


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_retries: int = 3
    retry_on_validation: bool = True
    retry_on_parse: bool = True
    retry_on_provider_errors: bool = True


async def call_with_retry(
    provider: "Provider",
    base_messages: list[LLMMessage],
    response_model: type[BaseModel],
    output_strategy: Any,
    *,
    config: RetryConfig | None = None,
    on_retry: Callable[[int, Exception], None] | None = None,
    timeout_s: float | None = None,
    cancel: CancelToken | None = None,
    stream: bool = False,
    on_stream_event: StreamCallback | None = None,
    pricing: Callable[[str, int, int], float] = calculate_cost,
    build_request: Callable[[list[LLMMessage]], LLMRequest] | None = None,
) -> tuple[BaseModel, float]:
    """Execute LLM call with automatic retry and cost accounting."""
    if config is None:
        config = RetryConfig()

    if build_request is None:
        raise ValueError("build_request is required")

    working_messages = list(base_messages)
    total_cost = 0.0

    for attempt in range(config.max_retries + 1):
        try:
            request = build_request(working_messages)
            response = await provider.complete(
                request,
                timeout_s=timeout_s,
                cancel=cancel,
                stream=stream,
                on_stream_event=on_stream_event,
            )

            # Cost is charged per attempt; accumulate across retries.
            total_cost += pricing(
                request.model,
                response.usage.input_tokens,
                response.usage.output_tokens,
            )

            parsed = output_strategy.parse_response(response, response_model)
            return parsed, total_cost

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

            working_messages.append(
                LLMMessage(
                    role="user",
                    parts=[TextPart(text=f"Invalid JSON: {e}. Please provide valid JSON.")],
                )
            )

        except LLMError as e:
            if (
                not config.retry_on_provider_errors
                or not e.retryable
                or attempt >= config.max_retries
            ):
                raise
            if on_retry:
                on_retry(attempt + 1, e)
            # Optional: backoff/jitter is applied here in real code.
            continue

    raise RuntimeError("Retry loop exited unexpectedly")
```

#### 5. Provider Base Class

Abstract base for all providers:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Protocol

@dataclass
class Usage:
    """Token usage statistics."""
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass
class CompletionResponse:
    """Normalized response from a completion call.

    Providers adapt SDK responses into this portable shape so that the rest of the
    system never needs to interpret provider-specific payload formats.
    """

    message: LLMMessage
    usage: Usage
    raw_response: Any
    reasoning_content: str | None = None


class CancelToken(Protocol):
    """Minimal cancellation contract compatible with PenguiFlow cancel propagation."""

    def is_cancelled(self) -> bool: ...


class Provider(ABC):
    """Abstract base class for LLM providers."""

    profile: ModelProfile

    @abstractmethod
    async def complete(
        self,
        request: LLMRequest,
        *,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
        stream: bool = False,
        on_stream_event: StreamCallback | None = None,
    ) -> CompletionResponse:
        """Execute a completion request.

        Requirements:
        - Respect cancellation (raise `asyncio.CancelledError` or `LLMCancelledError`)
        - Enforce `timeout_s` (raise `LLMTimeoutError`)
        - Emit `StreamEvent` via `on_stream_event` if streaming is enabled
        - Normalize provider-specific responses into `CompletionResponse.message`
        """
        ...
```

#### 5.1 Error Taxonomy (Retryability + User-Facing Messages)

The native layer should expose a small, stable error hierarchy with explicit retryability so that:

- The retry loop can make correct decisions without string matching
- The planner/runtime can log consistent failure reasons across providers
- User-visible errors can be extracted cleanly while retaining raw payloads for debugging

Illustrative error model:

```python
from dataclasses import dataclass
from typing import Any

@dataclass
class LLMError(Exception):
    message: str
    provider: str | None = None
    status_code: int | None = None
    retryable: bool = False
    raw: Any | None = None


@dataclass
class LLMTimeoutError(LLMError):
    retryable: bool = True


@dataclass
class LLMRateLimitError(LLMError):
    retryable: bool = True


@dataclass
class LLMServerError(LLMError):
    retryable: bool = True


@dataclass
class LLMInvalidRequestError(LLMError):
    retryable: bool = False


@dataclass
class LLMAuthError(LLMError):
    retryable: bool = False


@dataclass
class LLMCancelledError(LLMError):
    retryable: bool = False
```

Provider implementations map SDK exceptions → `LLMError` subclasses (including Databricks nested JSON extraction), attaching `raw` for observability while producing a clean `message`.

#### 5.2 Compatibility Contract: `JSONLLMClient`

PenguiFlow already depends on a JSON-only client protocol in planner code. The native layer must provide a compatibility adapter that preserves:

- **Inputs**: OpenAI-style `list[{"role": ..., "content": ...}]` messages (existing templates)
- **Streaming**: a callback that receives incremental assistant text (best-effort across providers)
- **Cancellation**: cancel propagation from PenguiFlow runtime into provider calls
- **Outputs**: parsed Pydantic model + accumulated cost across attempts

Implementation sketch:

```python
# penguiflow/llm/protocol.py

class JSONLLMClientAdapter:
    def __init__(self, client: LLMClient):
        self._client = client

    async def __call__(self, messages: list[dict], response_model: type[BaseModel], **kwargs):
        typed = adapt_openai_dict_messages(messages)  # -> list[LLMMessage]
        return await self._client.call(typed, response_model, **kwargs)
```

#### 6. Provider Implementations

**OpenAI Provider:**

```python
import asyncio
from openai import AsyncOpenAI


class OpenAIProvider(Provider):
    """OpenAI provider using native SDK (OpenAI-shaped wire format)."""

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
        request: LLMRequest,
        *,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
        stream: bool = False,
        on_stream_event: StreamCallback | None = None,
    ) -> CompletionResponse:
        params = self._to_openai_params(request)

        async def _run() -> CompletionResponse:
            if stream and on_stream_event:
                return await self._stream(params, on_stream_event)

            resp = await self._client.chat.completions.create(**params)
            usage = Usage(
                input_tokens=resp.usage.prompt_tokens,
                output_tokens=resp.usage.completion_tokens,
                total_tokens=resp.usage.total_tokens,
            )
            msg = resp.choices[0].message
            parts: list[ContentPart] = []
            if msg.content:
                parts.append(TextPart(text=msg.content))
            # Tool calls (if any) are normalized into ToolCallPart(s)
            for tc in getattr(msg, "tool_calls", []) or []:
                parts.append(
                    ToolCallPart(
                        name=tc.function.name,
                        arguments_json=tc.function.arguments,
                        call_id=getattr(tc, "id", None),
                    )
                )
            return CompletionResponse(
                message=LLMMessage(role="assistant", parts=parts),
                usage=usage,
                raw_response=resp,
                reasoning_content=getattr(msg, "reasoning_content", None),
            )

        if cancel and cancel.is_cancelled():
            raise asyncio.CancelledError()
        if timeout_s is None:
            return await _run()
        async with asyncio.timeout(timeout_s):
            return await _run()

    def _to_openai_params(self, request: LLMRequest) -> dict[str, Any]:
        """Adapt typed `LLMRequest` to OpenAI chat.completions params."""
        params: dict[str, Any] = {
            "model": self.model,
            "messages": adapt_messages_to_openai(request.messages),
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            params["max_tokens"] = request.max_tokens
        if request.tools:
            params["tools"] = adapt_tools_to_openai(request.tools)
        if request.tool_choice:
            params["tool_choice"] = {"type": "function", "function": {"name": request.tool_choice}}
        if request.structured_output:
            params["response_format"] = adapt_structured_output_to_openai(request.structured_output)
        if request.extra:
            params.update(request.extra)  # sanitized by higher layers
        return params

    async def _stream(self, params: dict[str, Any], on_stream_event: StreamCallback) -> CompletionResponse:
        params = dict(params)
        params["stream"] = True
        params["stream_options"] = {"include_usage": True}

        parts: list[ContentPart] = []
        text_acc: list[str] = []
        usage: Usage | None = None

        async with await self._client.chat.completions.create(**params) as s:
            async for chunk in s:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    text_acc.append(delta.content)
                    on_stream_event(StreamEvent(delta_text=delta.content))
                if getattr(chunk, "usage", None):
                    usage = Usage(
                        input_tokens=chunk.usage.prompt_tokens,
                        output_tokens=chunk.usage.completion_tokens,
                        total_tokens=chunk.usage.total_tokens,
                    )

        full_text = "".join(text_acc)
        if full_text:
            parts.append(TextPart(text=full_text))
        on_stream_event(StreamEvent(done=True, usage=usage))
        return CompletionResponse(
            message=LLMMessage(role="assistant", parts=parts),
            usage=usage or Usage(0, 0, 0),
            raw_response=None,
        )
```

**Anthropic Provider:**

```python
import json
from anthropic import AsyncAnthropic

class AnthropicProvider(Provider):
    """Anthropic provider using native SDK (content blocks + tool use)."""

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
        request: LLMRequest,
        *,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
        stream: bool = False,
        on_stream_event: StreamCallback | None = None,
    ) -> CompletionResponse:
        # Real implementation: enforce timeout/cancel, and use streaming events.
        system_text, msgs = adapt_messages_to_anthropic(request.messages)
        params: dict[str, Any] = {
            "model": self.model,
            "system": system_text or None,
            "messages": msgs,
            "max_tokens": request.max_tokens or 4096,
        }
        if request.temperature > 0:
            params["temperature"] = request.temperature
        if request.tools:
            params["tools"] = adapt_tools_to_anthropic(request.tools)
        if request.tool_choice:
            params["tool_choice"] = {"type": "tool", "name": request.tool_choice}

        # Anthropic "native structured" is typically tool use; schema-guided decoding is not OpenAI-style.
        if request.structured_output:
            params = adapt_structured_output_to_anthropic(params, request.structured_output, profile=self.profile)

        if stream and on_stream_event:
            return await anthropic_stream(params, on_stream_event)  # emits StreamEvent deltas

        resp = await self._client.messages.create(**params)
        usage = Usage(
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            total_tokens=resp.usage.input_tokens + resp.usage.output_tokens,
        )

        parts: list[ContentPart] = []
        for block in resp.content:
            if block.type == "text":
                parts.append(TextPart(text=block.text))
            elif block.type == "tool_use":
                parts.append(
                    ToolCallPart(
                        name=block.name,
                        arguments_json=json.dumps(block.input),
                        call_id=block.id,
                    )
                )

        return CompletionResponse(
            message=LLMMessage(role="assistant", parts=parts),
            usage=usage,
            raw_response=resp,
        )
```

**Bedrock Provider:**

```python
import asyncio
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
        request: LLMRequest,
        *,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
        stream: bool = False,
        on_stream_event: StreamCallback | None = None,
    ) -> CompletionResponse:
        # Bedrock uses the Converse APIs; real implementation must adapt typed content/tool shapes.
        system_content, bedrock_messages = adapt_messages_to_bedrock(request.messages)

        params: dict[str, Any] = {
            "modelId": self.model,
            "messages": bedrock_messages,
            "inferenceConfig": {
                "maxTokens": request.max_tokens or 4096,
                "temperature": request.temperature,
            },
        }

        if system_content:
            params["system"] = [{"text": system_content}]

        if request.tools:
            params["toolConfig"] = {
                "tools": adapt_tools_to_bedrock(request.tools),
            }

        # boto3 is sync; run in executor and respect timeout/cancel at the wrapper layer.
        loop = asyncio.get_event_loop()

        if stream and on_stream_event:
            response = await loop.run_in_executor(
                None,
                lambda: self._client.converse_stream(**params),
            )
            return await bedrock_process_stream(response, on_stream_event)

        response = await loop.run_in_executor(
            None,
            lambda: self._client.converse(**params),
        )

        usage = Usage(
            input_tokens=response["usage"]["inputTokens"],
            output_tokens=response["usage"]["outputTokens"],
            total_tokens=response["usage"]["totalTokens"],
        )

        parts = adapt_bedrock_output_to_parts(response)
        return CompletionResponse(
            message=LLMMessage(role="assistant", parts=parts),
            usage=usage,
            raw_response=response,
        )
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
        request: LLMRequest,
        *,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
        stream: bool = False,
        on_stream_event: StreamCallback | None = None,
    ) -> CompletionResponse:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": adapt_messages_to_openai(request.messages),
            "temperature": request.temperature,
        }

        if request.max_tokens is not None:
            params["max_tokens"] = request.max_tokens

        # Databricks supports schema-guided output via constrained decoding (OpenAI-compatible response_format).
        if request.structured_output:
            params["response_format"] = adapt_structured_output_to_databricks(request.structured_output)

        # Validate tool limits
        if request.tools:
            if len(request.tools) > self.MAX_TOOLS:
                raise ValueError(f"Databricks supports max {self.MAX_TOOLS} tools, got {len(request.tools)}")
            params["tools"] = adapt_tools_to_openai(request.tools)
        if request.tool_choice:
            params["tool_choice"] = {"type": "function", "function": {"name": request.tool_choice}}

        # Databricks does not support some OpenAI params (e.g. reasoning_effort); drop them safely.
        extra = dict(request.extra or {})
        extra.pop("reasoning_effort", None)
        params.update(extra)

        try:
            if stream and on_stream_event:
                return await self._stream_completion(params, on_stream_event)

            response = await self._client.chat.completions.create(**params)
        except Exception as e:
            # Extract clean error message from nested Databricks format
            raise self._wrap_error(e) from e

        usage = Usage(
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
        )

        msg = response.choices[0].message
        parts: list[ContentPart] = []
        if msg.content:
            parts.append(TextPart(text=msg.content))
        for tc in getattr(msg, "tool_calls", []) or []:
            parts.append(
                ToolCallPart(
                    name=tc.function.name,
                    arguments_json=tc.function.arguments,
                    call_id=getattr(tc, "id", None),
                )
            )

        return CompletionResponse(
            message=LLMMessage(role="assistant", parts=parts),
            usage=usage,
            raw_response=response,
        )

    async def _stream_completion(
        self,
        params: dict,
        on_stream_event: StreamCallback,
    ) -> CompletionResponse:
        """Stream completion with callback (OpenAI-compatible streaming)."""
        params["stream"] = True

        text_parts: list[str] = []
        usage: Usage | None = None

        async with await self._client.chat.completions.create(**params) as stream:
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    text_parts.append(text)
                    on_stream_event(StreamEvent(delta_text=text))

                if hasattr(chunk, "usage") and chunk.usage:
                    usage = Usage(
                        input_tokens=chunk.usage.prompt_tokens,
                        output_tokens=chunk.usage.completion_tokens,
                        total_tokens=chunk.usage.total_tokens,
                    )

        full_text = "".join(text_parts)
        on_stream_event(StreamEvent(done=True, usage=usage))

        return CompletionResponse(
            message=LLMMessage(
                role="assistant",
                parts=[TextPart(text=full_text)] if full_text else [],
            ),
            usage=usage or Usage(0, 0, 0),
            raw_response=None,
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
                return LLMInvalidRequestError(
                    message=inner_json.group(1).replace('\\"', '"'),
                    provider="databricks",
                    raw=exc,
                )

            return LLMInvalidRequestError(message=inner_msg, provider="databricks", raw=exc)

        return LLMServerError(message=str(exc), provider="databricks", raw=exc)
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
                    supports_schema_guided_output=True,
                    schema_transformer_class=OpenRouterAnthropicTransformer,
                    strict_mode_default=False,
                    native_structured_kind="openai_compatible_tools",
                )
            elif profile_type == "google":
                return ModelProfile(
                    supports_schema_guided_output=True,
                    schema_transformer_class=OpenRouterGoogleTransformer,
                    native_structured_kind="google_response_schema",
                )
            elif profile_type == "openai":
                return ModelProfile(
                    supports_schema_guided_output=True,
                    schema_transformer_class=OpenAIJsonSchemaTransformer,
                    native_structured_kind="openai_response_format",
                )

        # Default profile for unknown providers
        return ModelProfile(
            supports_schema_guided_output=False,
            supports_json_only_output=True,
            default_output_mode="prompted",
        )

    async def complete(
        self,
        request: LLMRequest,
        *,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
        stream: bool = False,
        on_stream_event: StreamCallback | None = None,
    ) -> CompletionResponse:
        # OpenRouter is OpenAI-compatible; reuse the OpenAI-shaped adapter.
        params: dict[str, Any] = {
            "model": self.model,
            "messages": adapt_messages_to_openai(request.messages),
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            params["max_tokens"] = request.max_tokens
        if request.tools:
            params["tools"] = adapt_tools_to_openai(request.tools)
        if request.tool_choice:
            params["tool_choice"] = {"type": "function", "function": {"name": request.tool_choice}}
        if request.structured_output:
            params["response_format"] = adapt_structured_output_to_openai(request.structured_output)
        if request.extra:
            params.update(request.extra)

        if stream and on_stream_event:
            return await openai_compatible_stream(self._client, params, on_stream_event)

        response = await self._client.chat.completions.create(**params)

        usage = Usage(
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
        )

        msg = response.choices[0].message
        parts: list[ContentPart] = []
        if msg.content:
            parts.append(TextPart(text=msg.content))
        for tc in getattr(msg, "tool_calls", []) or []:
            parts.append(
                ToolCallPart(
                    name=tc.function.name,
                    arguments_json=tc.function.arguments,
                    call_id=getattr(tc, "id", None),
                )
            )

        return CompletionResponse(
            message=LLMMessage(role="assistant", parts=parts),
            usage=usage,
            raw_response=response,
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
from typing import Any, Callable
from pydantic import BaseModel

class LLMClient:
    """Main LLM client interface for PenguiFlow.

    Orchestrates routing, schema planning, mode selection, retry, streaming,
    cancellation, timeouts, and cost/usage accounting.
    """

    def __init__(
        self,
        model: str,
        *,
        temperature: float = 0.0,
        max_retries: int = 3,
        timeout_s: float = 60.0,
        streaming_enabled: bool = False,
        on_stream_event: StreamCallback | None = None,
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
        self.on_stream_event = on_stream_event

        # Create provider
        self._provider = create_provider(
            model,
            api_key=api_key,
            base_url=base_url,
            **provider_kwargs,
        )

    @property
    def profile(self) -> ModelProfile:
        return self._provider.profile

    async def call_dict(
        self,
        messages: list[dict],
        response_model: type[BaseModel],
        **kwargs: Any,
    ) -> tuple[BaseModel, float]:
        """Convenience wrapper for OpenAI-style dict messages (for templates/back-compat)."""
        typed = adapt_openai_dict_messages(messages)
        return await self.call(typed, response_model, **kwargs)

    async def call(
        self,
        messages: list[LLMMessage],
        response_model: type[BaseModel],
        *,
        mode: OutputMode | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool | None = None,
        on_stream_event: StreamCallback | None = None,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
    ) -> tuple[BaseModel, float]:
        """Execute a structured output call.

        Args:
            messages: Typed conversation messages.
            response_model: Pydantic model for the expected response.
            mode: Force a specific structured output mode (otherwise choose + degrade).
            temperature: Override default temperature.
            max_tokens: Maximum tokens to generate.
            stream: Override default streaming setting.
            on_stream_event: Override default stream callback.
            timeout_s: Override default timeout.
            cancel: Cancellation token propagated from PenguiFlow.

        Returns:
            Tuple of (parsed response, cost in USD).
        """
        schema = response_model.model_json_schema()

        chosen_mode, plan = choose_output_mode(self._provider.profile, schema)
        if mode is not None:
            # If forced mode is incompatible, callers get a fast/clear failure.
            forced_plan = plan_schema(self._provider.profile, schema, mode=mode)
            if mode == OutputMode.NATIVE and not forced_plan.compatible_with_native:
                raise ValueError(f"Mode {mode} incompatible: {forced_plan.reasons}")
            if mode == OutputMode.TOOLS and not forced_plan.compatible_with_tools:
                raise ValueError(f"Mode {mode} incompatible: {forced_plan.reasons}")
            chosen_mode, plan = mode, forced_plan

        strategy = self._get_strategy(chosen_mode)

        # Build retry config
        retry_config = RetryConfig(
            max_retries=self.max_retries,
            retry_on_validation=True,
            retry_on_parse=True,
        )

        # Determine streaming
        should_stream = stream if stream is not None else self.streaming_enabled
        stream_callback = on_stream_event or self.on_stream_event

        def build_request(working_messages: list[LLMMessage]) -> LLMRequest:
            req = strategy.build_request(
                model=self.model,
                messages=working_messages,
                response_model=response_model,
                profile=self._provider.profile,
                plan=plan,
            )
            return LLMRequest(
                model=req.model,
                messages=req.messages,
                tools=req.tools,
                tool_choice=req.tool_choice,
                structured_output=req.structured_output,
                temperature=temperature if temperature is not None else self.temperature,
                max_tokens=max_tokens,
                extra=req.extra,
            )

        # Execute with retry
        result, total_cost = await call_with_retry(
            self._provider,
            base_messages=messages,
            response_model=response_model,
            output_strategy=strategy,
            config=retry_config,
            timeout_s=timeout_s if timeout_s is not None else self.timeout_s,
            cancel=cancel,
            stream=should_stream,
            on_stream_event=stream_callback,
            build_request=build_request,
        )
        return result, total_cost

    async def call_raw(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool | None = None,
        on_stream_event: StreamCallback | None = None,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
    ) -> CompletionResponse:
        """Execute a raw completion call without structured output."""
        should_stream = stream if stream is not None else self.streaming_enabled
        stream_callback = on_stream_event or self.on_stream_event

        request = LLMRequest(
            model=self.model,
            messages=messages,
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens,
        )
        return await self._provider.complete(
            request,
            timeout_s=timeout_s if timeout_s is not None else self.timeout_s,
            cancel=cancel,
            stream=should_stream,
            on_stream_event=stream_callback,
        )

    def _get_strategy(self, mode: OutputMode) -> Any:
        """Get output strategy for the given mode."""
        if mode == OutputMode.NATIVE:
            return NativeOutputStrategy()
        elif mode == OutputMode.TOOLS:
            return ToolsOutputStrategy()
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
    # Avoid heuristic routing based on "/" which misclassifies many real model IDs.
    # Prefer explicit prefixes and a small set of safe inferences for legacy model strings.
    #
    # Recommended model ref formats:
    # - "openai/gpt-4o"
    # - "anthropic/claude-3-5-sonnet"
    # - "google/gemini-2.0-flash"
    # - "bedrock/anthropic.claude-3-5-sonnet"
    # - "databricks/databricks-dbrx-instruct"
    # - "openrouter/anthropic/claude-3-5-sonnet"

    if model.startswith("openrouter/"):
        return OpenRouterProvider(model, api_key=api_key, **kwargs)

    if model.startswith("databricks/"):
        return DatabricksProvider(model.removeprefix("databricks/"), **kwargs)
    if model.startswith("databricks-"):
        return DatabricksProvider(model, **kwargs)

    if model.startswith("bedrock/"):
        return BedrockProvider(model.removeprefix("bedrock/"), **kwargs)
    if model.startswith(("anthropic.", "amazon.", "meta.")):
        return BedrockProvider(model, **kwargs)

    if model.startswith("openai/"):
        return OpenAIProvider(model.removeprefix("openai/"), api_key=api_key, base_url=base_url, **kwargs)
    if model.startswith(("gpt", "o1", "o3")):
        return OpenAIProvider(model, api_key=api_key, base_url=base_url, **kwargs)

    if model.startswith("anthropic/"):
        return AnthropicProvider(model.removeprefix("anthropic/"), api_key=api_key, **kwargs)
    if model.startswith("claude"):
        return AnthropicProvider(model, api_key=api_key, **kwargs)

    if model.startswith("google/"):
        return GoogleProvider(model.removeprefix("google/"), api_key=api_key, **kwargs)
    if model.startswith("gemini"):
        return GoogleProvider(model, api_key=api_key, **kwargs)

    # Default: OpenAI-compatible, but require explicit base_url for non-OpenAI servers.
    return OpenAIProvider(model, api_key=api_key, base_url=base_url, **kwargs)


def create_client(
    model: str,
    *,
    temperature: float = 0.0,
    max_retries: int = 3,
    timeout_s: float = 60.0,
    streaming_enabled: bool = False,
    on_stream_event: StreamCallback | None = None,
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
        on_stream_event: Default streaming callback.
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
        on_stream_event=on_stream_event,
        **provider_kwargs,
    )
```

### Usage + Cost Accounting

Maintain a single pricing table and calculate cost from normalized `Usage`. Cost is accounted **per attempt** and accumulated across retries (so callers can see true spend).

```python
# penguiflow/llm/pricing.py

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
| **4.1** | Streaming events + callbacks | `types.py`, provider adapters |
| **4.2** | Pricing + cost calculation | `pricing.py` |
| **4.3** | Telemetry hooks + attempt logs | `telemetry.py`, `retry.py` |

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

# tests/test_llm_routing_and_modes.py
def test_choose_output_mode_prefers_native_when_compatible():
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    profile = get_profile("gpt-4o-mini")
    mode, plan = choose_output_mode(profile, schema)
    assert mode in (OutputMode.NATIVE, OutputMode.TOOLS)
    assert plan.transformed_schema

def test_choose_output_mode_degrades_for_databricks_key_limit():
    schema = {"type": "object", "properties": {f"k{i}": {"type": "string"} for i in range(100)}}
    profile = get_profile("databricks-dbrx-instruct")
    mode, plan = choose_output_mode(profile, schema)
    assert mode != OutputMode.NATIVE
    assert plan.reasons  # negative/error-path coverage

# tests/test_llm_output_modes.py
@pytest.mark.asyncio
async def test_tools_output_strategy_builds_forced_tool_request():
    strategy = ToolsOutputStrategy()
    profile = get_profile("gpt-4o-mini")
    plan = plan_schema(profile, PersonModel.model_json_schema(), mode=OutputMode.TOOLS)
    req = strategy.build_request(
        model="openai/gpt-4o-mini",
        messages=[LLMMessage(role="user", parts=[TextPart(text="Extract name")])],
        response_model=PersonModel,
        profile=profile,
        plan=plan,
    )
    assert req.tool_choice == "structured_output"
    assert req.tools and req.tools[0].name == "structured_output"

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
    result, cost = await client.call_dict(
        [{"role": "user", "content": "What is 2+2? Return as JSON."}],
        MathResult,
    )
    assert result.answer == 4

@pytest.mark.integration
@pytest.mark.asyncio
async def test_anthropic_structured_output():
    client = create_client("claude-3-5-haiku")
    result, cost = await client.call_dict(
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

# Calls from templates/back-compat can keep OpenAI-style dict messages:
# result, cost = await client.call_dict([...], ResponseModel)
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
