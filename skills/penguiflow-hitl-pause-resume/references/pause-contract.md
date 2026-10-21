# The Pause Contract

## How pauses work mechanically

```
planner.run(user_message, tool_context=...)
  -> LLM step
  -> tool invocation
  -> tool calls `await ctx.pause(reason, payload)`
  -> pause raises an internal signal that exits the tool body
  -> planner catches the signal
  -> planner records the pause:
       in-memory always
       persisted via state_store if available
  -> planner returns PlannerPause(reason, payload, resume_token)
```

The tool's local variables are **gone**. The function did not "pause mid-execution" — it raised. After resume, the planner reads its trajectory and the LLM decides what to do next. Usually that means re-invoking the same tool with updated `tool_context` (the approval flag, the OAuth token, the missing field).

This is critical: design your tools to **re-check `tool_context` at the top** and pause if the requirement isn't met. Don't pause in the middle of a multi-step tool — split it into two tools or move the pause to the start.

## `ctx.pause(reason, payload)`

Parameters:
- `reason: str` — one of `approval_required` | `await_input` | `external_event` | `constraints_conflict`.
- `payload: dict` — JSON-friendly, application-defined.

`ctx.pause(...)` does not return. It raises an internal exception that exits the tool body.

### Reason taxonomy

#### `approval_required`
The tool wants to do something the user must explicitly OK. Examples: writing data, sending an email, calling an external API with side effects.

Typical payload:
```python
{
    "title": "Approve write",
    "preview": "First 200 chars of what will be written",
    "approval_key": "write_line",   # convention: tool name or operation id
    "tags": ["destructive"],
}
```

Guardrail `PAUSE` decisions (enforce mode) also produce `approval_required`, with payload `{"guardrail": {...}}` (the decision, including its `PauseSpec` under `"pause"`). See [[penguiflow-guardrails]].

#### `await_input`
The tool needs information the user has but hasn't provided. Examples: missing form field, ambiguous reference, clarification.

Typical payload:
```python
{
    "title": "Which database?",
    "options": ["prod", "staging", "dev"],
    "input_key": "target_db",
}
```

#### `external_event`
The tool depends on an out-of-band event. The canonical case is OAuth, but any "go do X then come back" handoff fits.

Typical payload (OAuth):
```python
{
    "pause_type": "oauth",
    "provider": "github",
    "auth_url": "https://...",
    "state": "<random>",
    "scopes": ["repo"],
    "display_name": "GitHub",
}
```

Typical payload (custom external):
```python
{
    "pause_type": "webhook",
    "callback_url": "https://app.example.com/callback/abc123",
    "expected_event": "payment.succeeded",
}
```

#### `constraints_conflict`
Policy / guardrail says no, and the user must decide whether to override. Distinct from `approval_required` in that the **default** is to deny.

Typical payload:
```python
{
    "title": "Policy violation",
    "rule": "no_writes_to_prod",
    "violation_summary": "Tool foo would write to prod tables",
    "override_key": "force_prod_write",
    "requires_role": "admin",
}
```

## Payload rules

1. **JSON-friendly only.** Strings, numbers, booleans, null, lists, dicts. No datetimes, no Pydantic models, no callables.
2. **Small.** Payloads travel through the planner record, possibly the StateStore, possibly your UI. Aim for <4 KB.
3. **No secrets.** Payloads are application-visible and likely logged.
4. **Stable schema per tool.** UIs render the payload — don't change the shape between releases without a UI migration.
5. **No prompt content.** Don't echo the user's full message; reference it by trace_id if needed.

### When a payload exceeds these limits

- Large content → upload to the artifact store, reference by id.
- Sensitive content → store in a side channel keyed by `session_id`, payload references the side-channel id.
- Many options → first-screen + "see more" pattern in the UI; truncate the payload list.

## Tool design patterns

### Pattern 1: Check-and-pause-at-entry

The default. Re-check on each invocation; pause only if the requirement isn't met.

```python
@tool(desc="...", side_effects="write")
async def my_tool(args, ctx):
    if not ctx.tool_context.get("approvals", {}).get(MY_KEY):
        await ctx.pause("approval_required", {"approval_key": MY_KEY, ...})
    return await do_work(args)
```

### Pattern 2: Plan-and-commit split

Split a destructive tool into two:
- `plan_action` — pure, returns a preview.
- `commit_action` — gated by approval; reads the preview from `tool_context["pending_plan"]`.

The LLM calls `plan_action` first, then asks for approval, then calls `commit_action`.

```python
@tool(desc="Plan a write", side_effects="pure")
async def plan_action(args, ctx):
    plan = await build_plan(args)
    ctx.tool_context["pending_plan"] = plan.model_dump()
    return PlanOut(preview=plan.summary, plan_id=plan.id)

@tool(desc="Commit the plan", side_effects="write")
async def commit_action(args, ctx):
    approvals = ctx.tool_context.get("approvals", {})
    if not approvals.get(args.plan_id):
        await ctx.pause("approval_required",
                       {"approval_key": args.plan_id, "preview": ctx.tool_context["pending_plan"]["summary"]})
    return await execute(ctx.tool_context["pending_plan"])
```

### Pattern 3: Multi-stage await-input

Each missing field is a separate pause:

```python
@tool(desc="...", side_effects="external")
async def send_email(args, ctx):
    if not args.recipient:
        await ctx.pause("await_input", {"input_key": "recipient", "prompt": "To whom?"})
    if not args.subject:
        await ctx.pause("await_input", {"input_key": "subject", "prompt": "What subject?"})
    return await sender.send(args)
```

This works **only** because pauses raise and the tool restarts on resume. The LLM provides the missing field via re-invocation with updated args.

## Operational rules

- **Always check at the top.** Don't pause halfway through computation.
- **Idempotent pre-pause work.** Anything you do before `ctx.pause(...)` re-runs on every retry.
- **Cheap re-entry.** If checking the precondition requires a slow DB call, cache it in `tool_context`.
- **One pause reason per pause site.** Don't multiplex reasons.
- **Use `pause_enabled=False`** in deployments where HITL shouldn't fire — `ctx.pause(...)` then raises `RuntimeError` (a tool error), surfacing bugs. Guardrail `PAUSE` decisions fail closed instead: the tool call (or its result) is blocked with `guardrail_pause_unavailable:<rule_id>` (LLM-event pauses end the run with "Unable to complete the request.").

## When pauses don't fire

| Symptom | Cause | Fix |
|---|---|---|
| Tool runs without prompting | `tool_context` already has the approval/input | Verify the check |
| Tool raises "pause disabled" | Planner has `pause_enabled=False` | Enable on planner config, or remove HITL from that deployment |
| Tool call errors `guardrail_pause_unavailable:*` | A guardrail returned PAUSE with `pause_enabled=False` (fail closed) | Enable pause, or change the rule's action |
| Pause exits early without payload | Misnamed reason | Use exactly the 4 reasons listed |
| Pause runs but never returns to caller | LLM ignored the resume and called a different tool | Check trajectory; tighten prompt to re-invoke the gated tool |
