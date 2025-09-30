# A2A server adapter tests

`tests/test_a2a_server.py` exercises the FastAPI adapter that exposes PenguiFlow
as an A2A-compliant agent surface. The suite covers:

* Agent discovery via `GET /agent` and unary execution through `message/send`.
* Streaming semantics over Server-Sent Events, including chunk propagation,
  artifact emission, and the `done` sentinel.
* Task cancellation mirrored from `/tasks/cancel` into the running PenguiFlow
  trace, ensuring the SSE stream emits a `TRACE_CANCELLED` error payload.
* Validation failures when required request headers (e.g., `tenant`) are
  omitted.
* Persistence of remote bindings through the configured `StateStore` for both
  unary and streaming pathways.

Use this document as a checklist when expanding coverage for new A2A features or
when porting the adapter to alternative web frameworks.
