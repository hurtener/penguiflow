# RFC: MCP Binary Content and Large Output Handling

**Status**: Draft
**Created**: 2025-12-22
**Author**: Claude + Santiago
**Version**: 0.1

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
      │   "content":      │   "artifact_id":   │  workbook 'Sales' (2.3MB
      │   "JVBERi..."     │   "art_abc123",    │  PDF). Artifact ID:
      │   (500KB)         │   "summary": "..." │  art_abc123"
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

Native tools in penguiflow have full access to `ToolContext`:

```python
async def download_report(query: DownloadQuery, ctx: ToolContext) -> DownloadResult:
    pdf_bytes = await fetch_pdf(query.report_id)

    # Option 1: Store in tool_context (available to other tools)
    ctx.tool_context["last_pdf"] = pdf_bytes

    # Option 2: Emit as artifact (available to frontend)
    artifact_id = await ctx.emit_artifact(
        artifact_type="pdf",
        content=pdf_bytes,
        metadata={"filename": "report.pdf", "size": len(pdf_bytes)},
    )

    # Return summary for LLM
    return DownloadResult(
        summary=f"Downloaded report ({len(pdf_bytes)} bytes)",
        artifact_id=artifact_id,
    )
```

**Key capabilities:**
- `tool_context`: Dict for inter-tool data (not LLM-visible)
- `emit_artifact()`: Stream binary content with metadata
- Return value goes to LLM as observation

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

The MCP specification (2024-11-05) defines several content types:

```typescript
// MCP Content Types
interface TextContent {
  type: "text";
  text: string;
}

interface ImageContent {
  type: "image";
  data: string;      // base64
  mimeType: string;  // e.g., "image/png"
}

interface EmbeddedResource {
  type: "resource";
  resource: {
    uri: string;     // Resource URI
    mimeType?: string;
    text?: string;   // For text resources
    blob?: string;   // For binary resources (base64)
  };
}
```

**Observation**: MCP already has `ImageContent` and `EmbeddedResource` types that signal binary content. We could detect these at the protocol level.

### 2.4 MCP Resources (Not Yet Supported)

MCP has a "resources" capability where servers can expose named resources:

```typescript
// Server advertises resources
{
  "resources": [
    {
      "uri": "tableau://workbook/12345/pdf",
      "name": "Sales Report PDF",
      "mimeType": "application/pdf"
    }
  ]
}

// Client reads resource
await client.readResource("tableau://workbook/12345/pdf");
// Returns: { contents: [{ uri, mimeType, blob }] }
```

**Benefits of resources:**
- Server can return URI instead of inline content
- Client decides when/if to fetch actual content
- Natural fit for large binary data
- Supports streaming via resource subscriptions

