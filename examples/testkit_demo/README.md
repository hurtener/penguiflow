# FlowTestKit demo

This example shows how to execute a PenguiFlow run and inspect its execution
order with the FlowTestKit helpers.

## Running the example

```bash
uv run python examples/testkit_demo/flow.py
```

The script emits a single message into a small flow, prints the returned
`FinalAnswer`, and asserts the sequence of nodes that were executed along the
way.
