# Downstream How-to: uv + PenguiFlow CLI + Spec-Generated Agents

This guide shows how to:

1. Bootstrap a new repo using `uv`
2. Install PenguiFlow with *all* optional feature dependencies
3. Use the PenguiFlow CLI to generate the sample Markdown files (`PENGUIFLOW.md`, `AGENTS.md`) and a starter spec
4. Use the spec generation engine (`penguiflow generate --spec ...`) to generate a ReAct-based **echo agent**
5. Enable advanced configuration knobs (spec + env overrides)

This guide will be updated once AGIV cli is finished.

## Prereqs

- Python 3.11+
- `uv` installed

## 1) Create a repo + uv environment

Create a folder for your work (this can be a scratch “tooling” workspace to run the CLI). If you want the **agent** to be the repo root, you can skip `git init` here and instead run `git init` inside the generated agent directory later.

```bash
mkdir pf-workspace && cd pf-workspace
git init
uv init --python 3.11
```

## 2) Install PenguiFlow with all optional dependencies

PenguiFlow does **not** ship a single `all` extra. Use this set of extras to enable the CLI + planner + LLM providers + A2A + tool transports:

```bash
uv add "penguiflow[cli,planner,llm,a2a-server,a2a-client,a2a-grpc,tools-cli,tools-websocket]"
uv sync
```

Quick sanity check:

```bash
uv run penguiflow --help
```

## 3) Generate the sample Markdown files + starter spec

This creates a **spec workspace** containing:

- `PENGUIFLOW.md` (how the workflow works)
- `AGENTS.md` (assistant/tooling instructions)
- `<agent-name>.yaml` (starter spec)

```bash
uv run penguiflow generate --init echo-agent
```

You should now have:

- `echo-agent/PENGUIFLOW.md`
- `echo-agent/AGENTS.md`
- `echo-agent/echo-agent.yaml`

Optional: scaffold VS Code helper files in your current directory:

```bash
uv run penguiflow init
```

## 4) Echo agent spec (ReAct template)

Replace the contents of `echo-agent/echo-agent.yaml` with this minimal echo agent spec:

```yaml
agent:
  name: echo-agent
  description: "Echo back the user input using a single tool call."
  template: react
  flags:
    memory: false

llm:
  primary:
    # Any LiteLLM model string works here (OpenAI, Anthropic, OpenRouter, Databricks, etc.)
    model: openai/gpt-4o-mini

tools:
  - name: echo
    description: "Echo the provided message verbatim."
    side_effects: pure
    args:
      message: str
    result:
      message: str

planner:
  max_iters: 3
  hop_budget: 3
  absolute_max_parallel: 1
  system_prompt_extra: |
    You are an echo agent.

    Rules:
    - Always call the `echo` tool exactly once.
    - Pass the user's latest message as `message`.
    - Return the tool result `message` as the final answer.
```

## 5) Generate the agent project from the spec

Run a dry run first (validates YAML + shows planned files):

```bash
uv run penguiflow generate --spec echo-agent/echo-agent.yaml --dry-run
```

Then generate:

```bash
uv run penguiflow generate --spec echo-agent/echo-agent.yaml
```

This uses the spec generation engine:

- Spec schema/validation: `penguiflow/cli/spec.py`
- Generator implementation: `penguiflow/cli/generate.py`
- Code templates: `penguiflow/cli/templates/`

## 6) Install and run the generated agent

The generated agent is a standalone `uv` project inside `echo-agent/`.

```bash
cd echo-agent
uv sync
cp .env.example .env
```

Edit `.env` with the correct provider key(s) for your chosen `LLM_MODEL`.

Run the agent module (the default `__main__.py` runs a demo query):

```bash
uv run python -m echo_agent
```

To change what it runs, edit the query string in `echo-agent/src/echo_agent/__main__.py`.

Recommended for interactive testing/debugging: use the Playground UI:

```bash
uv run penguiflow dev --project-root .
```

## 7) Implement the echo tool

Generation creates a stub tool that raises `NotImplementedError`.

Edit `echo-agent/src/echo_agent/tools/echo.py` and replace the `NotImplementedError` with a real implementation (keep the generated `@tool(...)` decorator):

