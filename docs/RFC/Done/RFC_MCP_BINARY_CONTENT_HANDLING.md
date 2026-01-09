# RFC: MCP Binary Content and Large Output Handling

**Status**: Draft
**Created**: 2025-12-22
**Author**: Claude + Santiago
**Version**: 0.3

---

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2025-12-22 | Initial draft |
| 0.2 | 2025-12-22 | Added MCP content types, layered strategy |
| 0.3 | 2025-12-23 | Resolved open questions, added planner guardrail (Section 4.4), hybrid ArtifactStore discovery (Section 5.2), revised implementation phases with realistic estimates |

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
- Accept `artifact_store` in `ReactPlanner` with hybrid discovery (see Section 5.2).

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

    # Layer 5: Custom transformer (escape hatch)
    if self.config.output_transformer:
        transformed = self.config.output_transformer(tool_name, result, ctx)
        if inspect.isawaitable(transformed):
            transformed = await transformed
        return transformed

    # Layer 4: Per-tool configured extraction
    if tool_name in self.config.artifact_extraction.tool_fields:
        result = await self._extract_configured_fields(tool_name, result, ctx)

    # Layer 1: MCP resource_link handling (preserve as lazy reference)
    result = await self._handle_resource_links(result, ctx)

    # Layer 2: MCP typed content blocks (image/audio/embedded blob)
    result = await self._transform_mcp_content_blocks(result, ctx)

    # Layer 3: Heuristic binary detection (recursive)
    result = await self._detect_and_extract_binary(result, ctx)

    # Layer 0: Size safety net
    result = await self._apply_size_limits(result, ctx)

    return result


async def _handle_resource_links(
    self,
    result: Any,
    ctx: ToolContext,
) -> Any:
    """
    Handle MCP resource_link content blocks.
    
    Policy options:
    - preserve: Keep as URI reference (default, lazy)
    - auto_read_small: Fetch if size < threshold
    """
    if not hasattr(result, "content"):
        return result
    
    policy = self.config.artifact_extraction.resources
    transformed_content = []
    
    for item in result.content:
        if getattr(item, "type", None) == "resource_link":
            # Extract link metadata
            link_info = {
                "type": "resource_link",
                "uri": item.uri,
                "mime_type": getattr(item, "mimeType", None),
                "size_bytes": getattr(item, "size", None),
            }
            
            # Policy: auto-read small resources
            if (
                policy.enabled
                and policy.auto_read_if_size_under_bytes > 0
                and link_info["size_bytes"] is not None
                and link_info["size_bytes"] < policy.auto_read_if_size_under_bytes
            ):
                try:
                    resource_contents = await self._mcp_client.read_resource(item.uri)
                    artifact_ref = await self._resource_contents_to_artifact(
                        resource_contents, ctx
                    )
                    link_info["artifact"] = artifact_ref
                    link_info["fetched"] = True
                except Exception as e:
                    link_info["fetch_error"] = str(e)
                    link_info["fetched"] = False
            else:
                # Preserve as lazy reference with hint for LLM
                link_info["fetched"] = False
                link_info["hint"] = (
                    f"Resource available at {item.uri}. "
                    f"Use {self.config.name}.resources_read to fetch."
                )
            
            transformed_content.append(link_info)
        else:
            transformed_content.append(item)
    
    if transformed_content:
        return {"content": transformed_content, **_extract_non_content(result)}
    return result
```

---

### 4.4 Planner-Level Observation Guardrail

The planner applies a **final guardrail** after tool execution to prevent any observation (from any tool source) from overflowing the LLM context window.

**Configuration:**

```python
class ObservationGuardrailConfig(BaseModel):
    """Configuration for observation size limits."""
    
    # Character limits
    max_observation_chars: int = 50_000
    max_field_chars: int = 10_000
    
    # Truncation behavior
    truncation_suffix: str = "\n... [truncated: {truncated_chars} chars]"
    preserve_structure: bool = True  # Keep JSON structure, truncate values
    
    # Artifact fallback
    auto_artifact_threshold: int = 20_000  # Store as artifact if above this
