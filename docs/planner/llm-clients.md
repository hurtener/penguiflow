# LLM clients

## What it is / when to use it

`ReactPlanner` talks to the LLM through a JSON-first protocol (`JSONLLMClient`) so planner actions can be machine-validated.

You care about this page when you:

- integrate a provider that isn’t supported by your LiteLLM setup,
- want deterministic “scripted” planner behavior for tests,
- need to understand streaming / retries / timeout behavior at the LLM boundary.

## Non-goals / boundaries

- This is not a catalog/tool guide (see **[Tool design](tool-design.md)** and **[Tooling](tooling.md)**).
- This page does not document each provider’s quirks; it explains the *contract PenguiFlow expects*.
- A custom `JSONLLMClient` is responsible for transport and auth; PenguiFlow does not manage secrets for you.

## Contract surface

### Two integration modes

You can configure `ReactPlanner` in two ways:

1. LiteLLM-backed:
   - `ReactPlanner(llm="gpt-4o-mini", ...)`
2. Custom client:
   - `ReactPlanner(llm_client=my_client, ...)`

### `JSONLLMClient.complete(...)`

Custom clients implement:

- `penguiflow.planner.models.JSONLLMClient`

Signature (conceptually):

- `complete(messages, response_format=None, stream=False, on_stream_chunk=None) -> str | (str, float)`

Where:

- `messages` is the chat history you send to your provider (`[{role, content}, ...]`)
- `response_format` is a best-effort schema hint (commonly `json_schema`); clients may ignore it if unsupported
- `stream=True` means the client should call `on_stream_chunk(text, done)` as chunks arrive

!!! note
    Planner “streaming” is two different things:
    - tool streaming (`ctx.emit_chunk`, `ctx.emit_artifact`) which is not tied to the LLM transport, and
    - LLM streaming (`stream_final_response=True`) where the LLM’s final answer can be streamed via `on_stream_chunk`.

### JSON schema mode

`json_schema_mode=True` instructs compatible LLM backends to enforce the `PlannerAction` schema via response formatting.

Operational guidance:

- keep it enabled in production (it materially reduces invalid JSON loops),
- if your provider can’t enforce strict schema output, expect more repair/arg-fill traffic.

See **[Actions & schema](actions-and-schema.md)** for the `PlannerAction` format.

## Operational defaults

- Prefer `temperature=0.0` and `json_schema_mode=True` for planners.
- Set `llm_timeout_s` aggressively (default is 60s) and tune `llm_max_retries` for your provider’s reliability.
- If you run multi-tenant, keep secrets out of `llm_context` (LLM-visible) and place them only in `tool_context`.

## Failure modes & recovery

### Provider cannot produce strict JSON

**Symptoms**

- repeated repair attempts
- frequent `budget_exhausted`

**Fix**

- enable schema mode if supported
- simplify tool schemas and add examples
- use `arg_fill_enabled=True` and keep `max_consecutive_arg_failures` bounded

### LLM stalls / long tail latency

**Symptoms**

- planner feels “stuck” on `llm_call`

**Fix**

- lower `llm_timeout_s`
- reduce catalog size (tool visibility/policy)
- turn on structured event logs (see Observability)

### Streaming callback not firing

**Likely causes**

- you didn’t enable `stream_final_response=True`
- your client ignores `stream=True` / `on_stream_chunk`

**Fix**

- verify your `JSONLLMClient.complete(..., stream=True, on_stream_chunk=...)` path
- record `PlannerEvent(event_type="llm_stream_chunk")` / `PlannerEvent(event_type="stream_chunk")` to confirm the path

## Observability

At minimum:

- record `PlannerEvent(event_type="llm_call")` latency and retry counts
- record finish reasons (`answer_complete` / `no_path` / `budget_exhausted`)
- log *redacted* inputs/outputs (avoid raw prompts and raw tool outputs)

See **[Planner observability](observability.md)**.

## Security / multi-tenancy notes

- Never embed secrets in `messages` or `llm_context`.
- If you use a hosted provider, verify whether prompts/completions are retained for training/logging.
- Treat the LLM as untrusted: the planner’s JSON schema validation is a safety mechanism, not a substitute for tool allowlists and HITL gating.

## Runnable example: scripted `JSONLLMClient` (deterministic)

This example demonstrates the **contract** without network calls by scripting the action outputs.

```python
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from penguiflow import ModelRegistry, Node
from penguiflow.catalog import build_catalog, tool
from penguiflow.planner import PlannerFinish, ReactPlanner, ToolContext
from penguiflow.planner.models import JSONLLMClient


class EchoArgs(BaseModel):
    text: str


class EchoOut(BaseModel):
    response: str


@tool(desc="Echo input", side_effects="pure")
async def echo(args: EchoArgs, ctx: ToolContext) -> EchoOut:
    del ctx
    return EchoOut(response=args.text)


class ScriptedClient(JSONLLMClient):
    def __init__(self) -> None:
        self._step = 0

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str:
        del messages, response_format
        self._step += 1

        if self._step == 1:
            # Tool call
            return json.dumps({"next_node": "echo", "args": {"text": "hello"}}, ensure_ascii=False)

        # Final response
        answer = "done"
        if stream and on_stream_chunk is not None:
            on_stream_chunk(answer, True)
        return json.dumps({"next_node": "final_response", "args": {"answer": answer}}, ensure_ascii=False)


async def main() -> None:
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo")], registry)

    planner = ReactPlanner(llm_client=ScriptedClient(), catalog=catalog)
    result = await planner.run("demo", tool_context={"session_id": "demo"})
    assert isinstance(result, PlannerFinish)
    print(result.reason, getattr(result.payload, "raw_answer", result.payload))


if __name__ == "__main__":
    asyncio.run(main())
```
