# ReactPlanner Configuration Templates (copy/paste)

This file is intentionally “template-heavy”. Keep `SKILL.md` lean and load this only when you need concrete code.

## Always-visible tool set (best practice)

If you use tool discovery + deferred activation, ensure these tools are **always visible** (and never denied by policy):

```py
ALWAYS_VISIBLE_TOOLS = {
    # discovery
    "tool_search",
    "tool_get",
    # skills
    "skill_search",
    "skill_get",
    "skill_list",
    # common
    "finish",
}

RICH_OUTPUT_TOOLS = {
    "render_component",
    "describe_component",
    "list_artifacts",
    "ui_form",
    "ui_confirm",
    "ui_select_option",
}

ALWAYS_VISIBLE_PATTERNS = [
    # tool discovery
    "tool_search",
    "tool_get",
    # skills
    "skill_search",
    "skill_get",
    "skill_list",
    # tasks (keep visible if background tasks are enabled in your host app)
    "tasks.*",
    # common
    "finish",
]
```

Notes:
- `ToolSearchConfig.always_loaded_patterns` uses glob patterns (via `fnmatch`).
- Only add rich output tools to the always-visible set if rich output is enabled.

## Native LLM: minimal configuration

```py
from penguiflow.planner.react import ReactPlanner

planner = ReactPlanner(
    llm={"model": "openai/gpt-4o", "api_key": "...", "base_url": None},
    use_native_llm=True,
    temperature=0.0,
    json_schema_mode=True,
)
```

Model string formats supported by the native layer include:
- `"openai/gpt-4o"` or `"gpt-4o"`
- `"anthropic/claude-3-5-sonnet"` or `"claude-3-5-sonnet"`
- `"google/gemini-2.0-flash"` or `"gemini-2.0-flash"`
- `"nim/<...>"`, `"bedrock/<...>"`, `"openrouter/<...>"`, etc.

## Thinking / reasoning knobs (native reasoning + effort)

ReactPlanner exposes:
- `use_native_reasoning: bool` (default `True`)
- `reasoning_effort: str | None` (commonly `"low" | "medium" | "high"`, provider/model dependent)

Recommended defaults:
- Keep `use_native_reasoning=True`.
- Set `reasoning_effort="medium"` for most production agents.
- Use `"low"` for latency-sensitive UX loops, `"high"` for complex planning / heavy synthesis.

```py
planner = ReactPlanner(
    llm="openai/gpt-4o",
    use_native_llm=True,
    use_native_reasoning=True,
    reasoning_effort="medium",
)
```

## Tool discovery + deferred activation (recommended “enterprise default”)

```py
from penguiflow.planner.models import ToolSearchConfig
from penguiflow.catalog import ToolLoadingMode

tool_search = ToolSearchConfig(
    enabled=True,
    default_loading_mode=ToolLoadingMode.DEFERRED,
    always_loaded_patterns=ALWAYS_VISIBLE_PATTERNS,
    activation_scope="session",  # requires tool_context["session_id"] for run/resume
    max_search_results=10,
)
```

Call sites must pass `tool_context["session_id"]` whenever `activation_scope="session"`:

```py
result = await planner.run(
    "…",
    tool_context={
        "session_id": session_id,   # required for session-scoped tool activation
        "tenant_id": tenant_id,     # recommended for multi-tenant visibility + skills scoping
        "project_id": project_id,   # recommended (optional)
    },
)
```

## Budgets and safety limits (recommended “guardrails on by default”)

These knobs are designed to prevent runaway runs and oversized context:

```py
from penguiflow.planner.models import ObservationGuardrailConfig

planner = ReactPlanner(
    llm="openai/gpt-4o",
    use_native_llm=True,
    # LLM transport-level limits
    llm_timeout_s=60.0,
    llm_max_retries=3,
    # Run-level limits
    deadline_s=120.0,        # wall-clock seconds
    hop_budget=40,           # max tool invocations
    # Context growth control
    token_budget=20_000,     # triggers trajectory summarization when history grows (heuristic estimate)
    summarizer_llm="openai/gpt-4o-mini",  # optional cheaper model for summarization
    # Output-size safety net for tool observations
    observation_guardrail=ObservationGuardrailConfig(
        max_observation_chars=50_000,
        auto_artifact_threshold=20_000,
    ),
    # Parallel safety
    absolute_max_parallel=50,
)
```

