---
name: penguiflow-guardrails
description: Add a policy-enforcement layer to a PenguiFlow ReactPlanner — pass a `guardrail_gateway` that inspects `llm_before`/`tool_call_start`/`tool_call_result`/`llm_stream_chunk` events and returns `GuardrailDecision`s with actions `STOP`/`PAUSE`/`RETRY`/`REDACT`/`ALLOW`, run in `shadow` or `enforce` mode, classify rules as FAST (sync, `sync_timeout_ms`) or DEEP (async, `SteeringGuardInbox`), choose `sync_fail_open` vs safety, and configure the separate `ObservationGuardrailConfig` reliability clamp that prevents context overflow on huge tool outputs. Use when a user says "add guardrails", "PII redaction", "tool denylist", "policy enforcement", "stop unsafe tool", "fail-closed", or names `GuardrailEvent`, `GuardrailDecision`, `GatewayConfig`.
---

# PenguiFlow Guardrails

## When to use
- Enforce tool allowlists/denylists at runtime, outside the prompt.
- Redact secrets/PII from streamed output and tool results.
- Fail-closed gating ("STOP if the tool would touch prod").
- HITL approval triggered by policy (`PAUSE`).
- Audit-friendly policy with stable `rule_id`s.
- Reliability safety net for oversized tool observations.

## When NOT to use
- Primary access control → use `ToolPolicy` / `ToolVisibilityPolicy` (see [[penguiflow-reactplanner-config]]). Guardrails are policy *enforcement*, not the access boundary.
- Sandboxing untrusted code → use a real sandbox; guardrails are a control plane.
- Content moderation product — guardrails are intentionally small and composable.
- HITL UX patterns → use [[penguiflow-hitl-pause-resume]] (guardrails can *trigger* a pause; the resume contract lives there).

## Hard boundaries
Guardrails are the **policy enforcement layer**. They don't replace tool visibility, ToolNode hardening, or sandboxing. The reliability `ObservationGuardrailConfig` is bundled here but is conceptually separate — it's a context-overflow safety net, not a policy decision.

## Workflow

### 1) Wire a gateway
Guardrails are off until you pass a `guardrail_gateway`. The gateway holds a `RuleRegistry` and an async-rule inbox:
```python
from penguiflow.planner.guardrails import GatewayConfig, GuardrailGateway, RuleRegistry
from penguiflow.steering.guard_inbox import SteeringGuardInbox

registry = RuleRegistry()
registry.register_sync(ToolAllowlistRule(...))
registry.register_async(MyDeepRule(...))

gateway = GuardrailGateway(
    registry=registry, guard_inbox=SteeringGuardInbox(),
    config=GatewayConfig(),     # defaults: mode="enforce", sync_fail_open=False, etc.
)
planner = ReactPlanner(..., guardrail_gateway=gateway, guardrail_conversation_history_turns=1)
```
Library defaults: `mode="enforce"`, `sync_timeout_ms=15.0`, `sync_parallel=True`, `async_enabled=True`, `sync_fail_open=False`, `async_fail_open=True`. Switch `mode="shadow"` during rollout.

### 2) Roll out via `shadow` then `enforce`
- `mode="shadow"` — evaluate and log; never block.
- `mode="enforce"` (default) — apply STOP/PAUSE/RETRY/REDACT.

Common rollout path: ship in shadow, measure rule hit rates and false positives, then revert to enforce per rule.

### 3) Classify rules — `RuleCost.FAST` or `RuleCost.DEEP`
Every rule sets a `cost` attribute:
- **`RuleCost.FAST`** — synchronous, runs on the request path bounded by `sync_timeout_ms` (15ms default). Register via `registry.register_sync(rule)`. Use for regex/allowlist checks and deterministic logic.
- **`RuleCost.DEEP`** — async via `SteeringGuardInbox`. Register via `registry.register_async(rule)`. Use for LLM-as-judge, network checks, slow external services.

### 4) Pick decision actions
Rules return `GuardrailDecision(action=GuardrailAction.X, rule_id=..., reason=..., severity=..., ...)`. Actions:
- `ALLOW` — pass through.
- `REDACT` — apply `redactions: tuple[RedactionSpec, ...]` (path-based redaction into the event payload).
- `RETRY` — re-run the LLM call with `RetrySpec(max_attempts=2, corrective_message=...)`.
- `PAUSE` — pause planner with `reason="approval_required"`; `PauseSpec(scope=Literal["run","step","tool_call"], approver_roles, prompt, timeout_s=300.0)`. Works on all four events (enforce mode): `run()` returns `PlannerPause(payload={"guardrail": {action, rule_id, reason, decision_id, severity, effects, stop, pause}})`, where `pause` is the `PauseSpec` as a dict (informational for your approval UI; not enforced). The guarded LLM call / tool does not run (`tool_call_result`: the tool ran, its result is withheld). On resume the LLM plans again and rules re-run — a rule that still matches pauses again. With `pause_enabled=False` it fails closed: tool events → error `guardrail_pause_unavailable:<rule_id>` (run continues); `llm_before`/stream → final answer "Unable to complete the request.".
- `STOP` — terminate with `StopSpec(error_code="GUARDRAIL_STOP", user_message=..., internal_reason=...)`. All fields default; override what you need.