```

**Implementation in `ReactPlanner.step()`:**

```python
async def step(self, trajectory: Trajectory) -> StepResult:
    # ... existing action selection and tool execution ...
    
    raw_observation = await self._execute_tool(action, ctx)
    
    # Apply observation guardrail
    clamped_observation, was_clamped = await self._clamp_observation(
        raw_observation,
        ctx,
    )
    
    if was_clamped:
        logger.warning(
            "Observation clamped for tool %s (original: %d chars, clamped: %d chars)",
            action.tool,
            self._estimate_size(raw_observation),
            self._estimate_size(clamped_observation),
        )
        self._emit_event(
            PlannerEvent(
                event_type="observation_clamped",
                ts=self._time_source(),
                trajectory_step=len(trajectory.steps),
                extra={
                    "tool": action.tool,
                    "original_size": self._estimate_size(raw_observation),
                    "clamped_size": self._estimate_size(clamped_observation),
                },
            )
        )
    
    # Use clamped observation for trajectory
    trajectory.steps.append(Step(action=action, observation=clamped_observation))


async def _clamp_observation(
    self,
    observation: Any,
    ctx: ToolContext,
) -> tuple[Any, bool]:
    """
    Clamp observation to prevent context overflow.
    
    Strategy:
    1. If small enough, return as-is
    2. If has artifact store and large, store as artifact
    3. Otherwise, truncate with structure preservation
    """
    size = self._estimate_size(observation)
    
    if size <= self._guardrail.max_observation_chars:
        return observation, False
    
    # Try artifact storage for very large content
    if (
        size > self._guardrail.auto_artifact_threshold
        and hasattr(ctx, "artifacts")
        and not isinstance(ctx.artifacts, NoOpArtifactStore)
    ):
        return await self._observation_to_artifact(observation, ctx), True
    
    # Fallback: truncate with structure preservation
    return self._truncate_observation(observation), True


def _truncate_observation(self, observation: Any) -> Any:
    """Truncate observation while preserving JSON structure."""
    
    if isinstance(observation, str):
        return self._truncate_string(observation)
    
    if isinstance(observation, dict):
        return self._truncate_dict(observation)
    
    if isinstance(observation, list):
        return self._truncate_list(observation)
    
    return self._truncate_string(str(observation))
```

**Two-Level Defense:**

```
┌─────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│ MCP Server  │────▶│ ToolNode            │────▶│ ReactPlanner        │
│             │     │ _transform_output() │     │ _clamp_observation()│
└─────────────┘     └─────────────────────┘     └─────────────────────┘
                            │                            │
                            │ Layer 0-5:                 │ Final guardrail:
                            │ - Binary detection         │ - Size limit
                            │ - MCP content types        │ - Auto-artifact
                            │ - Per-tool config          │ - Truncation
                            │ - Size safety net          │
