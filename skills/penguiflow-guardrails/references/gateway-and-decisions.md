# `GuardrailGateway` and Decision Surface

## `GuardrailGateway`

The gateway holds a `RuleRegistry`, an async-rule inbox, plus optional decision-policy / risk-router / context-builder hooks. Construction:

```python
from penguiflow.planner.guardrails import (
    GatewayConfig, GuardrailGateway, RuleRegistry,
)
from penguiflow.steering.guard_inbox import SteeringGuardInbox

registry = RuleRegistry()
registry.register_sync(ToolAllowlistRule(...))
registry.register_async(MyDeepRule(...))

gateway = GuardrailGateway(
    registry=registry,
    guard_inbox=SteeringGuardInbox(),
    config=GatewayConfig(),
)

planner = ReactPlanner(..., guardrail_gateway=gateway)
```

Rules are objects implementing the `GuardrailRule` protocol from `penguiflow.planner.guardrails.protocols`, not bare callables. The `DefaultDecisionPolicy` aggregates rule outputs by priority (STOP > PAUSE > RETRY > REDACT > ALLOW), combines redactions across all rules, and merges effects.

## `GatewayConfig`

| Field | Default | Purpose |
|---|---|---|
| `mode` | `"enforce"` | `"shadow"` (log only) or `"enforce"`. Library default is **enforce** — flip to `"shadow"` during rollout. |
| `sync_timeout_ms` | `15.0` | Hard deadline for sync rules. Beyond this, the rule's evaluation is abandoned. |
| `sync_parallel` | `True` | Run sync rules concurrently within the timeout. |
| `async_enabled` | `True` | Allow rules registered as DEEP to dispatch via `guard_inbox`. |
| `sync_fail_open` | `False` | Sync rule timeout: `False` = safety (block-equivalent), `True` = ALLOW. |
| `async_fail_open` | `True` | Async rule unavailable: `True` = ALLOW, `False` = block. |

The default profile is conservative (`mode="enforce"`, `sync_fail_open=False`). For staged rollout, set `mode="shadow"`, measure rule hit rates, then revert to enforce per rule.

## `GuardrailEvent`

What rules receive.

```python
GuardrailEvent(
    event_type: str,                # see table below
    run_id: str,                     # required; correlates with GuardrailContext.run_id
    text_content: str | None = None,  # user input or streamed text
    tool_name: str | None = None,     # tool involved (when applicable)
    tool_args: dict | None = None,    # tool args (when applicable)
    payload: dict = {},               # tool_call_id, action_seq, conversation_history, etc.
    timestamp: float = <now>,         # auto-set
)
```

Rules also have access to a `GuardrailContext(run_id, tenant_id, persona_id, tool_context, strike_counts, policy_config)` carrying durable session state. The gateway builds a `ContextSnapshotV1` (user_text, primary_source, contains_untrusted, available_tools, current_tool, max_tool_risk, requests_system_info, requests_capability_change, previous_violations) and passes it to rules and the decision policy.

### Event types

| `event_type` | When | Fields populated |
|---|---|---|
| `llm_before` | Before LLM planning step | `text_content` (user message), conversation history in `payload` |
| `tool_call_start` | Before tool execution | `tool_name`, `tool_args`, `payload.tool_call_id` |
| `tool_call_result` | After tool returns | `tool_name`, result in `payload`, `payload.tool_call_id` |
| `llm_stream_chunk` | Each streamed text chunk | `text_content` (chunk text) |

Rules subscribe to specific event types — don't run a tool-call rule on `llm_stream_chunk`.

## `GuardrailDecision`

What rules return.

```python
GuardrailDecision(
    action: GuardrailAction,                 # enum: ALLOW | REDACT | RETRY | PAUSE | STOP
    rule_id: str,                            # stable identifier for audit
    reason: str,                             # short human-readable cause; required
    decision_id: str = <uuid>,               # auto-generated
    correlation_id: str | None = None,
    severity: GuardrailSeverity = MEDIUM,    # enum: LOW | MEDIUM | HIGH | CRITICAL
    confidence: float | None = None,         # 0.0 - 1.0
    was_sync: bool = True,                   # gateway sets False for DEEP-rule outputs
    effects: tuple[str, ...] = (),           # arbitrary downstream effect tags
    redactions: tuple[RedactionSpec, ...] | None = None,
    retry: RetrySpec | None = None,
    pause: PauseSpec | None = None,
    stop: StopSpec | None = None,
    classifier_result: dict | None = None,
)
```

`rule_id` and `reason` are both required. There is no `metadata` field — use `effects` for tags and `classifier_result` for structured output.

### `RedactionSpec`
```python
RedactionSpec(
    path: str,                                # required; JSONPath-like into event payload
    replacement: str = "[REDACTED]",
    entity_type: str | None = None,
    start_offset: int | None = None,
    end_offset: int | None = None,
)
```
Redaction is path-based (into the event payload), not regex- or span-based. Multiple specs combine and are applied by the gateway after the winning decision is `REDACT`.

### `RetrySpec`
```python
RetrySpec(
    max_attempts: int = 2,
    corrective_message: str = "",
)
```
Use for fixable LLM mistakes (schema violation, unsafe phrasing). Don't use for transient errors — those are runtime retries (see [[penguiflow-core-flows]] `references/errors-retries.md`).

