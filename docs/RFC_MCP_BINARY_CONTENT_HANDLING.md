# RFC: MCP Binary Content and Large Output Handling

**Status**: Draft
**Created**: 2025-12-22
**Author**: Claude + Santiago
**Version**: 0.2

---

## Executive Summary

MCP (Model Context Protocol) tools can return large binary content (PDFs, images, files) that overwhelms LLM context windows. This RFC proposes mechanisms to intercept, transform, and route such content appropriately - keeping summaries in LLM context while storing actual binary data as artifacts accessible to downstream tools and the frontend.

---

## 1. Problem Statement

### 1.1 The Incident

When using the Tableau MCP server's `download_workbook` tool, the server returned a base64-encoded PDF directly in the tool response. This caused:

1. **Context overflow**: The base64 string consumed thousands of tokens
2. **Wasted computation**: LLM cannot interpret base64 - it's meaningless text
3. **Poor UX**: The actual PDF wasn't accessible to the user
4. **Potential failures**: Large responses can exceed context limits entirely

### 1.2 Current Flow (Problematic)

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────┐
│ MCP Server  │────▶│  ToolNode   │────▶│ ReactPlanner│────▶│   LLM   │
│ (Tableau)   │     │             │     │ (observation)│    │         │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────┘
      │                                        │
      │ Returns:                               │ Receives:
      │ {                                      │ "Observation: {content:
      │   "content": "JVBERi0xLjQK..."        │   'JVBERi0xLjQK...'
      │   (500KB base64)                       │   (500KB base64)
      │ }                                      │ }"
      │                                        │
      └────────────────────────────────────────┘
                    Problem: Full base64 in LLM context
```

### 1.3 Desired Flow

```
┌─────────────┐     ┌─────────────┐     ┌──────────────┐     ┌─────────┐
│ MCP Server  │────▶│  ToolNode   │────▶│ ReactPlanner │────▶│   LLM   │
│ (Tableau)   │     │ (intercept) │     │ (observation)│     │         │
└─────────────┘     └─────────────┘     └──────────────┘     └─────────┘
      │                   │                    │
      │ Returns:          │ Transforms to:     │ Receives:
      │ {                 │ {                  │ "Observation: Downloaded
      │   "content":      │   "artifact":      │  workbook 'Sales' (2.3MB
      │   "JVBERi..."     │   {"id":"..."},    │  PDF). Artifact: ..."
      │   (500KB)         │   "summary": "..." │
      │ }                 │ }                  │
      │                   │                    │
      │                   ▼                    │
      │           ┌──────────────┐             │
      │           │  Artifact    │             │
      │           │  Store       │◀────────────┘
      │           │ (tool_context│   Frontend can
      │           │  or external)│   download PDF
      │           └──────────────┘
```

---

## 2. Current State Analysis

### 2.1 Penguiflow Native Tools

Native tools in penguiflow already receive a `ToolContext`, but binary artifact handling is not first-class yet.

**Today:**
- `tool_context` can hold arbitrary Python objects, but it is not guaranteed to be JSON-serialisable (and is dropped during trajectory serialisation if it can't be encoded).
- `emit_artifact()` emits JSON-serialisable chunks into the planner event stream (useful for Playground/UI), but it is not a byte store.

**Target (this RFC):** introduce a dedicated `ArtifactStore` (bytes + large text) surfaced to *all* tools via `ToolContext` (e.g. `ctx.artifacts`), returning a small, JSON-safe `ArtifactRef` that can be included in observations.

```python
# Target tool shape once ArtifactStore is available
async def download_report(query: DownloadQuery, ctx: ToolContext) -> DownloadResult:
    pdf_bytes = await fetch_pdf(query.report_id)

    artifact = await ctx.artifacts.put_bytes(
        pdf_bytes,
        mime_type="application/pdf",
        filename="report.pdf",
        meta={"report_id": query.report_id},
    )

    return DownloadResult(
        summary=f"Downloaded report PDF ({artifact.size_bytes} bytes).",
        artifact=artifact,
    )