```

| Layer | Location | Purpose |
|-------|----------|---------|
| ToolNode (L0-L5) | Per-tool, MCP-aware | Handle known patterns, binary content |
| Planner guardrail | Universal | Catch anything that slipped through |

---

## 5. Decision Points

### 5.1 Two "Artifact" Concepts (Avoid Confusion)

Penguiflow has multiple artifact-like mechanisms, and they serve different purposes:

1. **Planner artifacts (structured)**: small JSON values collected into `trajectory.artifacts` via `json_schema_extra={"artifact": True}` and redacted from LLM context.
2. **Streaming artifact chunks**: `ctx.emit_artifact()` emits JSON-serialisable chunks into the planner event stream (Playground/UI).
3. **Binary artifacts (this RFC)**: bytes and large text stored out-of-band in an `ArtifactStore`, referenced by a compact `ArtifactRef` included in tool observations.

This RFC is primarily about (3), while remaining compatible with (1) and (2).

---

### 5.2 ArtifactStore: Hybrid Discovery Mechanism

**Decision:** Use explicit parameter with optional discovery fallback.

**Protocol Definition (`penguiflow/artifacts.py`):**

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class ArtifactStore(Protocol):
    """Protocol for binary/large-text artifact storage."""
    
    async def put_bytes(
        self,
        data: bytes,
        *,
        mime_type: str | None = None,
        filename: str | None = None,
        namespace: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Store binary data, return compact reference."""
        ...
    
    async def put_text(
        self,
        text: str,
        *,
        mime_type: str = "text/plain",
        filename: str | None = None,
        namespace: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Store large text, return compact reference."""
        ...
    
    async def get(self, artifact_id: str) -> bytes | None:
        """Retrieve artifact bytes by ID. Returns None if not found."""
        ...
    
    async def delete(self, artifact_id: str) -> bool:
        """Delete artifact. Returns True if deleted, False if not found."""
        ...
    
    async def exists(self, artifact_id: str) -> bool:
        """Check if artifact exists."""
        ...
```

**Discovery Function:**

```python
def discover_artifact_store(state_store: Any) -> ArtifactStore | None:
    """
    Attempt to discover ArtifactStore from state_store via duck-typing.
    
    Checks for:
    1. state_store.artifact_store attribute (preferred)
    2. state_store implementing ArtifactStore protocol directly
    """
    # Option 1: Explicit attribute
    if hasattr(state_store, "artifact_store"):
        candidate = state_store.artifact_store
        if isinstance(candidate, ArtifactStore):
            return candidate
    
    # Option 2: State store implements ArtifactStore directly
    if isinstance(state_store, ArtifactStore):
        return state_store
    
    return None
```

**ReactPlanner Integration:**

```python
class ReactPlanner:
    def __init__(
        self,
        *,
        state_store: StateStore | None = None,
        artifact_store: ArtifactStore | None = None,  # Explicit param
        # ...
    ):
        self._state_store = state_store
        
        # Resolution order:
        # 1. Explicit parameter (highest priority)
        # 2. Discovered from state_store
        # 3. NoOpArtifactStore fallback (lowest priority)
        if artifact_store is not None:
            self._artifact_store = artifact_store
        elif state_store is not None:
            discovered = discover_artifact_store(state_store)
            if discovered:
                self._artifact_store = discovered
                logger.debug("Discovered ArtifactStore from state_store")
            else:
                self._artifact_store = NoOpArtifactStore()
        else:
            self._artifact_store = NoOpArtifactStore()
```

**Resolution Summary:**

| Scenario | Artifact Store Used | Behavior |
|----------|---------------------|----------|
| `artifact_store=MyStore()` passed | `MyStore` | Full binary storage |
| `state_store` has `.artifact_store` | Discovered store | Full binary storage |
| `state_store` implements `ArtifactStore` | State store directly | Full binary storage |
| Neither provided | `NoOpArtifactStore` | Warnings + truncation |

**Rationale:**
- Explicit parameter gives users full control
- Duck-typing enables "single adapter" deployments without forcing all `StateStore` backends to implement blob storage
- `NoOpArtifactStore` fallback ensures library users aren't blocked if they don't configure artifact storage

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

### Overview

