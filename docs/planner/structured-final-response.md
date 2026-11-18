# Structured final response (`final_response_model`)

> **Audience:** teams adopting PenguiFlow's `ReactPlanner`.
> **What you get:** the planner's final answer as a **schema-validated Pydantic object**
> (in `result.payload["structured"]`) *in addition to* the human-readable text — with
> bounded self-repair and a safe degradation path so you never receive unvalidated data.

## What it is / when to use it

By default `ReactPlanner.run(...)` returns a free-text answer in
`result.payload["raw_answer"]`. That is great for humans, awkward for code.

`final_response_model` lets you attach a Pydantic model to the planner. When set:

- The model's JSON schema is injected into the planner's finishing instructions, so the
  LLM emits a machine-readable `structured` object alongside the human answer.
- The planner **validates** that object against your model before returning.
- If validation fails, the planner runs a **bounded corrective turn** (a "repair") asking
  the LLM to fix the payload against the schema.
- If repair is exhausted, the planner **degrades safely**: `structured` becomes `None`,
  a warning is added to `payload["warnings"]`, and a `final_response_structured_degraded`
  event fires. **`structured` never carries unvalidated data.**

Use it whenever a downstream system (a UI, an API, a workflow step) needs to consume the
planner's answer as typed data — classifications, extractions, verdicts, scores, routing
decisions, etc.

## Non-goals / boundaries

- It does **not** change how tools are typed. Tool I/O is still validated by the
  `ModelRegistry`; this feature is only about the *final* answer.
- It is **not** rich UI output. For UI artifacts (charts, tables, forms) see
  [Rich output](rich-output.md). The two are independent and can be combined.
