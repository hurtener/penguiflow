# RFC: Structured Planner Output with Artifacts

**Status:** Draft
**Author:** Santiago Benvenuto + Claude
**Created:** 2025-12-04
**Target Version:** v2.7 (Breaking Change)

---

## Summary

This RFC proposes a standardized output structure for the ReactPlanner that:
1. Enforces a predictable `FinalPayload` schema for all planner responses
2. Introduces artifact field marking to separate heavy data from LLM context
3. Supports streaming artifacts for real-time frontend updates
4. Provides extensible standard fields for downstream consumption

---

## Motivation

### Current Problems

1. **Unpredictable payload structure**: The planner returns `action.args` or `last_observation` as-is, with no guaranteed schema. Downstream code must guess which keys contain the answer (`answer`, `text`, `result`, etc.).

2. **Type mismatch**: Generated orchestrators expect `FinalAnswer` Pydantic objects, but the planner serializes everything to dicts via `model_dump(mode="json")`.

3. **Context pollution**: Heavy data (charts, CSVs, images) is passed to the LLM, wasting tokens, increasing latency, and risking hallucination.

4. **No separation of concerns**: Answer text and rendering data are mixed, making frontend integration difficult.

### Goals

- **Predictability**: Every planner response has the same structure
- **Efficiency**: Heavy artifacts are excluded from LLM context
- **Flexibility**: Rich data is preserved for downstream/frontend consumption
- **Extensibility**: Standard fields for common use cases (confidence, sources, etc.)
- **Streaming**: Real-time artifact delivery for responsive UIs

---

## Design

### 1. FinalPayload Schema

All planner responses will conform to this structure:

```python
from pydantic import BaseModel, Field
from typing import Any, Literal


class Source(BaseModel):
    """A citation or reference used in the answer."""

    title: str
    url: str | None = None
    snippet: str | None = None
    relevance_score: float | None = None


class SuggestedAction(BaseModel):
    """A follow-up action the user might want to take."""

    action_id: str  # e.g., "export_csv", "schedule_meeting"
    label: str      # Human-readable label
    params: dict[str, Any] = Field(default_factory=dict)


class FinalPayload(BaseModel):
    """Standard structure for all planner final answers.

    This is the contract between the planner and downstream consumers.
    All fields except `raw_answer` are optional but standardized.
    """

    # ─────────────────────────────────────────────────────────────
    # REQUIRED
    # ─────────────────────────────────────────────────────────────

    raw_answer: str = Field(
        description="The human-readable answer text. Always present."
    )

    # ─────────────────────────────────────────────────────────────
    # ARTIFACTS (heavy data for downstream, hidden from LLM)
    # ─────────────────────────────────────────────────────────────

    artifacts: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Heavy data collected from tool outputs. "
            "Keyed by tool name or artifact identifier. "
            "Examples: chart configs, file buffers, large JSON structures."
        )
    )

    # ─────────────────────────────────────────────────────────────
    # CONFIDENCE & QUALITY
    # ─────────────────────────────────────────────────────────────

    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Agent's confidence in the answer (0.0-1.0). "
            "Derived from reflection score or LLM self-assessment. "
            "Use for: UI indicators, human review triggers, routing decisions."
        )
    )

    # ─────────────────────────────────────────────────────────────
    # PROVENANCE & TRANSPARENCY
    # ─────────────────────────────────────────────────────────────

    sources: list[Source] = Field(
        default_factory=list,
        description=(
            "Citations and references used to construct the answer. "
            "Collected from retrieval tools, search results, documents. "
            "Use for: fact-checking, regulatory compliance, user transparency."
        )
    )

    # ─────────────────────────────────────────────────────────────
    # ROUTING & CATEGORIZATION
    # ─────────────────────────────────────────────────────────────

    route: str | None = Field(
        default=None,
        description=(
            "Categorization of the answer type. "
            "Examples: 'knowledge_base', 'calculation', 'generation', "
            "'clarification', 'error', 'partial'. "
            "Use for: frontend rendering decisions, analytics, A/B testing."
        )
    )

    # ─────────────────────────────────────────────────────────────
    # USER EXPERIENCE
    # ─────────────────────────────────────────────────────────────

    suggested_actions: list[SuggestedAction] = Field(
        default_factory=list,
        description=(
            "Recommended follow-up actions for the user. "
            "Use for: UI buttons, guided workflows, proactive suggestions. "
            "Examples: export_csv, schedule_meeting, share_report, dig_deeper."
        )
    )

    requires_followup: bool = Field(
        default=False,
        description=(
            "Whether the answer needs user clarification or input. "
            "Use for: HITL triggers, conversation flow control. "
            "When True, `raw_answer` typically contains a question."
        )
    )

    # ─────────────────────────────────────────────────────────────
    # WARNINGS & DIAGNOSTICS
    # ─────────────────────────────────────────────────────────────

    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Non-fatal issues encountered during execution. "
            "Examples: 'data_stale', 'partial_results', 'rate_limited', "
            "'low_confidence', 'missing_context'. "
            "Use for: debugging, user transparency, monitoring."
        )
    )

    # ─────────────────────────────────────────────────────────────
    # INTERNATIONALIZATION
    # ─────────────────────────────────────────────────────────────

    language: str | None = Field(
        default=None,
        description=(
            "ISO 639-1 language code of the response. "
            "Examples: 'en', 'es', 'zh'. "
            "Use for: i18n, localization routing, TTS voice selection."
        )
    )

    # ─────────────────────────────────────────────────────────────
    # EXTENSIBILITY
    # ─────────────────────────────────────────────────────────────

    extra: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Domain-specific fields not covered by standard schema. "
            "Use sparingly; prefer standard fields when applicable."
        )
    )
```

