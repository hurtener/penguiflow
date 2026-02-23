# Telemetry patterns

## What it is / when to use it

This page collects **production patterns** for observing PenguiFlow runs, with a bias toward:

- incident debugging (“why is this stuck/slow/failing?”),
- multi-tenant safety (what not to log/store),
- building dashboards and alerts from runtime events.

If you only set up one thing: capture `FlowEvent` + correlate by `trace_id`.

## Non-goals / boundaries

- This page does not mandate a telemetry vendor or SDK.
- This is not a complete spec for every event a system might emit; it documents practical patterns.
- Planner-specific observability is covered in planner docs; this page focuses on runtime + worker/service integration.

## Contract surface

### The canonical event primitive: `FlowEvent`

The runtime emits `FlowEvent` for:

- node lifecycle (`node_start`, `node_success`, `node_error`, `node_timeout`, `node_retry`, `node_failed`),
- control-plane behavior (`trace_cancel_*`, `deadline_skip`, trace cancel drops),
- queue depth and trace inflight/pending counts (when `trace_id` exists).

`FlowEvent` provides:

- `to_payload()` for structured logs,
- `metric_samples()` to derive numeric metrics.

### Correlation key: `trace_id`

Use `trace_id` to correlate:

- request/job boundaries,
- streaming chunks and final answers,
- logs across nodes and tools.

Operational rule:

- **logs**: it is correct to include `trace_id`,
- **metrics**: do not tag by `trace_id` (cardinality explosion).

### Tenant boundary: `Headers.tenant`

In envelope flows, `Headers.tenant` is your default tenant boundary. It should be present in logs and (low-cardinality) metric tags.

## Operational defaults (recommended)

- Emit/record:
  - `trace_id`, `tenant`, `node_name`, `event_type`, `latency_ms`, queue depth.
- Prefer:
  - structured logs (JSON) for production,
  - middleware-driven capture of runtime events (avoid ad-hoc prints).
- Persist events via `StateStore` when you need audit/replay or debugging without log access.

## Patterns (concrete)

### Pattern: capture runtime events centrally

- Attach `log_flow_events(...)` middleware to every flow.
- Optionally add a second middleware that forwards the full `FlowEvent` object to your metric pipeline.

See **[Logging](logging.md)**.

### Pattern: record queue depth + saturation

Queue depth is the first “is it stuck?” signal:

- sustained high `queue_depth_total` → saturation/backpressure,
- growing `trace_pending` for a trace → blocked downstream or dead egress,
- rising `trace_inflight` without completions → slow dependency or timeout misconfiguration.

Dashboards should include:

- queue depth over time,
- node latency distributions,
- node timeout/error counters.

### Pattern: classify failures by category

Instead of “everything is an exception”, classify:

- timeouts (`node_timeout`),
- application exceptions (`node_error` / `node_failed`),
- cancellation (`trace_cancel_*`, `node_trace_cancelled`),
- deadline skips (`deadline_skip`).

This directly maps to remediation playbooks:

- timeouts → tighten timeouts / reduce concurrency / fix dependency,
- errors → fix code / validation / schema drift,
- cancellation/deadlines → investigate upstream budgets or user cancel behavior.

### Pattern: protect sensitive payloads

Default stance:

- do not log raw tool payloads,
- do not store raw model prompts/responses unless you have explicit retention and redaction.

Safe alternatives:

- log hashes, ids, and summarized fields,
- store large content as artifacts/resources and log references.

## Failure modes & recovery

- **You can’t debug without reading code**: you’re missing FlowEvents, trace ids, or stable node names. Fix: structured logging + middleware + enforced naming.
- **Telemetry is too expensive**: high-volume `node_start` logs. Fix: adjust log level for `node_start` or sample.
- **Metrics are unusable**: you tagged metrics with `trace_id` or other high-cardinality values. Fix: keep trace correlation in logs only.
- **You leaked secrets**: you logged payloads/meta. Fix: redact at tool boundaries and do not store secrets in `Message.meta`.

## Observability (incident debugging flow)

When “something is wrong”, use this sequence:

1. Find the request/job `trace_id` (ingress log).
2. Filter logs by `trace_id`:
   - look for `node_timeout`, `node_error`, `node_failed`,
   - check the last successful node and the next node’s queue depth.
3. Check saturation:
   - queue depth trend,
   - node latency histograms for the “hot” node,
   - retry counts.
4. Check control-plane events:
   - cancellation start/finish,
   - deadline skips.
5. Decide remediation:
   - reduce concurrency / tighten timeouts,
   - disable retries temporarily,
   - gate or disable a problematic tool integration.

## Security / multi-tenancy notes

- Treat logs and event stores as sensitive; assume broad internal access.
- Keep tenant boundaries explicit (`Headers.tenant`) and avoid cross-tenant traces.
- Prefer storing redacted, summarized telemetry; keep raw content behind explicit approvals and retention.

## Runnable examples

```bash
uv run python examples/quickstart/flow.py
uv run python examples/roadmap_status_updates/flow.py
```

## Troubleshooting checklist

- Need log setup: see **[Logging](logging.md)**.
- Need alert recommendations: see **[Metrics & alerts](metrics-and-alerts.md)**.