**Current status in penguiflow:** Not implemented. ToolNode only uses `list_tools()` and `call_tool()`.

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
def _maybe_extract_artifact(self, result: Any, ctx: ToolContext) -> Any:
    if isinstance(result, str) and len(result) > self.config.max_inline_size:
        artifact_id = ctx.store_artifact(result)
        return {
            "artifact_id": artifact_id,
            "summary": f"Large content stored as artifact ({len(result)} chars)",
            "truncated_preview": result[:200] + "...",
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

**Approach**: Leverage MCP's native content types (`ImageContent`, `EmbeddedResource`).

```python
def _serialize_mcp_result(self, result: Any, ctx: ToolContext) -> Any:
    if hasattr(result, "content"):
        for item in result.content:
            if item.type == "image":
                # ImageContent - always artifact
                artifact_id = ctx.store_artifact(
                    base64.b64decode(item.data),
                    mime_type=item.mimeType,
                )
                return {"artifact_id": artifact_id, "type": "image"}

            if item.type == "resource":
                # EmbeddedResource - check if binary
                if item.resource.blob:
                    artifact_id = ctx.store_artifact(
                        base64.b64decode(item.resource.blob),
                        mime_type=item.resource.mimeType,
                    )
                    return {"artifact_id": artifact_id, "uri": item.resource.uri}
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

**Approach**: Add full MCP resources support to ToolNode.

```python
class ToolNode:
    async def connect(self):
        # Existing tool discovery
        mcp_tools = await self._mcp_client.list_tools()

        # NEW: Resource discovery
        if self._mcp_client.supports_resources:
            mcp_resources = await self._mcp_client.list_resources()
            self._resources = self._convert_resources(mcp_resources)

    async def read_resource(self, uri: str) -> bytes:
        """Fetch resource content by URI."""
        result = await self._mcp_client.read_resource(uri)
        return result.contents[0].blob  # or .text
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
    output_transformer: Callable[[str, dict, ToolContext], dict] | None = None

# Usage
def tableau_transformer(tool_name: str, result: dict, ctx: ToolContext) -> dict:
    if tool_name == "download_workbook" and "content" in result:
        pdf_bytes = base64.b64decode(result["content"])
        artifact_id = ctx.store_artifact(pdf_bytes, mime_type="application/pdf")
        return {
            "artifact_id": artifact_id,
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
│                    Layer 4: Custom Transformer                   │
│                    (User-provided function)                      │
├─────────────────────────────────────────────────────────────────┤
│                    Layer 3: Per-Tool Config                      │
│                    (artifact_fields mapping)                     │
├─────────────────────────────────────────────────────────────────┤
│                    Layer 2: Binary Detection                     │
│                    (Base64 signatures + MCP content types)       │
├─────────────────────────────────────────────────────────────────┤
│                    Layer 1: Size Safety Net                      │
│                    (Auto-artifact if > max_inline_size)          │
└─────────────────────────────────────────────────────────────────┘
```

**Processing order:**
1. Check for custom transformer → if exists, use it
2. Check per-tool artifact_fields config → extract configured fields
3. Detect MCP ImageContent/EmbeddedResource → extract as artifacts
4. Detect base64 binary signatures → extract as artifacts
5. Check size limits → auto-artifact if too large

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


class ArtifactExtractionConfig(BaseModel):
    """Configuration for extracting artifacts from tool outputs."""

    # Size-based safety net
    max_inline_size: int = 10_000
    auto_artifact_large_content: bool = True

    # Binary detection
    binary_detection: BinaryDetectionConfig = BinaryDetectionConfig()

    # Per-tool field configuration
    tool_fields: dict[str, list[ArtifactFieldConfig]] = {}

    # Summary templates
    default_summary_template: str = "Binary content stored as artifact ({size} bytes)"


class ExternalToolConfig(BaseModel):
    # ... existing fields ...

    # NEW: Artifact extraction
    artifact_extraction: ArtifactExtractionConfig = ArtifactExtractionConfig()

    # NEW: Custom transformer (escape hatch)
    output_transformer: Callable[[str, dict, ToolContext], dict] | None = None
```

### 4.3 Implementation Location

**In `ToolNode.call()`:**
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

    # Layer 4: Custom transformer
    if self.config.output_transformer:
        return self.config.output_transformer(tool_name, result, ctx)

    # Layer 3: Per-tool config
    if tool_name in self.config.artifact_extraction.tool_fields:
        result = self._extract_configured_fields(tool_name, result, ctx)

    # Layer 2: Binary detection
    result = self._detect_and_extract_binary(result, ctx)

    # Layer 1: Size safety net
    result = self._apply_size_limits(result, ctx)

    return result
```

---

## 5. Decision Points

### 5.1 Where to Store Artifacts?

**Options:**
1. **ToolContext dict**: `ctx.tool_context["artifacts"][artifact_id] = data`
2. **Dedicated artifact store**: Separate service/storage
3. **Emit via event callback**: `ctx.emit_artifact()` streams to frontend

**Recommendation**: Use existing `ctx.emit_artifact()` mechanism for consistency with native tools. Add `store_artifact()` for synchronous storage if needed.

---

### 5.2 Artifact ID Generation

**Options:**
1. **UUID**: `str(uuid.uuid4())`
2. **Content hash**: `hashlib.sha256(content).hexdigest()[:16]`
3. **Sequential**: `f"art_{counter}"`
4. **Namespaced**: `f"{tool_name}_{uuid4()[:8]}"`

**Recommendation**: Content hash with namespace prefix: `f"{namespace}_{sha256[:12]}"`. Benefits:
- Deduplication (same content = same ID)
- Traceable to source tool
- Short enough for display

---

### 5.3 What Goes in the LLM Summary?

**Minimum:**
- Artifact ID (so LLM can reference it)
- Content type (PDF, image, etc.)
- Size indicator

**Ideal:**
- Meaningful description from tool metadata
- Filename if available
- Action hints ("Use artifact_id to reference this file")

**Template system:**
```python
summary_template = "Downloaded {content_type} '{filename}' ({size_human}). Reference: {artifact_id}"
# Output: "Downloaded PDF 'Sales Report.pdf' (2.3 MB). Reference: tableau_a1b2c3d4"
```

---

### 5.4 How Does Frontend Access Artifacts?

**Options:**
1. **Inline in final response**: Include artifact data in response metadata
2. **Separate endpoint**: `/artifacts/{artifact_id}`
3. **SSE streaming**: Emit artifact chunks via existing event stream
4. **Signed URLs**: Generate temporary download URLs

**Recommendation**:
- Stream via `emit_artifact()` for real-time access
- Store in response metadata for persistence
- Add `/artifacts/{artifact_id}` endpoint to playground for download

---

### 5.5 MCP Resources: Now or Later?

**Arguments for now:**
- Protocol-correct solution
- Future-proofs the implementation
- Some servers already support it

**Arguments for later:**
- Tableau doesn't seem to use resources
- Adds complexity
- Can be added incrementally

**Recommendation**: Implement basic resources support in parallel, but don't block artifact extraction on it. Resources are the "right" long-term solution; artifact extraction is the pragmatic immediate fix.

---

## 6. Implementation Plan

### Phase 1: Core Artifact Extraction (MVP)

**Scope:**
- Add `ArtifactExtractionConfig` to `ExternalToolConfig`
- Implement `_transform_output()` in ToolNode
- Binary signature detection
- Size-based safety net
- Integration with `ToolContext` artifact system

**Files to modify:**
- `penguiflow/tools/config.py` - New config classes
- `penguiflow/tools/node.py` - Output transformation logic
- `penguiflow/planner/context.py` - Ensure `store_artifact()` available

**Estimated effort:** 2-3 days

### Phase 2: Per-Tool Configuration

**Scope:**
- Add `tool_fields` configuration
- Field path extraction (dot notation or JSONPath)
- Summary templates with variable substitution
- Documentation for common MCP servers

**Files to modify:**
- `penguiflow/tools/config.py` - `ArtifactFieldConfig`
- `penguiflow/tools/node.py` - Field extraction logic

**Estimated effort:** 1-2 days

### Phase 3: Frontend Integration

**Scope:**
- Add `/artifacts/{id}` endpoint to playground
- Modify SSE events to include artifact references
- UI component for artifact download/preview

**Files to modify:**
- `penguiflow/cli/playground.py` - New endpoint
- `penguiflow/cli/playground_ui/` - UI components

**Estimated effort:** 2-3 days

### Phase 4: MCP Resources Support

**Scope:**
- Add `list_resources()` to ToolNode
- Add `read_resource()` method
- Expose resources as tool capabilities
- Resource URI resolution

**Files to modify:**
- `penguiflow/tools/node.py` - Resource methods
- New file: `penguiflow/tools/resources.py`

**Estimated effort:** 3-4 days

---

## 7. Open Questions

1. **Should artifact extraction be opt-in or opt-out?**
   - Current recommendation: Opt-out (enabled by default with sensible limits)

2. **How to handle partial failures?**
   - If artifact storage fails, should we fall back to inline content?

3. **Artifact lifecycle management?**
   - When are artifacts cleaned up?
   - Session-scoped? Request-scoped? Explicit deletion?

4. **Cross-tool artifact references?**
   - Can Tool B access artifacts created by Tool A?
   - Via `tool_context` or explicit passing?

5. **Streaming large artifacts?**
   - For very large files (100MB+), should we stream to disk?
   - Memory limits?

---

## 8. References

- [MCP Specification 2024-11-05](https://spec.modelcontextprotocol.io/)
- [FastMCP Documentation](https://gofastmcp.com/)
- [Penguiflow ToolNode Implementation](../penguiflow/tools/node.py)
- [Penguiflow Artifact System](../penguiflow/planner/context.py)

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
