# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 3.12.0 — 2026-08-18

### Added
- **Trace-derived datasets and evaluations** (`penguiflow.evals` + `penguiflow eval`): recorded
  planner trajectories become versioned evaluation datasets that can be scored offline, without
  re-running the agent. `penguiflow eval collect` runs a query suite against a discovered agent and
  captures its traces; `penguiflow eval evaluate` scores a dataset against val/test splits and gates
  on a minimum score. Rows follow the `TraceExampleV1` shape specified in
  `docs/proposals/RFC_TRACE_DERIVED_DATASETS_AND_EVALS.md`.

  Metrics may be synchronous or asynchronous and are awaited on every path, so a metric built on the
  async `llm_judge` helper works directly. `wrap_metric` adapts several call signatures, from the
  full `(gold, pred, trace, pred_name, pred_trace)` form down to `(gold, pred)` or a bare
  `**kwargs`. The `@metric` decorator declares named pass/fail criteria that are reported per case
  alongside the score.

  The Playground gains the matching workflow: browse and tag traces, export a tagged selection as a
  dataset, browse discovered metrics, run an evaluation with per-case scores and criteria checks,
  and compare a prediction trajectory against its gold trace.

  Worth knowing when adopting it:
  - **Answer text is persisted to the state store, and no redaction is applied on that path.** The
    `redaction_profile` on an export is recorded as metadata; it does not filter content.
  - `collect_traces` fails loudly if a run's trajectory does not reach the `state_store` it was
    given, rather than reporting a successful collection over an empty store. An agent builder or
    orchestrator that cannot accept an injected `state_store` also warns at build time, before any
    query runs.
  - `outputs.status` is a closed enum (`ok` / `error` / `cancelled` / `paused` / `unknown`). Runs
    that ended without answering — exhausted deadline, hop budget or iteration limit — report
    `error`, with `trajectory.finish_reason` carrying which one it was.
  - Exporting a selector that matches no traces is a `400`, not a server error.
- **`Trajectory.final_answer` and `Trajectory.finish_reason`**: the planner now records both the
  answer a run terminated with and why it ended, so a persisted trajectory describes the answer the
  agent gave and not only the route it took. Previously the terminal action was never appended to
  `trajectory.steps` and the answer travelled only on `PlannerFinish`, so offline scoring had to
  re-run the agent to see any output.

  `final_answer` is recorded for `answer_complete` terminations only. The deadline, hop-budget and
  iteration-limit paths pass the last raw tool observation as their finish payload, so reading an
  answer out of it would promote internal tool data — any tool returning an `answer` key — to the
  run's answer. Terminations that deliberately produce a user-facing message on a failure reason,
  currently the guardrail STOP path, supply it explicitly instead.

### Changed
- `Trajectory.serialise()` emits two additional keys (`final_answer`, `finish_reason`), and
  `GET /trajectory/{trace_id}` returns them. The change is additive: `from_serialised()` reads both
  via `.get`, so trajectories stored before this release load unchanged with `None`. Only consumers
  asserting on the exact serialised key set need updating.

### Fixed
- Databricks Claude 4.7/4.8 adaptive reasoning is supported through LiteLLM 1.94.0, with the
  reasoning display path and reasoning config hardened.
- MCP Apps request typing stabilised, and the dependency floor now prevents CI from resolving
  MCP 2.x.

## 3.11.1 — 2026-07-09

Documentation-only release. No runtime or public API behavior changes.

### Added
- **API reference**: we generated a complete per-symbol reference for the public
  API, published under the docs site's "Reference → API reference" section. Pages
  are scoped to each package's `__all__` (`penguiflow`, `penguiflow.planner`,
  `penguiflow.llm`, `penguiflow.tools`), so internal runtime modules are
  intentionally omitted.

