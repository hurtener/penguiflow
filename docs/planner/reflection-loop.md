# ReactPlanner reflection loop

## What it is / when to use it

`ReactPlanner` includes an optional **reflection loop** that runs **after** the planner has produced a candidate final answer.

Use it when you want the planner to:

- critique answer quality before returning,
- revise incomplete or weak answers automatically,
- avoid returning low-quality answers when the available tools/data are insufficient,
- expose structured quality signals in logs, events, metadata, and cost reporting.

This is useful for external-agent integrations, customer-facing assistants, and production flows where "return something fast" is less important than "return something defensible".

## What it is not

- It is **not** a second tool-calling agent.
- It does **not** re-run the whole tool plan from scratch.
- It is **not** an arbitrary evaluator framework; it is a built-in post-answer critique and revision loop inside `ReactPlanner`.

## How the loop works

At a high level, the runtime does this:

1. Run the normal ReAct/tool loop until the planner produces a candidate answer.
2. If reflection is enabled, ask an LLM for a structured `ReflectionCritique`.
3. If the critique passes, return the answer unchanged.
4. If the critique fails and revision budget remains, ask the **main planner LLM** for another planner-schema response intended to improve the answer.
5. Critique the revised answer again.
6. If the answer still fails after `max_revisions`, generate an honest clarification response instead of silently returning a weak answer.

The critique payload is structured and includes:

- `score`
- `passed`
- `feedback`
- `issues`
- `suggestions`

Important behavior:

- The **separate reflection model**, when configured, is used for the **critique step**.
- The revision request is sent to the **main planner client**.
- In the current tests and examples, that revision response is a revised final-answer payload.
- If reflection passes on the first critique, the end user usually notices **no behavioral change** beyond extra latency and one extra LLM call.

## Public API imports

Import the reflection types from the public planner API:

```python
from penguiflow.planner import ReactPlanner, ReflectionConfig
```

Do not depend on internal modules such as `penguiflow.planner.react` unless you are working on PenguiFlow internals.

## Minimal configuration

This example enables reflection using the same model for planning and critique.

```python
from __future__ import annotations

from pydantic import BaseModel

from penguiflow import ModelRegistry, Node
from penguiflow.catalog import build_catalog, tool
from penguiflow.planner import ReactPlanner, ReflectionConfig, ToolContext


class SearchArgs(BaseModel):
    question: str


class SearchResult(BaseModel):
    answer: str


@tool(desc="Simple search", side_effects="read")
async def search(args: SearchArgs, ctx: ToolContext) -> SearchResult:
    del ctx
    return SearchResult(answer=f"Result for: {args.question}")


registry = ModelRegistry()
registry.register("search", SearchArgs, SearchResult)
catalog = build_catalog([Node(search, name="search")], registry)

planner = ReactPlanner(
    llm="openai/gpt-4o",
    use_native_llm=True,
    catalog=catalog,
    reflection_config=ReflectionConfig(
        enabled=True,
        quality_threshold=0.85,
        max_revisions=2,
    ),
)
```

## Using a separate critique model

Use this when you want a cheaper or faster model to judge answer quality.

```python
from penguiflow.planner import ReactPlanner, ReflectionConfig


planner = ReactPlanner(
    llm="openai/gpt-4o",
    use_native_llm=True,
    catalog=catalog,
    reflection_config=ReflectionConfig(
        enabled=True,
        quality_threshold=0.85,
        max_revisions=2,
        use_separate_llm=True,
    ),
    reflection_llm="openai/gpt-4o-mini",
)
```

Rules:

- If `use_separate_llm=True`, you must provide `reflection_llm`.
- `reflection_llm` is for critique only; revisions still come from the main planner client.
- If your app provides a custom `llm_client`, verify the auxiliary reflection-client wiring in your planner factory and tests instead of assuming the generic string-based path.

## Custom critique criteria

You can tune what "good enough" means:

```python
from penguiflow.planner import ReactPlanner, ReflectionConfig, ReflectionCriteria


planner = ReactPlanner(
    llm="openai/gpt-4o",
    use_native_llm=True,
    catalog=catalog,
    reflection_config=ReflectionConfig(
        enabled=True,
        quality_threshold=0.9,
        max_revisions=2,
        criteria=ReflectionCriteria(
            completeness="Addresses all requested sub-questions",
            accuracy="Grounded in tool results and avoids unsupported claims",
            clarity="Clear, well-structured, and actionable",
        ),
    ),
)
```

## Wiring reflection from environment variables

Environment variables alone are not enough. The planner factory must pass `reflection_config=...` into `ReactPlanner(...)`.

### `config.py`

Add reflection fields and load them from env:

