# MCP Resources Guide

This guide covers MCP resources support in PenguiFlow, including resource listing, reading, caching, and generated tools.

## Overview

MCP (Model Context Protocol) resources provide a way for servers to expose data that can be read by clients. Unlike tools (which execute actions), resources represent static or semi-static content like files, database records, or configuration data.

PenguiFlow's `ToolNode` automatically discovers MCP resources and generates convenient tools for interacting with them.

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│ MCP Server  │────>│ ToolNode     │────>│ Generated    │
│ (resources) │     │ (discovery)  │     │ Tools        │
└─────────────┘     └──────────────┘     └──────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │ ResourceCache│ (caches reads)
                    └──────────────┘
```

## Quick Start

Resources are automatically discovered when you connect a ToolNode:

```python
from penguiflow.tools import ToolNode, ExternalToolConfig, TransportType

config = ExternalToolConfig(
    name="filesystem",
    transport=TransportType.MCP,
    connection="npx -y @modelcontextprotocol/server-filesystem /data",
)

node = ToolNode(config=config, registry=registry)
await node.connect()

# Resources are now available
resources = await node.list_resources()
for r in resources:
    print(f"- {r.uri}: {r.name}")
```

## Generated Tools

When ToolNode connects to an MCP server that supports resources, it automatically generates three tools:

### 1. `{namespace}.resources_list`

Lists all available resources from the server.

```python
# LLM can call: filesystem.resources_list
# Returns:
{
    "resources": [
        {
            "uri": "file:///data/config.json",
            "name": "config.json",
            "mime_type": "application/json",
            "size_bytes": 1024
        },
        # ...
    ],
    "count": 42
}
```

### 2. `{namespace}.resources_read`

Reads a resource by URI.

```python
# LLM can call: filesystem.resources_read
# Input: {"uri": "file:///data/config.json"}
# Returns:
{
    "result": {
        "text": "{...config content...}",  # For text resources
        # OR
        "artifact": {  # For binary/large resources
            "id": "filesystem.resource_abc123",
            "mime_type": "application/pdf",
            "size_bytes": 50000
        }
    },
    "uri": "file:///data/config.json"
}
```

### 3. `{namespace}.resources_templates_list`

Lists resource templates (URI patterns with placeholders).

```python
# LLM can call: filesystem.resources_templates_list
# Returns:
{
    "templates": [
        {
            "uri_template": "file:///data/users/{user_id}/profile.json",
            "name": "User Profile",
            "description": "Profile data for a specific user"
        }
    ],
    "count": 5
}
```

## ResourceInfo Model

Resource metadata is represented by `ResourceInfo`:

```python
from penguiflow.tools.resources import ResourceInfo

resource = ResourceInfo(
    uri="file:///data/report.pdf",      # Unique identifier
    name="Q4 Report",                    # Human-readable name
    description="Quarterly sales report",
    mime_type="application/pdf",
    size_bytes=1048576,
    annotations={"author": "Sales Team"},
)
```

## Resource Templates

Templates allow dynamic resource URIs with placeholders:

```python
from penguiflow.tools.resources import ResourceTemplateInfo

template = ResourceTemplateInfo(
    uri_template="db://users/{user_id}/orders/{order_id}",
    name="User Order",
    description="Retrieve a specific order for a user",
    mime_type="application/json",
)

# Expand template to get actual resource URI
uri = "db://users/123/orders/456"
result = await node.read_resource(uri, ctx)
```

## Caching

PenguiFlow caches resource reads to avoid repeated fetches:

### Configuration

```python
from penguiflow.tools.resources import ResourceCacheConfig

cache_config = ResourceCacheConfig(
    enabled=True,                       # Enable caching
    max_entries=1000,                   # Maximum cached entries
    ttl_seconds=3600,                   # Cache TTL (1 hour)
    inline_text_if_under_chars=10_000,  # Inline small text
)
```

### How Caching Works

1. **First read**: Fetches from MCP server, stores in cache
2. **Subsequent reads**: Returns cached data immediately
3. **Binary content**: Stored in ArtifactStore, cache stores ArtifactRef
4. **Small text**: Inlined directly in cache entry
5. **Large text**: Stored in ArtifactStore as artifact

```python
from penguiflow.tools.resources import ResourceCache
from penguiflow.artifacts import InMemoryArtifactStore

artifact_store = InMemoryArtifactStore()
cache = ResourceCache(
    artifact_store=artifact_store,
    namespace="filesystem",
    config=ResourceCacheConfig(max_entries=500),
)

# get_or_fetch handles caching automatically
result = await cache.get_or_fetch(
    uri="file:///data/large_file.txt",
    read_fn=mcp_client.resources_read,
    ctx=ctx,
)
```

### Cache Invalidation

The cache invalidates entries when receiving MCP notifications:

```python
# Manual invalidation
cache.invalidate("file:///data/changed.txt")

# Invalidate all entries
count = cache.invalidate_all()

# Check cache size
print(f"Cache has {cache.size} entries")
```

## Resource Links

MCP tools may return `resource_link` content blocks that reference resources:

```python
# Tool output with resource_link
{
    "type": "resource_link",
    "uri": "file:///data/generated_report.pdf"
}
```

PenguiFlow transforms these into artifact stubs:

```python
# Transformed output
{
    "type": "artifact_stub",
    "resource_uri": "file:///data/generated_report.pdf",
    "summary": "Resource link: file:///data/generated_report.pdf",
    "note": "Use MCP resources/read to fetch content"
}
```

### ResourceHandlingConfig

Configure how resource links are processed:

```python
from penguiflow.tools.config import ResourceHandlingConfig

