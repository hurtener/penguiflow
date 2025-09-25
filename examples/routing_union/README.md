# Discriminated Union Router

Demonstrates routing based on a Pydantic discriminated union. `union_router` validates
incoming payloads against the supplied union model and forwards each variant to the node
whose `name` matches the discriminant (`kind`).

## Concepts covered

- Using `Annotated[..., Field(discriminator="kind")]` to describe union payloads.
- Keeping type safety on both sides of the router without manual `isinstance` checks.
- Emitting native Pydantic models instead of generic dictionaries.

## Run it

```bash
uv run python examples/routing_union/flow.py
```

You should see:

```
web::penguins
sql::metrics
```

Try adding another variant (e.g., `SearchApi`) and a matching node to see how the router
scales to more branches.
