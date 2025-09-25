# Predicate Router

This example shows how to branch work using the `predicate_router` helper. The router
looks at the incoming `Message.payload` and selects the appropriate successor nodes by
name (`metrics` vs `general`).

## Highlights

- `predicate_router(name, predicate)` returns a `Node` that evaluates the predicate for
  every message.
- The predicate can yield a single node, a list of nodes, node names (`str`), or `None`
  to drop the message.
- Downstream nodes receive the original message untouched.

## Run it

```bash
uv run python examples/routing_predicate/flow.py
```

Expected output:

```
[metrics] metric-usage
[general] ad-spend
```

The first message routes to the metrics branch because it starts with `"metric"`. The
second message falls through to the general branch. Try tweaking the predicate to emit
multiple successors (return a list) to fan out to both nodes.
