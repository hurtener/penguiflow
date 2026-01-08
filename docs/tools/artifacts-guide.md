# Artifact Handling Guide

This guide covers how PenguiFlow handles binary content, large text, and UI components through its multi-layer artifact system.

## Overview

Artifact storage solves a critical problem: LLM context windows are limited and expensive. When tools return binary content (PDFs, images) or large text, sending raw bytes or base64 data to the LLM wastes tokens and degrades performance.

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

**Key guarantees:**

1. **LLM context protection**: Raw binary/large content never enters token context
2. **Type safety**: Pydantic models with explicit artifact markers
3. **Scope isolation**: Access control via tenant/user/session scoping
4. **Automatic cleanup**: TTL expiration and LRU eviction

---

## Quick Start

```python
from penguiflow.artifacts import InMemoryArtifactStore, ArtifactRetentionConfig
from penguiflow.planner import ReactPlanner

# Configure retention policy
retention = ArtifactRetentionConfig(
    ttl_seconds=3600,               # 1 hour
    max_artifact_bytes=50_000_000,  # 50MB per artifact
    max_session_bytes=500_000_000,  # 500MB per session
    cleanup_strategy="lru",         # Evict least-recently-used
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

---

## Core Concepts

### ArtifactRef Model

When content is stored, you receive an `ArtifactRef` - a compact reference suitable for LLM context:

```python
from penguiflow.artifacts import ArtifactRef, ArtifactScope

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

### ArtifactStore Protocol

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

### ArtifactScope (Access Control)

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

---

## Declaring Artifact Fields in Tool Output Models

Use `json_schema_extra={"artifact": True}` on Pydantic fields to mark them as artifacts:

```python
from pydantic import BaseModel, Field
from typing import Any

class GatherDataFromGenieResult(BaseModel):
    """Tool output with artifact-marked fields."""

    # Normal fields (IN LLM context)
    status: str = Field(..., description="success | error")
    row_count: int = Field(0, description="Total rows returned")
    data_rows: list[dict[str, Any]] = Field(default_factory=list)

    # Artifact field (REDACTED from LLM context, passed laterally)
    chart_artifacts: dict[str, Any] | None = Field(
        None,
        json_schema_extra={"artifact": True},  # <-- Key marker!
    )
```

**What happens:**
- Normal fields → Included in LLM context
- Artifact fields → Redacted from LLM, collected for lateral passing to UI/frontend

---

## How Artifacts Work Internally

### 1. Redaction

When ReactPlanner processes tool output, it calls `_redact_artifacts()` (from `penguiflow/planner/llm.py`):

```python
def _redact_artifacts(out_model, observation):
    """Replace artifact fields with compact placeholders."""
    redacted = {}
    for field_name, value in observation.items():
        field_info = out_model.model_fields.get(field_name)
        extra = field_info.json_schema_extra or {}
        if extra.get("artifact"):
            # Replace with placeholder
            redacted[field_name] = _artifact_placeholder(value)
        else:
            redacted[field_name] = value
    return redacted

def _artifact_placeholder(value):
    """Generate: <artifact:dict size=5> or <artifact:bytes>"""
    type_name = type(value).__name__
    if hasattr(value, "__len__"):
        return f"<artifact:{type_name} size={len(value)}>"
    return f"<artifact:{type_name}>"
```

**Result:** The LLM sees `{"chart_artifacts": "<artifact:dict size=5>"}` instead of the full payload.

### 2. Collection

The `_ArtifactCollector` (from `artifact_handling.py`) collects artifact values for lateral passing:

```python
class _ArtifactCollector:
    def collect(self, node_name, out_model, observation):
        for field_name, field_info in out_model.model_fields.items():
            extra = field_info.json_schema_extra or {}
            if extra.get("artifact") and field_name in observation:
                self._artifacts[node_name] = {field_name: observation[field_name]}
```

### 3. Accessing Artifacts in Final Response

Artifacts are included in the `FinalPayload`:

```python
@dataclass
class FinalPayload:
    raw_answer: str
    artifacts: dict[str, Any]  # <-- Collected artifacts by node name
    sources: list[dict] | None = None
```

**Example access:**