```

### 2.2 MCP/ToolNode Current Implementation

```python
# penguiflow/tools/node.py

async def call(self, tool_name: str, args: dict, ctx: ToolContext) -> Any:
    # ... auth resolution ...
    result = await self._call_with_retry(original_name, args, auth_headers)
    # Result goes directly to planner - NO transformation
    return {"result": result}

def _serialize_mcp_result(self, result: Any) -> Any:
    # Handles CallToolResult, extracts text/structured content
    # But NO size checks, NO binary detection, NO artifact extraction
    ...
```

**Current gaps:**
- No size limit checks
- No binary content detection
- No artifact extraction
- No per-tool output transformation
- `ToolContext` not used for MCP results

### 2.3 MCP Protocol Content Types

The MCP specification (2025-11-25) defines content blocks that explicitly represent binary payloads *and* out-of-band resource references:

```typescript
type ContentBlock =
  | TextContent
  | ImageContent     // base64 `data`
  | AudioContent     // base64 `data`
  | ResourceLink     // URI pointer (no bytes)
  | EmbeddedResource // inline text or base64 `blob`
```

```typescript
interface ImageContent {
  type: "image";
  data: string;      // base64
  mimeType: string;  // e.g., "image/png"
}

interface AudioContent {
  type: "audio";
  data: string;      // base64
  mimeType: string;  // e.g., "audio/wav"
}

interface ResourceLink {
  type: "resource_link";
  uri: string;
  mimeType?: string;
  size?: number;     // raw bytes, if known
}

interface EmbeddedResource {
  type: "resource";
  resource: {
    uri: string;
    mimeType?: string;
    text?: string;   // For text resources
    blob?: string;   // For binary resources (base64)
  };
}
```

**Observation**: MCP already provides (1) a typed-inline path (`image`/`audio`/`resource` with `blob`) and (2) an out-of-band path (`resource_link` + `resources/read`) with a `size` hint to help Hosts avoid context blowups. A correct client should preserve resource links by default and only materialize bytes when explicitly requested or when policy allows.

### 2.4 MCP Resources (Not Yet Supported)

MCP resources are a first-class, lazy-loading mechanism intended for large data. The relevant RPCs include:

- `resources/list` (paginated)
- `resources/templates/list` (paginated)
- `resources/read` (returns `TextResourceContents` or `BlobResourceContents`)
- `resources/subscribe` / `resources/unsubscribe` and `notifications/resources/updated` (optional)

```typescript
// Client lists resources (paginated)
await client.listResources();

// Client reads a resource by URI
await client.readResource("tableau://workbook/12345/pdf");
// Returns: { contents: [{ uri, mimeType, blob }] }
```

**Benefits of resources:**
- Server can return URI instead of inline content
- Client decides when/if to fetch actual content
- Natural fit for large binary data
- Supports streaming via resource subscriptions

**Current status in penguiflow:** Not implemented. ToolNode only uses `tools/list` and `tools/call`.

---

## 3. Analysis of Options

### 3.1 Option A: Size-Based Auto-Artifact

**Approach**: Automatically convert any output field exceeding a size threshold to an artifact.

```python
class ExternalToolConfig:
    # New fields
    max_inline_size: int = 10_000  # chars before artifact extraction
    auto_artifact_enabled: bool = True
```

**Implementation:**
```python
async def _maybe_extract_artifact(self, result: Any, ctx: ToolContext) -> Any:
    if isinstance(result, str) and len(result) > self.config.max_inline_size:
        artifact = await ctx.artifacts.put_text(
            result,
            mime_type="text/plain",
            filename="tool-output.txt",
            meta={"tool": self.config.name},
        )
        return {
            "artifact": artifact,
            "summary": f"Large text stored as artifact ({len(result)} chars)",
            "preview": result[:200] + "…",
        }
    return result
