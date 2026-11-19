---
name: penguiflow-reactplanner-config
description: Configure PenguiFlow ReactPlanner in production codebases (native LLM adapter, tool discovery + deferred activation, skills packs, allowlists/visibility policies, rich output, A2A router integration, guardrails, and background tasks) with safe defaults and troubleshooting.
---

# PenguiFlow ReactPlanner Config (Enterprise)

Use this skill when you need to **wire, audit, or harden** a ReactPlanner integration:
- Enable the **native LLM layer** (`use_native_llm=True`) and keep JSON schema mode reliable.
- Explain or rollout the built-in **reflection loop** for post-answer quality control via [[penguiflow-reflection-loop]].
- Turn on **tool discovery** (`tool_search`/`tool_get`) with **deferred tool activation**.
- Enable **skills** (`skill_search`/`skill_get`/`skill_list`) and keep them safe under allowlists.
- Wire **runtime skills providers** (`skills_provider` / `skills_provider_factory`) for persona- or request-scoped skills.
- Optionally enable **draft-only skill authoring** with `skill_propose`.
- Enforce **tool allowlists / per-tenant visibility** without breaking discovery.
- Expose agents over **A2A** inside an existing FastAPI app using router-first integration.
- (Optional) Enable **rich output** tools and **background tasks** safely.

For copy/paste-ready templates and a full “golden config”, read:
- `references/reactplanner-config-templates.md`
  - Includes thinking effort (`reasoning_effort`), budgeting (`deadline_s`/`hop_budget`/`token_budget`), HITL (pause/resume + `state_store`), steering, and background tasks (`tasks.*`).
  - Includes pre-flight `llm_context_hooks` pattern for injecting external memory context.

## Workflow (do this in order)

### 1) Find the single source of truth for planner config
- Locate where `ReactPlanner(...)` is constructed (often a factory like `build_planner()` or `AppConfig.planner()`).
- Consolidate planner knobs into one place (avoid sprinkling params across call sites).
- If your app is multi-tenant or session-based, ensure you have a stable `session_id` you can pass in `tool_context`.

### 2) Turn on the native LLM adapter (recommended default for 2.x)
- Set `use_native_llm=True`.
- Pass `llm` as either a string (e.g. `"openai/gpt-4o"`) or a dict with credentials:
  - `{"model": "openai/gpt-4o", "api_key": "...", "base_url": "..."}`.
- Keep `json_schema_mode=True` and `temperature=0.0` for planner stability.

### 3) Enable tool discovery + deferred activation (so prompts stay small)
Use `ToolSearchConfig(enabled=True, default_loading_mode=DEFERRED, activation_scope="session")` and **require**:
- `tool_context["session_id"]` is present for every `run()` / `resume()` call (string).
- An **always-visible** tool set stays visible even when most tools are deferred.

Always-visible tools (best practice; keep visible and allowed):
- Discovery: `tool_search`, `tool_get`
- Skills: `skill_search`, `skill_get`, `skill_list`
- Common: `finish`
- Rich output (only if enabled): see [[penguiflow-rich-output]] for the full always-visible set.

Scoping discovery without rewriting the query (optional):
- `tool_search(args={"query": "send", "tags": ["mail", "write"]})` — AND match against the
  tool's **declared** tags (auto-expanded name tokens are ignored by this filter).
- `tool_search(args={"query": "send", "namespace": "microsoft_365"})` — match tools whose
  name equals the namespace or starts with `<namespace>.`. Dot-prefix only; `_` and `:` are
  not separators.
- Filters compose as AND with each other, with the query, and with `task_type` (skills).
  Empty defaults preserve legacy behavior.
- `skill_search` accepts the same `tags` / `namespace` args with identical semantics.

### 4) Enforce allowlists without breaking discovery (ToolPolicy + ToolVisibilityPolicy)
Use both layers when needed:
- `ToolPolicy` (planner init): coarse, global allow/deny list and tag requirements.
- `ToolVisibilityPolicy` (per `run()`): dynamic filtering per tenant/user/request **without** rebuilding the planner.

Hard rule: if you apply a visibility policy, it must still include the always-visible tools listed above, or the planner will lose discovery/skills mid-run.

### 5) Enable Skills (PenguiFlow skills subsystem)
- Configure `SkillsConfig(enabled=True, skill_packs=[...], redact_pii=True, top_k=...)`.
- Ensure skills packs are loaded at planner init (they are when `SkillsConfig.enabled=True`).
- In multi-tenant apps, pass `tenant_id` / `project_id` in `tool_context` so skills scope filtering works as intended.
- For runtime/persona skills, pass `skills_provider=...` or `skills_provider_factory=...` into `ReactPlanner(...)`.
- Runtime providers are Python API only; they compose with local packs, and runtime skills win on name collisions.
- Skills can declare applicability metadata:
  - `required_tool_names`
  - `required_namespaces`
  - `required_tags`
