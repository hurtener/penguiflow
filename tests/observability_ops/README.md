# Observability & Ops tests

This suite covers the phase 4 deliverables that polish PenguiFlow's
observability surface:

* `test_remote_observability.py` verifies that the new remote metrics emit
  structured `FlowEvent`s capturing latency, payload sizes, context/task
  identifiers, and cancellation reasons for unary, streaming, error, and
  cancellation scenarios.
* `test_admin_cli.py` exercises the `penguiflow-admin` developer CLI, ensuring
  trace history can be rendered and replayed via dynamically imported
  `StateStore` factories.

Each scenario runs against in-memory fakes so the async runtime executes the
same code paths used in production flows without requiring external services.