---

### 2. Artifact Field Marking

Tools mark heavy fields as artifacts using Pydantic's `json_schema_extra`:

```python
from pydantic import BaseModel, Field


class SalesChartResult(BaseModel):
    """Output from generate_sales_chart tool."""

    # Visible to LLM (lightweight)
    summary: str = Field(
        description="Brief description of what the chart shows"
    )
    data_points: int = Field(
        description="Number of data points rendered"
    )

    # Hidden from LLM, passed to frontend (heavyweight)
    chart_options: dict = Field(
        description="Apache ECharts configuration",
        json_schema_extra={"artifact": True}
    )


class PDFReportResult(BaseModel):
    """Output from generate_pdf_report tool."""

    # Visible to LLM
    page_count: int
    title: str

    # Hidden from LLM
    pdf_bytes: bytes = Field(
        json_schema_extra={"artifact": True}
    )
    thumbnail_base64: str = Field(
        json_schema_extra={"artifact": True}
    )
```

#### Artifact Redaction in LLM Context

When building messages for the LLM, artifact fields are replaced with placeholders:

```json
{
  "observation": {
    "summary": "Sales increased 20% YoY with Q4 being strongest",
    "data_points": 12,
    "chart_options": "<artifact:dict size=42KB>"
  }
}
```

The LLM sees the structure exists but not the heavy content.

---

### 3. Streaming Artifacts

For real-time UI updates, artifacts can be streamed:

```python
class StreamingChartResult(BaseModel):
    """Output with streaming artifact support."""

    summary: str

    chart_options: dict = Field(
        json_schema_extra={
            "artifact": True,
            "stream": True,           # Enable streaming
            "stream_id": "chart",     # Identifier for the stream
        }
    )
```

#### Streaming Flow

```
┌─────────────────────────────────────────────────────────────────┐
│ Tool Execution                                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  async def generate_chart(args, ctx) -> StreamingChartResult:   │
│      # Stream chunks as they're generated                       │
│      for chunk in build_chart_incrementally():                  │
│          await ctx.emit_artifact(                               │
│              stream_id="chart",                                 │
│              chunk=chunk,                                       │
│              done=False                                         │
│          )                                                      │
│                                                                 │
│      # Final artifact                                           │
│      await ctx.emit_artifact(                                   │
│          stream_id="chart",                                     │
│          chunk=final_config,                                    │
│          done=True                                              │
│      )                                                          │
│                                                                 │
│      return StreamingChartResult(                               │
│          summary="Chart complete",                              │
│          chart_options=final_config                             │
│      )                                                          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ SSE Events to Frontend                                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  event: artifact_chunk                                          │
│  data: {                                                        │
│    "stream_id": "chart",                                        │
│    "seq": 0,                                                    │
│    "chunk": {...partial config...},                             │
│    "done": false                                                │
│  }                                                              │
│                                                                 │
│  event: artifact_chunk                                          │
│  data: {                                                        │
│    "stream_id": "chart",                                        │
│    "seq": 1,                                                    │
│    "chunk": {...more config...},                                │
│    "done": true                                                 │
│  }                                                              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ Frontend Rendering                                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  // Progressive chart rendering                                 │
│  eventSource.addEventListener("artifact_chunk", (e) => {        │
│    const { stream_id, chunk, done } = JSON.parse(e.data);       │
│    if (stream_id === "chart") {                                 │
│      updateChart(chunk);                                        │
│      if (done) finalizeChart();                                 │
│    }                                                            │
│  });                                                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

### 4. Enforced LLM Schema

The system prompt mandates the `raw_answer` structure:

```python
PLANNER_SYSTEM_PROMPT = """
You are PenguiFlow ReactPlanner, a JSON-only planner.

Follow these rules strictly:

1. Respond with valid JSON matching the PlannerAction schema.

2. Use the provided tools when necessary; never invent new tool names.

3. Keep 'thought' concise and factual.

4. **When the task is complete**, set 'next_node' to null and provide 'args' with this structure:

   {
     "raw_answer": "Your complete, human-readable response to the user's query"
   }

   The 'raw_answer' field is REQUIRED. Write a full answer, not a summary.
   Artifacts from tool outputs are collected automatically - do not include them.

   Optionally include:
   - "confidence": 0.0-1.0 (your confidence in the answer)
   - "route": category string (e.g., "knowledge_base", "calculation", "clarification")
   - "requires_followup": true if you need clarification from the user
   - "warnings": ["string"] for any caveats or limitations

5. For parallel plans, set 'join.inject' to map join args to parallel outputs.

6. Do not emit plain text outside JSON.

Available tools:
{rendered_tools}
"""
```

---

### 5. Artifact Collection in Planner

The planner automatically collects artifacts during execution:

```python
# In ReactPlanner._run_loop()

