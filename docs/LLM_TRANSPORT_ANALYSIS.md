# LLM Transport Consolidation — Analysis & Branch Plan (3.11 line)

> Branch: `release/3.11` (integration branch; feature branches PR into it)
> Status: revision 3. Phase 0 spike executed; Phase 1, Phase 2, Phase 3, and
> Phase 4 shipped on the 3.11 line. Revision 3 pulls the
> native tool-calling planner mode into scope as Phase 5 (was "future").
> Supersedes: the initial liter-llm-only analysis (revision 1) and the
> tool-calling-out-of-scope plan (revision 2).
> Last updated: 2026-06-12

This document analyzes consolidating PenguiFlow's LLM transport — today a
hand-maintained native provider layer (7 providers) plus a legacy `litellm`
path — onto a battle-tested third-party client, and bundling four capability
upgrades into the same effort (the 3.11 release train):

1. **Multimodal inputs** — image (and later audio) content parts reaching the
   model.
2. **Streamlined fallback** — keep the rate-limit fallback shipped in 3.10.0
   transparent, with cleaner error classification underneath.
3. **Structured final answers** — the planner's final response validated
   against a developer-supplied Pydantic model instead of prompt-trusted
   formatting.
4. **Native tool-calling planner mode** *(revision 3)* — an opt-in planner
   mode where tool intent travels as provider-native tool calls instead of
   the prompted `{next_node, args}` JSON envelope. Replaces the wire format,
   not the decision shape — see §10 Phase 5.

**Hard constraint: zero breaking changes.** PenguiFlow is in production. An
agent that upgrades the library must behave identically or better with no code
changes. Every decision below is evaluated against that constraint first.

---

## 1. Summary of the recommendation

**Primary transport candidate: `pydantic-ai-slim`'s direct model layer
(`pydantic_ai.direct.model_request` / `model_request_stream`), pinned `<2`,
behind the existing `Provider` seam, opt-in first.** liter-llm moves to the
watch-list (re-evaluate in ~6 months). `litellm` is exited on a deprecation
schedule — the exit is now grounded in a verified package compromise, not just
CVE count (§4). The native provider layer is not deleted: it **shrinks to the
providers we can actually live-test** (Databricks, OpenRouter), and gains
automated cost tracking and OTel tracing that benefit every transport (§6).

The deciding constraint is **key coverage and trust** (§2): we hold API keys
for only a subset of providers and never will hold all of them. Provider code
we cannot exercise against a live endpoint is unverifiable liability — whether
we wrote it or a 2-month-old project did. The long tail must therefore be
delegated to the upstream with the strongest verifiable trust posture and the
broadest real-world test exposure. Among the candidates (§5), that is the
Pydantic organization: PEP 740 build attestations, a written V1
stability policy, ~weekly releases with a large contributor base, and a stack
we already build on (PenguiFlow is Pydantic-v2-native). It also happens to
solve the "streamline the native layer" wishlist directly: `genai-prices`
(cost tables, maintained by the same org) and OTel GenAI-semconv tracing
(MLflow-ingestable) come with the model layer.

The architecture from the first revision of this analysis is unchanged and
transport-agnostic: integrate behind the existing `Provider` ABC, opt-in flag,
parity gate before any default flip, and ship structured final answers +
multimodal inputs as provider-agnostic PenguiFlow features that do not depend
on which transport wins.

---

## 2. The deciding constraint: key coverage and trust

- We hold live credentials for **Databricks and OpenRouter** (the live
  validation matrix used for the 3.10.0 robustness work). We do not and will
  not hold keys for every provider the native layer claims to support
  (Bedrock, Google, NIM, native Anthropic/OpenAI in all configurations).
- Consequence 1: hand-maintained provider code for key-less providers can
  only be tested against recorded fixtures. Provider APIs drift; fixtures
  don't. Each such provider is an unbounded maintenance liability with no
  verification path — "streamlining" it doesn't change that.
- Consequence 2: the same logic applies to immature third-party transports.
  A library is only a trust upgrade if its providers are exercised by a large
  user base and its supply chain is verifiable. This is the bar liter-llm
  (2 months of stable releases, single primary maintainer, no build
  attestations found) does not yet meet, and litellm structurally failed
  (§4).
- Consequence 3: the providers we **can** test (Databricks above all — the
  production route, with quirks we already fixed in-house: GPT-5 fixed
  temperature, Claude Opus 4.7 no-temperature + reasoning mapping) are
  exactly where in-house code is cheapest and safest to keep. The trust
  argument runs in opposite directions for the head and the tail of the
  provider distribution.

This reframes the goal: **not** "replace the native layer", but "stop owning
provider code we cannot verify, keep owning the provider code only we can
verify."

---

## 3. Current state (what must not break)

### 3.1 The client contract

`JSONLLMClient` (`penguiflow/planner/models.py:93`) is the only contract the
planner depends on:

```python
class JSONLLMClient(Protocol):
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str | tuple[str, float]: ...
```

Implementations today: `NativeLLMAdapter` (`penguiflow/llm/protocol.py:161`),
`FallbackLLMClient` (`penguiflow/llm/fallback.py:137`), the legacy
`_LiteLLMJSONClient` (`penguiflow/planner/llm.py:536`), and `DSPyLLMClient`.
`NativeLLMAdapter` and `_LiteLLMJSONClient` additionally accept an
`on_reasoning_chunk` kwarg outside the protocol
(`penguiflow/planner/react_step.py:262–278`).

### 3.2 The stack underneath

```
ReactPlanner step
  └─ JSONLLMClient.complete()
       └─ FallbackLLMClient            (429 → cooldown → model/key rotation)
            └─ NativeLLMAdapter        (dict msgs → LLMMessage; output strategies;
                 │                      temperature-400 recovery; cost)
                 └─ Provider ABC       (validate_request / complete / stream)
                      └─ 7 concrete providers: OpenAI, Anthropic, Google,
                         Bedrock, Databricks, OpenRouter, NIM
```

Supporting machinery that encodes hard-won production knowledge:

- **Model profiles** (`penguiflow/llm/profiles/__init__.py`) — per-model
  capability metadata: `supports_temperature`, reasoning support, context
  windows. Includes route-specific quirks (e.g. `databricks-claude-opus-4-7`
  rejects temperature while native Anthropic does not; Databricks Claude
  Opus 4.7 reasoning mapping fixed in 3.10.0a3).
- **Rate-limit fallback** (`penguiflow/llm/fallback.py`) — shipped in 3.10.0:
  `ModelFallbackConfig`, per-`(model, key)` `CooldownStore`, revert-to-primary,
  mid-stream 429 semantics, auxiliary-client wiring. See
  `docs/LLM_PROVIDER_ROBUSTNESS_PLAN.md`.
