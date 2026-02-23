# Background tasks (subagents and tool jobs)

## What it is / when to use it

Background tasks let a `ReactPlanner` spawn **concurrent work** while the foreground agent continues:

- spawn a **subagent task** to do multi-step reasoning/tools in the background
- spawn a **tool job** to execute a single tool call in the background
- group tasks and merge results back into context with controlled strategies

Use background tasks when you need:

- responsiveness (foreground can keep chatting while long work runs),
- parallelism beyond a single `parallel` step,
- asynchronous tool execution with delayed, reviewable context patches.

## Non-goals / boundaries

- Background tasks do not provide a distributed queue by default. The library defines **contracts** (`TaskService`) and includes an in-process implementation for local use.
- Background tasks do not automatically make unsafe tools safe. You still need allowlists, HITL, and guardrails.
- “Tool job” mode is intentionally limited: it runs a single tool call and does not support pause/resume semantics inside the job pipeline.

## Contract surface

### Planner configuration: `BackgroundTasksConfig`

Enable background tasks by passing `BackgroundTasksConfig`:

- `ReactPlanner(..., background_tasks=BackgroundTasksConfig(enabled=True, ...))`

Key knobs (from `penguiflow.planner.models.BackgroundTasksConfig`):

- enablement:
  - `enabled`
  - `include_prompt_guidance`
- tool-initiated background spawns:
  - `allow_tool_background`
  - `default_mode` (`subagent` vs `job`)
  - `default_merge_strategy` (`HUMAN_GATED` / `APPEND` / `REPLACE`)
- limits:
  - `max_concurrent_tasks`, `max_tasks_per_session`, `task_timeout_s`
- proactive reporting (auto-merge modes):
  - `proactive_report_enabled`, `proactive_report_max_hops`, …
- task groups:
  - `default_group_merge_strategy`, `default_group_report`, `max_tasks_per_group`, …

### The tasks.* tool surface (foreground-only)

Background task orchestration is exposed to the planner through a tool surface:

- `tasks.spawn`, `tasks.list`, `tasks.get`, `tasks.cancel`, `tasks.prioritize`, …
- task-group tools: `tasks.seal_group`, `tasks.list_groups`, `tasks.apply_group`, …

You can add these tools to your catalog via:

- `penguiflow.sessions.task_tools.build_task_tool_specs()`

These tools require:

- `tool_context["task_service"]`: an object implementing the `TaskService` protocol
- `tool_context["session_id"]`: the active session id (string)

!!! warning
    Expose tasks.* tools only to the **foreground** agent. Subagents must not be able to spawn/manage additional tasks unless you explicitly intend recursive agents.

### LLM action opcodes: `task.subagent` and `task.tool`

The planner action schema includes opcodes:

- `next_node="task.subagent"`
- `next_node="task.tool"`

At runtime these are normalized into a `tasks.spawn` tool call (mode `subagent` or `job`).

See **[Actions & schema](actions-and-schema.md)** for the LLM-facing shape.

### Tool-initiated background spawns (ToolNode-style behavior, but planner-level)

Independently of the LLM choosing `tasks.spawn`, a tool call can spawn a background task **instead of running inline** when all are true:

- planner config: `background_tasks.allow_tool_background=True`
- tool spec metadata: `spec.extra["background"]["enabled"] is True`
- runtime context: `tool_context["task_service"]` exists and `tool_context["session_id"]` is a string

When this happens, the tool call returns a `BackgroundTaskHandle` observation:

- `{ "task_id": "...", "status": "PENDING", "message": "spawned:job|subagent" }`

This is intended for “slow but safe” integrations (e.g., scraping, batch ETL) where foreground can continue.

## Operational defaults (recommended)

- Start with:
  - `enabled=True`
  - `allow_tool_background=False` until you have strong idempotency + observability
  - `default_merge_strategy="HUMAN_GATED"` (review before merging context)
- Enforce ceilings (`max_concurrent_tasks`, `max_tasks_per_session`) and alert when hit.
- Prefer task groups for multi-step background work so the user gets one coherent report instead of many partial updates.

