# Testing the 3.11 Features on an Existing Agent

> Audience: agent developers validating the 3.11 line (`release/3.11`,
> currently `3.11.0a1`) on a real agent before the Phase 6 parity gate.
> Everything below is **opt-in** — an agent that changes nothing behaves
> byte-identically to 3.10.

## 0. Point the agent at the 3.11 library

In the agent's `pyproject.toml` (path dep, as the test agents already use):

```toml
dependencies = [
    "penguiflow-lite[planner] @ file:///absolute/path/to/penguiflow-lite",
]
```

Then, from the agent directory, after every library change:

```bash
git -C /path/to/penguiflow checkout release/3.11
uv sync --reinstall-package penguiflow
uv run python -c "import penguiflow; print(penguiflow.__version__)"   # 3.11.0a1
```

## 1. Native tool-calling mode (Phase 5 — the big one)

**What it is:** tool intent travels as provider-native function calls instead
of the prompted `{next_node, args}` JSON envelope. Same `PlannerAction`
decision shape downstream — parallel fanout, trajectory, events, pause/resume,
A2A are unchanged.

**Requirements:**
- `use_native_llm=True` (the litellm client is deprecated and has no native
  mode — a planner on litellm downgrades to prompted with an event).
- A tool-capable model whose route allows native function calling
  (`ModelProfile.supports_tools` AND `supports_native_tool_calls`).
  Known gate: `databricks-gpt-5-5` / `-pro` (chat-completions rejects tools;
  the planner downgrades to prompted automatically, never fails).
  Good first choice: `databricks/databricks-claude-opus-4-8`.

**Activation** — one parameter:

```python
planner = ReactPlanner(
    llm=config.llm_model,
    use_native_llm=True,
    tool_call_mode="native",        # default "prompted" = today's behavior
    stream_final_response=True,     # to see token-by-token answer streaming
    ...
)
```

Recommended: make it env-driven so you can flip without code changes
(pattern used in the youtube agent):

```python
# config.py
planner_tool_call_mode: str = "prompted"
# from_env():
planner_tool_call_mode=os.getenv("PLANNER_TOOL_CALL_MODE", "prompted"),

# planner construction
tool_call_mode=config.planner_tool_call_mode,
```

**What to verify in native mode:**

| Behavior | Expectation |
|---|---|
| Answer streaming | Token-by-token on the `answer` channel (`llm_stream_chunk`), parity-or-better vs prompted |
| Tool turns | Silent on the content channel (the prompt forbids preamble); tool calls arrive as `tool_call_start/...` events as before |
| Parallel | Multiple native calls in one turn → the existing parallel plan (no model-expressed joins in v1) |
| MCP tools with dotted names | Work automatically (wire-safe aliases, mapped back) |
| Ineligible model / client | One `tool_call_mode_downgraded` event per run, then prompted behavior |
| Disobedient preamble (rare) | Answer stream closes with `extra.superseded: true`, text re-emitted on `thinking` — teach your UI to honor the marker if you want pixel-perfect retraction |
| multi_action / auto_seq | Bypassed by design (parallel tool calls cover it) |

## 2. Structured final answers (Phase 2)

```python
from pydantic import BaseModel

class Report(BaseModel):
    answer: str
    confidence: float
    sources_used: list[str]

planner = ReactPlanner(..., final_response_model=Report, final_response_retries=1)
result = await planner.run(query)

result.payload["structured"]   # validated dict (json dump of Report) — or None
result.payload["warnings"]     # carries the degradation notice when None
```

**The guarantee:** `structured` either validated against your model or is
`None` — never unvalidated data. Watch events:
`final_response_structured_validated` (extra.repaired tells you if a
corrective turn ran), `..._repair_attempt`, `..._degraded`.

**In native mode** the structured payload always comes from one extraction
turn (content-finish carries no JSON), so expect exactly one extra LLM call
per run and `repaired: true`. Don't set `final_response_retries=0` in native
mode — it would always degrade.

## 3. Multimodal inputs (Phase 3)

```python
from penguiflow.llm.types import ImagePart, AudioPart

result = await planner.run(
    "What does this screenshot show?",
    input_parts=[ImagePart(data=png_bytes, media_type="image/png")],
)
```

- Parts attach to the initial user message only; trajectory summaries carry
  metadata stubs (`type`, `media_type`, `bytes`), never binary.
