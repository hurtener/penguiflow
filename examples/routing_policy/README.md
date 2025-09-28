# Routing by Policy

This example shows how to drive router decisions using a configuration policy. A
`DictRoutingPolicy` reads `policy.json` and routes customer requests to either a
marketing or support queue. Updating the JSON mapping immediately changes the
behavior without altering the code.

## Run It

```bash
uv run python examples/routing_policy/flow.py
```

Sample output:

```
marketing handled launch campaign
support handled reset password
support handled premium issue
```

The last line shows how the updated mapping rewires the `vip` tenant to the
support queue at runtime.