```python
async def echo(args: EchoArgs, ctx: ToolContext) -> EchoResult:
    del ctx
    return EchoResult(message=args.message)
```

If you want to regenerate later, treat these as generated/overwritten on `penguiflow generate`:

- `echo-agent/src/echo_agent/planner.py`
- `echo-agent/src/echo_agent/config.py`
- `echo-agent/src/echo_agent/tools/__init__.py`

## Advanced config knobs

This section lists the knobs you can enable either in the **spec** (source of truth) or via **env vars** (runtime overrides).

### Agent flags (spec: `agent.flags`)

- `memory`: include Memory Iceberg integration stubs; requires `planner.memory_prompt` in the spec.
- `streaming`: include streaming-friendly scaffolding in templates that support it.
- `hitl`: enable pause/resume flows and interactive rich output components.
- `a2a`: include A2A HTTP+JSON binding scaffolding.
- `background_tasks`: enable background task orchestration + subagent spawning support.

### LLM (spec: `llm.*`)

- `llm.primary.model` (required): LiteLLM model string.
- `llm.primary.provider` (optional): informational tag for docs/templates.
- `llm.summarizer.enabled/provider/model`: enable trajectory/memory summarization with a separate model.
- `llm.reflection.enabled`: enable self-critique loop.
- `llm.reflection.quality_threshold` / `max_revisions` / `criteria`: tune reflection strictness.

### Planner core (spec: `planner.*`)

- `max_iters`: max plan iterations.
- `hop_budget`: max tool calls.
- `absolute_max_parallel`: cap concurrent tool calls.
- `stream_final_response`: enable final-answer streaming.
- `multi_action_sequential`: if the model outputs multiple JSON actions, run extra tool calls sequentially.
- `multi_action_read_only_only`: gate extra actions to `pure`/`read` tools.
- `multi_action_max_tools`: cap extra tool calls per LLM response.
- `system_prompt_extra` (required): your agent’s core instruction block.
- `memory_prompt` (required if `agent.flags.memory: true`): how to use retrieved memories.

### Planning hints (spec: `planner.hints`)

- `ordering`: preferred tool order.
- `parallel_groups`: tools the model may run in parallel.
- `sequential_only`: tools that must run alone.
- `disallow`: tools the model must never call.

### Tool Search + Deferred Tool Activation (spec: `planner.tool_search`)

When enabled, these built-ins are added:

- `tool_search` (discover tools by capability)
- `tool_get` (fetch schema/examples for a discovered tool)

Most tools can be hidden by default (deferred) to reduce prompt bloat.

- `enabled`: turn on tool discovery.
- `cache_dir`: where the SQLite tool index lives (default `.penguiflow`).
- `default_loading_mode`: `always` | `deferred` (use `deferred` to hide tools by default).
- `always_loaded_patterns`: glob patterns for tools that must remain visible (e.g. `tool_search`, `finish`, critical tools).
- `activation_scope`: `run` | `session`.
- `preferred_namespaces`: rank namespaces higher in `tool_search` ordering.
- `fts_fallback_to_regex`: if FTS5 is unavailable, fall back to regex when `search_type=fts`.
- `enable_incremental_index`: avoid full reindex when catalog fingerprint matches.
- `rebuild_cache_on_init`: force reindex on startup.
- `max_search_results`: upper bound for returned results.

Optional prompt aids (opt-in):

- `hints.*`: inject a bounded per-turn shortlist of relevant tools into the prompt (powered by an internal `tool_search`).
  - `hints.enabled`: turn on per-turn hints.
  - `hints.top_k`: number of suggested tools to inject (default 5).
  - `hints.search_type`: `fts` | `regex` | `exact`.
  - `hints.include_always_loaded`: whether always-visible tools can appear in the hints.

- `directory.*`: inject a bounded “tool groups” directory into the prompt so the model has a map of what exists.
  - `directory.enabled`: turn on tool directory.
  - `directory.max_groups`: cap groups in the prompt.
  - `directory.max_tools_per_group`: include a small preview list per group.
  - `directory.include_tool_counts`: show group tool counts.
  - `directory.include_default_groups`: auto-group by namespace (e.g. MCP tool nodes).
  - `directory.groups`: developer-defined group rules (namespace/tag/pattern/name matching).

