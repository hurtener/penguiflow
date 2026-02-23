# Guardrails (ReactPlanner safety layer)

## What it is / when to use it

PenguiFlow guardrails are a **policy enforcement layer** that can inspect and constrain:

- user input before the LLM plans (`llm_before`)
- tool execution (`tool_call_start` / `tool_call_result`)
- streamed text output (`llm_stream_chunk`)

Use guardrails when you need:

- tool allowlists/denylists enforced outside the prompt,
- secret redaction in streamed output,
- “fail closed” behavior for high-risk actions (STOP / PAUSE),
- a policy pack you can audit and version.

This page focuses on guardrails **as used by `ReactPlanner`**. ToolNode also has its own hardening controls; see **[Tooling](tooling.md)**.

## Non-goals / boundaries

- Guardrails are not a replacement for `ToolPolicy` / tool visibility. Use those for primary access control.
- Guardrails do not make unsafe tools “safe”. They are a *control plane*, not a sandbox.
- Guardrails are not a full content moderation product; policy packs are intentionally small and composable.

## Contract surface

### Wiring guardrails into ReactPlanner

Guardrails are **off by default**. Enable them by passing a gateway at construction:

- `ReactPlanner(..., guardrail_gateway=gateway)`
- optionally: `guardrail_conversation_history_turns=N` (adds memory turns to the guardrail context payload)

When enabled, the planner can enforce these actions:

- `STOP`: terminate (or force-safe finish) with a user-safe message
- `PAUSE`: trigger HITL approval (planner pauses with `reason="approval_required"`)
- `RETRY`: re-run the LLM call with corrective instructions
- `REDACT`: redact text from streamed output and tool results
- `ALLOW`: do nothing

### Guardrail events and decisions

The gateway evaluates a `GuardrailEvent` and returns a `GuardrailDecision`.

Key fields:

- `GuardrailEvent.event_type`: e.g. `llm_before`, `tool_call_start`, `tool_call_result`, `llm_stream_chunk`
- `GuardrailEvent.text_content`: user input or streamed text (when applicable)
- `GuardrailEvent.tool_name` / `tool_args`: tool call metadata (when applicable)
- `GuardrailEvent.payload`: extra structured context (tool_call_id, action_seq, conversation history, etc.)

Decisions include:

- `action`: `ALLOW | REDACT | RETRY | PAUSE | STOP`
- `rule_id`: stable identifier for policy auditing
- `severity` / `confidence`
- optional action-specific payloads:
  - `redactions: tuple[RedactionSpec, ...]`
  - `retry: RetrySpec(max_attempts, corrective_message)`
  - `pause: PauseSpec(scope, approver_roles, prompt, timeout_s)`
  - `stop: StopSpec(error_code, user_message, internal_reason)`

### Modes: `shadow` vs `enforce`

Guardrail gateway config controls enforcement:

- `GatewayConfig(mode="shadow")`: evaluate and log decisions, but do not block execution
- `GatewayConfig(mode="enforce")`: apply STOP/PAUSE/RETRY/REDACT behavior

!!! tip
    Start in `shadow` mode in production to measure rule hit rates, then roll to `enforce`.

### Sync vs async rules (latency control)

Rules are classified by cost:

- **FAST** rules are evaluated synchronously (on the request path)
- **DEEP** rules can be evaluated asynchronously via a `SteeringGuardInbox`

Gateway options you will tune:

- `sync_timeout_ms` (default 15ms)
- `sync_parallel` (run sync rules concurrently)
- `sync_fail_open` / `async_fail_open` (availability vs safety tradeoff)

### Observation size “guardrail” (reliability safety net)

Separately from the guardrail gateway, `ReactPlanner` applies an **observation clamp** to prevent context overflow:

- `ReactPlanner(..., observation_guardrail=ObservationGuardrailConfig(...))`
- default: enabled with `ObservationGuardrailConfig()`

If a tool produces an overly large JSON observation, the planner will:

1) try to store it as an artifact (if an `ArtifactStore` is available) and return an artifact reference, otherwise
2) truncate it (optionally preserving JSON structure)

This is a reliability guardrail, not a policy decision system.

## Operational defaults (recommended)

- Use `ToolPolicy` / tool visibility as your primary access control.
- Run guardrails in `shadow` mode first, then enforce:
  - enforce **tool allowlists** (STOP on unauthorized tools)
  - enforce **secret redaction** (REDACT on streamed output/tool results)
- Keep sync rules small and deterministic; push high-latency checks to async rules.

Tradeoff guidance:

- `sync_fail_open=False` is safer (a guardrail timeout blocks), but can impact availability.
- `sync_fail_open=True` improves availability, but can allow policy bypass on timeouts.

## Failure modes & recovery

### Guardrails “stopping everything”

**Symptoms**

- planner finishes early with a generic safe message, or tool calls return `guardrail_stop:*`

**Fix**

- check which rule triggered (`rule_id`) and whether you are in `shadow` or `enforce`
- verify your allowlist/denylist config (especially when catalogs are tenant-scoped)

### Redaction doesn’t apply to streamed output

**Likely causes**

- `stream_final_response=False` (no LLM streaming path)
- guardrails aren’t enabled (no `guardrail_gateway`)
- gateway is in `shadow` mode (decision logged but not applied)

### Observation clamp triggers unexpectedly

**Symptoms**

- tool observations become `{artifact: ..., preview: ...}` or truncated stubs

**Fix**

- ensure ToolNode artifact extraction is configured correctly (large outputs should be artifacts)
- increase `ObservationGuardrailConfig.max_observation_chars` only if you have token budget headroom

## Observability

Recommended signals:

- log guardrail decisions (action, rule_id, severity, confidence) **without raw content**
- record `PlannerEvent(event_type="guardrail_retry")` counts (retries can hide a failing model/prompt)
- alert on repeated `guardrail_stop:*` outcomes for a tenant (misconfig or abuse)

Useful places to look:

- `PlannerEvent(event_type="tool_call_result").extra["result_json"]` may include a `failure.guardrail` payload on STOP/PAUSE.
- streamed output seen in `PlannerEvent(event_type="llm_stream_chunk")` is already post-redaction (when enforced).

See **[Planner observability](observability.md)**.

## Security / multi-tenancy notes

- Guardrails should not rely on “prompt compliance”. Enforce the policy in code.
- Prefer per-tenant configuration (tool visibility + allowlists) instead of global rules.
- Never log raw prompts/tool results when debugging guardrails; log decision metadata plus stable correlation ids.

## Runnable examples

Guardrails examples exist under `examples/guardrails/`:

- `uv run python examples/guardrails/huggingface/flow.py`
- `uv run python examples/guardrails/scope_classifier/flow.py`

## Troubleshooting checklist

- Is `ReactPlanner(..., guardrail_gateway=...)` set (guardrails are disabled otherwise)?
- Is `GatewayConfig.mode` set to `enforce` when you expect blocking/redaction?
- Do your rules list the correct `supports_event_types` for the events you care about?
- Are timeouts too aggressive (`sync_timeout_ms`) causing unexpected STOP (fail-closed) behavior?
- Are you confusing policy guardrails with the observation clamp (`ObservationGuardrailConfig`)?