### Changed
- **Docstring coverage substantially expanded across the public surface** so the
  API reference renders useful Args/Returns/Raises tables. `ReactPlanner` (class
  plus `run`/`resume`/`fork`) was reformatted to Google style; planner public
  models (`models.py`, `trajectory.py`) gained class docstrings and field
  descriptions; and the core runtime (`core.py`, `types.py`, `node.py`,
  `patterns.py`, errors/metrics/policies/streaming), sessions, skills, state,
  tools, remote, and steering modules were backfilled with Google-style
  docstrings. Docstrings and Pydantic field descriptions only — no logic changes.
- **Root README refreshed**: sharper positioning line, a problem-first "Why
  PenguiFlow" section, an architecture diagram, and a link to the new API
  reference; the stale "2.x line" statement is corrected to the current
  3.x / SemVer wording.

## 3.11.0 — 2026-07-08

### Fixed
- Databricks Claude Opus 4.8 reasoning: requests with `reasoning_effort` sent a
  thinking budget that Databricks rejects with 400 ("thinking.type.enabled is not
  supported"). Reasoning request shaping is now profile metadata
  (`ModelProfile.reasoning_request_style`) with model-name heuristics as fallback;
  the new `databricks-claude-opus-4-8` profile routes to adaptive thinking +
  `output_config.effort` (live-verified) and marks temperature unsupported
  (Databricks rejects the parameter for this model — live-verified).

### Added
- **Uniform built-in LLM fallback**: rate-limit (`429`) fallback is now applied at
  every PenguiFlow-managed client seam, not just the native transport. The
  deprecated LiteLLM planner path (`use_native_llm=False`) wraps in a shared
  fallback chain, and `penguiflow.llm.LLMClient` gains
  `LLMClient(..., fallback=..., cooldown_store=...)` and
  `generate_structured(..., fallback=..., cooldown_store=...)` backed by a
  provider-level `FallbackProvider`. Output mode for `LLMClient` fallback is
  chosen across the **intersection** of every chain model's capabilities (so a
  mode the primary supports but a fallback model does not is downgraded), and
  per-call cost/telemetry is attributed to the model that actually answered.
  Streaming and reasoning callbacks now flow through both fallback wrappers
  (native and LiteLLM); a 429 after output has streamed is not replayed.
  Defaults are unchanged — fallback only activates when `llm_fallback=` / `fallback=`
  is passed.

### Changed
- `llm_fallback` combined with a custom `llm_client=...` now raises `ValueError`
  instead of being silently ignored. Fallback applies only to PenguiFlow-managed
  clients (native or LiteLLM).

### Deprecated
- `DSPyLLMClient` is deprecated and unmaintained; constructing it emits a
  `DeprecationWarning`. It is excluded from built-in fallback. Use the native LLM
  layer (default) or `transport="pydantic-ai"`.

### Fixed
- LiteLLM planner fallback with a dict `llm` config carrying extra provider keys
  (e.g. `api_base`) crashed with `TypeError` (kwargs were both folded into the
  client config and re-passed to a constructor without `**kwargs`); the extra keys
  are now applied once.
