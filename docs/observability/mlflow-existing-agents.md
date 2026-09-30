# Add MLflow tracing to an existing agent

Use this guide when you already have a PenguiFlow agent and want to add the MLflow tracing included by `penguiflow new --with-mlflow`.

## Check your agent type

The generated MLflow integration is available for the planner-backed `minimal`, `react`, `parallel`, `rag_server`, `wayfinder`, `analyst`, and `enterprise` templates. The `flow` and `controller` templates do not include this planner tracing scaffold. You can still instrument a custom flow with MLflow's tracing API, but you will need to choose the spans and events that fit your runtime.

`penguiflow apply` does not add or remove MLflow code in an existing project. It applies spec changes, so adding `mlflow: true` to a spec and running `apply` will not retrofit the telemetry implementation.

## Use a generated project as a reference

Generate a temporary project with the same template as your agent. This gives you the current MLflow implementation without overwriting your work:

```bash
uv run penguiflow new mlflow-reference --template react --with-mlflow --output-dir /tmp
```

Replace `react` with your agent's template. Review and merge the generated changes into your project. Do not copy whole files over customized versions.

The migration touches these parts of the generated project:

1. Add `mlflow-tracing>=3.8,<4` to the project dependencies, then run `uv lock` and `uv sync`. If your lockfile pins an older PenguiFlow release, update it to a release that emits planner `llm_usage` events before syncing i.e. version >= 3.12.0.
2. Merge the MLflow support in `telemetry.py` into your existing `AgentTelemetry`. Keep the existing planner-event callback and structured logging.
3. In `orchestrator.py`, wrap each planner execution and resume operation in `trace_agent_run(...)`. Record the final answer or pause result with `record_agent_output(...)` before the span closes.
4. Keep the existing `event_callback=self._telemetry.record_planner_event` wiring in the `ReactPlanner` constructor. The telemetry callback uses planner events to create tool spans and close them when tool results arrive.
5. For token counts, keep the `llm_usage` event handler from the generated telemetry. The built-in planner clients report provider token usage through these events, and the telemetry callback aggregates them into `mlflow.chat.tokenUsage` for each agent run. A custom LLM client must report provider usage for counts to appear.

The core orchestration pattern looks like this. Keep your project's existing result handling inside the context manager:

```python
with self._telemetry.trace_agent_run(
    operation="execute",
    trace_id=trace_id,
    inputs={"query": query},
):
    result = await self._planner.run(
        query=query,
        llm_context=llm_context,
        tool_context=tool_context,
    )
    if isinstance(result, PlannerFinish):
        self._telemetry.record_agent_output({"answer": extract_answer(result.payload)})
```

Apply the same pattern around `ReactPlanner.resume(...)`, using the resume input as the span input. Adapt `extract_answer` to the result type your agent returns.

## Configure the tracking server

Set `MLFLOW_TRACKING_URI` in the environment of the process that runs your agent. For a local MLflow tracking server:

```bash
export MLFLOW_TRACKING_URI=http://localhost:5000
```

For a Databricks tracking server, set it to `databricks://<profile>` and make sure that profile is configured for the runtime environment. Keep credentials in your normal Databricks authentication setup. The agent does not select a profile for you.

The generated `.env.example` uses `http://localhost:5000` as an example. Change it to the tracking server you use. The CLI does not start a tracking server or configure its URL.

## Check the trace

Run the agent with a real LLM provider, then inspect its trace in MLflow. The generated integration records the agent input and output, tool inputs and outputs, and provider token usage when the provider returns usage values.

The default `stub-llm`/scripted path does not report token usage. Missing token counts on that path are expected. A custom LLM client must pass provider-reported input and output token counts into the planner telemetry path for them to appear.

Review the data captured by your traces before enabling this in a shared or production environment. Agent queries and tool arguments or results can contain user data or secrets. Limit access to the tracking server and set retention to match your data policy.

## Keep the integration current

When you upgrade PenguiFlow, generate a fresh reference project with `--with-mlflow` and compare its `telemetry.py`, `orchestrator.py`, LLM client, dependency, and environment-example changes with your agent. Merge the changes into your customized files and run your normal project checks.
