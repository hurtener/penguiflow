# MLflow Metrics Middleware

Demonstrates how PenguiFlow's phase-5 observability hooks expose structured
`FlowEvent` objects that can be forwarded to MLflow. The example attaches a
middleware that records queue depth, retry counts, and latency for each node.
If MLflow is installed, events are logged to a local tracking directory;
otherwise the middleware prints the captured metrics to stdout so the flow
remains runnable without extra dependencies.

## Run it

```bash
uv run python examples/mlflow_metrics/flow.py
```

The script spins up a three-node flow, attaches the MLflow middleware, emits a
single message, and prints the final `FinalAnswer` alongside the total number
of captured events.

To enable MLflow logging, install the optional dependency and re-run the
example:

```bash
uv pip install mlflow
uv run python examples/mlflow_metrics/flow.py
```

A local `mlruns/` folder will contain the recorded metrics and tags for each
PenguiFlow event.
