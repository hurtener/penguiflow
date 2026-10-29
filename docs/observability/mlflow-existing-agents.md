# Add MLflow tracing to an existing agent

PenguiFlow provides reusable agent and tool tracing in `penguiflow.observability`. Install the optional extra:

```bash
uv add "penguiflow[planner,mlflow]"
```

Configure the tracking server and opt in to LLM-call spans in the agent process:

```bash
export MLFLOW_TRACKING_URI=http://localhost:5000
export PENGUIFLOW_LLM_TRACING=mlflow
```

For Databricks, set `MLFLOW_TRACKING_URI=databricks://<profile>` and configure that profile in the runtime environment. PenguiFlow does not configure credentials or start a tracking server.

Create the shared telemetry object and pass its planner-event callback to `ReactPlanner`:

```python
import logging

from penguiflow.observability import AgentTelemetry
from penguiflow.planner import ReactPlanner

telemetry = AgentTelemetry(
    flow_name="my-agent",
    logger=logging.getLogger("my-agent"),
    mlflow_enabled=True,
)
planner = ReactPlanner(
    ...,
    event_callback=telemetry.record_planner_event,
)
```

Wrap each run or resume operation in `trace_agent_run`, then record the final response before leaving the span:

```python
with telemetry.trace_agent_run(
    operation="execute",
    trace_id=trace_id,
    inputs={"query": query},
):
    result = await planner.run(query=query)
    if isinstance(result, PlannerFinish):
        telemetry.record_agent_output({"answer": extract_answer(result.payload)})
    elif isinstance(result, PlannerPause):
        telemetry.record_agent_output({"pause": str(result.reason)})
```

The same callback captures tool-call spans, with tool arguments and results as span inputs and outputs. The native and LiteLLM clients use the same synchronous `LLMTraceSink` hook; reported token counts are attached as `mlflow.chat.tokenUsage`. The integration does not enable LiteLLM autologging and does not capture LLM prompts or completions. A custom LLM client must use a PenguiFlow trace sink itself to emit LLM-call spans.

Treat agent inputs, outputs, tool arguments, and tool results as sensitive data. Review retention and access controls on the tracking server before enabling tracing in a shared or production environment.