Note: `activation_scope: session` requires a stable `tool_context.session_id` and concurrency-safe session serialization.

### Skills (Local Skill Packs) (spec: `planner.skills`)

When enabled, skills are loaded from local Skill Packs into a local SQLite store and can be retrieved pre-flight and/or discovered in-flight.

- `enabled`: turn on skills subsystem.
- `cache_dir`: where `skills.db` lives (default `.penguiflow`).
- `max_tokens`: pre-flight injection budget.
- `summarize`: MVP default is deterministic structured compression.
- `redact_pii`: redact emails/phones/tokens in injected/returned skill text.
- `scope_mode`: `project` | `tenant` | `global` (project is default).
- `top_k`: number of skills to retrieve for pre-flight injection.
- `fts_fallback_to_regex`: same semantics as tool_search.

Skill packs (spec: `planner.skills.skill_packs[]`):
- `name`, `path`, `format` (`md`|`yaml`|`json`|`jsonl` or auto-detect)
- `scope_mode`, `enabled`, `update_existing_pack_skills`, `pinned_skill_names`

Optional directory exposure (spec: `planner.skills.directory`):
- `enabled`, `max_entries`, `include_fields`, `selection_strategy`

When `planner.skills.enabled: true`, these built-in tools are added:
- `skill_search` (find skill names)
- `skill_get` (fetch full content)
- `skill_list` (paged listing)

### Built-in short-term memory (spec: `planner.short_term_memory`)

- `enabled`: turn on session memory inside ReactPlanner.
- `strategy`: `rolling_summary` | `truncation` | `none`.
- `budget.*`: `full_zone_turns`, `summary_max_tokens`, `total_max_tokens`, `overflow_policy`.
- `isolation.*`: `tenant_key`, `user_key`, `session_key`, `require_explicit_key`.
- `include_trajectory_digest`: include compressed trace digest in the prompt.
- `summarizer_model`: optional separate summarizer model.
- `recovery_backlog_limit` / `retry_attempts` / `retry_backoff_base_s` / `degraded_retry_interval_s`.

### Rich output (spec: `planner.rich_output`)

- `enabled`: turn on UI component rendering support.
- `allowlist`: which components the planner may emit.
- `include_prompt_catalog` / `include_prompt_examples`.
- `max_payload_bytes` / `max_total_bytes`.

Note: interactive components (`form`, `confirm`, `select_option`) require `agent.flags.hitl: true`.

### Artifact store (spec: `planner.artifact_store`)

- `enabled`: store large outputs as artifacts (useful for UI attachments and large tool results).
- `retention.*`: TTL and size caps.

### Background tasks (spec: `planner.background_tasks`)

- `enabled`: allow subagent/job background execution.
- `allow_tool_background`: allow tools to declare background execution.
- `default_mode`: `subagent` | `job`.
- `default_merge_strategy`: `APPEND` | `REPLACE` | `HUMAN_GATED`.
- `context_depth`: `full` | `summary` | `none`.
- `propagate_on_cancel`: `cascade` | `isolate`.
- `spawn_requires_confirmation`: requires HITL.
- `include_prompt_guidance`.
- `max_concurrent_tasks` / `max_tasks_per_session` / `task_timeout_s` / `max_pending_steering`.

### Tool-level background execution (spec: `tools[].background`)

- `enabled`: mark a specific tool as eligible for background execution.
- `mode`: `job` | `subagent`.
- `default_merge_strategy`: `APPEND` | `REPLACE` | `HUMAN_GATED`.
- `notify_on_complete`.

### External tools (spec: `external_tools.*`)

- `external_tools.presets[]`: connect to a built-in MCP preset (use `penguiflow tools list`).
- `external_tools.custom[]`: connect to MCP/UTCP endpoints with:
  - `transport`: `mcp` | `utcp`
  - `connection`: command or URL
  - `auth_type`: `bearer` | `oauth` | `none`
  - `env` and `auth_config` (supports `${ENV_VAR}` substitution)

OAuth external tools require `agent.flags.hitl: true`.

### Services (spec: `services.*`)