- **Planner intent via structured output** — the planner asks for JSON
  (`response_format`), parses a `PlannerAction` (`next_node` + `args`),
  validates against tool schemas, and runs a bounded repair loop on bad JSON.
  Parallel plans are plain data (`next_node="parallel"`, `args.steps`) fanned
  out by the runtime. No provider-native tool calling anywhere.
- **Streaming** — `on_stream_chunk(text, done)` plus in-flight JSON extractors
  (`_StreamingArgsExtractor`, `_StreamingThoughtExtractor`) that surface the
  final answer token-by-token out of the structured JSON envelope.

### 3.3 What already exists but is dormant

- `ImagePart` exists in `penguiflow/llm/types.py` (`ContentPart = TextPart |
  ToolCallPart | ToolResultPart | ImagePart`; Phase 3 adds `AudioPart`) but
  the planner path is
  text-only: `NativeLLMAdapter._convert_messages()`
  (`penguiflow/llm/protocol.py:552`) maps `{"role", "content"}` dicts to a
  single `TextPart`.
- `StructuredOutputSpec` exists in the native layer, but the planner's final
  answer is free text inside `FinalPayload.raw_answer` — formatting is
  prompt-trusted, not schema-enforced.

---

## 4. The litellm exit, grounded (verified 2026-06-11)

The exit decision is no longer a judgment call. Verified record:

