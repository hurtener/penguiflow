# MLflow tracing

Use MLflow to inspect an agent's LLM calls, tool calls, token usage, and execution
status. Enable tracing through environment variables; your agent code and event
callbacks do not need to change.

## Enable tracing

Add the optional integration to your agent project:

```bash
uv add "penguiflow[mlflow]"
```

If your project already declares other PenguiFlow extras, add `mlflow` to that
list, for example `penguiflow[planner,mlflow]`.

Configure the tracking destination before starting your agent:

```bash
export PENGUIFLOW_LLM_TRACING=mlflow
export MLFLOW_TRACKING_URI=http://localhost:5000
export MLFLOW_EXPERIMENT_NAME=my-agent
```

Replace the tracking URI with your MLflow server's address and configure its
required authentication. For Databricks, use `MLFLOW_TRACKING_URI=databricks`
with your Databricks credentials.

Start your agent with `uv run` as usual, then open the experiment in MLflow and select its
**Traces** tab. Set these variables before the planner or LLM clients are
constructed. If you use `uv run penguiflow dev`, you can put them in your project's
`.env` file instead.

### Set up a tracking server

If you do not have a tracking server, follow the
[MLflow self-hosting guide](https://mlflow.org/docs/latest/self-hosting/),
then set `MLFLOW_TRACKING_URI` to your server's address. The
`penguiflow[mlflow]` extra supplies the client SDK; it does not set up a server.

## What you will see

Each `ReactPlanner.run()` or `resume()` call produces an agent span containing
the tool and LLM spans for that operation. For example:

```text
agent.run
├── llm.complete
├── lookup_customer
└── llm.complete
```

A span records one step; a trace groups the related spans. An agent span can
also nest under an existing MLflow trace in your application.

| Span | Information recorded |
| --- | --- |
| Agent | Operation (`run` or `resume`), planner class, status, and `trace_id` when supplied in `tool_context` |
| Tool | Tool name, call ID, trajectory step, and outcome |
| LLM | Provider, model, latency, cost, and available input/output token counts |

MLflow aggregates token counts across the LLM spans in a trace. Token totals
depend on the usage data available from your LLM client. Parallel tools appear
as separate spans under the agent. A resumed operation gets its own agent span.

Built-in native LLM adapters and the LiteLLM planner client support LLM tracing.
Custom LLM clients need their own instrumentation to contribute LLM spans and
token counts. Standalone built-in LLM clients can also use the same environment
setting without a planner.

## Data captured

This integration records metadata. It does not capture prompts, completions,
tool arguments, or tool results. Exception details may contain provider error
text, so review your provider's error messages before sending traces to a shared
server.

## Disable tracing

Unset `PENGUIFLOW_LLM_TRACING`, or set it to `off`, before starting your agent:

```bash
export PENGUIFLOW_LLM_TRACING=off
```

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| No traces appear | Set the environment variables before constructing the planner. Confirm the tracking server is reachable, authentication is configured, and you are viewing the right experiment. |
| A warning says MLflow is unavailable | Install `penguiflow[mlflow]` in the same Python environment that runs your agent. |
| LLM spans or token counts are missing | Check whether you use a built-in LLM client and whether the provider returns usage data. |
| Duplicate LLM spans or inflated token totals | Disable separate MLflow provider autologging when using PenguiFlow's MLflow tracing. |