Operational guidance:
- Start with `deadline_s` + `hop_budget` even for internal agents.
- Set `token_budget` when you expect long sessions; provide `summarizer_llm` to control cost.
- Treat `ObservationGuardrailConfig` as mandatory in multi-tenant environments.

## Allowlisting: ToolPolicy (static) + ToolVisibilityPolicy (dynamic)

### ToolPolicy (static, set at planner init)

Use this for “this agent must never call X”, or for a fixed allowlist.

```py
from penguiflow.planner.models import ToolPolicy

tool_policy = ToolPolicy(
    # If set, this is a strict allowlist.
    allowed_tools=None,
    # Always deny dangerous tools here.
    denied_tools=set(),
    # Optional: enforce required tags on every tool used by this planner.
    require_tags=set(),
)
```

If you use an allowlist, bake in the always-visible set:

```py
tool_policy = ToolPolicy(
    allowed_tools=set(user_allowlist) | ALWAYS_VISIBLE_TOOLS,
)
```

### ToolVisibilityPolicy (dynamic, per run/resume)

Use this for per-tenant/user/request filtering without building a new planner instance.

Hard rule: if you apply this, it must keep the always-visible set visible.

```py
from collections.abc import Mapping, Sequence
from penguiflow.catalog import NodeSpec
from penguiflow.planner.models import ToolVisibilityPolicy


class TenantToolVisibility(ToolVisibilityPolicy):
    def __init__(self, *, allowed: set[str]) -> None:
        self._allowed = set(allowed) | ALWAYS_VISIBLE_TOOLS

    def visible_tools(self, specs: Sequence[NodeSpec], tool_context: Mapping[str, object]) -> Sequence[NodeSpec]:
        return [spec for spec in specs if spec.name in self._allowed]
```

Usage:

```py
tool_visibility = TenantToolVisibility(allowed=tenant_tool_allowlist)
result = await planner.run("…", tool_context=tool_context, tool_visibility=tool_visibility)
```

## Memory hooks (ShortTermMemoryConfig)

If you enable short-term memory, pass **tenant/user/session identifiers** in `tool_context` (or pass an explicit `memory_key`).

```py
from penguiflow.planner.memory import MemoryIsolation, ShortTermMemoryConfig

short_term_memory = ShortTermMemoryConfig(
    strategy="rolling_summary",
    isolation=MemoryIsolation(
        tenant_key="tenant_id",
        user_key="user_id",
        session_key="session_id",
        require_explicit_key=True,  # default: safe-by-default (memory is disabled if no key)
    ),
    summarizer_model="openai/gpt-4o-mini",  # optional; uses planner LLM if omitted
    # Optional hooks (best-effort, must be awaitable):
    # on_turn_added=...,
    # on_summary_updated=...,
    # on_health_changed=...,
)
```

Then:

```py
result = await planner.run(
    "…",
    tool_context={"tenant_id": tenant_id, "user_id": user_id, "session_id": session_id},
)
```

If you want to bypass `tool_context` extraction, you can pass an explicit `memory_key`:

```py
from penguiflow.planner.memory import MemoryKey

result = await planner.run(
    "…",
    memory_key=MemoryKey(tenant_id=tenant_id, user_id=user_id, session_id=session_id),
)
```

## Pre-flight memory context hook (LLMContextHook)

Use `llm_context_hooks` when you need to inject **external/persistent memory** (profiles, preferences, entitlements, prior summaries) into `llm_context` **once before the first LLM call**.

Important behavior:
- Hooks run on `planner.run(...)` (pre-flight) and are best-effort; failures should not block the run.
- Hooks run **after** short-term memory context is applied, so your hook should usually **merge**, not replace.
- Do not put secrets in `llm_context` (it is LLM-visible). Keep secrets in `tool_context`.

```py
from collections.abc import Mapping
from typing import Any

from penguiflow.planner.llm_context_hooks import LLMContextHookInput


class ExternalMemoryHook:
    # Optional attributes used by the runtime for logging/merging behavior.
    name = "external_memory"
    overwrite = False

    async def before_run(self, inp: LLMContextHookInput) -> Mapping[str, Any] | None:
        # Provide your own store/service via tool_context (tool-only, not LLM-visible).
        store = inp.tool_context.get("memory_store")
        if store is None or inp.memory_key is None:
            return None

        # Example: fetch user profile/preferences by tenant/user (implementation-defined).
        profile = await store.get_user_profile(
            tenant_id=inp.memory_key.tenant_id,
            user_id=inp.memory_key.user_id,
        )
        if not profile:
            return None

        # Merge-friendly: add a new key instead of overwriting "memories".
        return {"user_profile": profile}
```