### `PauseSpec`
```python
PauseSpec(
    scope: Literal["run", "step", "tool_call"] = "tool_call",
    approver_roles: tuple[str, ...] = ("admin",),
    prompt: str = "",
    timeout_s: float | None = 300.0,
)
```
`scope` is a fixed `Literal`, not a free string. Triggers `PlannerPause(reason="approval_required")` on any of the four events (enforce mode). The decision is in `pause.payload["guardrail"]` (`action`, `rule_id`, `reason`, `decision_id`, `severity`, `effects`, `stop`, `pause`); `pause` holds these `PauseSpec` fields as a dict, or `None`. The planner does not enforce them (`approver_roles`, `timeout_s` are for your approval UI). The guarded LLM call / tool does not run (on `tool_call_result` the tool already ran; its result is withheld). The host resumes via `planner.resume(...)`; the LLM plans again and rules re-run (they don't see the approval). With `pause_enabled=False` the PAUSE fails closed: tool events → error `guardrail_pause_unavailable:<rule_id>`; `llm_before`/stream → final answer "Unable to complete the request.". See [[penguiflow-hitl-pause-resume]].

### `StopSpec`
```python
StopSpec(
    error_code: str = "GUARDRAIL_STOP",
    user_message: str = "I'm unable to complete that request.",
    internal_reason: str = "",
)
```
All fields default; override the ones you need. The planner emits a stopped result with `error_code` and `user_message`. Don't leak `internal_reason` to users.

## Decision aggregation (the actual rule)

`DefaultDecisionPolicy` sorts decisions by `(priority, confidence)` desc, where priority is:
```
STOP=5, PAUSE=4, RETRY=3, REDACT=2, ALLOW=1
```
The top decision wins. When the winner is `REDACT`, all rules' `redactions` from the sorted list are concatenated into a combined `RedactionSpec` tuple. `effects` across all decisions are merged onto the winner via `decision.with_effects(...)`.

`severity` is for logging and observability; it doesn't affect aggregation order.

## Conversation history in events

`guardrail_conversation_history_turns=N` (default `1`) on `ReactPlanner` includes the last N memory turns in `event.payload["conversation_history"]`. Useful for rules that need context ("did the user just deny this?"). Set to `0` to disable.

## Authoring a rule

A rule implements the `GuardrailRule` protocol (`penguiflow.planner.guardrails.protocols.GuardrailRule`):
```python
class GuardrailRule(Protocol):
    rule_id: str
    cost: RuleCost                           # FAST | DEEP

    async def evaluate(
        self,
        event: GuardrailEvent,
        ctx: GuardrailContext,
        snapshot: ContextSnapshotV1,
    ) -> GuardrailDecision | None: ...
```

Register via `RuleRegistry.register_sync(...)` for `RuleCost.FAST` and `register_async(...)` for `RuleCost.DEEP`. Returning `None` means "not applicable" — the policy treats it as ALLOW from this rule.

### Example: tool denylist
```python
from penguiflow.planner.guardrails import (
    GuardrailAction, GuardrailDecision, GuardrailSeverity, RuleCost, StopSpec,
)

class ToolDenylistRule:
    rule_id = "tool_denylist_v1"
    cost = RuleCost.FAST
    DENIED = {"shell", "exec", "delete_all"}

    async def evaluate(self, event, ctx, snapshot):
        if event.event_type != "tool_call_start":
            return None
        if event.tool_name in self.DENIED:
            return GuardrailDecision(
                action=GuardrailAction.STOP,
                rule_id=self.rule_id,
                reason=f"Tool {event.tool_name} is on the denylist.",
                severity=GuardrailSeverity.CRITICAL,
                stop=StopSpec(
                    error_code="tool_denylisted",
                    user_message="I can't run that operation.",
                    internal_reason=f"Tool {event.tool_name} is on the denylist.",
                ),
            )
        return None
```

### Example: PII redaction (path-based)
```python
from penguiflow.planner.guardrails import GuardrailAction, GuardrailDecision, RedactionSpec, RuleCost

class PIIRedactRule:
    rule_id = "pii_redact_v1"
    cost = RuleCost.FAST

    async def evaluate(self, event, ctx, snapshot):
        if event.event_type != "tool_call_result":
            return None
        # path is into the event/payload tree; the gateway applies redaction by path.
        return GuardrailDecision(
            action=GuardrailAction.REDACT,
            rule_id=self.rule_id,
            reason="Mask SSN-like values in tool output",
            redactions=(
                RedactionSpec(path="payload.result.body", entity_type="ssn", replacement="[SSN]"),
            ),
        )
```

### Example: approval gate
```python
from penguiflow.planner.guardrails import GuardrailAction, GuardrailDecision, PauseSpec, RuleCost

class ProdWriteApproval:
    rule_id = "prod_write_approval_v1"
    cost = RuleCost.FAST

    async def evaluate(self, event, ctx, snapshot):
        if event.event_type != "tool_call_start" or event.tool_name != "db.write":
            return None
        target = (event.tool_args or {}).get("target_db")
        if target == "prod":
            return GuardrailDecision(
                action=GuardrailAction.PAUSE,
                rule_id=self.rule_id,
                reason="Production write needs approval",
                pause=PauseSpec(
                    scope="tool_call",                             # Literal — must be run|step|tool_call
                    approver_roles=("admin",),
                    prompt=f"Approve write to prod for: {event.tool_args.get('preview', '...')}",
                    timeout_s=3600.0,
                ),
            )
        return None
```

As written, this rule pauses again after `planner.resume(...)` if the LLM retries the same prod write: rules don't see the approval, so a real rule needs its own way to recognise an approved call.

## Audit format

When the gateway acts, log:
```json
{
  "event_type": "tool_call_start",
  "tool_name": "db.write",
  "action": "PAUSE",
  "rule_id": "prod_write_approval_v1",
  "decision_id": "...",
  "severity": "medium",
  "reason": "Production write needs approval",
  "trace_id": "...",
  "run_id": "...",
  "was_sync": true
}
```

Drop everything sensitive (tool_args may contain user data). Keep enough to reconstruct what fired and why.
