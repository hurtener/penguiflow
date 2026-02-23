# Visualization

## What it is / when to use it

PenguiFlow can render a flow’s topology as:

- Mermaid (`flow_to_mermaid`) for docs and quick reviews
- Graphviz DOT (`flow_to_dot`) for richer diagram tooling

Use visualization when you want to:

- communicate graph topology in design reviews,
- verify that routing/join edges are wired as expected,
- generate “as-built” diagrams in CI or docs.

## Non-goals / boundaries

- Visualization is topology-only; it does not encode runtime state (queue depth, latency, retries).
- The helpers are best-effort and may not capture every nuance of internal endpoints in custom setups.

## Contract surface

Public helpers:

- `flow_to_mermaid(flow, direction="TD") -> str`
- `flow_to_dot(flow, rankdir="TB") -> str`

They emit a diagram string you can print or write to a file.

## Operational defaults (recommended)

- Use Mermaid for documentation (`direction="LR"` is often easiest to read).
- Use DOT if you want to post-process layout or styling in Graphviz.
- Keep node names stable (`Node(..., name="...")`) so diagrams are meaningful.

## Failure modes & recovery

- **Diagram contains anonymous labels**: your nodes were unnamed.
  - Fix: set `name=` on nodes you expect to show up in diagrams.
- **Graph looks wrong**: you constructed adjacency incorrectly.
  - Fix: generate the diagram from the same `flow` instance you run, and ensure routers are connected to successors explicitly.

## Observability

Visualization complements observability:

- diagram shows “what could happen”
- runtime events (`FlowEvent`) show “what did happen”

See **[Telemetry patterns](../observability/telemetry-patterns.md)**.

## Security / multi-tenancy notes

- Diagrams can reveal internal architecture; treat them as internal artifacts if they include sensitive tool node names or endpoints.

## Runnable example

The repo contains a runnable visualizer example:

```bash
uv run python examples/visualizer/flow.py
```

### Minimal example: print Mermaid

```python
from penguiflow import Node, create, flow_to_mermaid


async def a(msg, _ctx):
    return msg


async def b(msg, _ctx):
    return msg


a_node = Node(a, name="a")
b_node = Node(b, name="b")
flow = create(a_node.to(b_node), b_node.to())

print(flow_to_mermaid(flow, direction="LR"))
```

## Troubleshooting checklist

- If you need to share diagrams in docs, prefer Mermaid and paste into Markdown code fences.
- If you need richer styling, export DOT and render with Graphviz tooling.