The implementation is split into 5 phases, each independently shippable. Phases 0-2 form the **core functionality**; Phases 3-4 add **polish and hardening**.

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Phase 0: Foundation (2-3 days)                                          │
│ ArtifactStore protocol, ArtifactRef, ToolContext.artifacts, NoOp fallback│
├─────────────────────────────────────────────────────────────────────────┤
│ Phase 1: ToolNode Transform + Planner Guardrail (4-6 days)              │
│ _transform_output() layers, _clamp_observation(), binary detection      │
├─────────────────────────────────────────────────────────────────────────┤
│ Phase 2: MCP Resources (5-8 days)                                       │
│ resources/list, resources/read, subscribe, cache, generated tools       │
├─────────────────────────────────────────────────────────────────────────┤
│ Phase 3: Playground Integration (3-5 days)                              │
│ /artifacts endpoint, session scoping, UI downloads, resource browser    │
├─────────────────────────────────────────────────────────────────────────┤
│ Phase 4: Hardening & Documentation (3-5 days)                           │
│ Presets, negative tests, retention cleanup, docs                        │
└─────────────────────────────────────────────────────────────────────────┘

Total estimated: 17-27 days (3-5 weeks)
```

---

### Phase 0: Foundation — Protocols and Context Wiring

**Goal:** Establish the core abstractions so any tool can store/retrieve binary artifacts.

**Scope:**

1. **New file `penguiflow/artifacts.py`:**
   - `ArtifactRef` model (id, mime_type, size_bytes, filename, sha256, scope, source)
   - `ArtifactScope` model (tenant_id, user_id, session_id, trace_id)
   - `ArtifactStore` protocol (put_bytes, put_text, get, delete, exists)
   - `ArtifactRetentionConfig` model (ttl, size limits, cleanup strategy)
   - `NoOpArtifactStore` implementation (warnings + truncation fallback)
   - `discover_artifact_store()` function for duck-typing from StateStore
   - `InMemoryArtifactStore` implementation (for Playground)

2. **Modify `penguiflow/planner/context.py`:**
   - Add `artifacts: ArtifactStore` property to `ToolContext` protocol

3. **Modify `penguiflow/planner/react.py`:**
   - Add `artifact_store: ArtifactStore | None` parameter to `ReactPlanner.__init__`
   - Implement hybrid discovery (explicit > discovered > NoOp)
   - Wire `ctx.artifacts` in `_PlannerContext`

4. **Modify `penguiflow/cli/playground_state.py`:**
   - Add `PlaygroundArtifactStore` with session-scoped in-memory storage

**Files to modify/add:**
- `penguiflow/artifacts.py` (new)
- `penguiflow/planner/context.py`
- `penguiflow/planner/react.py`
- `penguiflow/cli/playground_state.py`

**Acceptance criteria:**
- `await ctx.artifacts.put_bytes(data)` returns `ArtifactRef` in any tool
- `await ctx.artifacts.get(ref.id)` returns bytes (or None if NoOp)
- NoOpArtifactStore logs warning on first use, returns truncated ref
- Unit tests for all ArtifactStore implementations

**Estimated effort:** 2-3 days

---

### Phase 1: ToolNode Output Transformation + Planner Guardrail

**Goal:** Intercept MCP tool outputs and clamp observations to prevent context overflow.

**Scope:**

1. **ToolNode output transformation (`penguiflow/tools/node.py`):**
   - Add `_transform_output()` with layered strategy:
     - L5: Custom transformer (escape hatch)
     - L4: Per-tool field extraction (`tool_fields` config)
     - L1: Resource link handling (`_handle_resource_links`)
     - L2: MCP typed content (`_transform_mcp_content_blocks`)
     - L3: Heuristic binary detection (`_detect_and_extract_binary`)
     - L0: Size safety net (`_apply_size_limits`)
   - Add `ArtifactExtractionConfig` to `ExternalToolConfig`
   - Add binary signature detection (PDF, PNG, JPEG, ZIP, GIF)

2. **Planner guardrail (`penguiflow/planner/react.py`):**
   - Add `ObservationGuardrailConfig` to `ReactPlanner`
   - Implement `_clamp_observation()` in `step()`
   - Add `_truncate_observation()` with structure preservation
   - Emit `observation_clamped` event when triggered

3. **Configuration models (`penguiflow/tools/config.py` or similar):**
   - `BinaryDetectionConfig` (signatures, min_size, require_magic_bytes)
   - `ResourceHandlingConfig` (auto_read threshold, cache settings)
   - `ArtifactFieldConfig` (field_path, content_type, summary_template)
   - `ObservationGuardrailConfig` (max chars, truncation settings)

**Files to modify/add:**
- `penguiflow/tools/node.py`
- `penguiflow/tools/config.py` (or inline in node.py)
- `penguiflow/planner/react.py`

**Acceptance criteria:**
- Tableau base64 PDF incident is impossible (auto-detected, stored as artifact)
- Tool returning 1MB JSON gets clamped with clear warning
- All layers tested in isolation and integration
- ToolNode returns remain < 50KB by default

**Estimated effort:** 4-6 days

---

### Phase 2: Full MCP Resources Support

**Goal:** Implement MCP resources protocol for lazy loading of large content.

**Scope:**

1. **ToolNode resources methods:**
   - `list_resources()` with pagination
   - `list_resource_templates()` with pagination
   - `read_resource(uri, ctx)` → `ArtifactRef` or inline text
   - `subscribe_resource(uri)` / `unsubscribe_resource(uri)`
   - Handle `notifications/resources/updated`

2. **Resource cache:**
   - `ResourceCache` class (URI → ArtifactRef mapping)
   - Invalidation on `resources/updated` notifications
   - Integration with ArtifactStore for persistence

3. **Generated tools for planner:**
   - `{namespace}.resources_list` → list available resources
   - `{namespace}.resources_read` → fetch resource by URI
   - `{namespace}.resources_templates_list` → list templates
   - Auto-generate NodeSpecs during `ToolNode.connect()`

4. **Error handling:**
   - Resource not found
   - Server doesn't support resources
   - Timeout on large resource reads

**Files to modify/add:**
- `penguiflow/tools/node.py`
- `penguiflow/tools/resources.py` (new, optional separation)

**Acceptance criteria:**
- Resources discoverable via generated tools
- Large binary resources never inline (always ArtifactRef)
- Cache invalidates correctly on server notifications
- Works gracefully when server doesn't support resources

**Estimated effort:** 5-8 days

---

### Phase 3: Playground Integration

**Goal:** Enable artifact downloads and resource browsing in Playground UI.

**Scope:**

1. **REST endpoints (`penguiflow/cli/playground.py`):**
   - `GET /artifacts/{artifact_id}` → binary download
   - `GET /artifacts/{artifact_id}/meta` → ArtifactRef metadata
   - `GET /resources/{namespace}` → list resources for a ToolNode
   - `GET /resources/{namespace}/{uri}` → read resource (with caching)
   - Session-scoped access control

2. **SSE events:**
   - `artifact_stored` event with ArtifactRef
   - `resource_updated` event for cache invalidation
   - No binary data over SSE (refs only)

3. **UI updates (`penguiflow/cli/playground_ui/`):**
   - Render ArtifactRef as download button/link
   - Resource browser panel (list + read on demand)
   - Preview for images, PDF viewer for PDFs
   - Size/type indicators

**Files to modify/add:**
- `penguiflow/cli/playground.py`
- `penguiflow/cli/playground_sse.py`
- `penguiflow/cli/playground_ui/` (various components)

**Acceptance criteria:**
- Artifacts downloadable via UI button
- Resources browseable without leaving Playground
- Session isolation enforced (can't access other sessions' artifacts)
- Works with large files (streaming download)

**Estimated effort:** 3-5 days

---

### Phase 4: Hardening, Documentation, and Presets

**Goal:** Production-ready quality with comprehensive docs and tests.

**Scope:**

1. **Presets for common MCP servers:**
   - Tableau: `tool_fields` for `download_workbook`, `get_view_as_pdf`
   - GitHub: file content extraction
   - Extensible preset registry

2. **Negative-path tests:**
   - Missing ArtifactStore (NoOp fallback behavior)
   - Malformed base64 content
   - Decode failures (corrupted data)
   - Oversized payloads (exceed limits)
   - Access denied (wrong session)
   - MCP server errors during resource read

3. **Retention and cleanup:**
   - TTL expiration hook
   - Size-based eviction (LRU)
   - Cleanup on session end
   - Playground garbage collection

4. **Documentation:**
   - `docs/artifacts-guide.md` — ArtifactStore protocol, implementations, configuration
   - `docs/mcp-resources-guide.md` — resources support, caching, generated tools
   - Update `docs/tools/statestore-guide.md` with artifact extension patterns
   - API reference for new endpoints

**Files to modify/add:**
- `penguiflow/tools/presets/` (new directory)
- `tests/test_artifacts.py` (new)
- `tests/test_toolnode_binary.py` (new)
- `tests/test_mcp_resources.py` (new)
- `docs/artifacts-guide.md` (new)
- `docs/mcp-resources-guide.md` (new)

**Acceptance criteria:**
- ≥85% test coverage for new code
- All negative paths tested
- Presets work out-of-box for Tableau
- Docs reviewed and clear

**Estimated effort:** 3-5 days

---

### Phase Dependencies

```
Phase 0 ─────────────────────────────────────────────────────────▶
         │
         ▼