Wire it into the planner:

```py
from penguiflow.planner.react import ReactPlanner

planner = ReactPlanner(
    llm="openai/gpt-4o",
    use_native_llm=True,
    llm_context_hooks=[ExternalMemoryHook()],
)
```

And pass the store + stable IDs at call time:

```py
result = await planner.run(
    "…",
    tool_context={
        "tenant_id": tenant_id,
        "user_id": user_id,
        "session_id": session_id,
        "memory_store": memory_store,
    },
)
```

## Guardrails (optional)

If you integrate guardrails, pass a `guardrail_gateway` into the planner and keep the conversation window small:

```py
from penguiflow.planner.react import ReactPlanner

planner = ReactPlanner(
    llm="openai/gpt-4o",
    use_native_llm=True,
    guardrail_gateway=guardrail_gateway,
    guardrail_conversation_history_turns=1,
)
```

## Steering (optional)

Steering lets your host app inject operator messages/cancellation while a run is in progress.

```py
result = await planner.run("…", steering=steering_inbox, tool_context=tool_context)
```

To push steering events:

```py
from penguiflow.state.models import SteeringEvent, SteeringEventType

await steering_inbox.push(
    SteeringEvent(
        session_id=session_id,
        task_id="foreground",
        event_type=SteeringEventType.CANCEL,
        payload={"reason": "operator_cancelled", "hard": True},
    )
)
```

Common patterns:
- `INJECT_CONTEXT`: add operator notes/corrections without restarting the run.
- `CANCEL`: stop the foreground run (and, if configured, cascade cancellation to background tasks).

## HITL (pause/resume) patterns

Pause/resume is enabled by default (`pause_enabled=True`). When a run pauses, `planner.run(...)` returns a `PlannerPause` with a `resume_token`.

Minimum host responsibilities:
- Store the `resume_token` and show the pause payload to the user/operator.
- Call `planner.resume(token, user_input=...)` when you have input.

```py
outcome = await planner.run("…", tool_context=tool_context)
if outcome.__class__.__name__ == "PlannerPause":
    # Persist outcome.resume_token + outcome.payload in your app
    ...

# Later:
outcome2 = await planner.resume(outcome.resume_token, user_input="approved", tool_context=tool_context)
```

Durable HITL (recommended):
- Pass `state_store=...` into `ReactPlanner(...)`.
- Your `state_store` should implement:
  - `save_planner_state(token: str, payload: dict) -> None | awaitable`
  - `load_planner_state(token: str) -> dict | awaitable`

## Skills: enable and load packs

```py
from penguiflow.skills.models import SkillsConfig, SkillPackConfig

skills = SkillsConfig(
    enabled=True,
    redact_pii=True,
    top_k=6,
    max_tokens=2000,
    skill_packs=[
        SkillPackConfig(
            name="product",
            path="skills/packs/product",  # file or directory (pack loader decides)
            # format="md" | "yaml" | "json" | "jsonl"  (optional)
            scope_mode="project",
        ),
    ],
)
```

Operational notes:
- Skills are loaded at planner init when `SkillsConfig.enabled=True`.
- Pass `tenant_id` / `project_id` in `tool_context` to make scoping predictable.
- If you hide tools via allowlists/visibility policies, skills content is automatically redacted to avoid referencing disallowed tools.

## Rich output: enable tools + prompt catalog

```py
from penguiflow.registry import ModelRegistry
from penguiflow.rich_output.runtime import RichOutputConfig, attach_rich_output_nodes

registry = ModelRegistry()

rich_output = RichOutputConfig(
    enabled=True,
    # allowlist=("markdown", "mermaid", "datagrid", ...)
    include_prompt_catalog=True,
)

rich_nodes = attach_rich_output_nodes(registry, config=rich_output)
```

If you enable rich output, also:
- Add `RICH_OUTPUT_TOOLS` to `ALWAYS_VISIBLE_TOOLS` / `ALWAYS_VISIBLE_PATTERNS`.
- Include `rich_nodes` in the planner tool catalog.

