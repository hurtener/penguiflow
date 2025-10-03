# PenguiFlow Library Hardening Ideas

## Background
Downstream teams integrating PenguiFlow highlighted pain points around surfaced errors, preserving the `Message` envelope across subflows, and diagnosing slow nodes. The incidents were triggered by application-level code, but there are opportunities for the core library to make the happy path clearer and expose better diagnostics out of the box.

## Library-Level Enhancements

### 1. FlowError Ergonomics
- Add a convenience attribute or method on `FlowEvent` (e.g., `event.error_payload`) that returns the decoded `FlowError.to_payload()` when present.
- Provide a small helper in `penguiflow.debug` (or similar) that formats `FlowEvent` objects with embedded errors for structured logging.
- Document the new helper in the runtime diagnostics section so adopters immediately see how to surface actionable errors.

### 2. Message Envelope Guardrails
- Added `testkit.assert_preserves_message_envelope(...)` so library tests can assert Message-aware nodes preserve the full envelope (headers + trace id) when returning results.
- The runtime now emits a `RuntimeWarning` whenever a node registered as `Message -> Message` returns a bare payload instead of a `penguiflow.types.Message`, surfacing mistakes before they hit production.
- Expanded the runtime documentation (`manual.md` / `llm.txt`) to call out the guardrails, the new helper, and best practices for Message-aware nodes.

### 3. Built-in Diagnostics Hooks
- Ship a reusable middleware (`log_flow_events`) that logs start/finish/elapsed plus error detail, so adopters can enable rich telemetry with one import.
- Consider lightweight latency instrumentation (histogram-ready timers) exposed via an optional callback interface, enabling services to attach metrics without re-implementing timing logic.

### 4. Test Coverage Additions
- Extend FlowTestKit examples/tests to cover the new envelope guardrails and logging helpers.
- Add a regression test ensuring `FlowEvent` exposes the embedded error payload for failed nodes.

These enhancements keep PenguiFlow lightweight while making it harder for integrators to fall into the documented traps.