Phase 1 ─────────────────────────────────────────────────────────▶
         │
         ├──────────────────────┐
         ▼                      ▼
Phase 2 ──────────────▶   Phase 3 ──────────────▶
         │                      │
         └──────────┬───────────┘
                    ▼
              Phase 4 ──────────────▶
```

- Phase 0 is prerequisite for all others
- Phase 1 depends on Phase 0
- Phases 2 and 3 can run in parallel after Phase 1
- Phase 4 depends on Phases 2 and 3

---

### Risk Mitigation

| Risk | Mitigation |
|------|------------|
| MCP servers don't use typed content | Heuristic detection (L3) catches base64 regardless |
| ArtifactStore adds latency | In-memory store for Playground; async writes |
| Large files overwhelm memory | Streaming support in Phase 3; size limits |
| Breaking change for existing tools | All changes additive; NoOp fallback preserves behavior |

---

## 7. Resolved Design Decisions

### 7.1 Default Retention and Limits

**Decision:** Use conservative defaults suitable for Playground development sessions.

```python
class ArtifactRetentionConfig(BaseModel):
    """Retention policy for artifacts."""
    
    # Time-to-live
    ttl_seconds: int = 3600  # 1 hour default
    
    # Size limits
    max_artifact_bytes: int = 50 * 1024 * 1024  # 50MB per artifact
    max_session_bytes: int = 500 * 1024 * 1024  # 500MB per session
    max_trace_bytes: int = 100 * 1024 * 1024    # 100MB per trace
    
    # Count limits
    max_artifacts_per_trace: int = 100
    max_artifacts_per_session: int = 1000
    
    # Cleanup behavior
    cleanup_strategy: Literal["lru", "fifo", "none"] = "lru"