```

**Pros:**
- Simple, generic solution
- No per-tool configuration needed
- Catches unexpected large outputs

**Cons:**
- May artifact-ify legitimate large text (e.g., long reports)
- No semantic understanding of content type
- Summary is generic, not meaningful

**Decision factors:**
- Good as a safety net
- Should not be the primary mechanism

---

### 3.2 Option B: Binary Pattern Detection

**Approach**: Detect base64-encoded binary content by pattern matching.

```python
BINARY_SIGNATURES = {
    "JVBERi": ("pdf", "application/pdf"),      # PDF
    "iVBORw": ("png", "image/png"),            # PNG
    "/9j/": ("jpeg", "image/jpeg"),            # JPEG
    "UEsDB": ("zip", "application/zip"),       # ZIP/DOCX/XLSX
    "R0lGOD": ("gif", "image/gif"),            # GIF
}

def _detect_binary(self, content: str) -> tuple[str, str] | None:
    """Detect binary content from base64 prefix."""
    for prefix, (ext, mime) in BINARY_SIGNATURES.items():
        if content.startswith(prefix):
            return (ext, mime)
    return None
```

**Pros:**
- Semantic understanding of content type
- Can provide meaningful summaries ("Downloaded PDF document")
- Works across all tools without configuration

**Cons:**
- Only catches known formats
- Relies on content being at field root (not nested)
- Base64 detection can have false positives

**Decision factors:**
- Good for common binary types
- Should be combined with other approaches

---

### 3.3 Option C: MCP Content Type Detection

**Approach**: Leverage MCP's native content types (`ImageContent`, `AudioContent`, `ResourceLink`, `EmbeddedResource`).

```python
async def _transform_mcp_content_blocks(self, result: Any, ctx: ToolContext) -> Any:
    if hasattr(result, "content"):
        for item in result.content:
            if item.type in {"image", "audio"}:
                # Explicit binary payloads: always materialize to ArtifactStore
                artifact = await ctx.artifacts.put_base64(
                    item.data,
                    mime_type=item.mimeType,
                )
                return {"type": item.type, "artifact": artifact}

            if item.type == "resource_link":
                # URI-only pointer: preserve as a link (lazy read via resources/read)
                return {"type": "resource_link", "uri": item.uri, "mimeType": item.mimeType, "size": item.size}

            if item.type == "resource":
                # EmbeddedResource: may include inline text or inline binary (base64 blob)
                if getattr(item.resource, "blob", None):
                    artifact = await ctx.artifacts.put_base64(
                        item.resource.blob,
                        mime_type=getattr(item.resource, "mimeType", None),
                    )
                    return {"type": "resource", "uri": item.resource.uri, "artifact": artifact}
                return {"type": "resource", "uri": item.resource.uri, "text": getattr(item.resource, "text", None)}
    ...
```

**Pros:**
- Protocol-native solution
- Servers explicitly signal binary content
- Clean separation of concerns

**Cons:**
- Depends on server using proper content types
- Many servers just return TextContent with base64 inside
- Tableau server returns structured JSON, not MCP content types

**Decision factors:**
- Should be implemented for spec compliance
- Not sufficient alone (servers don't always use it)

---

### 3.4 Option D: Per-Tool Field Configuration

**Approach**: Explicitly configure which tools have binary output fields.

```python
class ArtifactFieldConfig(BaseModel):
    field_path: str  # JSONPath or dot notation: "content" or "result.pdf_data"
    content_type: str  # "pdf", "image", "binary"
    mime_type: str | None = None
    summary_template: str = "Downloaded {content_type} ({size} bytes)"

class ExternalToolConfig:
    artifact_fields: dict[str, list[ArtifactFieldConfig]] = {
        "download_workbook": [
            ArtifactFieldConfig(
                field_path="content",
                content_type="pdf",
                mime_type="application/pdf",
                summary_template="Downloaded workbook '{name}' as PDF",
            )
        ],
        "get_view_as_pdf": [
            ArtifactFieldConfig(
                field_path="pdf_data",
                content_type="pdf",
            )
        ],
    }
