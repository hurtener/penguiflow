# Messages and Envelopes

PenguiFlow supports two message styles. **Pick one per flow** — mixing them in a single graph loses metadata propagation.

## Payload-only style

Nodes take and return Pydantic models directly.

```python
class In(BaseModel): text: str
class Out(BaseModel): upper: str

async def to_upper(msg: In, _ctx) -> Out:
    return Out(upper=msg.text.upper())
```

Use this when:
- You're learning the library.
- You're building a one-shot pipeline with no per-trace cancel/deadline/streaming needs.

Drawbacks:
- No `trace_id` → `flow.cancel(...)` is a no-op.
- No `Headers.tenant` → no built-in multi-tenant boundary.
- No `deadline_s`.
- `join_k` won't work (it requires `trace_id`).

## Envelope style (`Message`)

```python
from penguiflow import Headers, Message
msg = Message(payload=..., headers=Headers(tenant="acme"))
await flow.emit(msg)
```

Envelope fields:
- `payload: Any` — the actual data; usually a Pydantic model.
- `headers: Headers` — `tenant: str` (required), `topic: str | None`, `priority: int = 0`.
- `trace_id: str` — auto-generated UUID per `Message` unless you set it.
- `deadline_s: float | None` — wall-clock deadline; expired messages are skipped.
- `meta: dict` — arbitrary JSON-friendly bag.

Use this for any production deployment. Required for:
- Per-trace cancellation (`flow.cancel(trace_id)`).
- Deadlines (`Message(deadline_s=...)`).
- Streaming chunks (`ctx.emit_chunk(parent=msg, ...)` inherits `trace_id`).
- `join_k` (buckets by `trace_id`).
- Multi-tenant scoping (`Headers.tenant`).

## Other primitives in `penguiflow.types`

- `StreamChunk(stream_id, seq, text, done, meta)` — payload type for streaming nodes.
- `FinalAnswer(text, citations)` — canonical "done" payload for envelope flows.
- `Thought`, `PlanStep`, `WM` — ReactPlanner-side types; not used directly in core runtime.

## Trace-scoped roundtrips

For request/response across many concurrent traces:

```python
await flow.emit(msg, trace_id=msg.trace_id)
result = await flow.fetch(trace_id=msg.trace_id)
```

Behavior:
- `emit(trace_id=...)` attaches/overrides the message's `.trace_id`.
- Acquires a per-trace **roundtrip lock** so concurrent roundtrips for the same trace serialize.
- Activates a trace-scoped Rookery dispatcher: `fetch(from_=...)` filtering is **no longer supported** once trace-scoped fetching is on.

When to use: any system where multiple users hit the same flow concurrently — you need deterministic correlation, not first-come-first-served `fetch()`.

## Deadlines

Set `deadline_s` on the envelope. When a node receives an expired message:
- The node body is **skipped**.
- The runtime emits a `deadline_skip` event.
- For `Message` envelopes, the runtime emits a `FinalAnswer(text="Deadline exceeded")` to Rookery so callers see a result.

```python
Message(payload=..., headers=Headers(tenant="acme"), deadline_s=5.0)
```

Propagate deadlines explicitly when you `model_copy` an envelope downstream; otherwise the new message has no deadline.

## Multi-tenant rules

- Always set `Headers.tenant`. It's the boundary.
- **Never reuse a `trace_id` across tenants.** Joins, fetches, and cancels all key by `trace_id`.
- Treat `trace_id` as an authorization surface: a user must not cancel/fetch another user's trace.

## Streaming with envelopes

```python
async def compose(msg: Message, ctx) -> None:
    await ctx.emit_chunk(parent=msg, text="hello ", to=chunk_node)
    await ctx.emit_chunk(parent=msg, text="world", done=True, to=chunk_node)
    await ctx.emit(msg.model_copy(update={"payload": FinalAnswer(text="hello world")}), to=final_node)
```

Chunks inherit `trace_id` from `parent`. See [[penguiflow-streaming]] for the protocol-agnostic streaming layer.

## Common pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| Cancel does nothing | Message had no `trace_id` (payload-only) | Use `Message` envelope |
| Cross-trace mixups | Re-using `trace_id` across users/sessions | Generate a fresh `trace_id` per request |
| Streaming silent | Calling `ctx.emit_chunk` without `parent=Message` | Pass `parent=msg` so `trace_id` propagates |
| `fetch(trace_id=...)` errors with `from_` | Trace-scoped dispatcher disables `from_` filtering | Drop `from_=...`, use `trace_id` for correlation |
| Deadline silently passes | New downstream messages didn't inherit `deadline_s` | Re-propagate `deadline_s` in `model_copy(update=...)` |
| Mixed-style flow loses metadata | Some nodes return raw payloads after envelope nodes | Pick one style per flow; envelope nodes must return `Message` |

## Recommended defaults
- Envelope (`Message`) for anything beyond a toy.
- `Headers.tenant` always set.
- `trace_id` scoped per request/session (not per user, not global).
- Keep `meta` JSON-friendly so events serialize.
- Don't store secrets in `meta` or `payload` if events are persisted.