```

**Rationale:**
- 1-hour TTL covers typical Playground dev sessions
- 50MB per artifact handles most PDFs/images
- 500MB session limit prevents runaway storage
- LRU cleanup evicts least-used when limits hit

---

### 7.2 Access Control Contract

**Decision:** Host enforces at HTTP layer; `ArtifactStore` is scope-aware but not enforcing.

```python
class ArtifactRef(BaseModel):
    """Compact reference to stored artifact."""
    
    id: str
    mime_type: str | None = None
    size_bytes: int | None = None
    filename: str | None = None
    sha256: str | None = None
    
    # Scoping metadata (for access control, not enforcement)
    scope: ArtifactScope | None = None


class ArtifactScope(BaseModel):
    """Scoping information for access control."""
    
    tenant_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    trace_id: str | None = None
```

**Access Control Flow:**

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Frontend   │────▶│  HTTP Layer  │────▶│ ArtifactStore│
│              │     │ (enforces    │     │ (stores +    │
│ GET /artifacts/x   │  scope match)│     │  retrieves)  │
└──────────────┘     └──────────────┘     └──────────────┘
                            │
                            ▼
                     Check: request.session_id == artifact.scope.session_id
                     Check: request.tenant_id == artifact.scope.tenant_id
```

**Rationale:**
- Separation of concerns: storage vs access control
- Production deployments can use different enforcement mechanisms (JWT, signed URLs, etc.)
- `ArtifactStore` implementations remain simple and reusable