`STOP` is the fail-closed default for high-risk findings. `REDACT` for secrets. `PAUSE` for "human must approve". `RETRY` for fixable LLM mistakes.

### 5) Decide fail-open vs fail-closed semantics
| Knob | Effect | Trade-off |
|---|---|---|
| `sync_fail_open=True` | Sync rule timeout → ALLOW | Availability favored; policy bypass on timeout |
| `sync_fail_open=False` (recommended for high-risk) | Sync rule timeout → blocks (treated as STOP-like) | Safety favored; possible latency outages |
| `async_fail_open=True` (typical) | Async rule unavailable → ALLOW | Deep checks are advisory |
| `async_fail_open=False` | Async rule unavailable → block | Hard dependency on the deep service |

For internal-only or low-risk agents, `True` everywhere is fine. For external-facing high-risk agents, set `sync_fail_open=False`.

### 6) Tap the right event surfaces
| `event_type` | When it fires | What you can do |
|---|---|---|
| `llm_before` | Before the LLM plans | Inspect user input; STOP / RETRY / PAUSE / REDACT prompt content |
| `tool_call_start` | Before a tool runs | Inspect `tool_name`, `tool_args`; STOP / PAUSE / RETRY |
| `tool_call_result` | After a tool returns | Inspect result; REDACT secrets, STOP if disallowed content surfaced, PAUSE to withhold it for approval |
| `llm_stream_chunk` | Each streamed text chunk | REDACT secrets mid-stream; STOP / PAUSE (the LLM's proposed action is not executed) |

Each `GuardrailEvent` exposes `text_content`, `tool_name`, `tool_args`, and `payload` (tool_call_id, action_seq, conversation history). Use what you need.

With `tool_call_mode="native"` (opt-in), `llm_before` is not evaluated and stream STOP/PAUSE decisions are not enforced; tool-event guardrails still apply.

### 7) Wire the reliability observation clamp
Separately from policy guardrails: `ReactPlanner(..., observation_guardrail=ObservationGuardrailConfig(...))`. No `enabled` flag — the config object is the active state. Knobs (with defaults): `max_observation_chars=50_000`, `max_field_chars=10_000`, `truncation_suffix`, `preserve_structure=True`, `auto_artifact_threshold=20_000` (set `0` to disable artifact path), `preview_length=500`. On huge outputs: stores as artifact (when `auto_artifact_threshold>0` and `ArtifactStore` available) else structure-preserving truncation. Pass `observation_guardrail=None` to disable entirely. See `references/observation-clamp.md`.

### 8) Audit and tune
Every decision carries a stable `rule_id`. Log in shadow mode for 1-2 weeks: measure hit rates, surface timeouts (promote to DEEP), drop never-fire rules. Promote to `enforce` per rule.

## Troubleshooting (fast checks)
- **Guardrails stop everything** — likely `mode="enforce"` with overly broad rules; revert to `shadow` and measure.
- **Sync rules timeout** — exceeded `sync_timeout_ms` (default 15ms); move expensive checks to DEEP.
- **Secrets leaking through stream** — no `llm_stream_chunk` rule with REDACT; add one.
- **Tool allowlist not enforced** — rule fires on `tool_call_start` but mode is `shadow`; flip to `enforce`.
- **PAUSE fires but resume KeyError** — guardrail pause uses planner pause machinery; needs durable state store ([[penguiflow-hitl-pause-resume]]).
- **Unexpected `approval_required` pause** — read `pause.payload["guardrail"]["rule_id"]`; `__timeout__` means async rules didn't answer within the risk router's wait (`DefaultRiskRouter` uses `on_timeout="pause"` for HIGH tools, CRITICAL tools when `critical_fail_closed=False`, and `llm_before` when the catalog has HIGH/CRITICAL tools).
- **Tool errors `guardrail_pause_unavailable:*`** — a rule returned PAUSE but `pause_enabled=False`, so the call (or, on `tool_call_result`, its result) was blocked (fail closed).
- **`RETRY` exhausts retries** — `max_attempts` in `RetrySpec` too high or corrective message ineffective; reduce attempts or rewrite the message.
- **Context overflow despite observation clamp** — disabled or misconfigured; verify `observation_guardrail` is not `None`.
- **Async rules never fire** — `SteeringGuardInbox` not wired; check `async_fail_open` semantics.

## Worked example
- `examples/guardrails/flow.py` — runnable guardrail policy pack with shadow→enforce migration.
- Integration tests under `tests/test_guardrails*.py` exercise sync+async rules end-to-end.

## References (load only as needed)
- `references/gateway-and-decisions.md` — `GuardrailGateway`, `GatewayConfig`, `GuardrailEvent`, `GuardrailDecision`, action payloads (`RedactionSpec`, `RetrySpec`, `PauseSpec`, `StopSpec`).
- `references/rules-fast-vs-deep.md` — FAST/DEEP classification, sync timeouts, parallel evaluation, async via `SteeringGuardInbox`.
- `references/observation-clamp.md` — `ObservationGuardrailConfig`, artifact escape hatch, JSON-preserving truncation, why it's separate from the policy layer.