```python
from dataclasses import dataclass
import os


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw is not None else default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw is not None else default


@dataclass
class Config:
    llm_model: str = "openai/gpt-4o"
    reflection_enabled: bool = False
    reflection_model: str | None = None
    reflection_quality_threshold: float = 0.80
    reflection_max_revisions: int = 2
    reflection_use_separate_llm: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            llm_model=os.getenv("LLM_MODEL", "openai/gpt-4o"),
            reflection_enabled=_env_flag("REFLECTION_ENABLED", False),
            reflection_model=os.getenv("REFLECTION_MODEL"),
            reflection_quality_threshold=_env_float("REFLECTION_QUALITY_THRESHOLD", 0.80),
            reflection_max_revisions=_env_int("REFLECTION_MAX_REVISIONS", 2),
            reflection_use_separate_llm=_env_flag("REFLECTION_USE_SEPARATE_LLM", False),
        )
```

### `planner.py`

Build and pass the reflection config:

```python
from penguiflow.planner import ReactPlanner, ReflectionConfig


reflection_config = None
reflection_llm = None

if config.reflection_enabled:
    reflection_config = ReflectionConfig(
        enabled=True,
        quality_threshold=config.reflection_quality_threshold,
        max_revisions=config.reflection_max_revisions,
        use_separate_llm=config.reflection_use_separate_llm,
    )

    if config.reflection_use_separate_llm:
        reflection_llm = config.reflection_model


planner = ReactPlanner(
    llm=config.llm_model,
    use_native_llm=True,
    catalog=catalog,
    reflection_config=reflection_config,
    reflection_llm=reflection_llm,
)
```

### Environment variables

Shared-model setup:

```env
REFLECTION_ENABLED=true
REFLECTION_QUALITY_THRESHOLD=0.85
REFLECTION_MAX_REVISIONS=2
```

Separate critique-model setup:

```env
REFLECTION_ENABLED=true
REFLECTION_USE_SEPARATE_LLM=true
REFLECTION_MODEL=openai/gpt-4o-mini
REFLECTION_QUALITY_THRESHOLD=0.85
REFLECTION_MAX_REVISIONS=2
```

Naming note:

- The `config.py` example above uses `REFLECTION_MODEL`.
- The enterprise example uses `REFLECTION_LLM`.
- The `ReactPlanner(...)` constructor argument is `reflection_llm`.

## What to expect at runtime

### When the answer passes on the first critique

Usually:

- the user sees the same final answer they would have seen without reflection,
- there is one extra critique call,
- latency increases slightly,
- logs/metadata show one reflection pass.

This is the most common "nothing looks different, but quality control ran" case.

### When the answer fails and gets revised

Usually:

- you will see one or more critique attempts,
- the final answer may be more complete or better grounded,
- overall latency increases more noticeably.

### When the answer still fails after max revisions

The planner does not just return the weak answer. It generates a structured clarification response that:

- explains what was tried,
- asks clarifying questions,
- suggests what additional information or tools would help,
- marks the answer as unsatisfied.

## Observability: events, metadata, and cost

### Planner events

The reflection loop emits structured planner events such as:

- `reflection_critique`
- `reflection_clarification_generated`

The `reflection_critique` event includes fields such as:

- `score`
- `passed`
- `revision`
- `feedback`

### Result metadata

Completed runs include reflection metadata when reflection was active:

```python
result = await planner.run("Explain parallel execution with error recovery")

print(result.metadata["reflection"])
```

Example:

```python
{
    "score": 0.95,
    "revisions": 1,
    "passed": True,
    "feedback": "Answer now covers both parallel execution and error recovery",
}
```

### Cost metadata

Reflection calls are tracked separately:

```python
print(result.metadata["cost"]["reflection_llm_calls"])
```

This is useful when comparing:

- reflection enabled vs disabled,
- shared-model critique vs separate cheaper critique model.

## Troubleshooting

### "I set env vars, but nothing changed"

Most likely causes:

- your app never passes `reflection_config=...` into `ReactPlanner(...)`,
- the planner factory has no reflection wiring and only the env file was updated,
- reflection is enabled but the answer is already passing on the first critique, so the only change is latency/cost.

### `reflection_llm required when use_separate_llm=True`

You enabled a separate critique model without setting `reflection_llm`.

### "The answer changed, but there were no extra tool calls"

That can still be normal. In the current implementation, reflection revisions typically update the final-answer payload without executing another tool pass.

### "How do I know reflection really ran?"

Check one or more of:

- `result.metadata["reflection"]`
- `result.metadata["cost"]["reflection_llm_calls"]`
- planner events containing `reflection_critique`
- logs showing reflection-related event names or feedback

## Reference implementations in this repo

- `tests/test_react_reflection.py` - pass, revise, fail-to-clarification, budget, and event coverage
- `examples/planner_enterprise_agent_v2/main.py` - env-driven production pattern
- `examples/planner_enterprise_agent_v2/config.py` - reflection-oriented config surface
- `penguiflow/planner/react_runtime.py` - reflection loop runtime
- `penguiflow/planner/llm.py` - critique, revision, and clarification helpers