---

### 7.3 Resource Caching Semantics

**Decision:** Always invalidate on `resources/updated`; no version comparison.

```python
class ResourceCache:
    """Cache for MCP resource reads."""
    
    def __init__(self, artifact_store: ArtifactStore):
        self._store = artifact_store
        self._uri_to_artifact: dict[str, ArtifactRef] = {}
    
    async def get_or_fetch(
        self,
        uri: str,
        mcp_client: Any,
        ctx: ToolContext,
    ) -> ArtifactRef:
        """Get cached artifact or fetch from server."""
        if uri in self._uri_to_artifact:
            ref = self._uri_to_artifact[uri]
            if await self._store.exists(ref.id):
                return ref
            del self._uri_to_artifact[uri]
        
        contents = await mcp_client.read_resource(uri)
        ref = await self._contents_to_artifact(contents, ctx)
        self._uri_to_artifact[uri] = ref
        return ref
    
    def invalidate(self, uri: str) -> None:
        """Invalidate cache entry (called on resources/updated)."""
        if uri in self._uri_to_artifact:
            logger.debug("Invalidating cached resource: %s", uri)
            del self._uri_to_artifact[uri]
```

**Rationale:**
- Always invalidate is simple and correct
- Version comparison adds complexity for minimal gain (resources change infrequently)
- Host can react to invalidation events if needed

---

### 7.4 Fallback Behavior (No ArtifactStore)

**Decision:** Truncate with structured warning; never hard-error.

```python
class NoOpArtifactStore:
    """Fallback when no real store configured."""
    
    def __init__(self, max_inline_preview: int = 500):
        self._max_preview = max_inline_preview
        self._warned = False
    
    async def put_bytes(
        self,
        data: bytes,
        *,
        mime_type: str | None = None,
        filename: str | None = None,
        **kwargs,
    ) -> ArtifactRef:
        if not self._warned:
            logger.warning(
                "No ArtifactStore configured. Binary content will not be stored. "
                "Configure artifact_store= in ReactPlanner for full binary support."
            )
            self._warned = True
        
        return ArtifactRef(
            id=f"truncated_{hashlib.sha256(data).hexdigest()[:12]}",
            mime_type=mime_type,
            size_bytes=len(data),
            filename=filename,
            sha256=hashlib.sha256(data).hexdigest(),
            source={
                "warning": "Content not stored (no ArtifactStore configured)",
                "truncated": True,
                "original_size": len(data),
            },
        )
    
    async def put_text(self, text: str, **kwargs) -> ArtifactRef:
        # Similar implementation with preview
        ...
    
    async def get(self, artifact_id: str) -> bytes | None:
        return None  # Cannot retrieve truncated content
    
    async def exists(self, artifact_id: str) -> bool:
        return False
```

**LLM sees (when no store configured):**
```json
{
  "artifact": {
    "id": "truncated_a1b2c3d4e5f6",
    "mime_type": "application/pdf",
    "size_bytes": 524288,
    "source": {
      "warning": "Content not stored (no ArtifactStore configured)",
      "truncated": true
    }
  },
  "summary": "Downloaded PDF (512KB). Note: Binary content not stored."
}
```

**Rationale:**
- Library users shouldn't be blocked if they don't configure artifact storage
- Clear warnings help users understand the limitation
- Structured metadata allows downstream handling

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