## Failure modes & recovery

### `task_service_unavailable` / tasks.* tools fail

**Likely cause**

- `tool_context["task_service"]` not set (or wrong object)

**Fix**

- wire a `TaskService` implementation and attach it to `tool_context` for foreground calls.

### `session_id_missing`

**Likely cause**

- you didn’t pass `tool_context["session_id"]`

**Fix**

- set `session_id` for every call (also improves planner concurrency via per-session dispatch).

### Too many tasks / runaway recursion

**Symptoms**

- repeated spawning, queue growth, high tool latency, or “proactive hops exhausted”

**Fix**

- enforce limits in `BackgroundTasksConfig`
- keep `proactive_report_max_hops` small (default is 2)
- do not expose tasks.* tools to subagents

## Observability

Recommended signals:

- task spawn rate and active task count per session
- merge strategy distribution (HUMAN_GATED vs auto-merge)
- task latency, cancel rate, failure rate
- proactive report queue depth and drop rate

If you run session-backed tasks, emit structured updates and persist task state (see `penguiflow.sessions`).

See **[Planner observability](observability.md)** and the ops guidance in **[Production deployment](../deployment/production-deployment.md)**.

## Security / multi-tenancy notes

- Task spawning is a privileged operation. Treat it like “tool execution” and gate it per tenant/user.
- Subagents should receive a constrained catalog (read-only, narrow scope).
- Do not allow background tasks to inherit sensitive `tool_context` objects unless you control the task boundary tightly.

## Runnable example (contract-only)

This example shows the **contract wiring** (planner can call `tasks.spawn`) without implementing a full session runtime.

```python
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from penguiflow.planner import ReactPlanner
from penguiflow.planner.models import JSONLLMClient
from penguiflow.planner.models import BackgroundTasksConfig
from penguiflow.sessions.models import TaskStatus
from penguiflow.sessions.task_service import TaskSpawnResult
from penguiflow.sessions.task_tools import build_task_tool_specs


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
        del messages, response_format, stream, on_stream_chunk
        self._step += 1
        if self._step == 1:
            return json.dumps(
                {
                    "next_node": "tasks.spawn",
                    "args": {"mode": "subagent", "query": "Background: do something"},
                },
                ensure_ascii=False,
            )
        return json.dumps({"next_node": "final_response", "args": {"answer": "spawned"}}, ensure_ascii=False)


class FakeTaskService:
    async def spawn(self, *, session_id: str, query: str, **kwargs: Any) -> TaskSpawnResult:
        del query, kwargs
        return TaskSpawnResult(task_id="tsk_demo", session_id=session_id, status=TaskStatus.PENDING)

    async def spawn_tool_job(self, *, session_id: str, tool_name: str, tool_args: Any, **kwargs: Any) -> TaskSpawnResult:
        del tool_name, tool_args, kwargs
        return TaskSpawnResult(task_id="tsk_demo", session_id=session_id, status=TaskStatus.PENDING)


async def main() -> None:
    catalog = build_task_tool_specs()
    planner = ReactPlanner(
        llm_client=ScriptedClient(),
        catalog=catalog,
        background_tasks=BackgroundTasksConfig(enabled=True, include_prompt_guidance=False),
    )
    await planner.run(
        "demo",
        tool_context={"session_id": "demo", "task_service": FakeTaskService()},
    )


if __name__ == "__main__":
    asyncio.run(main())
```

!!! note
    Real production setups typically use `penguiflow.sessions` (StreamingSession + SessionManager) or a custom `TaskService` backed by your queue/database.

## Troubleshooting checklist

- Are tasks.* tools in the catalog for the **foreground** agent?
- Is `tool_context["task_service"]` set and implementing the needed methods?
- Is `tool_context["session_id"]` present?
- Are you keeping task merge strategies safe-by-default (`HUMAN_GATED`)?
- Are you preventing subagents from spawning/managing tasks unless intended?