- Databricks Claude Opus 4.8 reasoning: requests with `reasoning_effort` sent a
  thinking budget that Databricks rejects with 400 ("thinking.type.enabled is not
  supported"). Reasoning request shaping is now profile metadata
  (`ModelProfile.reasoning_request_style`) with model-name heuristics as fallback;
  the new `databricks-claude-opus-4-8` profile routes to adaptive thinking +
  `output_config.effort` (live-verified) and marks temperature unsupported
  (Databricks rejects the parameter for this model — live-verified).

### Added
- **Native tool-calling planner mode (opt-in)**: `ReactPlanner(tool_call_mode="native")`
  expresses tool intent through provider-native function calls instead of the prompted
  `{next_node, args}` JSON envelope — same `PlannerAction` decision shape, so parallel
  fanout, trajectory, events, pause/resume, and A2A are unchanged. Content-only turns
  finish the run; N tool calls in one turn map to the existing parallel plan; catalog
  names are declared under wire-safe aliases (MCP dotted names are rejected by provider
  function-name rules). Eligibility is profile-gated (`supports_native_tool_calls`;
  the Databricks gpt-5.5 route is gated) with per-run downgrade-to-prompted events,
  and structured final answers compose via one extraction turn. Streaming: the final
  answer streams token-by-token on the answer channel (parity with prompted mode);
  a disobedient preamble turn closes the stream with a `superseded` marker and
  re-emits on the thinking channel. New `NativeLLMAdapter.complete_with_tools()` /
  `FallbackLLMClient.complete_with_tools()` (shared 429-failover core). Default
  `"prompted"` — byte-identical behavior when unset.
- **Multimodal planner inputs (opt-in)**: `AudioPart` joins `ImagePart`, and
  `ReactPlanner.run(..., input_parts=[...])` appends image/audio parts to the
  initial user message while preserving text-only byte parity when omitted.
  `JSONLLMClient.messages` is widened to `Sequence[Mapping[str, Any]]`;
  `NativeLLMAdapter` accepts typed content parts, enforces
  `ModelProfile.supports_image_input` / `supports_audio_input`, and rejects
  inline binary data over the adapter's configurable
  `multimodal_inline_data_limit_bytes` limit (default 32 KiB) before provider
  serialization. Trajectory serialization and summarization keep metadata
  stubs only, never raw bytes. Databricks/OpenRouter native image paths and
  pydantic-ai image/audio `BinaryContent` mapping are wired; Databricks Claude
  image E2E passed through both native and pydantic-ai transports. Audio live
  validation is deferred until an audio-capable route is available.
- **Structured final answers (opt-in)**: `ReactPlanner(final_response_model=MyModel)`
  makes the planner's final response carry a `structured` payload validated against
  the supplied Pydantic model. On validation failure a bounded corrective turn
  (`final_response_retries`, default 1) re-prompts with the validation errors and
  schema; on exhaustion `payload.structured` is omitted (never unvalidated data),
  a warning lands in `payload.warnings`, and a
  `final_response_structured_degraded` event fires. `FinalPayload` gains
  `structured: dict | None`; `payload["raw_answer"]` and streaming are unchanged
  (answer streams first, `structured` validates on the assembled result).
  Default unset → byte-identical behavior. Provider-agnostic — live-verified on
  Databricks through both the native and pydantic-ai transports. In native
  tool-calling mode (`tool_call_mode="native"`) the planner declares a synthetic
  `final_response` tool that carries the `structured` object; the model writes the
  human-readable answer as plain text **and** calls that tool in the same finishing
  turn. So the answer still streams token-by-token on the answer channel while the
  structured payload arrives as provider-validated function-call arguments and
  validates on the first pass (no repair turn). The streamed finishing text is kept
  as the answer rather than being treated as a superseded preamble.
- **pydantic-ai transport (opt-in)**: `create_native_adapter(transport="pydantic-ai")`
  (or `NativeLLMAdapter(..., transport=...)`) routes completions through
  pydantic-ai's direct model layer behind the existing `Provider` seam — same
  `JSONLLMClient` surface, profiles, fallback, pricing, and tracing. Per-model
  pinning via the new `ModelProfile.preferred_transport` field (explicit kwarg >
  profile > `"native"` default); Databricks Claude reasoning models pin `"native"`
  while the generic transport cannot parse their reasoning content blocks.
  Requires the new `penguiflow[pydantic-ai]` extra. Live-verified on Databricks
  (streaming, `json_schema`, cost) and OpenRouter (streaming reasoning deltas).
- The litellm-backed planner client (`use_native_llm=False`) now emits a
  `DeprecationWarning`; it will be removed in a future release.
- LLM call auto-tracing: every `NativeLLMAdapter.complete()` (and therefore every
  `FallbackLLMClient` adapter, with spans attributed to the model actually called)
  can emit one span per LLM call to a pluggable `LLMTraceSink`. Ships with
  `MlflowLLMTraceSink` (MLflow Tracing spans, `span_type="LLM"`, lazy import,
  degrades to no-op when mlflow is absent) and `LoggingLLMTraceSink`. Enable
  explicitly via `create_native_adapter(trace_sink=...)` or transparently with
  `PENGUIFLOW_LLM_TRACING=mlflow` (or `log`) — no agent code changes, no message
  contents captured, default behavior unchanged when unset. The seam sits above
  the `Provider` abstraction, so future transports are traced identically.
- LLM cost automation: the existing `penguiflow.llm.pricing` facade now uses
  optional `genai-prices` as the maintained price source before falling back to
  the static table. `register_pricing()` remains the highest-priority override
  for private rates, `calculate_cost()` uses actual token counts so upstream
  tiered pricing applies, and `get_pricing()` keeps the historical base per-1K
  return shape. Added the `penguiflow[pricing]` extra and included
  `genai-prices` explicitly in `penguiflow[pydantic-ai]`.
- `examples/mlflow_llm_tracing/`, a runnable ReactPlanner example showing
  `PENGUIFLOW_LLM_TRACING=mlflow|log` spans alongside cost figures from the new
  pricing path and a private-rate override check.
- `databricks-claude-opus-4-8` model profile and pricing entries.
- Model profiles for GPT-5.5 / GPT-5.5 Pro, Gemini 3.5 Flash, and Claude Opus 4.7,
  plus their Databricks variants (`databricks-gpt-5-5`, `databricks-gpt-5-5-pro`,
  `databricks-gpt-5-4-mini`, `databricks-gpt-5-4-nano`, `databricks-gemini-3-5-flash`,
  `databricks-claude-opus-4-7`).
- `ModelProfile.supports_temperature` capability flag and runtime recovery: a
  temperature-related provider 400 now disables temperature for that model and
  retries automatically instead of failing the call.
- Rate-limit model fallback: `ModelFallbackConfig` + `FallbackLLMClient` and a new
  `ReactPlanner(llm_fallback=...)` option. On a 429 — even mid-run — the call fails
  over to the next configured model (rotating API keys within a model first) and
  places the rate-limited `(model, key)` pair in a cooldown (default 45s). After
  cooldown the higher-priority model is preferred again. All of a run's planner
  clients share one cooldown store.
- Enterprise-grade documentation site (MkDocs) and doc CI checks.
- Experimental A2A router continuity support for Phase 0/1, including specialist-side `a2a_context_id`
  session precedence, outbound A2A `contextId` support, and StateStore-backed remote conversation bindings.
- Experimental A2A router Phase 2/3 support, including normalized remote task lifecycle APIs, input/auth-required
  planner pause mapping, push notification config client helpers, agent registry scoring, and router delegation tools.
- Experimental A2A router Phase 4/5 API freeze candidate, including router policy guardrails, declarative registry
  loading, per-agent A2A auth headers, route decision metadata, and tag-triggered PyPI prerelease publishing.
- `penguiflow apply` for safe, Ansible-style reconciliation of spec changes into existing projects without
  overwriting implemented tool files.

### Changed
- **Temperature is now opt-in.** `LLMRequest.temperature`, `LLMClientConfig.temperature`,
  and `create_native_adapter(temperature=...)` default to `None`, meaning no `temperature`
  is sent and the model uses its provider default. Previously structured-output calls
  sent an implicit `temperature=0.0`. Callers that require deterministic output should
  now pass `temperature=0.0` explicitly. This also fixes 400 errors from models that
  reject the `temperature` parameter (e.g. Databricks GPT-5 reasoning models,
  `databricks-claude-opus-4-7`).
- Root README rewritten to be a concise “front door” with stable links.
- Generated tool registries and planner prompt constants now include managed markers so future `apply` runs can update
  only PenguiFlow-owned blocks.
- `penguiflow generate --init` now emits updated assistant instructions that direct ongoing changes through
  `penguiflow apply`.

## 3.10.0 — 2026-06-11

### Added
- **Opt-in temperature handling and rate-limit model fallback**: `ModelFallbackConfig`
  + `FallbackLLMClient` and a new `ReactPlanner(llm_fallback=...)` option. On a 429 —
  even mid-run — the call fails over to the next configured model (rotating API keys
  within a model first) and places the rate-limited `(model, key)` pair in a cooldown.
- Model profiles for **GPT-5.5, Gemini 3.5 Flash, and Claude Opus 4.7**, plus the
  fallback-adapter factory (Option A) and the LLM-provider robustness branch plan.

### Changed
- AG-UI session snapshots now sanitize non-serializable `llm_context` / `tool_context`
  via a JSON snapshot before persisting to session state, so runtime objects no longer
  break serialization (live tool context still keeps live objects).

### Fixed
- Databricks **Claude Opus 4.7 reasoning mapping** corrected (shipped through the
  3.10.0a3 prerelease).

## 3.9.0 — 2026-05-19

### Added
- **Tag/namespace filters for `tool_search` and `skill_search`**: typed filter args on
  `ToolSearchArgs`, `SkillSearchQuery`, `SkillQuery`, and `SkillListRequest`, backed by
  the FTS5-indexed `tags` columns. Tags are AND-matched against declared-only tag lists
  (a new `declared_tags` column prevents FTS name-token expansion from polluting filter
  results); namespaces use dot-prefix match, consistent with existing
  `match_namespaces` semantics. All filters compose.

## 3.8.1 — 2026-05-14

### Fixed
- Prerelease test failures resolved.

### Docs
- MkDocs site enhancements.

## 3.8.0 — 2026-05-13

### Added
- **Safe spec apply workflow** (`penguiflow apply`): Ansible-style reconciliation of spec
  changes into existing projects without overwriting implemented tool files, with new
  `docs/cli/apply-command.md` and updated generate/new command docs.

### Changed
- **Databricks provider token-refresh hardening** for singleton deployments.
- Skills drift reconciliation.

### Fixed
- Provider initialization issue (`llm/providers`).

## 3.7.0 — 2026-05-12

### Added
- **Skills generation**: first-class skills authoring/generation, shipped `skills/`
  bundles (e.g. `penguiflow-a2a-integration`) with SKILL.md + references.
- **A2A full spec** implementation and the **A2A remote task progress sink**.

### Fixed
- MCP Apps renderer fix.

## 3.6.3 — 2026-04-03

### Fixed
- Validation error in the planner/react runtime and web specs paths.

## 3.6.2 — 2026-03-25

### Security
- **Pinned `litellm<=1.82.6`** to avoid known-compromised releases.

### Changed
- CI configuration updates for ruff, mypy, and the docs pipeline; prompting fix.

## 3.6.1 — 2026-03-23

### Added
- **Rich-output enhanced wrappers** and substantial docs
  (`docs/planner/rich-output*.md`, `rich-output-skills.md`, `rich-output-extensions.md`),
  with `artifact_registry` refinements.

## 3.6.0 — 2026-03-19

### Added
- **New output renderers** to reduce post-processing fixes on agent outputs.
- Updated model **profiles and pricing**.
- **Complete router-only A2A discovery**.

## 3.5.0 — 2026-03-17

### Added
- **Extended A2A functionality** (broad surface update across docs, templates, and
  pricing).
- **Deferred tools activatable via parallel tool calling** (`planner/parallel.py`).
- **Runtime skill providers and draft-only skill proposals**
  (`skills/provider.py`, `skills/local_store.py`).

## 3.4.0 — 2026-03-13

### Added
- **Skills v2**: skill-provider enhancements.

### Fixed
- MCP app reconnect and resource proxying.

## 2.12.1

Initial entry for the current packaging version. Prior release notes are being backfilled.