```

**Pros:**
- Precise control per tool
- Meaningful summaries with templates
- Handles nested fields
- No false positives

**Cons:**
- Requires upfront configuration
- Doesn't handle unknown tools
- Configuration maintenance burden

**Decision factors:**
- Best for known tools with known schemas
- Should be combined with auto-detection fallback

---

### 3.5 Option E: Implement MCP Resources

**Approach**: Add full MCP resources support to ToolNode (list, templates, read, subscribe) and integrate with `ArtifactStore`.

```python
class ToolNode:
    async def connect(self):
        # Existing tool discovery
        mcp_tools = await self._mcp_client.list_tools()

        # NEW: Resource discovery (best-effort; server may not support)
        self._resources = await self._mcp_client.list_resources()
        self._resource_templates = await self._mcp_client.list_resource_templates()

    async def read_resource(self, uri: str, ctx: ToolContext) -> Any:
        """Fetch resource contents by URI, returning inline text or ArtifactRef(s)."""
        result = await self._mcp_client.read_resource(uri)
        # If contents include a blob -> store bytes to ArtifactStore and return ArtifactRef
        # If contents include large text -> store text to ArtifactStore and return ArtifactRef
        # Otherwise return text inline
        return self._convert_resource_contents(result, ctx)

    async def subscribe_resource(self, uri: str) -> None:
        """Subscribe to resource updates (notifications/resources/updated)."""
        await self._mcp_client.subscribe(uri)
```

**Usage flow:**
1. Tool returns `{"resource_uri": "tableau://workbook/123/pdf"}`
2. LLM sees: "Workbook available at resource_uri"
3. Frontend/downstream tool calls `read_resource(uri)` to fetch actual content

**Pros:**
- Protocol-native solution
- Lazy loading - content only fetched when needed
- Supports streaming via subscriptions
- Server controls content lifecycle

**Cons:**
- Requires server support (Tableau may not support it)
- More complex implementation
- Changes tool output semantics

**Decision factors:**
- Long-term correct solution
- Requires MCP server cooperation
- Should be implemented for future compatibility

---

### 3.6 Option F: Output Transformation Hooks

**Approach**: Allow custom transformation functions per tool or globally.

```python
class ExternalToolConfig:
    # May be sync or async; ToolNode should await if needed.
    output_transformer: Callable[[str, Any, ToolContext], Any] | None = None

# Usage
async def tableau_transformer(tool_name: str, result: dict, ctx: ToolContext) -> dict:
    if tool_name == "download_workbook" and "content" in result:
        pdf_bytes = base64.b64decode(result["content"])
        artifact = await ctx.artifacts.put_bytes(
            pdf_bytes,
            mime_type="application/pdf",
            filename="workbook.pdf",
            meta={"tool": tool_name},
        )
        return {
            "artifact": artifact,
            "summary": f"Downloaded workbook ({len(pdf_bytes)} bytes)",
            "workbook_name": result.get("name"),
        }
    return result