- **March 24, 2026 — litellm 1.82.7 / 1.82.8 on PyPI were backdoored**
  (GHSA-5mg7-485q-xm76; attacker "TeamPCP", downstream of the March 19 Trivy
  CI-action compromise that stole litellm's PyPI publishing credentials).
  v1.82.8 shipped a `litellm_init.pth` that executed **on every interpreter
  startup** — SDK-only users were fully exposed; ~47k downloads in the ~40
  minutes before quarantine. Payload harvested env vars, SSH keys, cloud
  credentials, CI/CD secrets. Confirmed by litellm's own advisory, Datadog,
  Snyk, Wiz, Sonatype.
- **April–June 2026 — two server-side CVEs exploited in the wild**: pre-auth
  SQLi in proxy key verification (CVE-2026-42208, CVSS 9.3, exploited within
  ~36h of disclosure, leaks upstream provider keys) and MCP command injection
  (CVE-2026-42271, on **CISA KEV** since 2026-06-08). Proxy-side, so they
  don't hit our SDK-only usage — but they are the 24th and 23rd advisories in
  ~26 months, with recurring vulnerability classes (SQLi and SSTI each
  appearing in both 2024 and 2026).
- Footprint: ~13 direct deps (including the full `openai` SDK, `aiohttp` and
  `httpx` both), 94 `requires_dist` entries across extras, ~15 MB wheel,
  Python `<3.14` ceiling.

Note our current pin is `litellm>=1.77.3,<=1.82.6` (`pyproject.toml:84`) —
the upper bound stops **exactly one release before** the malicious 1.82.7.
That was luck, not protection. Action regardless of transport choice:
deprecate the `litellm` extra; until removal, hold the pin below 1.82.7 or
move it to `>=1.83.0` (post-rebuild pipeline) with hash-pinned locks.

---

## 5. Candidate assessment

Hard requirements: async completion + async streaming **with reasoning
deltas** (the Databricks Opus 4.7 work depends on them), OpenAI-style
`json_schema` response format, typed/classifiable errors (429 detection feeds
`FallbackLLMClient`), coverage of OpenAI / Anthropic / Gemini / Bedrock /
Databricks / OpenRouter / NIM + generic OpenAI-compatible, multimodal input,
supply-chain trust, light dependency footprint, Pydantic v2 compatibility.

| | litellm | liter-llm | any-llm (Mozilla) | **pydantic-ai-slim (direct)** | openai-SDK consolidation | streamlined native |
|---|---|---|---|---|---|---|
| Async + streaming | ✓ | ✓ (binding fixed only in v1.4.0) | ✓ | ✓ | ✓ | ✓ (ours) |
| Reasoning deltas | partial | unverified in Python | normalized, **but Databricks-Claude path verified broken** (flag enabled untested, PR #556) | **`ThinkingPartDelta` — best surveyed** | not in OpenAI spec; per-provider sniffing forever | ✓ (we wrote it) |
| `json_schema` output | ✓ | ✓ | ✓ (gaps, e.g. #542) | ✓ native mode + prompted fallback | varies by compat layer (Anthropic compat silently drops it) | ✓ (ours) |
| Typed 429 | ✓ | ✓ | ✓ `RateLimitError(retry_after)` | ✓ `ModelHTTPError.status_code` | ✓ per-SDK | ✓ (ours) |
| Provider coverage | ~100 | 143 | good; no NIM provider | broad; **no Databricks provider** (issue #2947, PR #4036 open) | ~70% of our matrix; Bedrock-Claude and Anthropic-compat not production-viable | 7, mostly untestable by us |
| Multimodal input | ✓ | ✓ image+audio | image ✓, audio unverified | ✓ `ImageUrl`/`AudioUrl`/`BinaryContent` | ✓ where compat layer allows | would build ourselves |
| Cost tracking | ✓ | ✓ in core | ✓ | ✓ `genai-prices` (same org, standalone-usable) | manual | would build ourselves |
| Tracing | callbacks | unconfirmed in Python | ✓ | ✓ OTel GenAI semconv built in; MLflow-ingestable | manual | would build ourselves |
| Supply chain | **backdoored 2026-03** | 1 maintainer, 2.5 months, no attestations found | Mozilla AI, attestations, 2-maintainer bus factor | **Pydantic Inc, PEP 740 attestations, written V1 stability policy** | OpenAI/Anthropic-grade | ours (trusted but unverifiable long tail) |
| Install weight | ~15 MB wheel, 13+ deps | ~0 Python deps (native binary; **no musl wheels**) | 24 pkgs (bundles openai+anthropic SDKs always) | 32 pkgs (`[openai,anthropic]`), no provider SDKs in base | 16 pkgs/SDK | 0 new |
| Maturity risk | mature but compromised + 3.4k open issues | **high** | medium (breaking changes in minors) | low-medium (**2.0 betas in flight → pin `<2`**) | low | n/a |

Eliminated outright: **aisuite** (sync-only, no streaming, dormant since
2025-11), **instructor** (structured-output layer, not a transport — could
still sit on top later), **llm**/CLI-first, **mirascope** (3 providers),
**unify** (pivoted away), **LangChain** (weight).

### Why pydantic-ai-slim (direct) wins

- `pydantic_ai.direct.model_request` / `model_request_stream` are documented
  precisely for "building your own abstractions" without adopting the Agent
  framework. `ModelRequestParameters` supports `output_mode='native'` +
  `output_object` (json_schema) with a `prompted_output` fallback — i.e. the
  downgrade chain from §7-finding-2 exists upstream.
- Streaming events include `ThinkingPartDelta` with provider details — the
  only surveyed library with a first-class reasoning-delta model, which is
  what `on_reasoning_chunk` parity requires.
- `FallbackModel(default, *fallbacks, fallback_on=...)` exists upstream, but
  **we keep our `FallbackLLMClient`** — ours carries cooldown semantics,
  key rotation, and revert-to-primary that production already depends on.
- Trust: the same organization whose validation library is already
  PenguiFlow's core dependency; attestations; stability policy; 17.7k stars;
  weekly releases; 3 OSV advisories ever, all in optional features, all
  patched.
- **The gap is Databricks — and that is the least bad place to have one.**
  pydantic-ai has no Databricks provider, and the generic
  OpenAI-compatible workaround fails Pydantic validation on Databricks-served
  Claude content-block responses (verified at source level). But Databricks is
  the one provider where we hold keys, production traffic, and fresh expertise
  (3.10.0a3 reasoning fix). We keep our native `DatabricksProvider` (or write
  a small custom `Model` subclass — the plug-point is a documented ABC) and
  track upstream PR #4036 for eventual retirement.

### Why not the others

- **liter-llm** (first revision's candidate): genuinely impressive
  engineering, and its provider breadth is unmatched — but every red flag
  lands on the surface we'd depend on: the Python binding (async semantics
  broken until v1.4.0, renames in v1.4/v1.5, fallback and hooks not clearly
  exposed to Python), single primary maintainer, ~2.5 months of stable
  history, no musl wheels, and a compiled core we cannot patch when the next
  Databricks-route quirk appears. **Watch-list: re-evaluate ~2026-12** (≥9
  months of binding stability, attestations, multi-maintainer).
- **any-llm**: closest philosophical fit (wraps official SDKs, unified errors,
  normalized reasoning deltas, dedicated Databricks provider) — but its
  Databricks-Claude reasoning support is non-functional, **verified live
  2026-06-11** against our Databricks workspace (v1.17.0, models
  `databricks-claude-opus-4-7/4-8`, `databricks-gpt-5-5`,
  `databricks-gpt-5-4-mini`):
  - `DatabricksProvider` is an empty `BaseOpenAIProvider` subclass with
    `SUPPORTS_COMPLETION_REASONING = True` flipped on (the flag was enabled
    untested — upstream PR #556). There is no per-model request translation:
    the unified `reasoning_effort` param is forwarded verbatim and Databricks
    Claude rejects it with **400 "reasoning_effort: Extra inputs are not
    permitted"** (both stream and non-stream). PenguiFlow's provider
    translates per family (`databricks.py:558–593`); any-llm does not.
  - Hand-crafting the correct request (`extra_body={"thinking":
    {"type": "adaptive"}}`) gets past Databricks, but **streaming then
    crashes inside any-llm's own types**: `ValidationError: 2 validation
    errors for ChatCompletionChunk — usage.completion_tokens must be int,
    got None` on Databricks' usage chunk. Hard failure, not silent drop.
  - Reasoning normalization only reads top-level string fields
    (`reasoning_content`/`thinking`/`think`/`chain_of_thought`) and
    `<think>`-style XML tags; Databricks Claude's list-shaped reasoning
    content blocks (`type: "reasoning"`, `summary[].text` — what
    `databricks.py:454–463` parses) have no conversion path.
  - GPT-5 family on Databricks works through any-llm (`reasoning_effort`
    accepted), with no visible reasoning stream — consistent with the
    provider hiding reasoning; not a defect.
  Re-evaluate alongside liter-llm.
- **openai-SDK consolidation** (official `openai` SDK against compat
  endpoints): maximal supply-chain trust, but the compat layers are leaky
  precisely where we need them — Anthropic's is documented non-production and
  silently ignores `json_schema` and thinking; Bedrock-Claude's chat
  completions support is contradicted within AWS's own docs; reasoning deltas
  are not in the OpenAI spec at all, so every provider diverges
  (`reasoning_details`, `reasoning_content`, `<think>` tags, content blocks).
  Choosing this means owning per-provider normalization forever — the burden
  we're trying to shed.
- **streamlined native only** (cost + trace automation on the current layer):
  assessed in §6 — the automation is worth shipping, but it does not solve
  the §2 trust problem for the provider long tail.

---

## 6. The "streamlined native" track — adopted in reduced scope

The idea of keeping the native layer but automating cost tracking and tracing
is sound and is **partially adopted** — not as the whole answer, but as
hardening for the providers we keep:

1. **Native layer shrinks to what we can verify.** Keep: `DatabricksProvider`,
   `OpenRouterProvider` (live keys, production traffic). Deprecate-and-delegate
   to the new transport: OpenAI, Anthropic, Google, Bedrock, NIM (NIM is
   OpenAI-compatible and reachable through the transport's compat path).
   Result: 7 hand-maintained providers → 2 we actually test.
2. **Cost tracking via `genai-prices`** (Pydantic's standalone price
   database) replaces our hand-maintained pricing tables as the source of
   truth for both the native providers and the transport; our
   `calculate_cost`/`register_pricing` API stays as the stable facade
   (override-capable for private Databricks rates). Shipped as an optional
   `penguiflow-lite[pricing]` extra and explicitly included in
   `penguiflow-lite[pydantic-ai]`; imports are lazy, and core falls back to the
   static table when the package is absent.
3. **Auto-trace at the adapter edge, transport-agnostic.** One OTel
   GenAI-semconv span per `Provider.complete()` /
   `JSONLLMClient.complete()` call (model, tokens, cost, latency,
   finish_reason; never raw prompts by default), emitted through the existing
   `metrics.py`/`middlewares.py` hooks. MLflow ingests OTel traces, and
   MLflow's tracing integrations list PydanticAI autologging (verify exact
   MLflow version in the spike). This lands once at our seam and covers every
   transport, including the remaining native providers.

What this track does **not** do alone: make untestable provider code
trustworthy. Hence the hybrid in §1.

---

## 7. Findings incorporated (prior internal architecture research)

Internal research on LLM-client architecture for agent runtimes (validated
against 20+ providers in a separate production system) settled several
principles we adopt here rather than re-derive:

1. **The client is transport; the runtime owns intent.** Keep the LLM client
   to one completion method. Parsing, validation, repair, and tool dispatch
   live in the runtime, never in the client. PenguiFlow already conforms
   (`JSONLLMClient` + planner repair loop). The migration must not leak the
   transport's tool-calling or typed-response shapes into the planner.
2. **Structured-output modes need a downgrade chain.** Providers fail
   `json_schema` in diverse ways. The proven chain is
   `json_schema → json_object → prompted-text`, triggered by invalid-schema
   error classification, with an event emitted on each downgrade. PenguiFlow's
   output strategies (`penguiflow/llm/output/`) already implement the modes;
   pydantic-ai's `output_mode='native'` + `prompted_output` matches this
   shape upstream.
3. **Validator-driven retry with a corrective turn.** For schema-enforced
   responses, on validation failure append a corrective message containing the
   validation error and retry, bounded per model profile (default 1 retry).
   This is the mechanism for the new structured final answer — it lives in
   PenguiFlow regardless of transport.
4. **Per-provider quirks are a correction layer, not client flags.** Message
   reordering, schema sanitization, reasoning-effort routing, temperature
   support — all keyed off model profiles. PenguiFlow's profile system is the
   same idea; it must keep working when the transport is third-party code we
   cannot patch.
5. **Multimodal inputs as a content-part sum type with size discipline.**
   Image/audio/file parts with three supply forms (URL, inline data, artifact
   reference), and a hard threshold above which inline data must not reach the
   LLM edge raw. Inputs first; generation/transcription outputs are tools,
   not client methods.
6. **Prompt-engineered and native tool calling can coexist** behind a planner
   opt-in, because both reduce to the same decision shape (`PlannerAction`).
   The runtime machinery that executes decisions — parallel fanout, join
   injection, trajectory, events, pause/resume — is invariant to where the
   decision came from; only the extraction layer differs. Native tool calling
   is the right long-term shape for tool-calling-capable models
   (provider-validated args, clean content channel, native parallel calls);
   prompted JSON is the permanent compatibility floor for models without
   tool-calling fine-tunes. *(Revision 3 pulls this in as §10 Phase 5.)*

---

## 8. Decisions (proposed, revised)

1. **Integration point: the `Provider` ABC, not the adapter.** A
   `PydanticAIProvider` in `penguiflow/llm/providers/pydantic_ai.py` wraps
   `pydantic_ai.direct` behind the existing `Provider` interface. Everything
   above — `NativeLLMAdapter`, output strategies, temperature recovery,
   `FallbackLLMClient`, profiles, pricing facade, telemetry — is reused
   verbatim. *(Unchanged from first revision except the transport.)*
2. **Opt-in first, default later.** Transport selection via
   `create_native_adapter(transport="native" | "pydantic-ai")`, defaulting to
   `"native"`. Default flips per model-family only after the §11 parity gate;
   Databricks and OpenRouter stay on the kept native providers until upstream
   support is proven equivalent. `litellm` extra deprecated immediately (§4
   pin action now, removal on schedule).
3. **Pin `pydantic-ai-slim<2`** with per-provider extras only
   (`[openai,anthropic,google,bedrock]`), hash-pinned lockfiles. Re-evaluate
   the 2.x line after it stabilizes.
4. **Fallback stays ours.** `FallbackLLMClient`/`CooldownStore` semantics
   unchanged; the transport's typed errors (`ModelHTTPError.status_code ==
   429`) become the single classification path. Upstream `FallbackModel` is
   not used.
5. **Structured final answer is a PenguiFlow feature, provider-agnostic.**
   New opt-in `ReactPlanner(final_response_model: type[BaseModel] | None =
   None)`: schema injected into the final-response action, `model_validate`
   on the result, bounded corrective-turn retry (finding 3). `FinalPayload`
   gains `structured: BaseModel | None`; `raw_answer` keeps today's text.
   Default unset → byte-for-byte today's behavior.
6. **Multimodal inputs are a PenguiFlow feature, provider-agnostic.**
   `AudioPart` joins `ImagePart`; `JSONLLMClient.messages` widens
   `Sequence[Mapping[str, str]]` → `Sequence[Mapping[str, Any]]`
   (parameter-type widening — non-breaking). Profile capability flags
   (`supports_image_input`, `supports_audio_input`) fail loudly; 32 KB
   default inline-data threshold at the adapter edge, configurable via
   `multimodal_inline_data_limit_bytes`.
7. **Cost + trace automation per §6** ships regardless of transport outcome:
   `genai-prices` behind the existing pricing facade; OTel GenAI-semconv
   spans at the adapter seam through existing hooks.
8. **`JSONLLMClient` frozen** except the widening in (6) and formally adding
   the already-shipped `on_reasoning_chunk` as an optional kwarg.
9. **Native tool calling: in scope as an opt-in planner mode** *(revision 3 —
   was "out of scope, not precluded")*. Binding constraint: it replaces the
   **wire format**, never the **decision shape**. Provider tool calls are
   mapped into the same `PlannerAction` the runtime already executes; the
   prompted mode remains the default until the parity gate and the permanent
   floor for non-tool-calling models; eligibility is profile-driven
   (`supports_tools`); per-model downgrade to prompted mode inside mixed
   fallback chains. Design detail in §10 Phase 5.
10. **Route/model gates are profile configuration, not code** *(added after
    the Phase 0 spike; same pattern as `reasoning_request_style` in 3.10.1)*.
    The two spike findings become `ModelProfile` fields, each landing with
    its first consumer (never as dead config):
    - `preferred_transport: Literal["native", "pydantic-ai"] | None = None`
      *(consumer: Phase 1 factory)*. Resolution order in
      `create_native_adapter`: explicit `transport=` kwarg → profile →
      default. Databricks Claude reasoning models pin `"native"` while the
      content-block gap (upstream #2947) is open; when upstream fixes it,
      flipping a model to the transport is a profile edit — or an operator
      `register_profile()` call — with zero library code changes.
    - `supports_native_tool_calls: bool = True` *(consumer: Phase 5
      planner)*. Distinct from `supports_tools` (the model can use tools)
      — this gates the native *wire format* on the model's route.
      `databricks-gpt-5-5` sets `False` (chat-completions rejects function
      tools; Responses API required); native-mode planners downgrade that
      call to prompted with an event instead of failing.
    Both are runtime-overridable through the existing `register_profile()`
    surface, so operators can re-route or un-gate models from their own
    config without waiting for a penguiflow release.

---

## 9. Non-goals

- **Removing** the prompted (structured-output-as-intent) planner mode.
  Native tool calling is an opt-in second mode; prompted stays the default
  until the parity gate and the permanent compatibility floor afterwards.
- Adopting pydantic-ai's Agent, tools, or graph machinery — **only**
  `pydantic_ai.direct` + model classes + message types. PenguiFlow remains
  the agent runtime.
- Multimodal **outputs** (image generation, TTS, transcription) — tools, not
  client capabilities.
- Removing the kept native providers (Databricks, OpenRouter).
- liter-llm integration (watch-list, re-evaluate ~2026-12).
- Process-wide cooldown store (tracked in the robustness plan).

---

## 10. Phased delivery

### Phase 0 — Spike & go/no-go gate — **EXECUTED 2026-06-11: GO**

Throwaway harness (not shipped) driving `pydantic-ai-slim==1.107.0` via
`pydantic_ai.direct` against live routes. Results:

- [x] **Typed error mapping** — `ModelHTTPError(status_code, model_name,
      body)` verified live on 400s and 401s across both routes; exactly the
      single classification path `FallbackLLMClient` needs.
- [x] **Databricks via pydantic-ai (`OpenAIChatModel` + workspace base_url)**
      — `databricks-gpt-5-5` and `databricks-claude-opus-4-8`:
      - Plain + usage tokens: OK on both.
      - **Streaming is fully incremental** (81 / 57 `TextPartDelta`s on a
        long answer; short answers arrive in `PartStartEvent` — harness must
        count both).
      - **Native `json_schema` output mode: OK on both models**, valid parsed
        JSON — Phase 2's primary mechanism works through the transport on
        the production route.
      - **Native parallel tool calls: OK on Claude Opus 4.8** after setting
        `OpenAIModelProfile(openai_supports_strict_tool_definition=False)`
        (without it: 400 `tools.0.custom.strict`). Both tools called in one
        response with valid args — the Phase 5 wire format works on the
        production route, and the quirk is config-level (corrections-layer
        pattern), not code.
      - **Thinking gap confirmed exactly as predicted**: adaptive thinking →
        `UnexpectedModelBehavior` (content-block list fails `content: str`
        validation; upstream issue #2947). The kept native
        `DatabricksProvider` remains mandatory for reasoning workloads.
      - **New route limitation**: `databricks-gpt-5-5` + function tools on
        chat-completions → hard 400 ("use /v1/responses"). Affects our native
        provider equally; Phase 5 profile-gates native mode off for this
        model until a Responses-API path exists.
- [x] **Image input round-trip** — `BinaryContent(png)` → Databricks Claude
      Opus 4.8 answered correctly. Image leg of Phase 3 verified on the
      production route. Audio leg deferred (no audio-capable route in our
      key set).
- [x] **`genai-prices`** — opus-4-7 ($5/$25) and gpt-5-4-mini ($0.75/$4.50)
      match our tables exactly; **opus-4-8 $5/$25 independently confirms the
      3.10.1 assumed pricing**; discrepancy flagged on sonnet-4-5 ($6/$22.50
      vs our $3/$15) was resolved in Phase 4 as tiered pricing behavior:
      base 1K-token usage returns $3/$15 per MTok, while >200K-token usage
      enters the long-context tier ($6/$22.50 per MTok for Sonnet 4.5 in the
      bundled snapshot).
- [x] **Dependency audit** — `pydantic-ai-slim[openai,openrouter]` +
      `genai-prices`: 30 packages standalone; resolves cleanly alongside
      penguiflow's full dev set (124 total, zero conflicts). Confirmed hard
      transitive deps: `opentelemetry-api`, `pydantic-graph`, `griffelib`,
      `openai` SDK.
- [x] **OpenRouter route** (re-run 2026-06-11 after key refresh) —
      `openai/gpt-oss-20b` via `openrouter:` model names: plain response
      carries a typed `ThinkingPart` (pydantic-ai maps OpenRouter reasoning
      natively); **streaming yields `ThinkingPartDelta`s** (35 thinking + 117
      text deltas) — `on_reasoning_chunk` parity verified; native
      `json_schema` ✓; prompted fallback ✓; native tool calling ✓ (single
      call on this small model; parallel proven on Databricks Claude).
- [ ] OpenAI / Anthropic / Gemini direct — deferred, no keys (accepted: §2
      delegates these to upstream's test exposure).
- [ ] Mid-stream 429 — not triggerable on demand; covered instead by Phase 1
      unit tests with stub transports + the typed-error verification above.
- [ ] MLflow ingest E2E — deferred to Phase 4's remaining half (the trace
      seam itself already shipped in 3.11.0a1).

**Verdict: GO.** Every blocking item passed; the two red items are a
credential refresh and provider keys we never planned to hold. The thinking
gap and the gpt-5-5 tools limitation reinforce (not weaken) the §6 decision
to keep the native Databricks provider.

### Phase 1 — `PydanticAIProvider` (opt-in)

- `penguiflow/llm/providers/pydantic_ai.py` implementing the `Provider` ABC;
  optional extra `penguiflow-lite[pydantic-ai]` with `<2` pin.
- Transport selection in `create_native_adapter` (default `"native"`).
- Error mapping + profile-driven request building (temperature, reasoning,
  schema sanitization) applied before the request enters the transport.
- `litellm` pin action (§4) + deprecation warning on the litellm path.
- Tests: provider conformance suite parameterized over `native` and
  `pydantic-ai` stub transports; mid-stream behavior; negative paths.

### Phase 2 — Structured final answers (provider-agnostic) — **SHIPPED**

- `ReactPlanner(final_response_model=..., final_response_retries=1)` per
  Decision 5. Schema injected into the system prompt (and the conditional
  finish schema on Gemini-family routes); `args["structured"]` validated via
  `model_validate`; corrective turn re-prompts with errors + schema; on
  exhaustion the field is stripped (never unvalidated data) with a
  `payload.warnings` entry + `final_response_structured_degraded` event.
- Deviation from Decision 5 noted: `FinalPayload.structured` is the
  **json-mode dict dump** of the validated instance, not the instance —
  `result.payload` is already a serialized dict on every delivery path
  (sessions, A2A, AG-UI); carrying instances would break serialization.
- Streaming unchanged: answer streams first (prompt orders args keys);
  `structured` validates on the assembled result.
- Live-verified on Databricks `gpt-5-5` through BOTH transports (identical
  validated payloads, no repair needed) — and the degradation path verified
  live via a misconfigured client (repair attempt → degraded event → run
  completes with warning).

### Phase 3 — Multimodal inputs (provider-agnostic) — **SHIPPED**

- `AudioPart`; planner-level input parts; message widening per Decision 6;
  capability flags on kept-native + transport providers; threshold
  enforcement (`multimodal_inline_data_limit_bytes`, default 32 KiB).
- Planner input surface: `ReactPlanner.run(query, input_parts=...)`, where
  `input_parts` is an optional sequence of `ImagePart | AudioPart`. The
  string `query` remains the textual user request and keeps today's default
  behavior byte-for-byte when omitted. Parts are appended to the initial user
  message only (not `llm_context`, not tool context, not every repair turn),
  so the planner trajectory can preserve a typed stub while summaries render
  only metadata (`type`, `media_type`, `bytes`) and never inline binary data.
  Resume remains text-only in Phase 3; multimodal follow-up turns can be added
  later with the same message-part contract.
- Tests: parts survive the planner trajectory (incl. trajectory summarization
  — parts must be summarized/stubbed, never inlined); loud failure on
  unsupported provider; threshold violation.
- Live validation: Databricks Claude image round-trip passed through the full
  `ReactPlanner` + adapter stack on both transports (`native` and
  `pydantic-ai`) using `databricks/databricks-claude-opus-4-7`. OpenRouter
  native planner smoke passed after loading `.env` with last-key-wins
  semantics (the file contains duplicate `OPENROUTER_API_KEY` entries). Audio
  mapping is implemented and unit-tested via pydantic-ai `BinaryContent`; live
  audio validation remains deferred because the available key set has no
  audio-capable route.

### Phase 4 — Cost + trace automation — **SHIPPED**

- ~~Trace seam~~ **landed in 3.11.0a1**: `penguiflow/llm/tracing.py` —
  pluggable `LLMTraceSink` at the `NativeLLMAdapter.complete()` seam (covers
  every transport and every `FallbackLLMClient` adapter), with
  `MlflowLLMTraceSink` (MLflow Tracing spans) and `LoggingLLMTraceSink`;
  transparent enablement via `PENGUIFLOW_LLM_TRACING=mlflow|log`.
- **Cost automation shipped**: `genai-prices>=0.0.66,<1` is now the source of
  truth behind the existing `penguiflow.llm.pricing` facade. Public API stays
  frozen (`calculate_cost`, `calculate_cost_from_usage`, `get_pricing`,
  `register_pricing`); resolution order is registered override →
  `genai-prices` candidate lookup → static fallback table → existing warning
  and `(0.0, 0.0)`. Exact static free-tier entries keep their prior zero-cost
  behavior to avoid broad upstream prefix matches changing defaults.
- **Normalization shipped**: slash providers are peeled from left to right
  (`openrouter/openai/gpt-5.3-chat` → `openai/gpt-5.3-chat` →
  `gpt-5.3-chat`), dotted IDs keep hyphenated aliases, and Databricks endpoint
  prefixes are tried stripped (`databricks-gpt-5-4-mini` →
  `gpt-5-4-mini`).
- **Tiered-price handling shipped**: `get_pricing()` returns the facade's
  historical base per-1K rates using a 1K synthetic usage; `calculate_cost()`
  and `calculate_cost_from_usage()` use the actual usage through
  `genai-prices.calc_price()`, so long-context tiering is applied when token
  counts cross the upstream thresholds.
- **Sonnet 4.5 finding resolved**: Anthropic's current public pricing page
  lists Claude Sonnet 4.5 base pricing at $3 input / $15 output per MTok;
  `genai-prices` also returns that base rate for 1K usage. The spike's
  $6/$22.50 figure came from pricing a 1M-token synthetic request, which hits
  the bundled Sonnet 4.5 long-context tier above 200K input tokens. No static
  base-table correction was required.
- **Example shipped**: `examples/mlflow_llm_tracing/` is runnable via
  `uv run python examples/mlflow_llm_tracing/flow.py`, supports
  `PENGUIFLOW_LLM_TRACING=mlflow`, includes a deterministic scripted path and
  a live Databricks mode (`PENGUIFLOW_PHASE4_LIVE=1`), and has an integration
  test scenario.
- **Validation shipped**: unit tests cover override precedence, upstream over
  static, static fallback when `genai-prices` is absent, Databricks prefix
  normalization, production model parity
  (`databricks-claude-opus-4-7/4-8`, `databricks-gpt-5-5`,
  `databricks-gpt-5-4-mini`), and tiered Sonnet 4.5 actual-cost calculation.

### Phase 5 — Native tool-calling planner mode (opt-in)

The deepest change of the train, governed by Decision 9: provider-native tool
calls replace the prompted `{next_node, args}` JSON envelope as the **wire
format**, while `PlannerAction` remains the **decision shape** the runtime
executes. The extraction layer changes; nothing downstream of it does.

What it eliminates (the expected payoff):

- **The in-flight JSON streaming extractors** (`_StreamingArgsExtractor`,
  `_StreamingThoughtExtractor`): with tool calls on their own channel, the
  content channel is plain text — final-answer streaming becomes raw content
  deltas, no JSON envelope to parse token-by-token.
- **Most of the parse/repair pressure**: args are provider-validated against
  the declared schema before they reach us; fence-stripping, multi-object
  salvage, and last-ditch regex become prompted-mode-only code.
- **The synthetic parallel envelope**: native parallel tool calls (multiple
  `tool_calls` in one response) replace `next_node="parallel"` + `args.steps`
  as the wire form. They map into the **same** parallel plan the runtime
  already executes (`execute_parallel_plan` unchanged).
- **Prompt weight**: tool schemas move from prompt text into API tool
  declarations (cheaper, prompt-cache-friendly, no schema drift between
  prompt and validator).

What it must preserve (binding):

- **Decision-shape invariance.** A mapping layer converts
  `ToolCallPart`(s) → `PlannerAction`; parallel fanout, explicit join
  injection, trajectory recording, planner events, pause/resume, and the A2A
  surface are byte-identical across modes. The dual-mode conformance suite
  (below) is the proof.
- **Join semantics**: native parallel calls don't express a join step; the
  explicit join injection contract (v2.4) is preserved by the mapping layer,
  not delegated to the model.

Implementation decisions (settled 2026-06-12, before code):

- **D5.1 — Finish form: content-only turn = final answer.** No synthetic
  `final_response` tool is declared. A native turn that returns zero tool
  calls finishes the run; its content is the answer (maps to
  `PlannerAction(next_node="final_response", args={"answer": content})`).
  Content alongside tool calls (a real pattern — live probe showed Claude
  emitting 76–116-char preambles with parallel tool calls) maps to
  `action.thought`.
- **D5.2 — Trajectory encoding unchanged (§12.7 resolved by design).** The
  conversation history stays text-encoded exactly as today; native assistant
  tool-call messages and tool-role results are NOT replayed, so there is no
  `tool_call_id` round-trip to orphan. Compaction, pause/resume, and A2A are
  untouched. (This supersedes the earlier "tool-result round-trip as
  tool-role messages" sketch — that form returns only if provider-side
  tool-result caching is ever needed.)
- **D5.3 (revised same day after review) — token-by-token answer streaming
  with a superseded guard.** The native prompt forbids text alongside tool
  calls ("emit ONLY the tool calls"); content deltas then stream
  optimistically to the `answer` channel token-by-token — full parity with
  prompted mode. If a model disobeys (preamble before tool calls), the answer
  stream closes with `superseded: True` and the text re-emits on `thinking`,
  so consumers that honor the marker retract cleanly and consumers that
  ignore it degrade to briefly-shown preamble, never broken state.
  Live-verified: Claude Opus 4.8 fully complies (29 token chunks on the
  answer channel vs 19 in the prompted control; zero superseded events
  across runs) — the earlier flush-only design (v1 of this decision) was
  rejected as a UX regression. Reasoning deltas render on the `thinking`
  channel (prompted-mode parity; the playground UI has no `reasoning`
  channel case). Three wiring fixes from the playground live check:
  chunk `action_seq` must equal the runtime's `step_start` seq (the UI
  answer gate silently drops mismatches — the off-by-one made SSE mode a
  wall of text while AG-UI, which doesn't gate, streamed fine); emissions
  go through `_enqueue_llm_stream_chunk` (guardrail redaction parity with
  prompted mode); and reasoning uses channel `thinking`. SSE wire
  verified post-fix: 36/36 answer chunks matching the gate.
- **D5.4 — Structured final answers reuse Phase 2 machinery (supersedes the
  "final_response as declared tool" sketch).** A content-finish carries no
  `structured` payload, so `_ensure_structured_final` runs its existing
  corrective extraction turn (schema + answer → validated payload). Cost: one
  extra LLM call per run in native mode when `final_response_model` is set;
  note `final_response_retries=0` would always degrade in native mode.
- **D5.5 — Parallel mapping**: N>1 tool calls → `next_node="parallel"`,
  `args={"steps": [{"node", "args"}...]}` (no join — joins are not
  expressible natively in v1); 1 call → direct tool action. Tool args parse
  defensively (`json.loads`; on failure `{}` → existing arg-validation
  repair).
- **D5.6 — multi_action / auto_seq are prompted-mode-only** (they parse
  multiple JSON objects from text; native turns can't produce that shape —
  parallel tool calls cover the use case).
- **§12.8 resolved live (2026-06-12)**: adaptive thinking + parallel native
  tool calls compose on `databricks-claude-opus-4-8` through the native
  provider. `databricks-gpt-5-5` + tools reconfirmed 400 (Responses API) →
  `supports_native_tool_calls=False`.
- **Prompt**: native mode swaps the `<output_format>`/`<action_schema>`/
  `<finishing>`/`<parallel_execution>` sections for native-calling variants
  and omits `<structured_final_response>` (the extraction turn is
  self-contained). The `<available_tools>` catalog section is kept v1
  (descriptions/side-effects still inform selection); schema-dedup token
  savings deferred.

Wiring:

- Planner opt-in: `ReactPlanner(tool_call_mode="prompted" | "native")`,
  default `"prompted"` (zero behavior change). Eligibility gated by
  `ModelProfile.supports_tools` AND `supports_native_tool_calls` (Decision
  10 — route-level gate, e.g. `databricks-gpt-5-5`); a native-mode planner
  running a model (or a fallback-chain member) that fails either gate
  downgrades that call to prompted mode with an event, never fails.
- Adapter surface: additive — a new method on `NativeLLMAdapter` (e.g.
  `complete_with_tools()` returning content + typed tool calls), leaving
  `JSONLLMClient.complete()` untouched per Decision 8. The pydantic-ai
  transport carries this naturally (`function_tools` in
  `ModelRequestParameters`, streamed `ToolCallPartDelta` assembly handled
  upstream); the kept native providers already have `ToolSpec`/
  `_to_openai_tools` plumbing.
- Tool-call streaming-delta assembly (provider-divergent fragment merging,
  index-keyed) is the transport's job, not ours — one more reason the
  transport phase lands first.

Tests: the planner test matrix runs in **both modes** (dual-mode conformance
suite); parallel + join parity; mixed fallback-chain downgrade; streaming
final answer in native mode; negative paths (model without tool support,
malformed provider tool call).

**SHIPPED (2026-06-12).** Implementation:

- `ReactPlanner(tool_call_mode="prompted" | "native")` (default `"prompted"`,
  invalid values fail loudly). Native step path in
  `penguiflow/planner/react_step_native.py`; the prompted path in
  `react_step.py` is untouched. Eligibility checked per step
  (`complete_with_tools` present + `supports_tools` +
  `supports_native_tool_calls`); ineligible runs downgrade to prompted with a
  one-per-run `tool_call_mode_downgraded` event.
- Adapter surface: `NativeLLMAdapter.complete_with_tools()` →
  `NativeToolCallResult(content, tool_calls, cost, reasoning_content)`,
  traced (`response_format_kind="native_tools"`), with retry/temperature
  recovery; `FallbackLLMClient.complete_with_tools()` shares the refactored
  429-failover core (`_with_failover`).
- **Wire-safe tool aliases** (found live on the youtube agent): catalog names
  like MCP's `youtube.download_video` violate provider function-name patterns
  (`^[a-zA-Z0-9_-]{1,128}$`, hard 400 on Databricks). Tools are declared
  under sanitized aliases with collision handling; responses map back to real
  catalog names before `PlannerAction`.
- Native-mode system prompt swaps `<output_format>`/`<finishing>`/
  `<parallel_execution>` and drops `<action_schema>` +
  `<structured_final_response>` (~40% smaller prompt).

Live verification (Databricks `claude-opus-4-8`, real youtube-download agent
with MCP tools): native tool call executed and answered correctly;
**token-by-token answer streaming** per D5.3-revised (29 answer-channel
chunks vs 19 in the prompted control; zero preamble/superseded events —
the no-preamble instruction holds); **structured final answer through the
native path validated** (`final_response_structured_repair_attempt` →
`validated`, exactly one extraction turn); `databricks/databricks-gpt-5-5` in
native mode downgraded live with the route reason and completed via prompted
mode with full token streaming. Reasoning callback path verified by unit
tests + OpenRouter.

**Databricks Claude thinking visibility — settled by raw-wire capture
(2026-06-12).** Suspicion that we were dropping thinking content was checked
by dumping the raw route output (no penguiflow parsing): Opus 4.7 AND 4.8
with adaptive thinking DO return `{"type": "reasoning", "summary":
[{"type": "summary_text", "text": "", "signature": "<encrypted blob>"}]}`
blocks — the model thinks, but the route REDACTS the content: empty
`summary_text`, signature only (the signature exists for thinking-integrity
round-trips). Verified across: both endpoint paths (`/{model}/invocations`
AND `/chat/completions`), stream + non-stream, Opus 4.7 AND 4.8, effort
low/high/max — the signature blob scales ~9x with effort (1.9KB → 16.7KB)
while visible text stays zero, so effort is ruled out. There is no
`output_config.summary` knob (400 "Extra inputs are not permitted");
`thinking.type.enabled` stays rejected on 4.8. Our parser is correct;
nothing visible arrives **on the dev workspace** today.

**UNRESOLVED — production contradiction (2026-06-12):** a production 3.10
deployment reportedly shows thinking for Databricks Opus 4.7. Two surviving
hypotheses after the probe matrix above: (a) the prod workspace/serving
endpoint is configured differently and exposes thinking summaries; (b) what
prod renders as "thinking" is not native extended thinking at all but the
prompted-mode thought channel (the `_StreamingThoughtExtractor` streams a
JSON `thought` field, and `PlannerEvent.thought` rides step events).
Discriminate with ONE prod sample: the event/channel name carrying the
visible thinking text (native reasoning arrives as `llm_stream_chunk`
channel `thinking` sourced from provider reasoning deltas; extractor
thought arrives with `phase="observation"` from the JSON envelope), or a
raw response body from the prod workspace.
The provider now logs `databricks_reasoning_redacted_by_route` (once per
stream) when redacted blocks are detected, so this is observable instead of
re-investigated. Visible reasoning works wherever routes expose it (e.g.
OpenRouter reasoning deltas, live-verified).

### Phase 6 — Parity gate, per-family default flips, native shrink

- Full suite + live matrix (Databricks + OpenRouter) green under
  `transport="pydantic-ai"`, in **both planner modes** for tool-capable
  models.
- Flip defaults per model family where upstream is proven; mark OpenAI,
  Anthropic, Google, Bedrock, NIM native providers deprecated (≥2 minor
  releases); `litellm` extra removal scheduled; CHANGELOG migration notes.
- `tool_call_mode` default stays `"prompted"` in 3.11; a default flip for
  tool-capable models is a post-3.11 decision backed by production telemetry
  (the trace seam gives per-mode latency/cost/error evidence).

### Phase 7 (future, separate branches)

- Databricks native-provider retirement if upstream #4036 lands and proves
  out; liter-llm / any-llm re-evaluation (~2026-12); process-wide cooldown
  store; multimodal outputs as tools; `tool_call_mode` default flip.

---

## 11. Acceptance criteria (binding)

1. **Zero breaking changes:** the entire existing test suite passes unmodified
   on every phase; `JSONLLMClient` signature unchanged except documented
   widening; all `penguiflow.llm` / `penguiflow.planner` exports unchanged.
2. A production agent built on 3.10.0 (ReactPlanner + `llm="databricks-..."` +
   `llm_fallback=...`) runs identically with no code changes, with the new
   transport on and off.
   Current code status on 2026-06-15: built-in rate-limit fallback is now
   uniform across every PenguiFlow-managed client seam — native adapters
   (incl. native tool-calling and the pydantic-ai transport), the deprecated
   LiteLLM planner path, and `penguiflow.llm.LLMClient` (provider-level
   `FallbackProvider`, with chain-intersection mode selection and
   actual-model cost attribution). It is intentionally **not** applied to
   arbitrary custom `llm_client` implementations: combining `llm_fallback`
   with `llm_client=...` raises `ValueError`. The `DSPyLLMClient` path is
   deprecated and excluded from fallback.
3. New features are **opt-in**; defaults reproduce today's behavior exactly
   (including the structured-output prompt content when
   `final_response_model` is unset).
4. Every phase ships unit + negative-path tests and keeps coverage ≥ 84.5%.
5. Default flips (Phase 6) require the live validation matrix per family, not
   just unit tests.
6. Native tool-calling mode (Phase 5) ships only with the dual-mode
   conformance suite green: the planner test matrix passes identically in
   `"prompted"` and `"native"` modes for tool-capable models, including
   parallel + join parity.
7. Supply-chain hygiene: exact pins + hash-locked resolution for the
   transport; attestation check documented in CI notes.

---

## 12. Open questions

1. pydantic-ai 2.0 timeline and migration surface — how long is the `<2` pin
   sustainable, and does 2.x change `pydantic_ai.direct`? (Watch upstream;
   revisit at Phase 6.)
2. `opentelemetry-api` lands as a hard transitive dep via pydantic-ai-slim —
   acceptable, or does it need to stay behind the optional extra? (It aligns
   with the §6 tracing track, but penguiflow core today has no OTel dep.)
3. Per-model routing string mapping: penguiflow profile names
   (`databricks-claude-opus-4-7`) ↔ transport model identifiers — one mapping
   table in profiles, or adopt upstream naming for new models?
4. Flat `api_keys` in `ModelFallbackConfig` across mixed transports — the
   per-model key form (robustness plan open question) likely becomes
   necessary; resolve before Phase 6.
5. Should `DSPyLLMClient` get the structured-final-answer path too, or is it
   exempt? (It bypasses the native layer entirely.)
6. NIM: served through the transport's OpenAI-compatible path — does any
   production consumer rely on NIM-specific behavior (`reasoning_content`,
   `<think>` tags) that needs a profile correction?
7. Native-mode trajectory compaction: tool-role messages with `tool_call_id`
   pairing must survive (or be coherently dropped by) trajectory
   summarization — providers reject dangling tool results whose paired
   assistant tool_call was summarized away. Needs an explicit compaction rule
   before Phase 5 ships.
8. Databricks Claude tool calling + adaptive thinking interaction: verify in
   the Phase 5 live tests that `thinking` and `tools` compose on the
   Databricks route (Anthropic requires preserving thinking blocks across
   tool-use turns — confirm the Databricks OpenAI-compat surface handles
   this or gate native mode off for those models via profile).
   *(Resolved live 2026-06-12 — they compose; see Phase 5.)*
9. **Native-mode tool-turn preamble as a UX affordance — pending upstream UI
   team validation.** Phase 5's D5.3 went through two designs, and BOTH are
   legitimate UX positions:
   - **Shipped (D5.3-revised)**: the native prompt forbids text alongside
     tool calls → the answer streams token-by-token on the `answer` channel
     (29 vs 19 chunks against the prompted control, live). Tool turns are
     silent on the content channel.
   - **Rejected v1 (in git history, `b5e4246^`)**: allow the preamble
     ("I'll check that…") and stream it on the `thinking` channel as live
     status text, flushing the answer at turn end. Better perceived activity
     during tool turns; worse (flush-only) answer streaming.
   The machinery supports either choice — and a hybrid is feasible (keep the
   no-preamble prompt for the answer-streaming win, and have the UI render
   the existing `reasoning`/`thinking` channels or planner step events as
   status). If the UI team wants the preamble, the switch is a prompt-section
   toggle (e.g. a `native_preamble: bool` planner knob), NOT a redesign; the
   `superseded` marker contract on the answer channel already covers
   consumers either way. Decide before flipping any `tool_call_mode`
   defaults (Phase 7).

---

## 13. Side finding (live verification 2026-06-11) — pre-existing bug

While verifying any-llm against our Databricks workspace, Databricks rejected
`thinking={"type": "enabled", "budget_tokens": N}` for
**`databricks-claude-opus-4-8`** with: *"thinking.type.enabled" is not
supported for this model. Use "thinking.type.adaptive" and
"output_config.effort"*. PenguiFlow's own provider routes only
`databricks-claude-opus-4-7*` to adaptive thinking (`databricks.py:563`);
Opus 4.8 falls through to the budget branch (`databricks.py:576–584`) and
would hit the same 400 whenever a caller sets `reasoning_effort`. Fix
independently of this branch: widen the adaptive-thinking routing (and the
corresponding profile) to Opus 4.8, with a live regression test.