class ArtifactCollector:
    """Collects artifact fields from tool outputs during execution."""

    def __init__(self):
        self._artifacts: dict[str, dict[str, Any]] = {}

    def collect(
        self,
        node_name: str,
        out_model: type[BaseModel],
        observation: dict[str, Any]
    ) -> None:
        """Extract artifact-marked fields from an observation."""
        tool_artifacts = {}

        for field_name, field_info in out_model.model_fields.items():
            extra = field_info.json_schema_extra or {}
            if extra.get("artifact") and field_name in observation:
                tool_artifacts[field_name] = observation[field_name]

        if tool_artifacts:
            self._artifacts[node_name] = tool_artifacts

    def get_all(self) -> dict[str, Any]:
        """Return all collected artifacts."""
        return self._artifacts.copy()


# Usage in _run_loop:
artifact_collector = ArtifactCollector()

# After each tool execution:
if observation is not None:
    artifact_collector.collect(spec.name, spec.out_model, full_observation)

    # Redact for LLM context
    llm_observation = _redact_artifacts(spec.out_model, full_observation)

# When finishing:
if action.next_node is None:
    args = action.args or {}

    final_payload = FinalPayload(
        raw_answer=args.get("raw_answer", _fallback_answer(last_observation)),
        artifacts=artifact_collector.get_all(),
        confidence=args.get("confidence"),
        route=args.get("route"),
        requires_followup=args.get("requires_followup", False),
        warnings=args.get("warnings", []),
        sources=_collect_sources(trajectory),  # Auto-collected from retrieval tools
    )

    return self._finish(
        trajectory,
        reason="answer_complete",
        payload=final_payload.model_dump(mode="json"),
        ...
    )
```

---

### 6. Source Collection

Sources are automatically collected from tools that return retrieval results:

```python
class SearchResult(BaseModel):
    """Mark as a source-producing result."""

    title: str
    url: str
    snippet: str
    score: float = Field(json_schema_extra={"source_field": "relevance_score"})

    class Config:
        json_schema_extra = {"produces_sources": True}


def _collect_sources(trajectory: Trajectory) -> list[Source]:
    """Extract sources from trajectory steps."""
    sources = []

    for step in trajectory.steps:
        if step.observation is None:
            continue

        spec = get_spec_for_step(step)
        if not spec:
            continue

        model_extra = getattr(spec.out_model, "Config", None)
        if model_extra and getattr(model_extra, "json_schema_extra", {}).get("produces_sources"):
            # Extract source objects from observation
            sources.extend(_extract_sources_from_observation(step.observation, spec.out_model))

    return sources
```

---

## Migration Guide

### For Tool Authors

**Before:**
```python
class MyToolResult(BaseModel):
    answer: str
    data: dict  # Heavy data mixed with answer
```

**After:**
```python
class MyToolResult(BaseModel):
    summary: str  # Lightweight, visible to LLM
    data: dict = Field(json_schema_extra={"artifact": True})  # Heavy, hidden from LLM
