# PenguiFlow Library Hardening Ideas

## Background
Downstream teams integrating PenguiFlow highlighted pain points around surfaced errors, preserving the `Message` envelope across subflows, and diagnosing slow nodes. The incidents were triggered by application-level code, but there are opportunities for the core library to make the happy path clearer and expose better diagnostics out of the box.

## Library-Level Enhancements

### 1. FlowError Ergonomics
- Add a convenience attribute or method on `FlowEvent` (e.g., `event.error_payload`) that returns the decoded `FlowError.to_payload()` when present.
- Provide a small helper in `penguiflow.debug` (or similar) that formats `FlowEvent` objects with embedded errors for structured logging.
- Document the new helper in the runtime diagnostics section so adopters immediately see how to surface actionable errors.

### 2. Message Envelope Guardrails
- Introduce an optional `ensure_message_output` decorator or FlowTestKit assertion (`assert_preserves_message_envelope(node_fn)`) that verifies nodes return a `Message` when expected.
- Emit a runtime warning when a node configured as `Message -> Message` returns a bare payload, helping catch regressions earlier.
- Expand library docs to state the contract explicitly and link to the helper utilities above.

### 3. Built-in Diagnostics Hooks
- Ship a reusable middleware (`log_flow_events`) that logs start/finish/elapsed plus error detail, so adopters can enable rich telemetry with one import.
- Consider lightweight latency instrumentation (histogram-ready timers) exposed via an optional callback interface, enabling services to attach metrics without re-implementing timing logic.

### 4. Test Coverage Additions
- Extend FlowTestKit examples/tests to cover the new envelope guardrails and logging helpers.
- Add a regression test ensuring `FlowEvent` exposes the embedded error payload for failed nodes.

These enhancements keep PenguiFlow lightweight while making it harder for integrators to fall into the documented traps.