- It works in **both** planner modes: the default prompted mode (`tool_call_mode="prompted"`)
  and native tool-calling mode (`tool_call_mode="native"`). See
  [Native tool-calling mode](#native-tool-calling-mode) for the small behavioural difference.
- It is **off by default**: omit `final_response_model` and there is **no behavioral change
  and no extra LLM calls**. The `structured` key is always present in the payload (it simply
  stays `null` when the feature is unused), so consumers can read `payload["structured"]`
  unconditionally.

---

## Step-by-step end to end

### Step 1 — Install / environment

You need the planner extra and a configured LLM provider. PenguiFlow reads provider
credentials from the environment (a `.env` works with `python-dotenv`):

```bash
uv add "penguiflow-lite[planner]"        # or: pip install "penguiflow-lite[planner]"
```

```dotenv
# .env  — example for Databricks-hosted Claude (any LiteLLM-supported provider works)
DATABRICKS_API_BASE=https://<your-workspace>.cloud.databricks.com/serving-endpoints
DATABRICKS_API_KEY=<token>
```

> `load_dotenv()` (used below) reads `.env` from the **current working directory**. Run your
> script from the directory that holds `.env`, or pass an explicit path:
> `load_dotenv("/abs/path/to/.env")`.

### Step 2 — Define your final-answer model

This is an ordinary Pydantic v2 model. Add `Field(description=...)` — the descriptions are
sent to the LLM as part of the schema and materially improve first-pass accuracy.

```python
from pydantic import BaseModel, Field


class Verdict(BaseModel):
    """The structured final answer the planner must produce."""

    answer: int = Field(description="The numeric result of the computation.")
    method: str = Field(description="One short phrase naming how it was solved.")
    confident: bool = Field(default=True, description="Whether the answer is trustworthy.")
```

Keep it small and flat. Models with many constraints (regex, tight min/max, deeply nested
objects) are more likely to need a repair turn.

### Step 3 — Build your tools and catalog (unchanged)

Structured final response sits on top of a normal planner setup — your tools are wired
exactly as usual.

```python
from pydantic import BaseModel

from penguiflow import ModelRegistry, Node
from penguiflow.catalog import build_catalog


class Query(BaseModel):
    text: str


class MathResult(BaseModel):
    product: int


async def multiply(payload: Query, ctx: object) -> MathResult:
    a, b = (int(p) for p in payload.text.replace("?", "").split("*"))
    return MathResult(product=a * b)


registry = ModelRegistry()
registry.register("multiply", Query, MathResult)
catalog = build_catalog([Node(multiply, name="multiply")], registry)
```

### Step 4 — Construct the planner with `final_response_model`

The only new arguments are `final_response_model` and (optionally) `final_response_retries`
(how many corrective turns to attempt before degrading; default `1`).

```python
from dotenv import load_dotenv

from penguiflow.planner import ReactPlanner

load_dotenv()  # load provider creds from .env

planner = ReactPlanner(
    llm="databricks/databricks-claude-sonnet-4-5",   # any LiteLLM model id
    catalog=catalog,
    final_response_model=Verdict,    # <-- opt in here
    final_response_retries=1,        # corrective turns before degrading (default 1)
)
```

> Already have a custom client? Pass `llm_client=<your JSONLLMClient>` instead of `llm=...`.
> `final_response_model` works the same way regardless of how the planner reaches the LLM.

### Step 5 — Run and consume the typed result

```python
import asyncio


async def main() -> None:
    result = await planner.run("Use the multiply tool: what is 17*23?")

    print(result.reason)                      # "answer_complete"
    print(result.payload["raw_answer"])       # "17 × 23 = 391"   (human text)

    structured = result.payload["structured"]  # dict | None
    if structured is not None:
        verdict = Verdict.model_validate(structured)   # re-hydrate the typed object
        print(verdict.answer, verdict.method, verdict.confident)
    else:
        # Validation degraded — see payload["warnings"]
        print("No structured answer:", result.payload.get("warnings"))


asyncio.run(main())
```

`result.payload["structured"]` is a plain JSON-safe `dict` (the validated model dumped with
`mode="json"`), or `None` if the planner degraded. Re-hydrate it with
`YourModel.model_validate(...)` when you want the typed instance back.

> The example assumes a **completed** run (`PlannerFinish`, `reason == "answer_complete"`).
> If you use pause/resume (HITL), `run(...)` can instead return a `PlannerPause`, which has
> no `raw_answer`/`structured` — check `result.reason` before reading the payload.

### Step 6 (optional) — Observe validation / repair / degradation

Attach an `event_callback` to see exactly what happened. Three event types are emitted:

| Event type                                  | When it fires                                            |
| ------------------------------------------- | -------------------------------------------------------- |
| `final_response_structured_validated`       | Payload validated. `extra["repaired"]` is `True`/`False`. |
| `final_response_structured_repair_attempt`  | A corrective turn was issued. `extra["attempt"]` is the attempt number; the validation error is on the top-level `event.error` field (not in `extra`). |
| `final_response_structured_degraded`        | Repair exhausted; `structured` set to `None`, warning added. The last error is on `event.error`. |

```python
import asyncio

from penguiflow.planner.models import PlannerEvent


async def main() -> None:
    events: list[PlannerEvent] = []
    planner = ReactPlanner(
        llm="databricks/databricks-claude-sonnet-4-5",
        catalog=catalog,
        final_response_model=Verdict,
        event_callback=events.append,
    )

    await planner.run("Use the multiply tool: what is 17*23?")

    validated = [e for e in events if e.event_type == "final_response_structured_validated"]
    print("repaired:", validated[0].extra["repaired"] if validated else "n/a")


asyncio.run(main())
```

---

## Behaviour reference

| Scenario | `payload["structured"]` | Extra LLM calls | Events |
| --- | --- | --- | --- |
| Model returns valid payload | validated `dict` | 0 | `…_validated` (`repaired=False`) |
| Invalid payload, repair succeeds | validated `dict` | 1 per attempt | `…_repair_attempt` + `…_validated` (`repaired=True`) |
| Invalid payload, `final_response_retries=0` | `None` | 0 | `…_degraded` |
| Repair exhausted | `None` + warning | up to `retries` | `…_repair_attempt`(s) + `…_degraded` |
| `final_response_model` unset (default) | `None` | 0 | none |

Key guarantee: **`payload["structured"]` is either schema-valid or `None` — never raw,
unvalidated model output.**

## Native tool-calling mode

`final_response_model` is fully supported when the planner runs in native tool-calling mode
(`tool_call_mode="native"`). In that mode the planner declares a synthetic `final_response`
tool that carries the `structured` object. The model finishes in a single turn by **writing
the answer as plain text and calling that tool together**: the plain text streams to the user
token-by-token (just like prompted mode), while the `structured` payload arrives as
**provider-validated function-call arguments** and validates on the first pass — no repair turn:

```python
planner = ReactPlanner(
    llm="databricks/databricks-claude-sonnet-4-5",
    catalog=catalog,
    use_native_llm=True,         # native adapter (exposes provider function calling)
    tool_call_mode="native",
    final_response_model=Verdict,
    stream_final_response=True,  # answer streams on the answer channel
)
```

So both modes stream the answer and both validate structured on the first pass. The finishing
turn is the one place where the native planner intentionally emits plain text alongside a tool
call; the streamed text is kept as the answer rather than being treated as a superseded
preamble. The finish tool also accepts an optional `answer` field as a fallback for the rare
turn where the model puts the answer in the call instead of as text; if a finish somehow
arrives without a structured payload, the same bounded repair turn recovers it.

> Native answer streaming depends on the model emitting text *and* the `final_response` call in
> the same turn. Claude-family models do this reliably. A model that suppresses text when it
> emits a tool call would instead return the answer via the tool's optional `answer` field —
> still correct and still validated, but delivered in one chunk rather than streamed. Structured
> validation is unaffected either way.

## Caveats

- **Cost of strict schemas.** Heavily constrained models trigger more repair turns, each of
  which is an additional LLM call. Loosen constraints or raise `final_response_retries` as
  needed, and watch `final_response_structured_repair_attempt` events in production.
- **Forks inherit the setting.** Background-task / per-session forks of the planner preserve
  `final_response_model` and `final_response_retries`, so structured output is consistent
  across forked runs.

## Verifying locally against a real model

A manual end-to-end harness lives at `scripts/live_structured_final_test.py`. With a
populated `.env` it exercises the happy path, the default-off parity case, and a strict
schema (validation/repair) against a live endpoint:

```bash
LLM_MODEL_LIVE=databricks/databricks-claude-sonnet-4-5 \
  uv run python scripts/live_structured_final_test.py
```

The deterministic unit tests (stubbed client, all branches incl. repair and degradation)
are in `tests/test_react_structured_final.py`:

```bash
uv run pytest tests/test_react_structured_final.py -q
```
