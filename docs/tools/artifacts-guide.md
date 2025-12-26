# Artifact Storage Guide

This guide covers the ArtifactStore protocol, implementations, and configuration for handling binary and large text content in PenguiFlow.

## Overview

Artifact storage solves a critical problem: LLM context windows are limited and expensive. When MCP servers return binary content (PDFs, images) or large text files, sending raw bytes or base64 data to the LLM wastes tokens and degrades performance.

**The solution:** Store content out-of-band and pass only compact `ArtifactRef` references in LLM context.

```
┌─────────────┐     ┌──────────────┐     ┌───────────────┐
│ Tool Output │────>│ ArtifactStore│────>│ ArtifactRef   │
│ (50KB PDF)  │     │ (stores data)│     │ (100 bytes)   │
└─────────────┘     └──────────────┘     └───────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │ /artifacts   │ (HTTP endpoint for downloads)
                    └──────────────┘
```

## Quick Start

```python
from penguiflow.artifacts import InMemoryArtifactStore, ArtifactRetentionConfig
from penguiflow.planner import ReactPlanner

# Configure retention policy
retention = ArtifactRetentionConfig(
    ttl_seconds=3600,           # 1 hour
    max_artifact_bytes=50_000_000,  # 50MB per artifact
    max_session_bytes=500_000_000,  # 500MB per session
    cleanup_strategy="lru",     # Evict least-recently-used
)

# Create store
artifact_store = InMemoryArtifactStore(retention=retention)

# Use with ReactPlanner
planner = ReactPlanner(
    llm_client=llm,
    artifact_store=artifact_store,
    # ...
)
```

## ArtifactRef Model

When content is stored, you receive an `ArtifactRef` - a compact reference suitable for LLM context:

```python
from penguiflow.artifacts import ArtifactRef

ref = ArtifactRef(
    id="tableau_a1b2c3d4e5f6",      # Unique ID (namespace + content hash)
    mime_type="application/pdf",    # Content type
    size_bytes=1048576,             # 1MB
    filename="report.pdf",          # Suggested download name
    sha256="abc123...",             # Content hash for integrity
    scope=ArtifactScope(            # Access control metadata
        session_id="sess_123",
        user_id="user_456",
    ),
    source={                        # Additional metadata
        "tool": "tableau.download_workbook",
        "workbook_name": "Sales Dashboard",
    },
)
```

The LLM sees only the reference, not the binary data:

```
Tool result: Downloaded workbook 'Sales Dashboard' as PDF (1048576 bytes).
Artifact ID: tableau_a1b2c3d4e5f6
```

## ArtifactStore Protocol

The `ArtifactStore` protocol defines the interface for all implementations:

```python
from penguiflow.artifacts import ArtifactStore, ArtifactRef, ArtifactScope

class ArtifactStore(Protocol):
    async def put_bytes(
        self,
        data: bytes,
        *,
        mime_type: str | None = None,
        filename: str | None = None,
        namespace: str | None = None,
        scope: ArtifactScope | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Store binary data and return a compact reference."""
        ...

    async def put_text(
        self,
        text: str,
        *,
        mime_type: str = "text/plain",
        filename: str | None = None,
        namespace: str | None = None,
        scope: ArtifactScope | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Store large text and return a compact reference."""
        ...

    async def get(self, artifact_id: str) -> bytes | None:
        """Retrieve artifact bytes by ID."""
        ...

    async def get_ref(self, artifact_id: str) -> ArtifactRef | None:
        """Retrieve artifact metadata by ID."""
        ...

    async def delete(self, artifact_id: str) -> bool:
        """Delete an artifact."""
        ...

    async def exists(self, artifact_id: str) -> bool:
        """Check if an artifact exists."""
        ...
```

## Implementations

### InMemoryArtifactStore

For development, testing, and Playground environments:

```python
from penguiflow.artifacts import InMemoryArtifactStore, ArtifactRetentionConfig

store = InMemoryArtifactStore(
    retention=ArtifactRetentionConfig(
        ttl_seconds=3600,
        max_session_bytes=500_000_000,
        cleanup_strategy="lru",
    ),
)

# Store binary content
ref = await store.put_bytes(
    pdf_bytes,
    mime_type="application/pdf",
    filename="report.pdf",
    namespace="tableau",
)

# Retrieve later
data = await store.get(ref.id)
```