config = ResourceHandlingConfig(
    enabled=True,                        # Enable resource link handling
    auto_read_if_size_under_bytes=50000, # Auto-read small resources (0 = never)
    inline_text_if_under_chars=10_000,   # Inline small text responses
    cache_reads_to_artifacts=True,       # Cache to artifact store
)
```

## Integration with Artifact Extraction

Resources integrate with the artifact extraction pipeline:

```python
from penguiflow.tools import ExternalToolConfig
from penguiflow.tools.config import ArtifactExtractionConfig, ResourceHandlingConfig

config = ExternalToolConfig(
    name="filesystem",
    transport=TransportType.MCP,
    connection="npx -y @modelcontextprotocol/server-filesystem /data",
    artifact_extraction=ArtifactExtractionConfig(
        resources=ResourceHandlingConfig(
            enabled=True,
            auto_read_if_size_under_bytes=100_000,
            inline_text_if_under_chars=5_000,
            cache_reads_to_artifacts=True,
        ),
    ),
)
```

### Using Presets

Presets include sensible resource configurations:

```python
from penguiflow.tools.presets import get_artifact_preset

# Filesystem preset has resource-friendly settings
preset = get_artifact_preset("filesystem")
# auto_read_if_size_under_bytes=100_000
# inline_text_if_under_chars=50_000
# cache_reads_to_artifacts=True
```

## Subscriptions

ToolNode supports resource subscriptions for real-time updates:

```python
from penguiflow.tools.resources import ResourceSubscriptionManager

sub_manager = ResourceSubscriptionManager(namespace="filesystem")

# Subscribe to resource updates
await sub_manager.subscribe(
    uri="file:///data/live_data.json",
    subscribe_fn=mcp_client.resources_subscribe,
    callback=lambda uri: print(f"Resource updated: {uri}"),
)

# Check subscription status
if sub_manager.is_subscribed("file:///data/live_data.json"):
    print("Subscribed!")

# List subscribed URIs
for uri in sub_manager.subscribed_uris:
    print(f"Subscribed to: {uri}")

# Unsubscribe
await sub_manager.unsubscribe(
    uri="file:///data/live_data.json",
    unsubscribe_fn=mcp_client.resources_unsubscribe,
)
```

### Handling Updates

When the MCP server sends `notifications/resources/updated`:

```python
# The subscription manager routes to callbacks
await sub_manager.handle_update("file:///data/live_data.json")

# Combined with cache invalidation
def on_resource_updated(uri: str):
    cache.invalidate(uri)  # Clear cache entry
    # Optionally refetch...

await sub_manager.subscribe(
    uri=uri,
    subscribe_fn=mcp_client.resources_subscribe,
    callback=on_resource_updated,
)
```

## Programmatic Access

Beyond generated tools, you can access resources programmatically:

```python
# List resources
resources = await node.list_resources()

# List templates
templates = await node.list_resource_templates()

# Read a resource
result = await node.read_resource("file:///data/config.json", ctx)

# Check if resources are supported
if node._resources_supported:
    print("Server supports resources!")
```

## Error Handling

Resources may not be available or supported:

```python
# Server doesn't support resources
resources = await node.list_resources()
if not resources:
    print("No resources available (server may not support them)")

# Resource read fails
result = await node.read_resource("invalid://uri", ctx)
if "error" in result:
    print(f"Failed: {result['error']}")

# Cache miss with network error
try:
    result = await cache.get_or_fetch(uri, read_fn, ctx)
except Exception as e:
    print(f"Resource fetch failed: {e}")
```

## Best Practices

### 1. Configure Appropriate Cache Limits

```python
# High-traffic environment
ResourceCacheConfig(
    max_entries=5000,
    ttl_seconds=1800,  # 30 minutes
)

# Low-memory environment
ResourceCacheConfig(
    max_entries=100,
    ttl_seconds=300,  # 5 minutes
)
```

### 2. Use Auto-Read Wisely

```python
# For filesystem access with small files
ResourceHandlingConfig(
    auto_read_if_size_under_bytes=50_000,  # Auto-read < 50KB
)

# For large binary resources
ResourceHandlingConfig(
    auto_read_if_size_under_bytes=0,  # Never auto-read
)
```

### 3. Integrate with ArtifactStore

```python
# Always pair resource cache with artifact store
from penguiflow.artifacts import InMemoryArtifactStore
from penguiflow.tools.resources import ResourceCache

artifact_store = InMemoryArtifactStore()
cache = ResourceCache(
    artifact_store=artifact_store,
    namespace="myserver",
)
```

### 4. Handle Missing Support Gracefully

```python
# Check before using resource features
if node._resources_supported:
    resources = await node.list_resources()
else:
    logger.info("Server doesn't support MCP resources")
```

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ ToolNode                                                     │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────────┐     ┌─────────────────┐                │
│  │ MCP Client      │────>│ Resource        │                │
│  │ (resources/*)   │     │ Discovery       │                │
│  └─────────────────┘     └─────────────────┘                │
│           │                       │                          │
│           ▼                       ▼                          │
│  ┌─────────────────┐     ┌─────────────────┐                │
│  │ ResourceCache   │────>│ Generated Tools │                │
│  │ (with Artifact  │     │ - resources_list│                │
│  │  Store)         │     │ - resources_read│                │
│  └─────────────────┘     │ - templates_list│                │
│           │              └─────────────────┘                │
│           ▼                                                  │
│  ┌─────────────────┐                                        │
│  │ ArtifactStore   │  (binary/large content)                │
│  └─────────────────┘                                        │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## Related Documentation

- [Artifacts Guide](./artifacts-guide.md) - ArtifactStore protocol and implementations
- [Configuration Guide](./configuration-guide.md) - ExternalToolConfig details
- [StateStore Guide](./statestore-guide.md) - State management integration