```python
result = await planner.run(query)
charts = result.artifacts.get("gather_data_from_genie", {}).get("chart_artifacts")
```

---

## Storing Binary Content via ToolContext

Tools can store binary/large text via the `ToolContext.artifacts` API:

```python
from penguiflow.planner.context import ToolContext

async def my_tool(ctx: ToolContext, url: str) -> dict:
    """Download PDF and store as artifact."""
    pdf_bytes = await download_pdf(url)

    # Store in artifact store
    ref = await ctx.artifacts.put_bytes(
        pdf_bytes,
        mime_type="application/pdf",
        filename="report.pdf",
        namespace="my_tool",  # Groups artifacts by tool
    )

    # Return reference (compact) instead of raw bytes
    return {
        "status": "success",
        "artifact": ref.model_dump(),  # Only ArtifactRef in context
        "summary": f"Downloaded PDF ({ref.size_bytes} bytes)",
    }
```

### Event Emission

When artifacts are stored, the `_EventEmittingArtifactStoreProxy` emits an `artifact_stored` event for real-time UI updates:

```python
PlannerEvent(
    event_type="artifact_stored",
    ts=timestamp,
    trajectory_step=current_step,
    extra={
        "artifact_id": ref.id,
        "mime_type": ref.mime_type,
        "size_bytes": size_bytes,
        "artifact_filename": ref.filename,
        "source": {"namespace": "my_tool"},
    },
)
```

---

## Safety Nets (Multi-Layer Protection)

PenguiFlow implements multiple layers of protection to prevent large content from entering LLM context:

### Layer 0: Size Safety Net (`_clamp_observation`)

Final guardrail in `payload_builders.py`:

```python
async def _clamp_observation(observation, config, artifact_store, ...):
    """Apply size guardrails to prevent context overflow."""

    serialized = json.dumps(observation)

    # Fast path: under limit
    if len(serialized) <= config.max_observation_chars:
        return observation, False

    # Auto-artifact large content
    if len(serialized) >= config.auto_artifact_threshold:
        ref = await artifact_store.put_text(
            serialized,
            namespace=f"observation.{spec_name}",
        )
        return {
            "artifact": ref.model_dump(),
            "summary": f"Large observation stored ({len(serialized)} chars)",
            "preview": serialized[:config.preview_length] + "...",
        }, True

    # Fallback: truncation
    return _truncate_observation(observation, config), True
```

### Layer 3: Binary Detection

Heuristic detection of binary content via base64 signatures:

```python
# From config.py
DEFAULT_BINARY_SIGNATURES = {
    "JVBERi": ("pdf", "application/pdf"),      # PDF magic bytes
    "iVBORw": ("png", "image/png"),            # PNG magic bytes
    "/9j/": ("jpg", "image/jpeg"),             # JPEG magic bytes
    "UEsDB": ("zip", "application/zip"),       # ZIP magic bytes
    "R0lGOD": ("gif", "image/gif"),            # GIF magic bytes
}
```

### Layer 4: Field Extraction