- Applicability is enforced against the request's allowed capability set, so skills only surface when the matching tools/namespaces/tags are active.

### 5a) Draft new skills safely (optional)
- Enable `SkillsConfig(..., proposal={"enabled": True})` to expose `skill_propose`.
- `skill_propose` drafts a structured skill only; it does **not** persist anything.
- Keep persistence/review in the host product (for example, Canvas or your own admin UI/API).

### 6) Enable rich output (optional)
Rich output (charts, tables, forms, interactive HITL components) is owned by [[penguiflow-rich-output]]. From the planner-config side, two things matter: call `attach_rich_output_nodes(registry, config=RichOutputConfig(enabled=True, ...))` and include the returned nodes in your catalog; and add rich-output tool names to the always-visible set **only if** rich output is enabled. Everything else (components, schemas, builders, interactive HITL) lives in the rich-output skill.

### 7) Background tasks + steering (optional)
- Enable `BackgroundTasksConfig(enabled=True, ...)` if your host app wires `tasks.*` tools (e.g., `tasks.spawn`) and a task service.
- Provide `steering=SteeringInbox(...)` to `run()` / `resume()` if you need live operator steering or external cancellation.

### 7a) Streaming final responses (optional, v2.6+)
- Set `stream_final_response=True` on `ReactPlanner` to stream the synthesized final answer to clients.
- The underlying `JSONLLMClient` protocol accepts `stream` and `on_stream_chunk` parameters; the planner wires these through automatically when streaming is enabled.
- Frontends consume the stream as `llm_stream_chunk` planner events (with `extra["channel"] == "answer"`) delivered through your `event_callback`; see [[penguiflow-streaming]] for shipping them over SSE/WebSocket.
- Hard rule: streaming is a final-response affordance; intermediate planner steps remain non-streamed JSON.

### 8) Expose the agent over A2A without a standalone server
Prefer the router-first A2A binding for spec-compliant `A2AService` integrations:
- Use `install_a2a_http(app, service)` when the host app owns the FastAPI instance.
- Use `create_a2a_http_router(service)` when the host app wants explicit `include_router(...)` composition.
- Keep `create_a2a_http_app(service)` only as the convenience path for standalone agents.

Practical rules:
- Keep `/.well-known/agent-card.json` installed at the app level, not inside a reusable router.
- Let the A2A router own `A2AService` startup/shutdown via router lifespan when directly included.
- Do not add app-global validation handlers just for A2A routes; parse and map A2A request errors inside the binding so unrelated host routes keep normal FastAPI behavior.

### 9) Adapt planner behavior for A2A callers
If the same planner serves both human UI traffic and downstream A2A callers, make delivery channel explicit in `tool_context`:
- Set `tool_context["delivery_channel"] = "a2a"` and, when useful, `tool_context["response_mode"] = "text_only"`.
- Apply a `ToolVisibilityPolicy` for A2A requests that hides UI-only and rich-output tools such as `render_component`, `describe_component`, `ui_form`, `ui_confirm`, and `ui_select_option`.
- Add matching prompt or `llm_context` guidance so the planner returns fully materialized text or JSON instead of relying on rendered components the downstream agent cannot see.

Use this pattern when:
- an agent serves a UI client and is also exposed through A2A
- a background-task agent delegates to another PenguiFlow agent over A2A
- rich output is valuable for humans but lossy for machine-to-machine callers

## Troubleshooting (fast checks)
- `tool_search is not configured`: `ToolSearchConfig.enabled` is False or you didn’t include planner init that builds the `ToolSearchCache`.
- `activation_scope='session' requires tool_context['session_id']`: you enabled session activation but aren’t passing `session_id`.
- `skill_search is not configured`: `SkillsConfig.enabled` is False or packs didn’t load; also ensure `skill_*` tools are not filtered out.
- `skill_propose is not configured`: `skills.proposal.enabled` is False.
- Runtime skills not appearing: confirm the custom provider is injected, the request's `ToolVisibilityPolicy` still allows the relevant tools, and the skill's applicability metadata matches those allowed tools/tags/namespaces.
- Rich output tools missing: you didn’t attach nodes via `attach_rich_output_nodes(...)` or they were filtered out by policy.
- A2A routes return `422` instead of A2A problem details: confirm route handlers still use FastAPI `Request` injection and that A2A validation is handled inside the binding, not via host-app exception overrides.
- Downstream A2A callers get incomplete answers: confirm the request sets `delivery_channel="a2a"` and that rich-output/UI tools are hidden for that path.

## References
- Templates and knob-by-knob guidance: `references/reactplanner-config-templates.md`
