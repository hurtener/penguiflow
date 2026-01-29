# RFC: ReactPlanner Vision (Image Input)

Status: ToDo

## Context

PenguiFlow's `ReactPlanner` is JSON-only: every LLM response must be a valid `PlannerAction` object.

The native LLM layer (`penguiflow.llm`) already supports multimodal *input* via `ImagePart`, and several providers already serialize image parts correctly (OpenAI-compatible, Anthropic, Bedrock).

Today, the planner uses a text-only message contract (`Sequence[Mapping[str, str]]`) and the native adapter (`NativeLLMAdapter`) converts all messages into `TextPart` only.

This RFC proposes a v1 implementation to accept images as *inputs* to the planner and send them to vision-capable models, while keeping the planner's JSON-only action contract unchanged.

## Goals

- Add first-class image input to `ReactPlanner.run()` (and optionally `resume()`) for vision-capable models.
- Use the native LLM layer for multimodal serialization (v1 scope).
- Keep LLM-visible context free of raw bytes/base64: store images as artifacts and reference them compactly.
- Preserve pause/resume compatibility when a real `ArtifactStore` is configured.
- Add capability gating via `ModelProfile.supports_vision` (explicit model capability, not heuristics).

## Non-Goals (v1)

- Image generation output directly from the model.
- Audio/video input.
- Supporting multimodal through LiteLLM (`_LiteLLMJSONClient`) (v1 is native-only).
- Making images implicitly appear from tool outputs (explicit follow-up phase).

## Proposed Public API

Add an optional image input parameter to `ReactPlanner.run()`:

```python
await planner.run(
    query="Describe what's in this image",
    images=[...],
    llm_context={...},
    tool_context={...},
)
```

Recommended signature shape:

```python
async def run(
    self,
    query: str,
    *,
    images: Sequence[VisionInput] | None = None,
    llm_context: Mapping[str, Any] | None = None,
    tool_context: Mapping[str, Any] | None = None,
    ...
) -> PlannerFinish | PlannerPause:
    ...
```

`VisionInput` normalization (v1):

- `penguiflow.llm.types.ImagePart` (bytes + `media_type` + optional `detail`)
- `str | os.PathLike[str]` (local file path; read bytes, infer `media_type`)
- `ArtifactRef | str` (existing artifact reference/id pointing to an image)

Notes:

- Images are attached to the *initial* user message for the run.
- For pause/resume continuity, store only compact references in durable state.

## Capability Gating (v1)

We gate vision based on model profiles (explicit configuration):

- Add `supports_vision: bool = False` to `penguiflow.llm.profiles.ModelProfile`.
- Set `supports_vision=True` in known vision-capable model profiles:
  - OpenAI: `gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo` (and any other known-vision IDs you support)
  - Anthropic: Claude 3+ / 4+ variants that support images
  - Google: Gemini 1.5 / 2.0 variants that support images
  - Bedrock: model IDs that support image input in Converse API
  - OpenRouter: best-effort where the underlying model is known to support vision

Enforcement behavior:

- If `images` is provided and the planner is not using the native adapter (`use_native_llm=False`), raise a clear `ValueError` (v1 native-only constraint).
- If `images` is provided and the selected model profile does not support vision, either:
  - (strict) raise a `ValueError` explaining the model doesn't support vision, or
  - (lenient) drop images with a warning and continue text-only.

Recommendation: strict by default to avoid silent quality regressions.

## Data Model and Storage

### Artifact-first approach

We should not inline base64 or raw bytes into planner observations or durable state.

Proposed approach:

1. Normalize incoming `images` into bytes + `mime_type` + optional metadata.
2. Store each image using `planner._artifact_store.put_bytes(...)` with a namespace like `"input_image"`.
3. Register each stored artifact in `planner._artifact_registry.register_binary_artifact(...)` so UIs can render it.
4. Persist only compact refs in `trajectory.metadata["input_images"]`:
   - list of `{artifact_id, mime_type, filename?, detail?}`.

### Resume behavior

On `resume()`, read `trajectory.metadata["input_images"]` and rehydrate image bytes from `ArtifactStore.get(...)`.

Degraded mode:

- If the configured artifact store cannot retrieve bytes (expired, missing, or `NoOpArtifactStore`), drop images and add a warning into the planner result or trajectory metadata (and optionally instruct the user to reattach).

## Planner Message Construction

We keep the planner JSON-only action contract unchanged.

### Where images appear

Images are injected into the *current* user message that contains the rendered user prompt. Concretely, instead of always emitting:

```json
{"role": "user", "content": "..."}
```