ExternalToolConfig(
    name="tableau",
    output_transformer=tableau_transformer,
)
```

**Pros:**
- Maximum flexibility
- Handles complex transformations
- User-defined logic

**Cons:**
- Requires code for each integration
- Not declarative
- Testing burden

**Decision factors:**
- Good escape hatch for complex cases
- Should not be primary mechanism

---

## 4. Recommended Approach

### 4.1 Layered Strategy

Implement multiple layers that work together:

```
┌─────────────────────────────────────────────────────────────────┐
│                    Layer 5: Custom Transformer                   │
│                    (User-provided function)                      │
├─────────────────────────────────────────────────────────────────┤
│                    Layer 4: Per-Tool Config                      │
│                    (artifact_fields mapping)                     │
├─────────────────────────────────────────────────────────────────┤
│                    Layer 3: Heuristic Detection                  │
│                    (Base64 + magic-bytes, recursive)             │
├─────────────────────────────────────────────────────────────────┤
│                    Layer 2: MCP Typed Content                    │
│              (image/audio/embedded blob/resource_link)           │
├─────────────────────────────────────────────────────────────────┤
│                    Layer 1: MCP Resources                        │
│           (resource_link + resources/read, lazy by default)      │
├─────────────────────────────────────────────────────────────────┤
│                    Layer 0: Size Safety Net                      │
│              (Auto-artifact if above thresholds)                 │
└─────────────────────────────────────────────────────────────────┘
```

**Processing order:**
1. Apply custom transformer (escape hatch) → if it returns, stop
2. Apply per-tool configured field extraction → convert known fields to `ArtifactRef`
3. Parse MCP typed content blocks:
   - `image`/`audio`/embedded `blob` → store bytes to `ArtifactStore`, return `ArtifactRef`
   - `resource_link` → preserve as link (URI + metadata), lazy-read by default
4. Apply recursive heuristic extraction on structured JSON (base64-likeness + magic bytes)
5. Apply size limits on remaining large text/JSON to avoid LLM context blowups
6. Final guardrail (planner-level): clamp any over-budget observation regardless of tool source

### 4.2 Configuration Schema

```python
class BinaryDetectionConfig(BaseModel):
    """Configuration for automatic binary content detection."""
    enabled: bool = True
    signatures: dict[str, tuple[str, str]] = {
        "JVBERi": ("pdf", "application/pdf"),
        "iVBORw": ("png", "image/png"),
        "/9j/": ("jpeg", "image/jpeg"),
        "UEsDB": ("zip", "application/zip"),
    }
    min_size_for_detection: int = 1000  # Don't check tiny strings
    max_decode_bytes: int = 5_000_000   # Safety cap while probing content
    require_magic_bytes: bool = True    # Avoid false positives on base64-like strings


class ResourceHandlingConfig(BaseModel):
    """Policy for MCP resources/resource_links."""

    enabled: bool = True
    auto_read_if_size_under_bytes: int = 0  # 0 = never auto-read; prefer explicit read
    inline_text_if_under_chars: int = 10_000
    cache_reads_to_artifacts: bool = True


class ArtifactExtractionConfig(BaseModel):
    """Configuration for extracting artifacts from tool outputs."""

    # Size-based safety net
    max_inline_size: int = 10_000
    auto_artifact_large_content: bool = True

    # Binary detection
    binary_detection: BinaryDetectionConfig = BinaryDetectionConfig()

    # MCP resources + links
    resources: ResourceHandlingConfig = ResourceHandlingConfig()

    # Per-tool field configuration
    tool_fields: dict[str, list[ArtifactFieldConfig]] = {}

    # Summary templates
    default_summary_template: str = "Binary content stored as artifact ({size} bytes)"


class ExternalToolConfig(BaseModel):
    # ... existing fields ...

    # NEW: Artifact extraction
    artifact_extraction: ArtifactExtractionConfig = ArtifactExtractionConfig()

    # NEW: Custom transformer (escape hatch). May be sync or async.
    output_transformer: Callable[[str, Any, ToolContext], Any] | None = None
```

### 4.3 Implementation Location

**In `ReactPlanner` + `ToolContext` (applies to all tools):**
- Define `ArtifactRef` + a separate `ArtifactStore` protocol.
- Expose an artifact store handle via `ToolContext` (e.g. `ctx.artifacts`) so *any* tool (native or external) can store bytes/large text out-of-band and return compact refs.
- Accept `artifact_store` in `ReactPlanner`. If not provided, attempt to discover it from `state_store` via duck-typing (same adapter object can implement both).

**In `ToolNode.call()` (intercept external outputs):**
```python
async def call(self, tool_name: str, args: dict, ctx: ToolContext) -> Any:
    # ... existing code ...
    result = await self._call_with_retry(original_name, args, auth_headers)

    # NEW: Transform output before returning
    transformed = await self._transform_output(tool_name, result, ctx)
    return {"result": transformed}

