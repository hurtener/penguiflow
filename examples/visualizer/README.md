# Flow visualizer example

This example generates Mermaid and Graphviz outputs for a small controller
loop that routes into a summarizer node.

## Run it

```bash
uv run python examples/visualizer/flow.py
```

The script writes two files next to the source:

- `diagram.md` – Mermaid source suitable for documentation or Markdown preview
- `diagram.dot` – Graphviz DOT definition that can be rendered with `dot -Tpng`

Both outputs highlight:

- the controller loop (self-edge annotated as `loop`)
- the automatic OpenSea/Rookery boundaries (`ingress` and `egress` edges)

The generated Mermaid block is already wrapped in a fenced code block so it
can be copy/pasted directly into docs.
