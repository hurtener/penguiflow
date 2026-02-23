# Native LLM layer (planner integration)

## What it is / when to use it

PenguiFlow includes a native LLM implementation (`penguiflow.llm`) and a compatibility adapter (`NativeLLMAdapter`) that implements the planner’s `JSONLLMClient` protocol.

Use the native LLM layer when you want:

- provider-specific correctness fixes (message normalization, schema normalization),
- structured output fallback behavior (e.g. schema mode downgrade when a provider rejects a schema),
- optional native reasoning extraction and reasoning streaming callbacks,
- a single internal implementation surface you can instrument and test.

## Non-goals / boundaries

- This page does not document every provider’s environment variables or auth story.
- The native layer is not required; `ReactPlanner(llm="...")` (LiteLLM-backed) remains supported.
- The planner still consumes a `JSONLLMClient` protocol; native integration is an implementation detail behind that protocol.

## Contract surface

### Enabling native LLM on ReactPlanner

Set:

- `ReactPlanner(..., use_native_llm=True)`

Then pass:

- `llm="provider/model"` or `llm={"model": "...", ...}`

When enabled, the planner internally builds a native adapter via:

- `penguiflow.llm.create_native_adapter(...)`

### `create_native_adapter(...)` input shape

`create_native_adapter` accepts:

- a string model id: `"openai/gpt-4o"` (example format), or
- a config mapping:
  - required: `{"model": "..."}`
  - optional: `{"api_key": "...", "base_url": "...", ...}`

Any extra keys in the mapping are forwarded as provider kwargs.

### Structured output and schema mode behavior

The planner requests structured output through `response_format` (commonly `json_schema`).

The native adapter:

- normalizes schemas for stricter validators (e.g. ensures object roots),
- may downgrade structured mode when a provider rejects a schema (e.g. `json_schema → json_object → text`).

If your provider frequently downgrades, you will see increased invalid JSON/repair traffic at the planner layer.

### Native reasoning integration

Planner configuration:

- `use_native_reasoning=True` (default)
- `reasoning_effort="low" | "medium" | "high" | None` (provider/model dependent)

When enabled and supported, the adapter can call the planner’s reasoning callback during streaming. This is used only for observability; the planner action schema remains JSON-only.

## Operational defaults (recommended)

- Keep `temperature=0.0` for planners.
- Keep `json_schema_mode=True` unless your provider cannot handle it.
- Set aggressive timeouts (`llm_timeout_s`) and bounded retries (`llm_max_retries`) per SLO.
- Enable `stream_final_response=True` only when you have an event sink that can handle chunk volume safely.

## Failure modes & recovery

### Provider rejects response_format / JSON schema

**Symptoms**

- repeated invalid JSON repairs
- logs indicating response_format downgrade

**Fix**

- simplify tool schemas (fewer nested objects, fewer unions)
- reduce catalog size and ambiguity
- if needed, set `json_schema_mode=False` temporarily (expect more repair traffic)

### System/developer message ordering issues

Some providers are sensitive to system messages not being first. The native adapter applies provider-specific normalization, but you should still keep system prompts consistent and avoid dynamic system-message insertion outside the planner.

### Reasoning callback never fires

**Likely causes**

- provider/model does not support native reasoning
- `use_native_reasoning=False`
- streaming disabled for the call path

## Observability

At minimum:

- record `PlannerEvent(event_type="llm_call")` latency and retry counts
- record whether schema mode was requested (`json_schema_mode`) and whether streaming was enabled
- for incident debugging, log only redacted prompt/response summaries (not raw content)

See **[Planner observability](observability.md)**.

## Security / multi-tenancy notes

- Do not place API keys or credentials into `llm_context`.
- Prefer environment-variable based secrets and inject them into provider config at construction time (or via `tool_context`-held factories).
- Treat model/provider configuration as tenant-controlled only if you have explicit allowlists; otherwise it becomes an injection vector.

## Runnable examples

Native LLM usage requires provider credentials. Minimal planner example:

```python
from __future__ import annotations

from pydantic import BaseModel

from penguiflow import ModelRegistry, Node
from penguiflow.catalog import build_catalog, tool
from penguiflow.planner import ReactPlanner, ToolContext


class EchoArgs(BaseModel):
    text: str


class EchoOut(BaseModel):
    text: str


@tool(desc="Echo", side_effects="pure")
async def echo(args: EchoArgs, ctx: ToolContext) -> EchoOut:
    del ctx
    return EchoOut(text=args.text)


def build_planner() -> ReactPlanner:
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo")], registry)

    return ReactPlanner(
        llm={"model": "openai/gpt-4o"},
        catalog=catalog,
        use_native_llm=True,
        json_schema_mode=True,
        temperature=0.0,
    )
```

## Troubleshooting checklist

- Did you set `use_native_llm=True` (otherwise the LiteLLM path is used)?
- Are your model ids provider-qualified consistently (`openai/...`, `anthropic/...`, etc.)?
- Are you seeing schema downgrade logs (indicating provider incompatibility with the schema)?
- Are you relying on native reasoning content for behavior (don’t; it is observability-only)?