Extract specific fields based on configuration (see [ToolNode Integration](#integration-with-toolnode)).

---

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

---

## Retention Configuration

```python
from penguiflow.artifacts import ArtifactRetentionConfig

config = ArtifactRetentionConfig(
    # Time-to-live
    ttl_seconds=3600,                # Default: 1 hour

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

---

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

### ArtifactExtractionConfig Options

```python
class ArtifactExtractionConfig(BaseModel):
    # Size-based safety net
    max_inline_size: int = 10_000        # Auto-artifact above this
    auto_artifact_large_content: bool = True

    # Binary detection
    binary_detection: BinaryDetectionConfig

    # Per-tool field configs
    tool_fields: dict[str, list[ArtifactFieldConfig]] = {}

    # Summary templates
    default_binary_summary: str = "Binary stored ({mime_type}, {size} bytes)"
    default_text_summary: str = "Large text stored ({size} chars)"
```

---

## Artifact Registry (UI Components)

The `ArtifactRegistry` tracks artifacts for UI rendering:

### Registration

```python
from penguiflow.planner.artifact_registry import ArtifactRegistry

registry = ArtifactRegistry()

# Register from tool output model
registry.register_tool_artifacts(
    tool_name="chart_generator",
    out_model=ChartOutput,
    observation={"chart": {"type": "echarts", "config": {...}}},
    step_index=3,
)

# Register binary artifact
registry.register_binary_artifact(
    ref=artifact_ref,
    source_tool="pdf_downloader",
    step_index=5,
)
```

### Resolution for UI

```python
# Resolve artifact reference to renderable component
component = registry.resolve_ref("artifact_0", session_id="sess_123")

# Returns component payload:
# {"component": "echarts", "props": {"option": {...}}}
# {"component": "image", "props": {"src": "/artifacts/img_abc123"}}
# {"component": "embed", "props": {"url": "/artifacts/pdf_xyz789"}}
```

### Supported Component Types

| MIME Type | Component | Props |
|-----------|-----------|-------|
| `image/*` | `image` | `src`, `alt`, `caption` |
| `application/pdf` | `embed` | `url`, `title`, `height` |
| ECharts config | `echarts` | `option` |
| Plotly config | `plotly` | `data`, `layout`, `config` |
| Mermaid diagram | `mermaid` | `code` |
| Other binary | `markdown` | `content` (download link) |

---

## External Nodes (A2A)

The A2A adapter (`penguiflow_a2a/server.py`) handles artifacts at the protocol level.

### No Special Artifact Handling

A2A operates at the **message level**, not the artifact level. Artifacts are:

1. **Handled by ReactPlanner** before reaching A2A
2. **Serialized in the final output** as part of the result payload
3. **Not specially processed** by the A2A HTTP surface

### Artifact Flow in A2A

```
Remote Agent (A2A Client)
        │
        ▼
  A2AServerAdapter.handle_send()
        │
        ▼
  PenguiFlow.emit() → ReactPlanner.run()
        │
        ▼
  [Artifact handling happens here in ReactPlanner]
        │
        ▼
  FinalPayload with artifacts dict
        │
        ▼
  A2AServerAdapter._to_jsonable()
        │
        ▼
  JSON response: {"output": {..., "artifacts": {...}}}
```

### Streaming Artifacts

In SSE streaming mode, artifacts appear in `artifact` events:

```python
# From server.py
yield self._format_event(
    "artifact",
    {
        "taskId": task_id,
        "contextId": context_id,
        "output": self._to_jsonable(payload),  # Includes artifacts
    },
)
```

---

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

| Preset | Tools | Extraction |
|--------|-------|------------|
| **Tableau** | `download_workbook`, `get_view_as_pdf`, `get_view_as_image`, `export_dashboard` | PDF, Image |
| **GitHub** | `get_file_contents`, `download_artifact`, `get_release_asset` | Binary, ZIP |
| **Filesystem** | `read_file` | Auto-detect binary |
| **Google Drive** | `download_file`, `export_document` | Binary, PDF |

---

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

---

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

---

## Implementing Custom Stores

For production with persistent storage:

```python
import hashlib
import boto3
from penguiflow.artifacts import ArtifactRef, ArtifactScope

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

---

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

---

## Summary Table

| Layer | Location | Purpose |
|-------|----------|---------|
| **Field Marker** | `json_schema_extra={"artifact": True}` | Declare artifact fields |
| **Redaction** | `_redact_artifacts()` in `llm.py` | Remove artifacts from LLM context |
| **Collection** | `_ArtifactCollector` | Gather artifacts for lateral passing |
| **Binary Storage** | `ctx.artifacts.put_bytes()` | Store binary out-of-band |
| **Size Guardrail** | `_clamp_observation()` | Final safety net for large observations |
| **Registry** | `ArtifactRegistry` | Track artifacts for UI rendering |
| **Event Emission** | `_EventEmittingArtifactStoreProxy` | Real-time UI updates |
| **A2A** | `A2AServerAdapter` | Pass-through (no special handling) |

---

## Related Documentation

- [Configuration Guide](./configuration-guide.md) - ExternalToolConfig and artifact extraction
- [MCP Resources Guide](./mcp-resources-guide.md) - Resource handling and caching
- [StateStore Guide](./statestore-guide.md) - Integration with state management