**Features:**
- TTL-based expiration
- LRU or FIFO eviction
- Content deduplication (same content = same ID)
- Session/trace size limits

**Limitations:**
- No persistence across restarts
- Single-process only

### NoOpArtifactStore

Fallback when no real store is configured:

```python
from penguiflow.artifacts import NoOpArtifactStore

store = NoOpArtifactStore(max_inline_preview=500)
```

**Behavior:**
- Logs a warning on first use
- Returns truncated references with `warning` in source metadata
- Text content includes a preview
- `get()` always returns `None`

Use this when you want the system to continue without binary storage but want to be alerted.

## Retention Configuration

```python
from penguiflow.artifacts import ArtifactRetentionConfig

config = ArtifactRetentionConfig(
    # Time-to-live
    ttl_seconds=3600,           # Default: 1 hour

    # Size limits
    max_artifact_bytes=50_000_000,   # Default: 50MB per artifact
    max_session_bytes=500_000_000,   # Default: 500MB per session
    max_trace_bytes=100_000_000,     # Default: 100MB per trace

    # Count limits
    max_artifacts_per_trace=100,     # Default: 100
    max_artifacts_per_session=1000,  # Default: 1000

    # Cleanup behavior
    cleanup_strategy="lru",          # "lru", "fifo", or "none"
)
```

### Eviction Strategies

| Strategy | Behavior |
|----------|----------|
| `lru`    | Remove least-recently-accessed artifacts first |
| `fifo`   | Remove oldest artifacts first |
| `none`   | No automatic eviction (will reject new artifacts when full) |

## Access Control with ArtifactScope

`ArtifactScope` provides metadata for access control enforcement:

```python
from penguiflow.artifacts import ArtifactScope

scope = ArtifactScope(
    tenant_id="acme-corp",
    user_id="user_123",
    session_id="sess_456",
    trace_id="trace_789",
)

ref = await store.put_bytes(data, scope=scope)
```

**Important:** The `ArtifactStore` stores scope metadata but doesn't enforce access control. Enforcement happens at the HTTP layer (e.g., Playground `/artifacts` endpoint validates session_id from cookies).

## Integration with ToolNode

ToolNode automatically extracts binary content using `ArtifactExtractionConfig`:

```python
from penguiflow.tools import ExternalToolConfig, TransportType
from penguiflow.tools.config import ArtifactExtractionConfig, ArtifactFieldConfig

config = ExternalToolConfig(
    name="tableau",
    transport=TransportType.MCP,
    connection="http://tableau-mcp:8080/sse",
    artifact_extraction=ArtifactExtractionConfig(
        max_inline_size=5000,
        auto_artifact_large_content=True,
        tool_fields={
            "download_workbook": [
                ArtifactFieldConfig(
                    field_path="content",
                    content_type="pdf",
                    mime_type="application/pdf",
                    summary_template="Downloaded '{name}' ({size} bytes). ID: {artifact_id}",
                ),
            ],
        },
    ),
)
```

See [Configuration Guide](./configuration-guide.md) for more on `ExternalToolConfig`.

## Using Presets

PenguiFlow includes presets for common MCP servers:

```python
from penguiflow.tools.presets import get_artifact_preset, ARTIFACT_PRESETS

# Available presets
print(list(ARTIFACT_PRESETS.keys()))
# ['tableau', 'github', 'filesystem', 'google-drive']

# Get a preset
tableau_config = get_artifact_preset("tableau")

# Use with overrides
from penguiflow.tools.presets import get_artifact_preset_with_overrides

custom = get_artifact_preset_with_overrides(
    "tableau",
    max_inline_size=2000,
)

# Merge preset tool_fields into custom config
from penguiflow.tools.presets import merge_artifact_preset

my_config = ArtifactExtractionConfig(max_inline_size=10000)
merged = merge_artifact_preset(my_config, "github")
```

### Preset Details

**Tableau Preset:**
- `download_workbook` → PDF extraction
- `get_view_as_pdf` → PDF extraction
- `get_view_as_image` → Image extraction
- `export_dashboard` → PDF extraction

**GitHub Preset:**
- `get_file_contents` → Binary file extraction
- `download_artifact` → ZIP extraction
- `get_release_asset` → Binary extraction

**Filesystem Preset:**
- `read_file` → Auto-detect binary files

**Google Drive Preset:**
- `download_file` → Binary extraction
- `export_document` → PDF export