```

### For Orchestrator Authors

**Before:**
```python
payload = result.payload
answer = payload.text if isinstance(payload, FinalAnswer) else str(payload)
```

**After:**
```python
payload = result.payload  # Always a FinalPayload dict
answer = payload["raw_answer"]
artifacts = payload["artifacts"]
confidence = payload.get("confidence")
```

### For Frontend Authors

**Before:**
```typescript
// Guessing where the answer is
const answer = response.answer ?? response.text ?? response.payload?.answer;
```

**After:**
```typescript
// Predictable structure
const { raw_answer, artifacts, confidence, sources } = response.payload;

// Render answer
displayAnswer(raw_answer);

// Render artifacts
if (artifacts.generate_chart) {
  renderEChart(artifacts.generate_chart.chart_options);
}

// Show confidence indicator
if (confidence !== null) {
  showConfidenceBadge(confidence);
}
```

---

## Implementation Plan

### Phase 1: Core Models & Redaction ✅ COMPLETED
- [x] Add `FinalPayload`, `Source`, `SuggestedAction` to `penguiflow/planner/models.py`
- [x] Add `_redact_artifacts()` to `penguiflow/planner/llm.py`
- [x] Add `ArtifactCollector` to `penguiflow/planner/react.py`
- [x] Update `_finish()` to construct `FinalPayload`

### Phase 2: Prompt Updates ✅ COMPLETED
- [x] Update `penguiflow/planner/prompts.py` with new system prompt
- [x] Add `raw_answer` requirement to finish instructions
- [x] Update repair messages for new schema

### Phase 3: Source Collection ✅ COMPLETED
- [x] Add `produces_sources` model marker
- [x] Implement `_collect_sources()` in trajectory processing
- [x] Auto-populate `sources` field in `FinalPayload`

### Phase 4: Streaming Artifacts ✅ COMPLETED
- [x] Add `emit_artifact()` to `ToolContext`
- [x] Add `artifact_chunk` event type to `PlannerEvent`
- [x] Update SSE endpoints in playground
- [x] Update Svelte UI for artifact streaming

### Phase 5: Template Updates ✅ COMPLETED
- [x] Update `penguiflow/templates/new/*/orchestrator.py.jinja`
- [x] Update `penguiflow/cli/templates/` for generate command
- [x] Remove `FinalAnswer` checks from orchestrators
- [x] Update tool templates with artifact examples

### Phase 6: Documentation & Testing
- [x] Update `REACT_PLANNER_INTEGRATION_GUIDE.md`
- [x] Update `TEMPLATING_QUICKGUIDE.md`
- [ ] Add artifact examples to `examples/`
- [ ] Add tests for artifact redaction
- [ ] Add tests for source collection
- [ ] Add tests for streaming artifacts

---

## Backward Compatibility

**This is a breaking change.** The payload structure changes from arbitrary dict to `FinalPayload`.

### Migration Path

1. **v2.6.x**: Current behavior (no changes)
2. **v2.7.0**: New structure only.

### Deprecation Warnings

In v2.6.x, add deprecation warnings:
```python
# In orchestrator templates
import warnings
if isinstance(payload, dict) and "raw_answer" not in payload:
    warnings.warn(
        "Unstructured planner payloads are deprecated. "
        "Upgrade to FinalPayload structure in v2.7. "
        "See docs/RFC_STRUCTURED_PLANNER_OUTPUT.md",
        DeprecationWarning
    )
```

---

## Open Questions

1. **Artifact size limits**: Should we enforce max artifact size? Log warnings for >1MB?

2. **Artifact compression**: Should large artifacts be gzip'd in the payload?

3. **Artifact references**: For very large artifacts, should we support references (e.g., S3 URLs) instead of inline data?

4. **Source deduplication**: If multiple tools return the same source, should we deduplicate?

5. **Confidence aggregation**: If reflection provides a score, should it override LLM-provided confidence?

---

## Appendix A: Full Example

### Tool Definition

```python
from pydantic import BaseModel, Field
from penguiflow.catalog import tool
from penguiflow.planner import ToolContext


class SalesAnalysisArgs(BaseModel):
    quarter: str
    year: int
    include_chart: bool = True


class SalesAnalysisResult(BaseModel):
    """Sales analysis with optional chart artifact."""

    # Visible to LLM
    summary: str = Field(description="Key findings from the analysis")
    total_revenue: float = Field(description="Total revenue in USD")
    yoy_growth: float = Field(description="Year-over-year growth percentage")
    top_products: list[str] = Field(description="Top 3 products by revenue")

    # Hidden from LLM, passed to frontend
    chart_options: dict | None = Field(
        default=None,
        json_schema_extra={"artifact": True, "stream": True}
    )
    raw_data: list[dict] = Field(
        default_factory=list,
        json_schema_extra={"artifact": True}
    )


@tool(desc="Analyze sales data and generate visualizations", tags=["analytics"])
async def analyze_sales(args: SalesAnalysisArgs, ctx: ToolContext) -> SalesAnalysisResult:
    # Fetch and analyze data
    data = await fetch_sales_data(args.quarter, args.year)
    analysis = compute_analysis(data)

    # Generate chart if requested
    chart = None
    if args.include_chart:
        chart = generate_echart_config(data)

        # Stream chart for progressive rendering
        await ctx.emit_artifact(
            stream_id="sales_chart",
            chunk=chart,
            done=True
        )

    return SalesAnalysisResult(
        summary=f"Q{args.quarter} {args.year}: Revenue ${analysis.total:,.0f}, {analysis.growth:+.1f}% YoY",
        total_revenue=analysis.total,
        yoy_growth=analysis.growth,
        top_products=analysis.top_3,
        chart_options=chart,
        raw_data=data
    )
```

### LLM Context (Redacted)

```json
{
  "observation": {
    "summary": "Q4 2024: Revenue $1,234,567, +15.2% YoY",
    "total_revenue": 1234567.89,
    "yoy_growth": 15.2,
    "top_products": ["Widget Pro", "Gadget Plus", "Service Bundle"],
    "chart_options": "<artifact:dict stream=sales_chart>",
    "raw_data": "<artifact:list size=847 items>"
  }
}
```

### Final Payload

```json
{
  "raw_answer": "Your Q4 2024 sales analysis shows strong performance with $1.23M in revenue, representing 15.2% year-over-year growth. Top performers were Widget Pro, Gadget Plus, and Service Bundle. The attached chart visualizes the quarterly trend.",
  "artifacts": {
    "analyze_sales": {
      "chart_options": { "...full 40KB ECharts config..." },
      "raw_data": [ "...847 data points..." ]
    }
  },
  "confidence": 0.92,
  "sources": [],
  "route": "analytics",
  "suggested_actions": [
    {"action_id": "export_csv", "label": "Export Raw Data", "params": {"format": "csv"}},
    {"action_id": "compare_quarters", "label": "Compare to Q3", "params": {"quarter": "Q3"}}
  ],
  "requires_followup": false,
  "warnings": [],
  "language": "en",
  "extra": {}
}
```

### Frontend Consumption

```typescript
interface FinalPayload {
  raw_answer: string;
  artifacts: Record<string, Record<string, unknown>>;
  confidence: number | null;
  sources: Source[];
  route: string | null;
  suggested_actions: SuggestedAction[];
  requires_followup: boolean;
  warnings: string[];
  language: string | null;
  extra: Record<string, unknown>;
}

// Usage
const response = await fetch('/chat', { method: 'POST', body: JSON.stringify({ query }) });
const { payload } = await response.json() as { payload: FinalPayload };

// Display answer
chatBubble.textContent = payload.raw_answer;

// Show confidence
if (payload.confidence !== null) {
  confidenceBadge.textContent = `${(payload.confidence * 100).toFixed(0)}%`;
  confidenceBadge.className = payload.confidence > 0.8 ? 'high' : 'medium';
}

// Render chart artifact
const chartArtifact = payload.artifacts.analyze_sales?.chart_options;
if (chartArtifact) {
  const chart = echarts.init(document.getElementById('chart'));
  chart.setOption(chartArtifact);
}

// Show suggested actions
payload.suggested_actions.forEach(action => {
  const button = document.createElement('button');
  button.textContent = action.label;
  button.onclick = () => executeAction(action.action_id, action.params);
  actionsContainer.appendChild(button);
});

// Handle warnings
if (payload.warnings.length > 0) {
  warningBanner.textContent = payload.warnings.join('; ');
  warningBanner.style.display = 'block';
}
```

---

## Appendix B: SSE Event Types

| Event | Payload | Purpose |
|-------|---------|---------|
| `chunk` | `{stream_id, seq, text, done}` | Token streaming for answer |
| `artifact_chunk` | `{stream_id, seq, chunk, done, artifact_type}` | Streaming artifact data |
| `step` | `{node, status, latency_ms, thought}` | Tool execution progress |
| `done` | `{raw_answer, artifacts, confidence, ...}` | Final response |
| `error` | `{error, code, trace_id}` | Execution failure |

---

## References

- [PenguiFlow Best Practices](./PENGUIFLOW_BEST_PRACTICES.md)
- [React Planner Integration Guide](./REACT_PLANNER_INTEGRATION_GUIDE.md)
- [Apache ECharts Documentation](https://echarts.apache.org/)
- [Pydantic Field Customization](https://docs.pydantic.dev/latest/concepts/fields/)