## Background tasks: enable + expose tasks.* tools (host app responsibility)

ReactPlanner can emit background-task opcodes (`task.subagent` / `task.tool`). Your host app must expose task tools (e.g. `tasks.spawn`) and a task service.

```py
from penguiflow.planner.models import BackgroundTasksConfig

background_tasks = BackgroundTasksConfig(
    enabled=True,
    max_concurrent_tasks=5,
    task_timeout_s=3600,
)
```

These limits are enforced by the session, not the planner: they apply once the config reaches the session (`InProcessTaskService(background_config=...)`, the AG-UI adapter, or `session.configure_background_tasks(config)`), and only the fields you set explicitly override the session's `SessionLimits` (3 concurrent / 8 active tasks / 900 s by default).

`tasks.*` tools expect the foreground `tool_context` to include a task service under `"task_service"`:

```py
tool_context = {
    "session_id": session_id,
    "task_service": task_service,  # required for tasks.spawn / tasks.list / ...
}
```

Operator-oriented defaults to consider:
- `spawn_requires_confirmation=True` for high-risk environments.
- `max_concurrent_tasks` and `max_tasks_per_session` (active tasks) to prevent runaway cost.
- `context_depth="summary"` for privacy/cost, unless tasks require full context.
- `propagate_on_cancel="cascade"` to avoid orphaned work.

```py
background_tasks = BackgroundTasksConfig(
    enabled=True,
    spawn_requires_confirmation=True,
    max_concurrent_tasks=5,
    max_tasks_per_session=50,
    task_timeout_s=3600,
    context_depth="summary",
    propagate_on_cancel="cascade",
)
```

To expose the default `tasks.*` tool set in your catalog, you can include prebuilt specs:

```py
from penguiflow.sessions.task_tools import build_task_tool_specs

task_specs = build_task_tool_specs()
```

`build_task_tool_specs()` returns `NodeSpec` entries with their own registry; use it as a starting point if you already pass `catalog=` into `ReactPlanner(...)`.

## Full “golden” factory (native LLM + discovery + skills + rich output)

This skeleton assumes you build the tool catalog from `nodes + registry` (recommended when you want `ToolSearchConfig.default_loading_mode=DEFERRED` to apply broadly).

```py
from penguiflow.node import Node
from penguiflow.planner.models import ToolExamplesConfig, ToolSearchConfig
from penguiflow.planner.react import ReactPlanner
from penguiflow.registry import ModelRegistry
from penguiflow.rich_output.runtime import RichOutputConfig, attach_rich_output_nodes
from penguiflow.skills.models import SkillPackConfig, SkillsConfig
from penguiflow.catalog import ToolLoadingMode


def build_planner(*, llm_api_key: str) -> ReactPlanner:
    registry = ModelRegistry()

    # 1) Your app tools (examples only)
    nodes: list[Node] = [
        # Node(my_tool_fn, name="my_tool"),
    ]

    # 2) Optional: rich output tools
    rich_nodes = attach_rich_output_nodes(
        registry,
        config=RichOutputConfig(enabled=True, include_prompt_catalog=True),
    )
    nodes.extend(rich_nodes)

    # 3) Planner configuration knobs
    always_visible_patterns = [
        "tool_search",
        "tool_get",
        "skill_search",
        "skill_get",
        "skill_list",
        "tasks.*",
        "finish",
        "render_component",
        "describe_component",
        "list_artifacts",
        "ui_form",
        "ui_confirm",
        "ui_select_option",
    ]
    tool_search = ToolSearchConfig(
        enabled=True,
        default_loading_mode=ToolLoadingMode.DEFERRED,
        always_loaded_patterns=always_visible_patterns,
        activation_scope="session",
    )

    skills = SkillsConfig(
        enabled=True,
        redact_pii=True,
        skill_packs=[
            SkillPackConfig(name="default", path="skills/packs/default", scope_mode="project"),
        ],
    )

    planner = ReactPlanner(
        llm={"model": "openai/gpt-4o", "api_key": llm_api_key},
        use_native_llm=True,
        nodes=nodes,
        registry=registry,
        temperature=0.0,
        json_schema_mode=True,
        tool_search=tool_search,
        tool_examples=ToolExamplesConfig(enabled=True),
        skills=skills,
        stream_final_response=True,
    )
    return planner
```