we allow a multi-part `content` payload:

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "..."},
    {"type": "image", "artifact_id": "input_image_deadbeef", "media_type": "image/png", "data": "<bytes>", "detail": "auto"}
  ]
}
```

Implementation detail:

- `build_messages(...)` should read `trajectory.metadata["input_images"]`, fetch bytes via `ArtifactStore.get(...)`, and emit a list of parts.
- `NativeLLMAdapter._convert_messages(...)` must be extended to map this into `LLMMessage(parts=[TextPart(...), ImagePart(...)])`.

### Important: log/telemetry safety

Any debug logging and any token-estimation logic must sanitize out raw bytes:

- replace image bytes with a compact placeholder (e.g., `<image image/png 12345 bytes>`)
- never `json.dumps(messages)` with raw bytes

## Native Adapter Changes

Update `penguiflow.llm.protocol.NativeLLMAdapter._convert_messages(...)`:

- Accept `content` as either `str` or a `list[dict[str, Any]]` parts array.
- Convert:
  - `{"type":"text","text":...}` -> `TextPart`
  - `{"type":"image", "data": bytes, "media_type": "image/png", "detail": "auto"}` -> `ImagePart`

Also update native adapter cost-estimation fallback to avoid serializing raw bytes.

## Guardrails and Streaming

- Guardrails should evaluate only *text* extracted from the latest user message.
- Streaming remains text-only (planner streams JSON / answer fields). We do not stream images.

## Testing Plan (minimum)

- Unit: `NativeLLMAdapter._convert_messages` converts a mixed `content` list into `LLMMessage` with `ImagePart`.
- Unit: message sanitization helpers do not include raw bytes.
- Integration-ish: `ReactPlanner.run(..., images=[...])` with a stub JSON client verifies the user message includes image parts.
- Resume: with `InMemoryArtifactStore`, ensure `pause -> resume` keeps image attachments available.
- Negative:
  - `images` + non-native client raises an actionable error.
  - missing artifact bytes on resume drops images and surfaces a warning.

## Implementation Phases

### Phase 1: Capability + Message Plumbing (core)

- Add `supports_vision: bool` to `penguiflow.llm.profiles.ModelProfile`.
- Mark known vision models in profiles across providers.
- Update planner message typing internally to allow non-string `content`.
- Extend `NativeLLMAdapter._convert_messages` to accept multi-part content and create `ImagePart`.
- Add message sanitization for token estimation/debug logging.

Exit criteria:

- Unit tests green for adapter conversion and sanitization.
- No raw bytes are logged.

### Phase 2: Planner API + Artifact-backed Attachments

- Add `images=` parameter to `ReactPlanner.run()`.
- Normalize inputs and store via `ArtifactStore.put_bytes` (`namespace="input_image"`).
- Register artifacts in `ArtifactRegistry` so UI can render them.
- Persist compact refs in `trajectory.metadata["input_images"]`.
- Update `build_messages(...)` to inject image parts into the current user message by rehydrating bytes from `ArtifactStore.get(...)`.

Exit criteria:

- `ReactPlanner.run(..., images=[...])` results in an LLM call that contains image parts.
- If the model profile lacks `supports_vision`, planner rejects the call (strict).

### Phase 3: Pause/Resume Durability

- Persist `input_images` metadata through pause record serialization.
- On `resume()`, ensure images are rehydrated for subsequent LLM calls.
- Define degraded behavior if artifacts are missing/expired.

Exit criteria:

- `pause -> resume` flow works with `InMemoryArtifactStore`.

### Phase 4 (Follow-up): Auto-promote Tool Image Artifacts to Vision Inputs

Goal: allow tools that return image artifacts to automatically become available as vision inputs in subsequent steps.

Design constraints:

- Must be controlled by policy (to prevent prompt bloat / accidental leakage).
- Must enforce size limits and a maximum number of images.

Proposed mechanism:

- Introduce a planner config (example name): `VisionContextPolicy` with fields like:
  - `enabled: bool`
  - `max_images: int`
  - `max_total_bytes: int`
  - `allowed_sources: set[str]` (tool names/tags)
  - `allow_mime_types: set[str]` (image/*)
- When a tool stores a binary artifact of `image/*`, if policy allows, append it to `trajectory.metadata["vision_context"]`.
- `build_messages(...)` attaches the policy-approved set as additional image parts.

Exit criteria:

- Approved tool-produced images flow into subsequent LLM calls without manual wiring.
- Policy/limits prevent runaway context.

### Phase 5 (Follow-up): Playground UI upload-to-`images=` plumbing

Goal: add a stable UX in Playground that uploads an image, stores it as an artifact, and passes a reference into `ReactPlanner.run(images=[...])`.

Notes:

- UI should show thumbnail previews (already supported by `ArtifactRegistry` for `image/*`).
- UI should not inline base64 into planner calls; it should create/resolve artifacts via the existing artifacts endpoint/contract.

Exit criteria:

- Playground supports attaching one or more images to a planner run.
- Uploaded images survive pause/resume when artifact store is configured.

## Risks and Mitigations

- Risk: accidental logging of raw image bytes/base64.
  - Mitigation: explicit sanitization helpers + tests asserting logs/serialization are byte-free.

- Risk: pause/resume loses images when `NoOpArtifactStore` is used.
  - Mitigation: document requirement; surface warnings and require reattach.

- Risk: token budgeting undercounts images.
  - Mitigation: conservative per-image token penalty in estimator (v1), refine later.

## Open Questions

- Should `resume(..., images=...)` allow users to add/replace attachments during resume, or keep resume strictly based on persisted `input_images`?
- Should the planner support both strict and lenient gating modes (raise vs warn+drop)?