async def _transform_output(
    self,
    tool_name: str,
    result: Any,
    ctx: ToolContext,
) -> Any:
    """Apply artifact extraction and output transformation."""

    # Layer 5: Custom transformer
    if self.config.output_transformer:
        transformed = self.config.output_transformer(tool_name, result, ctx)
        if inspect.isawaitable(transformed):
            transformed = await transformed
        return transformed

    # Layer 4: Per-tool config
    if tool_name in self.config.artifact_extraction.tool_fields:
        result = self._extract_configured_fields(tool_name, result, ctx)

    # Layer 2: MCP typed content blocks
    result = await self._transform_mcp_content_blocks(result, ctx)

    # Layer 3: Binary detection (recursive)
    result = self._detect_and_extract_binary(result, ctx)

    # Layer 0: Size safety net
    result = self._apply_size_limits(result, ctx)

    return result
```

---

## 5. Decision Points

### 5.1 Two "Artifact" Concepts (Avoid Confusion)

Penguiflow has multiple artifact-like mechanisms, and they serve different purposes:

1. **Planner artifacts (structured)**: small JSON values collected into `trajectory.artifacts` via `json_schema_extra={"artifact": True}` and redacted from LLM context.
2. **Streaming artifact chunks**: `ctx.emit_artifact()` emits JSON-serialisable chunks into the planner event stream (Playground/UI).
3. **Binary artifacts (this RFC)**: bytes and large text stored out-of-band in an `ArtifactStore`, referenced by a compact `ArtifactRef` included in tool observations.

This RFC is primarily about (3), while remaining compatible with (1) and (2).

---

### 5.2 ArtifactStore: Separate Protocol, Discoverable from StateStore

**Recommendation:**
- Define `ArtifactStore` as a separate protocol.
- `ReactPlanner` accepts `artifact_store: ArtifactStore | None`.
- If not provided, attempt to discover it from `state_store` via duck-typing (same adapter object can implement both).
- Playground provides an in-memory implementation by default.

**Duck-typing options:**
- `state_store.artifact_store -> ArtifactStore` attribute, or
- optional methods directly on `state_store` (e.g. `put_artifact`, `get_artifact`, `delete_artifact`), adapted to `ArtifactStore`.

**Rationale:**
- `StateStore` is optimized for event history and pause/resume payloads.
- Binary artifacts have different durability, size, throughput, and access-control needs (often best served by disk/object storage).
- Duck-typing keeps “single adapter object” deployments possible without forcing every `StateStore` backend to implement blob storage.

---

### 5.3 Artifact ID Generation

**Options:**
1. **UUID**: `str(uuid.uuid4())`
2. **Content hash**: `hashlib.sha256(content).hexdigest()[:16]`
3. **Sequential**: `f"art_{counter}"`
4. **Namespaced**: `f"{namespace}_{uuid4()[:8]}"`

**Recommendation**: Content hash with namespace prefix: `f"{namespace}_{sha256[:12]}"`. Benefits:
- Deduplication (same content = same ID)
- Traceable to source tool
- Short enough for display

---

### 5.4 ArtifactRef Shape (LLM-safe)

The LLM should only ever see compact refs (never base64, never raw bytes):

```python
class ArtifactRef(BaseModel):
    id: str
    mime_type: str | None = None
    size_bytes: int | None = None
    filename: str | None = None
    sha256: str | None = None
    source: dict[str, Any] = {}
