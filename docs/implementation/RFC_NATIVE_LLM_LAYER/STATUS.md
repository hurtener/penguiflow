# RFC Native LLM Layer - Implementation Status

> **Last Updated**: 2026-01-12
> **Status**: In Progress (Tests Remaining)
> **Target**: Enterprise production-ready

## Overview

Implementing the Native LLM Layer as specified in RFC_NATIVE_LLM_LAYER.md. Replacing LiteLLM dependency with native penguiflow-owned LLM abstraction layer.

## Target Providers (v1)

1. OpenAI
2. Anthropic
3. Google (Gemini)
4. AWS Bedrock
5. Databricks
6. OpenRouter

## Phase Progress

### Phase 1: Core Infrastructure
| Task | Status | File(s) |
|------|--------|---------|
| 1.1 Module structure | [x] | `penguiflow/llm/__init__.py` |
| 1.2 Types (messages, tools, events) | [x] | `penguiflow/llm/types.py` |
| 1.3 Error taxonomy | [x] | `penguiflow/llm/errors.py` |
| 1.4 Provider ABC | [x] | `penguiflow/llm/providers/base.py` |
| 1.5 ModelProfile + registry | [x] | `penguiflow/llm/profiles/__init__.py` |
| 1.6 Base JsonSchemaTransformer | [x] | `penguiflow/llm/schema/transformer.py` |
| 1.7 Schema plan | [x] | `penguiflow/llm/schema/plan.py` |

### Phase 2: Provider Implementations
| Task | Status | File(s) |
|------|--------|---------|
| 2.1 OpenAI provider + transformer | [x] | `providers/openai.py`, `schema/openai.py`, `profiles/openai.py` |
| 2.2 Anthropic provider + transformer | [x] | `providers/anthropic.py`, `schema/anthropic.py`, `profiles/anthropic.py` |
| 2.3 Google provider + transformer | [x] | `providers/google.py`, `schema/google.py`, `profiles/google.py` |
| 2.4 Bedrock provider + transformer | [x] | `providers/bedrock.py`, `schema/bedrock.py`, `profiles/bedrock.py` |
| 2.5 Databricks provider + transformer | [x] | `providers/databricks.py`, `schema/databricks.py`, `profiles/databricks.py` |
| 2.6 OpenRouter provider + routing | [x] | `providers/openrouter.py`, `profiles/openrouter.py` |

### Phase 3: Output Modes & Retry
| Task | Status | File(s) |
|------|--------|---------|
| 3.1 Tool output strategy | [x] | `output/tool.py` |
| 3.2 Native output strategy | [x] | `output/native.py` |
| 3.3 Prompted output strategy | [x] | `output/prompted.py` |
| 3.4 Output mode selection | [x] | `output/__init__.py` |
| 3.5 Retry mechanism | [x] | `penguiflow/llm/retry.py` |

### Phase 4: Streaming & Cost
| Task | Status | File(s) |
|------|--------|---------|
| 4.1 Streaming events + callbacks | [x] | `types.py` updates |
| 4.2 Pricing + cost calculation | [x] | `penguiflow/llm/pricing.py` |
| 4.3 Telemetry hooks | [x] | `penguiflow/llm/telemetry.py` |

### Phase 5: Integration & Migration
| Task | Status | File(s) |
|------|--------|---------|
| 5.1 LLMClient implementation | [x] | `penguiflow/llm/client.py` |
| 5.2 JSONLLMClient protocol compat | [x] | `penguiflow/llm/protocol.py` |
| 5.3 Provider routing/factory | [x] | `penguiflow/llm/routing.py` |
| 5.4 Tests for all components | [ ] | `tests/test_llm_*.py` |
| 5.5 Update pyproject.toml deps | [ ] | `pyproject.toml` |

### Phase 6: CI Verification
| Task | Status |
|------|--------|
| 6.1 Ruff passes | [ ] |
| 6.2 Mypy passes | [ ] |
| 6.3 Pytest passes (85%+ coverage) | [ ] |

## File Manifest

```
penguiflow/llm/
├── __init__.py              # Public API: create_client(), LLMClient ✓
├── client.py                # LLMClient implementation ✓
├── protocol.py              # JSONLLMClient protocol (existing interface compat) ✓
├── types.py                 # Typed requests/messages/tools/events ✓
├── routing.py               # ProviderRegistry + model string parsing ✓
├── pricing.py               # Pricing table + cost calculation ✓
├── errors.py                # LLMError taxonomy + provider error mapping ✓
├── telemetry.py             # Hooks: attempts, retries, usage, cost ✓
├── retry.py                 # ModelRetry, ValidationRetry, retry loop ✓
│
├── profiles/
│   ├── __init__.py          # ModelProfile dataclass, PROFILES registry ✓
│   ├── openai.py            # OpenAI/GPT model profiles ✓
│   ├── anthropic.py         # Claude model profiles ✓
│   ├── google.py            # Gemini model profiles ✓
│   ├── bedrock.py           # AWS Bedrock model profiles ✓
│   ├── databricks.py        # Databricks model profiles ✓
│   └── openrouter.py        # OpenRouter routing + profile mapping ✓
│
├── schema/
│   ├── __init__.py          # JsonSchemaTransformer ABC ✓
│   ├── transformer.py       # Base transformer with recursive walking ✓
│   ├── plan.py              # SchemaPlan: transformed schema + compatibility ✓
│   ├── openai.py            # OpenAI: additionalProperties, strict mode ✓
│   ├── anthropic.py         # Anthropic: constraint relocation ✓
│   ├── google.py            # Google: const→enum, format in description ✓
│   ├── bedrock.py           # Bedrock: inline defs transformer ✓
│   └── databricks.py        # Databricks: no anyOf/oneOf, max 64 keys ✓
│
├── output/
│   ├── __init__.py          # OutputMode enum, choose_output_mode() ✓
│   ├── tool.py              # Tool-based structured output (portable) ✓
│   ├── native.py            # Provider-native structured output ✓
│   └── prompted.py          # Schema in prompt + parse/retry (fallback) ✓
│
└── providers/
    ├── __init__.py          # Provider registry, create_provider() ✓
    ├── base.py              # Provider ABC ✓
    ├── openai.py            # AsyncOpenAI direct ✓
    ├── anthropic.py         # AsyncAnthropic direct ✓
    ├── google.py            # google-genai direct ✓
    ├── bedrock.py           # boto3 bedrock-runtime direct ✓
    ├── databricks.py        # OpenAI-compatible + Databricks quirks ✓
    └── openrouter.py        # OpenAI-compatible + model routing ✓
```

## Notes

- All implementations follow RFC specifications
- Enterprise-grade error handling
- Full streaming support
- Cost tracking across retries
- Compatible with existing JSONLLMClient protocol
- ✓ = File created and implemented

## Remaining Tasks

1. Create comprehensive test suite
2. Update pyproject.toml with optional dependencies
3. Run full CI pipeline (ruff, mypy, pytest)
4. Verify 85%+ coverage target