## Discovery Pattern

When integrating with StateStore:

```python
from penguiflow.artifacts import discover_artifact_store

# If state_store has artifact_store attribute
artifact_store = discover_artifact_store(state_store)

if artifact_store is not None:
    ref = await artifact_store.put_bytes(data)
```

The discovery function checks:
1. `state_store.artifact_store` attribute
2. `state_store` implementing `ArtifactStore` protocol directly

## Best Practices

### 1. Configure Appropriate Limits

```python
# Production: generous limits, longer TTL
ArtifactRetentionConfig(
    ttl_seconds=7200,  # 2 hours
    max_session_bytes=1_000_000_000,  # 1GB
)

# Development: tight limits, short TTL
ArtifactRetentionConfig(
    ttl_seconds=600,  # 10 minutes
    max_session_bytes=100_000_000,  # 100MB
)
```

### 2. Use Namespaces

```python
# Namespace helps identify artifact source
ref = await store.put_bytes(data, namespace="tableau")
# ID: tableau_a1b2c3d4e5f6

ref = await store.put_bytes(data, namespace="github")
# ID: github_a1b2c3d4e5f6
```

### 3. Include Source Metadata

```python
ref = await store.put_bytes(
    data,
    meta={
        "tool": "tableau.download_workbook",
        "workbook_name": "Q4 Sales",
        "server": "tableau.acme.com",
    },
)
```

### 4. Handle Missing Store Gracefully

```python
from penguiflow.artifacts import NoOpArtifactStore

# Falls back to NoOpArtifactStore if not configured
store = artifact_store or NoOpArtifactStore()
```

## Implementing Custom Stores

For production with persistent storage:

```python
import boto3
from penguiflow.artifacts import ArtifactStore, ArtifactRef, ArtifactScope

class S3ArtifactStore:
    """S3-backed artifact store for production."""

    def __init__(self, bucket: str, prefix: str = "artifacts/"):
        self._bucket = bucket
        self._prefix = prefix
        self._s3 = boto3.client("s3")

    async def put_bytes(
        self,
        data: bytes,
        *,
        mime_type: str | None = None,
        filename: str | None = None,
        namespace: str | None = None,
        scope: ArtifactScope | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        content_hash = hashlib.sha256(data).hexdigest()[:12]
        artifact_id = f"{namespace or 'art'}_{content_hash}"
        key = f"{self._prefix}{artifact_id}"

        # Store to S3
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=mime_type or "application/octet-stream",
            Metadata={
                "filename": filename or "",
                "scope_session": scope.session_id if scope else "",
            },
        )

        return ArtifactRef(
            id=artifact_id,
            mime_type=mime_type,
            size_bytes=len(data),
            filename=filename,
            sha256=hashlib.sha256(data).hexdigest(),
            scope=scope,
            source=dict(meta or {}),
        )

    async def get(self, artifact_id: str) -> bytes | None:
        try:
            response = self._s3.get_object(
                Bucket=self._bucket,
                Key=f"{self._prefix}{artifact_id}",
            )
            return response["Body"].read()
        except self._s3.exceptions.NoSuchKey:
            return None

    # ... implement remaining protocol methods
```

## Troubleshooting

### "No ArtifactStore configured" Warning

The system is using `NoOpArtifactStore`. Binary content won't be stored.

**Fix:** Configure `artifact_store=` in ReactPlanner:

```python
from penguiflow.artifacts import InMemoryArtifactStore

planner = ReactPlanner(
    artifact_store=InMemoryArtifactStore(),
    # ...
)
```

### Artifact Not Found After Restart

`InMemoryArtifactStore` doesn't persist data. For production, implement a persistent store (S3, Redis, PostgreSQL).

### Large Memory Usage

Check your retention configuration:

```python
# Reduce limits
config = ArtifactRetentionConfig(
    max_session_bytes=100_000_000,  # 100MB instead of 500MB
    ttl_seconds=1800,  # 30 minutes instead of 1 hour
)
```

### Artifact Expired Unexpectedly

Check `ttl_seconds` in your retention config. Artifacts accessed within TTL are kept; unused ones expire.

## Related Documentation

- [Configuration Guide](./configuration-guide.md) - ExternalToolConfig and artifact extraction
- [MCP Resources Guide](./mcp-resources-guide.md) - Resource handling and caching
- [StateStore Guide](./statestore-guide.md) - Integration with state management