```

---

### 5.5 What Goes in the LLM Summary?

**Minimum:**
- Artifact ref (`ArtifactRef.id` at minimum)
- Content type (PDF, image, etc.)
- Size indicator

**Ideal:**
- Meaningful description from tool metadata
- Filename if available
- Action hints ("Use this artifact id to reference the file")

**Template system:**
```python
summary_template = "Downloaded {content_type} '{filename}' ({size_human}). Artifact: {artifact_id}"
```

---

### 5.6 How Does Frontend Access Artifacts?

**Options:**
1. **Separate endpoint**: `/artifacts/{artifact_id}` (Playground, backed by `ArtifactStore`)
2. **Signed URLs**: Generate temporary download URLs (production stores)
3. **Inline metadata only**: Include `ArtifactRef` in observations/metadata (never bytes)
4. **SSE for refs/metadata**: Emit `ArtifactRef` notifications/chunks (never raw bytes)

**Recommendation**:
- Use `/artifacts/{artifact_id}` (or signed URLs) for bytes
- Keep only `ArtifactRef` + summaries in observations/metadata
- Use SSE only for announcing refs/updates, not for delivering blobs

---

### 5.7 MCP Resources: Required (Full Support)

**Definition of “full support” for resources:**
- `resources/list` (paginated)
- `resources/templates/list` (paginated)
- `resources/read` (text/blob contents)
- `resources/subscribe` / `resources/unsubscribe`
- `notifications/resources/updated` handling (invalidate cache + emit a host event)

**Policy defaults:**
- Preserve `resource_link` in tool results (URI + metadata) and lazy-read by default.
- Only call `resources/read` when:
  - the LLM explicitly asks (via a dedicated tool), or
  - the frontend requests preview/download, or
  - `auto_read_if_size_under_bytes` policy allows.
- Never inline binary blobs: always store to `ArtifactStore` and return `ArtifactRef`.
- Inline small text resources only up to `inline_text_if_under_chars`; otherwise store as text artifact.

---

## 6. Implementation Plan

### Phase 0: Protocols and Context Wiring (Foundation)

**Scope:**
- Define `ArtifactRef` and a separate `ArtifactStore` protocol (bytes + large text).
- Expose an artifact store handle via `ToolContext` (e.g. `ctx.artifacts`) so *any* tool (native or external) can store artifacts out-of-band.
- Add `artifact_store` to `ReactPlanner`. If not provided, attempt to discover it from `state_store` via duck-typing.
- Provide an in-memory `ArtifactStore` for Playground (session/trace scoped) and a no-op fallback for library users who don't configure one.

**Files to modify/add (expected):**
- `penguiflow/planner/context.py`
- `penguiflow/planner/react.py`
- new `penguiflow/artifacts.py`
- `penguiflow/cli/playground_state.py` (Playground storage)
- `docs/tools/statestore-guide.md` (document optional artifact extensions)

**Acceptance criteria:**
- A native tool can `await ctx.artifacts.put_bytes(...)` and safely return an `ArtifactRef`.
- No bytes/base64 are required to be present in `tool_context` or SSE events to make downloads possible.

**Estimated effort:** 1-2 days

### Phase 1: ToolNode Output Transformation + Universal Guardrails

**Scope:**
- Implement `_transform_output()` in ToolNode with the updated layered strategy:
  1. Custom transformer (sync/async)
  2. Per-tool configured extraction (`tool_fields`)
  3. MCP typed content blocks (`image`/`audio`/embedded `blob`/`resource_link`)
  4. Recursive heuristic detection (base64-likeness + magic-bytes validation)
  5. Size-based safety net for large text/JSON (store as text artifact)
- Add a planner-level “last resort” observation clamp so *any* tool (not just ToolNode) can't blow up the context window. If the clamp triggers:
  - store content to `ArtifactStore` when possible and replace with `ArtifactRef`, or
  - truncate and emit a clear warning when no store is configured.

**Acceptance criteria:**
- The original Tableau incident becomes impossible: base64 never reaches the LLM unbounded.
- ToolNode returns remain JSON-serialisable and small by default (refs + summaries only).

**Estimated effort:** 2-4 days

### Phase 2: Full MCP Resources Support (Required)

**Scope:**
- Implement resources in ToolNode:
  - `list_resources()` and pagination support
  - `list_resource_templates()` and pagination support
  - `read_resource(uri, ctx)` with conversion:
    - `BlobResourceContents.blob` → `ArtifactRef` (stored to `ArtifactStore`)
    - `TextResourceContents.text` → inline if small; else store as text artifact and return `ArtifactRef`
  - `subscribe_resource(uri)` / `unsubscribe_resource(uri)`
  - handle `notifications/resources/updated`
- Add caching for `read_resource` results (URI → `ArtifactRef`) when `cache_reads_to_artifacts=True`.
- Expose resources to:
  - Planner: generated NodeSpecs (e.g. `{namespace}.resources_list`, `{namespace}.resources_read`, `{namespace}.resources_templates_list`), and/or
  - Playground: REST endpoints for browsing and reading resources.

**Acceptance criteria:**
- Resources can be discovered and fetched end-to-end without inlining blobs into LLM context.
- Resource updates can be observed (subscribe + updated notifications) and reflected in host state.

**Estimated effort:** 3-6 days

### Phase 3: Playground API + UI

**Scope:**
- Add `/artifacts/{artifact_id}` endpoint backed by `ArtifactStore` with strict session/trace scoping.
- Add Playground endpoints for resources browse/read/subscribe (per ToolNode namespace).
- UI: show (a) artifact refs as downloadable items and (b) resource links as clickable entries with on-demand read/preview.

**Estimated effort:** 2-4 days

### Phase 4: Documentation, Presets, and Hardening

**Scope:**
- Document the `ArtifactStore` protocol, recommended backends, and how to co-locate it with `StateStore` via duck-typing.
- Add presets for common servers (e.g. Tableau) with `tool_fields` rules to extract known base64 fields.
- Add negative-path tests: missing store, decode failures, access denied, oversized payloads, servers that return malformed base64.
- Define lifecycle/retention defaults (TTL, max bytes per session/trace) and cleanup hooks.

**Estimated effort:** 2-4 days

---

## 7. Open Questions

1. **Default retention and limits**: what are the default TTL and max-bytes per (session, trace) for the Playground `ArtifactStore`?
2. **Access control contract**: should `ArtifactStore` enforce `tenant_id/user_id/session_id/trace_id` scoping itself, or should the host enforce it at the HTTP layer (or both)?
3. **Resource caching semantics**: how should `resources/updated` invalidation work (e.g., always invalidate, or compare `size`/`lastModified` when available)?
4. **Fallback behavior**: if no `ArtifactStore` is configured, do we hard-error on large/binary outputs or truncate with warnings?

---

## 8. References

- [MCP Schema (TypeScript)](https://github.com/modelcontextprotocol/specification/blob/main/schema/2025-11-25/schema.ts)
- [FastMCP Documentation](https://gofastmcp.com/)
- [OpenAPI 3.1 binary guidance](https://spec.openapis.org/oas/latest.html)
- [GitHub REST: size-gated inline content pattern](https://docs.github.com/en/rest/repos/contents?apiVersion=2022-11-28#get-repository-content)
- [Penguiflow ToolNode Implementation](../penguiflow/tools/node.py)
- [Penguiflow ToolContext Protocol](../penguiflow/planner/context.py)

---

## Appendix A: Binary Signatures Reference

| Signature | Format | MIME Type | Notes |
|-----------|--------|-----------|-------|
| `JVBERi` | PDF | application/pdf | "%PDF-" in base64 |
| `iVBORw` | PNG | image/png | PNG header |
| `/9j/` | JPEG | image/jpeg | JPEG header |
| `R0lGOD` | GIF | image/gif | "GIF8" in base64 |
| `UEsDB` | ZIP | application/zip | Also DOCX, XLSX, PPTX |
| `PK` | ZIP | application/zip | Raw ZIP header |
| `AAAA` | Various | - | Common but ambiguous |

## Appendix B: Example Tableau Tool Outputs

```json
// download_workbook response
{
  "content": "JVBERi0xLjQKJeLjz9MKMyAwIG9iago8PAov...",  // base64 PDF
  "name": "Sales Dashboard",
  "format": "pdf"
}

// get_view_as_pdf response
{
  "pdf_data": "JVBERi0xLjQK...",  // base64 PDF
  "view_name": "Revenue by Region",
  "generated_at": "2025-12-22T10:30:00Z"
}

// list_workbooks response (no binary - should pass through)
{
  "workbooks": [
    {"id": "123", "name": "Sales", "project": "Analytics"},
    {"id": "456", "name": "Marketing", "project": "Analytics"}
  ]
}
```