- `services.memory_iceberg.enabled/base_url`
- `services.rag_server.enabled/base_url`
- `services.wayfinder.enabled/base_url`

### Runtime env var overrides (generated `config.py`)

All of these can be overridden without regenerating:

- `LLM_MODEL`, `SUMMARIZER_MODEL`, `REFLECTION_MODEL`
- `MEMORY_ENABLED`, `SUMMARIZER_ENABLED`, `REFLECTION_ENABLED`
- `PLANNER_MAX_ITERS`, `PLANNER_HOP_BUDGET`, `PLANNER_ABSOLUTE_MAX_PARALLEL`
- `PLANNER_STREAM_FINAL_RESPONSE`
- `PLANNER_MULTI_ACTION_SEQUENTIAL`, `PLANNER_MULTI_ACTION_READ_ONLY_ONLY`, `PLANNER_MULTI_ACTION_MAX_TOOLS`
- `TOOL_SEARCH_*` (enabled/cache_dir/default_loading_mode/activation_scope/always_loaded_patterns/hints/directory/etc.)
- `SKILLS_*` (enabled/cache_dir/max_tokens/top_k/redaction/directory settings; packs are spec-defined)
- `RICH_OUTPUT_ENABLED`, `RICH_OUTPUT_ALLOWLIST`, `RICH_OUTPUT_INCLUDE_PROMPT_CATALOG`, `RICH_OUTPUT_INCLUDE_PROMPT_EXAMPLES`, `RICH_OUTPUT_MAX_PAYLOAD_BYTES`, `RICH_OUTPUT_MAX_TOTAL_BYTES`
- `ARTIFACT_STORE_ENABLED`, `ARTIFACT_STORE_TTL_SECONDS`, `ARTIFACT_STORE_MAX_*`, `ARTIFACT_STORE_CLEANUP_STRATEGY`
- `SHORT_TERM_MEMORY_*` (enabled/strategy/budget/isolation/retry)
- `BACKGROUND_TASKS_*` (if background tasks are generated in your project)


## Safety notes

- Do not commit `.env`.
- Avoid putting real secrets in `.env.example`.
- Re-run `penguiflow generate --spec ...` after spec edits; expect some generated files to be overwritten.

## Appendix A: Full spec example (all knobs)

This is a single spec YAML that exercises every supported section/knob in the spec schema.