- Model must have `supports_image_input` / `supports_audio_input` in its
  profile — unsupported models fail loudly (`LLMInvalidRequestError`), no
  silent dropping. Use `register_profile()` to enable for your model.
- Inline parts are capped at 32 KiB by default; real photos need
  `create_native_adapter(..., multimodal_inline_data_limit_bytes=N)` or the
  same kwarg through the planner's LLM config dict. URL/artifact supply forms
  are not in 3.11.

## 4. pydantic-ai transport (Phase 1)

The wiring is automatic and profile-driven: every `ReactPlanner(llm=...,
use_native_llm=True)` resolves its transport per model via
`ModelProfile.preferred_transport`. Today **no profile prefers
"pydantic-ai"** — that flip is deliberately gated behind the Phase 6 parity
matrix — so to test it you force it, planner-level:

```python
# pip install 'penguiflow-lite[pydantic-ai]'
planner = ReactPlanner(
    llm="claude-sonnet-4-5",
    use_native_llm=True,
    llm_transport="pydantic-ai",   # forces ALL of this planner's clients
    ...
)
```

(Equivalent lower-level form: `llm_client=create_native_adapter(model,
transport="pydantic-ai")`.)

- Resolution per client: explicit (`llm_transport` / `transport`) >
  `ModelProfile.preferred_transport` > `"native"`. When Phase 6 flips a model
  family's profile, agents pick it up with zero code changes — that's the
  designed end state; the kwarg exists for testing ahead of the flip.
- Operators can flip a single model today without a release:
  `register_profile("my-model", ModelProfile(..., preferred_transport="pydantic-ai"))`.
- Databricks Claude reasoning models are profile-pinned to the native
  transport (the generic one can't parse their reasoning content blocks);
  the explicit kwarg overrides the pin if you want to test anyway.
- Works with `tool_call_mode="native"` and all features above.

## 5. Tracing + cost (Phase 4 + 3.11.0a1 seam)

Zero code changes:

```bash
export PENGUIFLOW_LLM_TRACING=mlflow   # or "log" for structured log lines
```

One span per LLM call (model, provider, tokens, cost, latency, errors; never
message contents), including every fallback-chain adapter — so model failover
is visible per model actually called. Runnable example:
`examples/mlflow_llm_tracing/`.

Cost: with `penguiflow-lite[pricing]` installed, `genai-prices` is the price
source behind the unchanged facade; `register_pricing()` still wins for
private rates.

## 6. Observability cheat sheet (what to watch in your event stream)

| Event / signal | Meaning |
|---|---|
| `llm_stream_chunk` `channel=answer` | User-visible answer tokens (gate: `action_seq` must match `step_start`'s) |
| `llm_stream_chunk` `channel=thinking` | Reasoning deltas / retracted preambles / prompted-mode thought |
| `extra.superseded: true` on answer done | Retract the streamed text (rare; native mode) |
| `tool_call_mode_downgraded` | Native→prompted fallback, with reason |
| `final_response_structured_*` | Structured answer lifecycle |
| `databricks_reasoning_redacted_by_route` (log) | The model IS thinking; the route returns signature-only blocks — nothing visible to render. Known for Databricks Claude Opus 4.7/4.8 on the dev workspace; if YOUR workspace shows visible thinking, capture one event sample — see doc §12 "production contradiction" |

## 7. Known caveats in 3.11.0a1

- Native mode requires the native LLM layer (`use_native_llm=True`).
- Joins are not model-expressible in native parallel plans (v1).
- `ReactPlanner` still defaults `use_native_llm=False` (litellm,
  deprecated) — flip decision pending Phase 6.
- Audio input is implemented and unit-tested but not yet live-validated
  (no audio-capable route in the dev key set).
- Native-mode tool-turn preamble is suppressed by prompt; whether to expose
  it as a UX status affordance is an open question for the upstream UI team
  (docs/LLM_TRANSPORT_ANALYSIS.md §12.9).

## 8. Rollback

Everything is opt-in: unset `PLANNER_TOOL_CALL_MODE` (or set `prompted`),
drop `final_response_model` / `input_parts` / `transport` / the tracing env
var, and behavior is byte-identical to 3.10. The library can be downgraded
independently since no API was broken.
