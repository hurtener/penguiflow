# MCP resources

## What it is / when to use it

Many MCP servers expose **resources** (URIs) in addition to tools.

Resources are:

- addressable by URI (`file://...`, `mcp://...`, etc.)
- often read-heavy and cached
- useful for “browse and then act” workflows (list → read → summarize → decide)

ToolNode supports MCP resources by:

- discovering resources and templates on connect,
- generating resource tools for the planner,
- caching reads into artifacts (or inlining small text) via `ResourceCache`,
- handling resource update notifications to invalidate cache/subscriptions.

## Non-goals / boundaries

- Resources are not guaranteed stable; servers can change URIs and contents.
- ToolNode does not automatically “auto-read” all resource links; it converts them into stubs and gives you explicit read tools.
- Subscription semantics are server-dependent; treat them as best-effort.

## Contract surface

### Discovery and generated tools

If an MCP server supports resources, ToolNode generates these tools:

- `{namespace}.resources_list`
- `{namespace}.resources_read`
- `{namespace}.resources_templates_list`

They appear alongside the server’s normal tools in `ToolNode.get_tools()`.

### Programmatic API

ToolNode also exposes programmatic methods:

- `await tool_node.list_resources(refresh=False)`
- `await tool_node.read_resource(uri, ctx, use_cache=True)`
- `await tool_node.list_resource_templates(refresh=False)`
- `await tool_node.subscribe_resource(uri, callback=None)`
- `await tool_node.unsubscribe_resource(uri)`

### Caching and artifacts

`read_resource` uses a `ResourceCache`:

- small text may be returned inline (`{"text": ...}`)
- binary and large text are stored as artifacts (`{"artifact": <ArtifactRef>}`)

The inline threshold defaults to `ExternalToolConfig.artifact_extraction.resources.inline_text_if_under_chars`.

## Operational defaults

- Use a real `ArtifactStore` in production (otherwise resource reads can’t be cached/stored safely).
- Keep caching enabled unless you have strong correctness requirements that demand always-refresh reads.
- Prefer explicit reads over inlining: list resources, then read the specific URI you need.

## Failure modes & recovery

### “Resources not supported”

ToolNode treats resources as best-effort. If the server doesn’t support them:

- `resources_supported` is false
- resource tools are not generated

**Fix**

- use the server’s tools instead, or upgrade to a resources-capable MCP server.

### Resource read returns `{"error": ...}`

**Likely causes**

- ToolNode not connected
- URI invalid/expired
- server error or auth failure

**Fix**

- verify connection/auth
- re-list resources and select a fresh URI

### Cache returns stale content

**Fix**

- call `read_resource(..., use_cache=False)` for a one-off fresh read
- subscribe to resource updates (if supported) so cache invalidation can occur

## Observability

Track at minimum:

- resource tool call latency/error rate (`tool_call_*` events for `{namespace}.resources_*`)
- artifact storage volume and size distribution (`artifact_stored`)
- cache hit rate (application-level; ResourceCache is in-memory)

## Security / multi-tenancy notes

- Treat resource URIs as sensitive if they embed identifiers or paths.
- Ensure artifact retrieval endpoints enforce scope checks (tenant/user/session).
- Don’t allow an LLM to read arbitrary URIs across tenants; use tool visibility/policy if needed.

## Runnable example: list and read resources

```python
from __future__ import annotations

import asyncio

from penguiflow import ModelRegistry
from penguiflow.tools import ExternalToolConfig, ToolNode, TransportType


async def main() -> None:
    registry = ModelRegistry()

    node = ToolNode(
        config=ExternalToolConfig(
            name="filesystem",
            transport=TransportType.MCP,
            connection="npx -y @modelcontextprotocol/server-filesystem /data",
        ),
        registry=registry,
    )
    await node.connect()

    resources = await node.list_resources()
    for r in resources[:5]:
        print(r.uri, r.name)


if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting checklist

- **No resources tools generated**: server may not support resources.
- **Reads return artifacts only**: content is binary/large or above inline threshold.
- **Reads are slow**: you may be bypassing cache; check artifact store performance and network latency.