```yaml
agent:
  name: echo-agent
  description: "Echo agent with every config knob enabled."
  template: react
  flags:
    streaming: true
    hitl: true
    a2a: true
    memory: true
    background_tasks: true

llm:
  primary:
    provider: openai
    model: openai/gpt-4o-mini

  summarizer:
    enabled: true
    provider: openai
    model: openai/gpt-4o-mini

  reflection:
    enabled: true
    provider: openai
    model: openai/gpt-4o-mini
    quality_threshold: 0.85
    max_revisions: 2
    criteria:
      completeness: "Addresses all parts of the query"
      accuracy: "Factually correct based on observations"
      clarity: "Well-structured and easy to follow"

tools:
  - name: echo
    description: "Echo the provided message verbatim."
    tags: ["demo", "echo"]
    side_effects: pure
    args:
      message: str
    result:
      message: str

  - name: slow_echo
    description: "Echo in the background (demonstrates tool background config)."
    tags: ["demo", "background"]
    side_effects: pure
    args:
      message: str
      delay_s: Optional[float]
    result:
      message: str
    background:
      enabled: true
      mode: subagent
      default_merge_strategy: HUMAN_GATED
      notify_on_complete: true

external_tools:
  presets:
    - preset: github
      auth_override: oauth
      env:
        GITHUB_TOKEN: "${GITHUB_TOKEN}"
    - preset: postgres
      auth_override: bearer
      env:
        DATABASE_URL: "${DATABASE_URL}"

  custom:
    - name: weather_api
      transport: utcp
      connection: "https://api.example.com/.well-known/utcp.json"
      auth_type: bearer
      auth_config:
        token: "${WEATHER_API_TOKEN}"
      env:
        WEATHER_API_BASE_URL: "${WEATHER_API_BASE_URL}"
      description: "Example UTCP server"

flows:
  - name: echo_flow
    description: "A tiny flow that echoes once."
    dependencies:
      - name: formatter
        type_hint: EchoFormatter
    nodes:
      - name: echo
        description: "Echo step"
        input_type: EchoIn
        output_type: EchoOut
        policy:
          validate: both
          timeout_s: 10.0
          max_retries: 1
          backoff_base: 0.5
        uses: [formatter]
    steps: [echo]

services:
  memory_iceberg:
    enabled: true
    base_url: http://localhost:8000
  rag_server:
    enabled: true
    base_url: http://localhost:8081
  wayfinder:
    enabled: true
    base_url: http://localhost:8082

planner:
  max_iters: 12
  hop_budget: 8
  absolute_max_parallel: 5
  stream_final_response: true
  multi_action_sequential: true
  multi_action_read_only_only: true
  multi_action_max_tools: 2

  system_prompt_extra: |
    You are an echo agent.

    Rules:
    - Always call the `echo` tool for the final answer.
    - Prefer pure/read tools unless explicitly asked to do writes.

  memory_prompt: |
    Use retrieved memories to preserve user preferences and avoid repetition.
    If no relevant memory is present, proceed normally.

  hints:
    ordering: [echo]
    parallel_groups: [[echo, slow_echo]]
    sequential_only: [echo]
    disallow: []

  tool_search:
    enabled: true
    cache_dir: .penguiflow
    default_loading_mode: deferred
    always_loaded_patterns: [tasks.*, tool_search, tool_get, finish]
    activation_scope: run
    preferred_namespaces: []
    fts_fallback_to_regex: true
    enable_incremental_index: true
    rebuild_cache_on_init: false
    max_search_results: 10

    hints:
      enabled: true
      top_k: 5
      include_always_loaded: false
      search_type: fts

    directory:
      enabled: true
      max_groups: 20
      max_tools_per_group: 6
      include_tool_counts: true
      include_default_groups: true
      groups: []

  skills:
    enabled: true
    cache_dir: .penguiflow
    max_tokens: 2000
    summarize: false
    redact_pii: true
    scope_mode: project
    top_k: 6
    fts_fallback_to_regex: true
    skill_packs:
      - name: core
        path: skills/packs/core
        format: md
        scope_mode: project
        enabled: true
        update_existing_pack_skills: true
        pinned_skill_names: []
    directory:
      enabled: true
      max_entries: 30
      include_fields: [name, title, trigger]
      selection_strategy: pinned_then_recent

  short_term_memory:
    enabled: true
    strategy: rolling_summary
    budget:
      full_zone_turns: 5
      summary_max_tokens: 1000
      total_max_tokens: 10000
      overflow_policy: truncate_oldest
    isolation:
      tenant_key: tenant_id
      user_key: user_id
      session_key: session_id
      require_explicit_key: true
    include_trajectory_digest: true
    summarizer_model: openai/gpt-4o-mini
    recovery_backlog_limit: 20
    retry_attempts: 3
    retry_backoff_base_s: 2.0
    degraded_retry_interval_s: 30.0

  artifact_store:
    enabled: true
    retention:
      ttl_seconds: 3600
      max_artifact_bytes: 52428800
      max_session_bytes: 524288000
      max_trace_bytes: 104857600
      max_artifacts_per_trace: 100
      max_artifacts_per_session: 1000
      cleanup_strategy: lru

  rich_output:
    enabled: true
    allowlist:
      - markdown
      - json
      - echarts
      - mermaid
      - plotly
      - datagrid
      - metric
      - report
      - grid
      - tabs
      - accordion
      - code
      - latex
      - callout
      - image
      - video
      - form
      - confirm
      - select_option
    include_prompt_catalog: true
    include_prompt_examples: true
    max_payload_bytes: 250000
    max_total_bytes: 2000000

  background_tasks:
    enabled: true
    allow_tool_background: true
    default_mode: subagent
    default_merge_strategy: HUMAN_GATED
    context_depth: full
    propagate_on_cancel: cascade
    spawn_requires_confirmation: false
    include_prompt_guidance: true
    max_concurrent_tasks: 5
    max_tasks_per_session: 50
    task_timeout_s: 3600
    max_pending_steering: 2
```

