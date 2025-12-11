# ToolNode UTCP Echo Example

Minimal example of using `ToolNode` with a UTCP base-url integration. It hits `https://httpbin.org/anything/echo` via UTCP and echoes a JSON payload.

## Run

```bash
uv run python examples/toolnode_utcp_echo/flow.py
```

Requires `penguiflow[planner]` (for `utcp` and `tenacity`) and network access to httpbin.
