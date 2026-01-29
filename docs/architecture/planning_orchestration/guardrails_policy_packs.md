# Guardrails & Policy Packs

This document explains how PenguiFlow guardrails work in the ReactPlanner, how to
configure them with policy packs, and how decision resolution behaves when multiple
rules trigger in the same turn.

Guardrails are designed to be **opt-in** and **composable**:
- Nothing runs unless you provide a `GuardrailGateway` to the planner.
- Rules can be synchronous (fast) or asynchronous (deep/model-based).
- Decisions are merged via a deterministic priority policy.

## Concepts

### Guardrail Events
The planner emits guardrail events at key points in the run, such as:
- `llm_before` (right before asking the LLM to choose an action)
- `tool_call_start` (before a tool executes)
- `tool_call_result` (after a tool returns)
- `llm_stream_chunk` (streamed LLM output chunks)

Each event contains:
- `event_type`
- `text_content` (where applicable)
- tool metadata (`tool_name`, `tool_args`) when relevant
- a payload enriched with optional context (e.g. `task_scope`, `last_assistant`)

### Rules: sync vs async
Rules are classified by cost:
- `RuleCost.FAST` → **sync** evaluation (inline, time-boxed)
- `RuleCost.DEEP` → **async** evaluation (queued, may or may not be awaited)

Synchronous rules are used for “must-not-miss” checks (e.g., obvious prompt injection
strings). Async rules are used for model-based classification (e.g., jailbreak
detectors, scope classifiers).

### Actions
Rules return a `GuardrailDecision` whose `action` is one of:
- `ALLOW`: no action needed
- `REDACT`: redact content (tool results and streaming chunks)
- `RETRY`: re-run with corrective guidance
- `PAUSE`: request human intervention / explicit user input
- `STOP`: terminate the run and return a safe user message

## Per-rule custom messaging (RETRY/STOP)

Guardrails support **per-rule customized user-facing messaging**:

### RETRY: `RetrySpec.corrective_message`
If a rule returns `RETRY` with a `RetrySpec.corrective_message`, the planner will:
- inject that message as an additional `system` message, and
- attempt a retry (up to `RetrySpec.max_attempts`).

This is how you implement “nudge the model” behaviors (clarification prompts, reminders,
or tool-usage suggestions).

**Tool steering is allowed**: the guardrail does not execute tools itself, but the
corrective system message can instruct the model to call a specific tool on retry.
For example, a team can say: “If confidence is low, call tool X to verify before answering.”

### STOP: `StopSpec.user_message`
If a rule returns `STOP` with a `StopSpec.user_message`, the planner will terminate
and return that message as the final answer (no further LLM/tool calls).

This is appropriate for hard policies like system prompt exfiltration, direct tool escalation,
or high-confidence jailbreak signals.

## Multiple rules in the same turn

More than one rule can trigger on a single event/turn.

### Decision resolution
The gateway resolves multiple decisions using a priority policy:

`STOP` > `PAUSE` > `RETRY` > `REDACT` > `ALLOW`

Notes:
- The gateway chooses one “winning” action to enforce.
- Redactions can be combined across multiple rules when the winning action is `REDACT`.
- Effects can be unioned across decisions (e.g. `flag_trajectory`, `increment_strike`).

### What this means in practice
- If any rule returns `STOP`, it will win over `RETRY`/`REDACT`.
- If there’s no `STOP` but a rule returns `PAUSE`, it will win over `RETRY`.
- `RETRY` is useful when you want the model to comply safely, but it may still require
  a hard policy rule to ensure an actual block (use `STOP`) when needed.

## Policy Packs

Policy packs are YAML files that configure guardrails without changing code.
They can control:
- gateway mode (`shadow` vs `enforce`)
- sync/async evaluation behavior
- tool risk tiers and risk routing
- rule enablement and rule-specific configuration

### Loading and applying a policy pack
Use:
- `load_policy_pack(path, env=...)`
- `apply_policy_config(...)`

Reference implementation: `penguiflow/planner/guardrails/config.py`

Minimal wiring example:
```python
from penguiflow.planner.guardrails import (
    GuardrailGateway,
    RuleRegistry,
    ContextSnapshotBuilder,
    DefaultRiskRouter,
    load_policy_pack,
    apply_policy_config,
)
from penguiflow.steering import InMemoryGuardInbox
from penguiflow.planner.guardrails import AsyncRuleEvaluator

registry = RuleRegistry()
gateway = GuardrailGateway(
    registry=registry,
    guard_inbox=InMemoryGuardInbox(AsyncRuleEvaluator(registry)),
)
builder = gateway.context_builder
router = gateway.risk_router  # DefaultRiskRouter by default

policy = load_policy_pack("docs/policies/default.yaml", env="local")
apply_policy_config(registry, gateway, builder, router, policy)
```

### Policy pack shape (YAML)
```yaml
policy_pack: default-guardrails
version: "1.0"
gateway:
  mode: enforce
  sync:
    timeout_ms: 800
    fail_open: true
  async:
    enabled: true
    fail_open: true
sync_rules:
  - id: injection-patterns
    enabled: true
async_rules:
  - id: scope-classifier
    enabled: true
tool_risks:
  tools.delete_user: critical
risk_router:
  high_risk_wait_ms: 500
```

## Operational guidance

- Prefer **STOP** for high-confidence safety violations (prompt exfiltration, tool escalation).
- Prefer **RETRY** for “helpful correction” flows (clarify intent, enforce scope, ask for missing parameters).
- When building async rules, ensure the planner is configured to wait for them where required,
  otherwise treat them as monitoring/telemetry.