## Appendix B: Sample .env.example (all knobs)

This is a superset `.env.example` covering the env overrides generated by spec-based projects.

```bash
# =============================================================================
# LLM Provider Configuration (set the key(s) you actually use)
# =============================================================================

# OpenAI
OPENAI_API_KEY=

# Anthropic
ANTHROPIC_API_KEY=

# OpenRouter
OPENROUTER_API_KEY=

# Azure OpenAI (if applicable)
AZURE_API_KEY=
AZURE_API_BASE=
AZURE_API_VERSION=

# Google Gemini (if applicable)
GEMINI_API_KEY=

# AWS Bedrock (if applicable)
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION_NAME=

# Core model selection
LLM_MODEL=openai/gpt-5.2-mini
SUMMARIZER_MODEL=
REFLECTION_MODEL=

# =============================================================================
# Feature Flags
# =============================================================================
MEMORY_ENABLED=true
SUMMARIZER_ENABLED=true
REFLECTION_ENABLED=true

# =============================================================================
# Reflection Settings
# =============================================================================
REFLECTION_QUALITY_THRESHOLD=0.85
REFLECTION_MAX_REVISIONS=2

# =============================================================================
# Service URLs
# =============================================================================
MEMORY_BASE_URL=http://localhost:8000
RAG_SERVER_BASE_URL=http://localhost:8081
WAYFINDER_BASE_URL=http://localhost:8082

# =============================================================================
# Planner Settings
# =============================================================================
PLANNER_MAX_ITERS=12
PLANNER_HOP_BUDGET=8
PLANNER_ABSOLUTE_MAX_PARALLEL=5
PLANNER_STREAM_FINAL_RESPONSE=true

PLANNER_MULTI_ACTION_SEQUENTIAL=true
PLANNER_MULTI_ACTION_READ_ONLY_ONLY=true
PLANNER_MULTI_ACTION_MAX_TOOLS=2

# =============================================================================
# Tool Search + Deferred Activation
# =============================================================================
TOOL_SEARCH_ENABLED=true
TOOL_SEARCH_CACHE_DIR=.penguiflow
TOOL_SEARCH_DEFAULT_LOADING_MODE=deferred
TOOL_SEARCH_ALWAYS_LOADED_PATTERNS=tasks.*,tool_search,tool_get,finish
TOOL_SEARCH_ACTIVATION_SCOPE=run
TOOL_SEARCH_PREFERRED_NAMESPACES=
TOOL_SEARCH_FTS_FALLBACK_TO_REGEX=true
TOOL_SEARCH_ENABLE_INCREMENTAL_INDEX=true
TOOL_SEARCH_REBUILD_CACHE_ON_INIT=false
TOOL_SEARCH_MAX_SEARCH_RESULTS=10

# Optional prompt aids
TOOL_SEARCH_HINTS_ENABLED=true
TOOL_SEARCH_HINTS_TOP_K=5
TOOL_SEARCH_HINTS_INCLUDE_ALWAYS_LOADED=false
TOOL_SEARCH_HINTS_SEARCH_TYPE=fts

TOOL_SEARCH_DIRECTORY_ENABLED=true
TOOL_SEARCH_DIRECTORY_MAX_GROUPS=20
TOOL_SEARCH_DIRECTORY_MAX_TOOLS_PER_GROUP=6
TOOL_SEARCH_DIRECTORY_INCLUDE_TOOL_COUNTS=true
TOOL_SEARCH_DIRECTORY_INCLUDE_DEFAULT_GROUPS=true

# =============================================================================
# Skills (Local Skill Packs)
# =============================================================================
SKILLS_ENABLED=true
SKILLS_CACHE_DIR=.penguiflow
SKILLS_MAX_TOKENS=2000
SKILLS_SUMMARIZE=false
SKILLS_REDACT_PII=true
SKILLS_SCOPE_MODE=project
SKILLS_TOP_K=6
SKILLS_FTS_FALLBACK_TO_REGEX=true
SKILLS_DIRECTORY_ENABLED=true
SKILLS_DIRECTORY_MAX_ENTRIES=30
SKILLS_DIRECTORY_INCLUDE_FIELDS=name,title,trigger
SKILLS_DIRECTORY_SELECTION_STRATEGY=pinned_then_recent

# =============================================================================
# Artifact Store (binary/large text)
# =============================================================================
ARTIFACT_STORE_ENABLED=true
ARTIFACT_STORE_TTL_SECONDS=3600
ARTIFACT_STORE_MAX_ARTIFACT_BYTES=52428800
ARTIFACT_STORE_MAX_SESSION_BYTES=524288000
ARTIFACT_STORE_MAX_TRACE_BYTES=104857600
ARTIFACT_STORE_MAX_ARTIFACTS_PER_TRACE=100
ARTIFACT_STORE_MAX_ARTIFACTS_PER_SESSION=1000
ARTIFACT_STORE_CLEANUP_STRATEGY=lru

# =============================================================================
# Rich Output (Component Artifacts)
# =============================================================================
RICH_OUTPUT_ENABLED=true
RICH_OUTPUT_ALLOWLIST=markdown,json,echarts,mermaid,plotly,datagrid,metric,report,grid,tabs,accordion,code,latex,callout,image,video,form,confirm,select_option
RICH_OUTPUT_INCLUDE_PROMPT_CATALOG=true
RICH_OUTPUT_INCLUDE_PROMPT_EXAMPLES=true
RICH_OUTPUT_MAX_PAYLOAD_BYTES=250000
RICH_OUTPUT_MAX_TOTAL_BYTES=2000000

# =============================================================================
# Built-in Short-Term Memory (ReactPlanner)
# =============================================================================
SHORT_TERM_MEMORY_ENABLED=true
SHORT_TERM_MEMORY_STRATEGY=rolling_summary
SHORT_TERM_MEMORY_FULL_ZONE_TURNS=5
SHORT_TERM_MEMORY_SUMMARY_MAX_TOKENS=1000
SHORT_TERM_MEMORY_TOTAL_MAX_TOKENS=10000
SHORT_TERM_MEMORY_OVERFLOW_POLICY=truncate_oldest
SHORT_TERM_MEMORY_TENANT_KEY=tenant_id
SHORT_TERM_MEMORY_USER_KEY=user_id
SHORT_TERM_MEMORY_SESSION_KEY=session_id
SHORT_TERM_MEMORY_REQUIRE_EXPLICIT_KEY=true
SHORT_TERM_MEMORY_INCLUDE_TRAJECTORY_DIGEST=true
SHORT_TERM_MEMORY_SUMMARIZER_MODEL=openai/gpt-4o-mini
SHORT_TERM_MEMORY_RECOVERY_BACKLOG_LIMIT=20
SHORT_TERM_MEMORY_RETRY_ATTEMPTS=3
SHORT_TERM_MEMORY_RETRY_BACKOFF_BASE_S=2.0
SHORT_TERM_MEMORY_DEGRADED_RETRY_INTERVAL_S=30.0

# =============================================================================
# Background Tasks (Subagents)
# =============================================================================
BACKGROUND_TASKS_ENABLED=true
BACKGROUND_TASKS_ALLOW_TOOL_BACKGROUND=true
BACKGROUND_TASKS_DEFAULT_MODE=subagent
BACKGROUND_TASKS_DEFAULT_MERGE_STRATEGY=HUMAN_GATED
BACKGROUND_TASKS_CONTEXT_DEPTH=full
BACKGROUND_TASKS_PROPAGATE_ON_CANCEL=cascade
BACKGROUND_TASKS_SPAWN_REQUIRES_CONFIRMATION=false
BACKGROUND_TASKS_INCLUDE_PROMPT_GUIDANCE=true
BACKGROUND_TASKS_MAX_CONCURRENT_TASKS=5
BACKGROUND_TASKS_MAX_TASKS_PER_SESSION=50
BACKGROUND_TASKS_TASK_TIMEOUT_S=3600
BACKGROUND_TASKS_MAX_PENDING_STEERING=2

# =============================================================================
# External Tools (examples)
# =============================================================================
GITHUB_TOKEN=
DATABASE_URL=
WEATHER_API_TOKEN=
WEATHER_API_BASE_URL=
```
