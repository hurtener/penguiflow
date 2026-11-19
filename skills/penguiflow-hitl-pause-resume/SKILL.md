---
name: penguiflow-hitl-pause-resume
description: Add human-in-the-loop pause/resume to a PenguiFlow ReactPlanner — let tools call `ctx.pause(reason, payload)` to gate approvals, OAuth handoffs, await-input prompts, and policy decisions; consume the resulting `PlannerPause(reason, payload, resume_token)` in your UI; call `planner.resume(resume_token, user_input=..., tool_context=...)` to continue; and persist pause state across workers via a `StateStore` implementing `save_planner_state`/`load_planner_state`. Use when a user says "pause for HITL", "human in the loop", "approval gate", "wait for user input", "OAuth handoff", "resume after restart", or names `PlannerPause`, `ctx.pause`, `resume_token`.
---

# PenguiFlow HITL Pause and Resume

## When to use
- A tool must wait for a human decision (approval, edit, OAuth completion).
- A run must survive a process restart between pause and resume.
- You need a structured pause payload your UI renders (not just a yield).

## When NOT to use
- Async background work the user doesn't directly approve → use [[penguiflow-background-tasks]].
- Streaming partial responses to the user → use [[penguiflow-streaming]].
- The durable backend that persists pauses → use [[penguiflow-statestore]].
- The OAuth bookkeeping inside `ToolNode` → use [[penguiflow-mcp-integration]] (this skill covers the planner-side pause machinery).

## Hard boundaries
This skill is the **pause/resume contract** between tools, the planner, and your host app. It does not define your UI, your auth model, or the durable backend (statestore does). It does explain how OAuth pauses look from the planner's perspective, but the MCP skill owns the ToolNode-side OAuth wiring.

## Workflow

### 1) Decide what to pause for
| `reason` | Use case |
|---|---|
| `approval_required` | "Is it OK to send this email / write this row?" |
| `await_input` | "Please provide the missing field" |
| `external_event` | OAuth handoff, third-party webhook, async callback |
| `constraints_conflict` | Policy violation that needs operator override |

The reason is observable; the planner emits it in events and the payload travels with it.

### 2) Pause from a tool
```python
@tool(desc="Write a line to a system (approval required)", side_effects="write")
async def write_line(args: WriteArgs, ctx: ToolContext) -> WriteOut:
    approvals = ctx.tool_context.get("approvals") or {}
    if not approvals.get("write_line"):
        await ctx.pause(
            "approval_required",
            {
                "title": "Approve write",
                "preview": args.text,
                "approval_key": "write_line",
            },
        )
    # safe path after approval
    return WriteOut(ok=True)
```

Critical mental model: **`ctx.pause(...)` raises an internal signal that exits the tool body.** The tool does **not** resume mid-function. After `planner.resume(...)`, the planner replays its trajectory and the LLM decides the next step — usually calling the same tool again with the approval flag now set.

### 3) Consume the pause in the host app
```python
result = await planner.run(user_message, tool_context=tool_ctx)

while isinstance(result, PlannerPause):
    # render result.payload in the UI
    # wait for user action
    updated_ctx = {**tool_ctx, "approvals": {"write_line": True}}
    result = await planner.resume(
        result.resume_token,
        user_input="approved",       # optional
        tool_context=updated_ctx,
    )

# result is now a final answer
```

`PlannerPause` fields:
- `reason: str` — one of the four above.
- `payload: dict` — JSON-friendly, application-defined.
- `resume_token: str` — opaque, **treat as a secret**.

### 4) Make `resume_token` survive restarts
Pause records live in memory by default. For multi-worker / restart-prone deployments, configure a `StateStore` implementing `SupportsPlannerState`:
- `save_planner_state(token, payload) -> None`
- `load_planner_state(token) -> payload | None` (consume-on-load recommended)

Attach via `ReactPlanner(state_store=YourStore())`. A failing store does not block the pause — it's logged and ignored, and the in-memory record still works for the current process.

See [[penguiflow-statestore]] for backend implementations.

### 5) Pass session identity on every call
On both `run()` and `resume()` pass `tool_context={"session_id": ..., "tenant_id": ..., "user_id": ...}`. Required for session dispatch, multi-tenant memory ([[penguiflow-memory]]), and OAuth tools. On resume, `tool_context` **overrides** any stored value — use this to supply the answer (set `approvals`, add a missing field, etc.).

### 6) Keep payloads small and safe
JSON-friendly only. No secrets. Large binary → upload to artifact store, reference by id. Long strings → truncate.

### 7) Special case: OAuth pauses
ToolNodes with `AuthType.OAUTH2_USER` pause with `reason="external_event"` and payload `{"pause_type": "oauth", "provider": ..., "auth_url": ..., "state": ..., "scopes": [...], "display_name": ...}`. UI opens `auth_url`; callback stores token; you call `planner.resume(...)`. See [[penguiflow-mcp-integration]] for the callback/token store contract.

## Troubleshooting (fast checks)
- **`planner.resume(...)` raises `KeyError`** — in-memory record lost on restart **or** StateStore doesn't implement `save_planner_state`/`load_planner_state`. Add durable planner state.
- **Resume missing clients** — non-JSON `tool_context` values got dropped on persist. Re-pass `tool_context`; keep clients in factories/registries.
- **Pauses never happen** — `pause_enabled=False`, or the tool doesn't call `ctx.pause(...)`.
- **Same approval keeps re-prompting** — resume `tool_context` didn't carry the approval flag, or the tool reads a different key.
- **OAuth keeps re-pausing after resume** — callback stored token under a different `(user_id, provider)` than the planner reads.
- **`resume_token` reused** — not one-time use by default. Delete on load in your `StateStore` to enforce.
- **Cross-tenant replay** — tenant-scope StateStore keys (`tenant:<id>:planner:pause:<token>`).
- **Payload too large** — offload to artifacts; reference by id.

## Worked example
- `examples/react_pause_resume/main.py` — approval-gated tool with explicit pause + resume.

## References (load only as needed)
- `references/pause-contract.md` — `ctx.pause(...)`, pause reasons, the "raise-and-replay" mental model, payload schema rules.
- `references/resume-mechanics.md` — `planner.resume(...)` semantics, `tool_context` override behavior, `user_input` usage, OAuth resume specifics.
- `references/durability-and-statestore.md` — `SupportsPlannerState`, save/load semantics, TTL, token security, cross-worker recipes.
