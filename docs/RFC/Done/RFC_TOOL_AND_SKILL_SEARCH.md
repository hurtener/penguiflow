# RFC: Context Discovery & Learning in Penguiflow

## Tool Search + Deferred Tool Activation + Tool Examples + Local Skills (ACE-style, Local-First)

> **Status:** Draft
> **Version:** 0.3
> **Target Release:** Penguiflow v2.12
> **Created:** 2026-01-08
> **Last Updated:** 2026-01-28
> **Owner:** Penguiflow Core Team
> **License:** MIT 

## Changelog

| Version |       Date | Summary                                                                                                                                                                                                                 |
| ------- | ---------: | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0.1     | 2026-01-08 | Tool search + deferred loading + tool examples                                                                                                                                                                          |
| 0.2     | 2026-01-28 | Standalone spec + safe deferred activation model + score normalization + catalog fingerprinting + skills (ACE)                                                                                                          |
| 0.3     | 2026-01-28 | **Remove MCP external skills provider (out of scope). Add Skill Packs (developer-registered skills) + Skill Directory + `skill_search` / `skill_get` / `skill_list` so LLM can learn names and do targeted retrieval.** |

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Goals and Non-Goals](#goals-and-non-goals)
3. [Glossary](#glossary)
4. [Architecture Overview](#architecture-overview)
5. [Part 1: Tool Search and Deferred Tool Activation](#part-1-tool-search-and-deferred-tool-activation)
6. [Part 2: Tool Use Examples](#part-2-tool-use-examples)
7. [Part 3: Skills (Local-First ACE-style)](#part-3-skills-local-first-ace-style)
8. [Configuration Reference](#configuration-reference)
9. [Events and Observability](#events-and-observability)
10. [Implementation Plan and Phases](#implementation-plan-and-phases)
11. [File-by-File Work List](#file-by-file-work-list)
12. [Testing Plan](#testing-plan)
13. [Migration / Rollout](#migration--rollout)
14. [Acceptance Criteria](#acceptance-criteria)
15. [Open Questions / Future Work](#open-questions--future-work)
16. [Appendix A: Prompt Blocks](#appendix-a-prompt-blocks)
17. [Appendix B: Hashing and Token Estimation](#appendix-b-hashing-and-token-estimation)
18. [Appendix C: Redaction Rules](#appendix-c-redaction-rules)
19. [Appendix D: Skill Pack Format Reference](#appendix-d-skill-pack-format-reference)

---

## Executive Summary

This RFC introduces three **opt-in** capabilities that improve agent performance and reduce prompt bloat:

1. **Tool Search + Deferred Tool Activation**
   Keep only a small “always-visible” tool set in the prompt. The rest are **deferred** and discoverable via a built-in `tool_search` tool backed by a local SQLite FTS index. Deferred tools become usable only after **activation** into the run’s visible catalog.

2. **Tool Use Examples**
   Attach compact structured examples to tools to improve tool-call correctness without expanding schemas.

3. **Local-First Skills (ACE-style, MVP for v2.12)**
   A local skills system that supports:

   * **developer-registered Skill Packs** (curated skills shipped with apps),
   * **bounded pre-flight injection** of relevant skills into a strict token budget,
   * **skill discovery by name** via `skill_search` and `skill_get`,
   * an optional **bounded skill directory** (names + short descriptors only).

   Advanced learning features (auto-ingest, negative skills, clustering, typed decay, durability) are **deferred to v2.13**.
   See `docs/RFC/ToDo/RFC_SKILLS_LEARNING_V213.md`.

### Design commitments (locked-in)

* **No reconstruction of native tools from SQLite.** Deferred tools are activated into visibility; execution bindings remain in-memory.
* `tool_search` and skill discovery MUST respect **policy + per-run visibility** and must not leak denied capabilities.
* Any per-run catalog shaping MUST be concurrency-safe (session serialization or fork-per-request).
* Skills must be **scope-safe** and **depersonalized by default**.
* Tool activation is **run-scoped by default**. Session-scoped (“sticky”) activation is allowed only when explicitly configured and concurrency-safe.

---

## Goals and Non-Goals

### Goals

* Reduce tool catalog prompt tokens via deferred visibility.
* Provide robust tool discovery (`tool_search`) with stable scoring and safe filtering.
* Improve tool call accuracy via concise examples.
* Provide a local-first skills system that supports:

  * pre-flight retrieval (bounded tokens),
  * developer-provided skills via **Skill Packs**,
  * discovery by **skill name** and targeted fetch,
  * optional bounded directory exposure (names only).

### Non-Goals (v0.3)

* An external skills provider / MCP server for skills (explicitly removed from scope).
* Embedding-based tool search (tools use FTS/regex/exact in v0.3).
* Automatic example generation.
* Fully distributed multi-process skill coherence (local-first only; can be extended later).
* “Always list all skills in the prompt” by default (directory listing is optional and bounded).

Deferred to v2.13 (tracked in `docs/RFC/ToDo/RFC_SKILLS_LEARNING_V213.md`):

* Post-flight skill auto-ingest from trajectories (learning).
* Skill dedup/merge with lineage tracking.
* Negative skills and failure-driven skill creation.
* Clustering and cluster-driven expansion.
* Typed decay / staleness warnings.
* Auto-activating tools from skill retrieval.
* Durability: snapshots to ArtifactStore and/or ingestion as persisted tasks.

---

## Glossary

* **NodeSpec**: The internal tool specification including schemas + execution binding.
* **Execution registry**: All tools the planner could run (post ToolPolicy).
* **Visible catalog**: Tools visible to the LLM and resolvable in the current run.
* **ToolPolicy**: Hard allow/deny constraints applied at init or fork time.
* **ToolVisibilityPolicy**: Run-time visibility filter (RBAC/entitlements); hidden tools are also not executable.
* **Deferred tool**: Present in execution registry but hidden by default.
* **Activation**: Promoting a deferred tool into the visible catalog for a run/session.
* **Skill**: A reusable “trigger → steps (+ warnings)” recipe.
* **Skill Pack**: A developer-provided set of skills shipped as files (MD/YAML/JSON).
* **Skill name**: Stable identifier the LLM can reference for targeted lookup.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                 ReactPlanner                                  │
│                                                                              │
│  Pre-flight (sync)                 Run (ReAct)                                │
│  ┌───────────────────────┐        ┌─────────────────────────────────────┐     │
│  │ Skills.get_relevant    │        │ LLM loop + tools                     │     │
│  │ - pack + local store   │        │ - tool_search + activation           │     │
│  │ - budgeted injection   │        │ - skill_search / skill_get / list    │     │
│  └───────────┬───────────┘        └──────────────┬──────────────────────┘     │
│              │                                   │                            │
│              ▼                                   ▼                            │
│    Inject bounded skill context         Visible tool catalog                   │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│ Local stores (default)                                                        │
│  ┌────────────────────────────┐        ┌───────────────────────────────┐     │
│  │ ToolSearchCache (SQLite)    │        │ SkillStore (SQLite)            │     │
│  │ - FTS5 metadata index       │        │ - pack skills + FTS directory  │     │
│  └────────────────────────────┘        └───────────────────────────────┘     │
│                                                                              │
│ Deferred to v2.13: learning/ingest, negative skills, clustering, typed decay, │
│ and durability snapshots/tasks (see RFC_SKILLS_LEARNING_V213.md).             │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 1: Tool Search and Deferred Tool Activation

### 1.1 Loading Modes

```python
class ToolLoadingMode(str, Enum):
    ALWAYS = "always"      # Visible by default (current behavior)
    DEFERRED = "deferred"  # Hidden by default; discoverable via tool_search; activatable
```

Default remains `ALWAYS` to preserve existing behavior.

---

### 1.2 Execution Registry vs Visible Catalog

**Invariant (required):**

* All executable tools exist in an in-memory **execution registry** (post ToolPolicy).
* Each run uses a **visible catalog** subset for:

  * prompt tool listing, and
  * runtime tool resolution.

Deferred tools are not visible until activated; they are never “reconstructed from SQLite”.

---

### 1.3 Policy and Visibility Safety Requirements

**ToolPolicy (init-time):**

* Removes tools from the execution registry entirely.

**ToolVisibilityPolicy (run-time):**

* Shapes which tools are visible **and** executable during a run.

**Hard requirement:** `tool_search` MUST return only tools that are:

* present in the execution registry, AND
* visible/activatable under the run’s ToolVisibilityPolicy.

---

### 1.4 Tool Search Index (SQLite FTS)

* DB path: `.penguiflow/tool_cache.db`
* stores metadata only (name, description, tags, side effects, loading mode, hashes)
* includes catalog fingerprinting to support incremental updates.

(Reference schema omitted here for brevity; it must include tables `tools`, `tools_fts`, `tool_index_meta` and triggers to sync FTS.)

#### 1.4.1 FTS5 availability + fallback (required)

SQLite FTS5 is common but not guaranteed in every runtime.

* On init, the planner MUST detect whether FTS5 is available.
* If FTS5 is unavailable:

  * `tool_search` MUST still work using `regex` + `exact` against the same indexed fields, and
  * when the caller requests `search_type="fts"`, the implementation MUST fall back to `regex` and set `search_type` in the output to the **effective** search type used.

Rationale: fail-soft discovery is better than silently returning no tools.

---

### 1.5 Search Algorithms and Score Contract

`tool_search` supports: exact / regex / fts.

Score contract (required):

* `score` is a float in `[0, 1]` where higher is better.
* Scores are comparable within a single query response; do not treat them as global relevance.

Normalization (required):

* `exact`: `score = 1.0`
* `regex`: assign a bounded score based on match strength:

  * full tool-name match: `0.95`
  * tool-name prefix match: `0.90`
  * tool-name substring match: `0.85`
  * match only in description/tags: `0.75`

* `fts`: use SQLite FTS5 `bm25()` (lower is better) and transform:

  * `raw = bm25(fts)`
  * `score_raw = 1 / (1 + max(raw, 0.0))`  (bounded in `(0, 1]`)
  * min-max normalize across returned candidates:
    `score = (score_raw - min(score_raw)) / (max(score_raw) - min(score_raw))`
    (if `max == min`, set `score = 0.5`)
  * clamp to `[0, 1]`

Deterministic ordering (required):

Sort by the tuple:

1. `-score`
2. namespace preference rank (config-driven)
3. side_effects rank: `pure < read < write < external < stateful`
4. name length (shorter first)
5. name lexicographic (final tie-break)

---

### 1.6 `tool_search` Built-in Tool

#### Input

```json
{
  "type": "object",
  "required": ["query"],
  "properties": {
    "query": { "type": "string" },
    "search_type": { "type": "string", "enum": ["fts", "regex", "exact"], "default": "fts" },
    "limit": { "type": "integer", "minimum": 1, "maximum": 20, "default": 8 },
    "include_always_loaded": { "type": "boolean", "default": false }
  }
}
```

#### Output

```json
{
  "type": "object",
  "required": ["tools", "query", "search_type"],
  "properties": {
    "tools": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "description", "score", "match_type", "loading_mode"],
        "properties": {
          "name": { "type": "string" },
          "description": { "type": "string" },
          "score": { "type": "number" },
          "match_type": { "type": "string", "enum": ["exact", "regex", "fts"] },
          "loading_mode": { "type": "string", "enum": ["always", "deferred"] }
        }
      }
    }
  }
}
```

---

### 1.7 Activation / Resolution Rules

Recommended behavior: **activation-on-first-use**.

#### 1.7.1 Activation scope contract (required)

Activation MUST be explicitly scoped:

* `run` (default): activation state lives on the per-run runtime object. No cross-run mutation.
* `session` (optional): activation can persist across turns within a stable session.

If `activation_scope="session"`, the host app MUST provide a stable `tool_context["session_id"]` and MUST ensure per-session serialization.

When runtime sees a tool call for a tool that is not currently visible:

1. If tool exists in execution registry AND is activatable under current visibility → activate + execute.
2. Else → error (do not leak existence).

Emit a `tool_activated` event on success.

---

### 1.8 Concurrency Rules (Mandatory)

Per-run visibility shaping or activation must be concurrency-safe. Supported safe patterns:

* per-session serialization (recommended; requires stable `tool_context["session_id"]`)
* fork-per-request
* single-thread sequential usage (local use only)

Fail-closed requirement:

* If the host app cannot guarantee one of these patterns, it MUST NOT enable deferred activation.
* If `activation_scope="session"` but `tool_context["session_id"]` is missing, the planner MUST refuse to enable activation (configuration error).

---

## Part 2: Tool Use Examples

Tool examples are prompt aids that reduce ambiguity.

### 2.1 Example Schema

```python
class ToolInputExample(BaseModel):
    args: dict[str, Any]
    description: str | None = None
    tags: list[str] = []
```

### 2.2 Declaration Sources

Examples can be attached via:

* `@tool(examples=[...])`
* `NodeSpec.extra["examples"]`
* external tool source overrides (if applicable)

Precedence:

1. external overrides
2. NodeSpec.extra
3. decorator

### 2.3 Rendering Rules

* include at most `max_examples_per_tool` (default 3)
* prefer tagged: minimal → common → edge-case
* render as compact YAML-ish blocks under schema

---

## Part 3: Skills (Local-First ACE-style)

This part is the primary change in v0.3: **skills become discoverable by name** and **developers can register skill packs**.

### 3.1 Core UX: Broad Search vs Targeted Lookup

We explicitly support two LLM workflows:

#### Workflow A: Broad discovery (“I need help with X”)

* pre-flight: `Skills.get_relevant(task="...")` injects best context automatically
* in-flight: LLM can call `skill_search(query="...")` to find named skills

#### Workflow B: Targeted lookup (“I know the skill name”)

* LLM calls `skill_get(names=[...])` to fetch exact skill content and inject it

This mirrors `tool_search` + “call tool by name”.

---

### 3.2 Skill Name and Identity

Every skill MUST have:

* `id`: opaque unique ID (`sk_...`) used for storage
* `name`: stable identifier used for discovery and targeted retrieval
* `title`: human-friendly short label (optional but recommended)

#### Naming conventions

To avoid collisions and to help the LLM:

* **Skill Pack skills:** `pack.<pack_name>.<skill_slug>`
* **Learned skills (reserved for v2.13):** `learned.<task_type>.<short_hash_or_slug>`

Examples:

* `pack.core.browser_auth.login_basic`
* `pack.core.api.pagination.cursor_loop`
* `learned.browser.3f2a1c`
* `learned.code.parse_iso_dates`

**Rule:** `name` must be unique within the SkillStore scope (project/tenant/global).

---

### 3.3 Skill Provider Interface (Local Only in v0.3)

We keep a provider abstraction for clean architecture, but only ship a **LocalSkillProvider** in v2.12.

The MVP provider surface is intentionally small: retrieval + name discovery.
Learning/ingest and feedback APIs are deferred to v2.13 (see `docs/RFC/ToDo/RFC_SKILLS_LEARNING_V213.md`).

```python
class SkillProvider(Protocol):
    async def get_relevant(self, query: SkillQuery, *, tool_context: dict) -> RetrievalResponse: ...
    async def search(self, query: "SkillSearchQuery", *, tool_context: dict) -> "SkillSearchResponse": ...
    async def get_by_name(self, names: list[str], *, tool_context: dict) -> list["SkillResultDetailed"]: ...
    async def list(self, req: "SkillListRequest", *, tool_context: dict) -> "SkillListResponse": ...
```

---

### 3.4 Skill Store (SQLite) Schema (Local)

Default location:

* `.penguiflow/skills.db`

Minimum required tables:

* `skills` (core)
* `skills_fts` (recommended; supports `skill_search` when FTS5 is available)

Deferred to v2.13:

* `skill_clusters`, `cluster_members`
* `skill_feedback`
* negative skills + merge lineage

#### Core schema (reference)

```sql
CREATE TABLE IF NOT EXISTS skills (
  id TEXT PRIMARY KEY,

  -- scope
  scope_mode TEXT NOT NULL,            -- 'project'|'tenant'|'global'
  scope_tenant_id TEXT,
  scope_project_id TEXT,

  -- identity
  name TEXT NOT NULL UNIQUE,
  title TEXT,
  description TEXT,

  -- retrieval
  trigger TEXT NOT NULL,
  task_type TEXT NOT NULL,             -- browser|api|code|domain|unknown
  tags TEXT NOT NULL,                  -- JSON array

  -- content
  steps TEXT NOT NULL,                 -- JSON array
  preconditions TEXT NOT NULL,         -- JSON array
  failure_modes TEXT NOT NULL,         -- JSON array

  -- origin
  origin TEXT NOT NULL,                -- 'pack'|'learned' (learned reserved for v2.13)
  origin_ref TEXT,                     -- pack name/version or trace ref
  content_hash TEXT NOT NULL,          -- sha256 of normalized skill content

  -- stats (used for directory selection)
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  last_used INTEGER NOT NULL,
  use_count INTEGER NOT NULL,
);

CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts USING fts5(
  name,
  title,
  trigger,
  description,
  tags,
  content='skills',
  content_rowid='rowid',
  tokenize='porter unicode61'
);

CREATE INDEX IF NOT EXISTS idx_skills_scope ON skills(scope_mode, scope_tenant_id, scope_project_id);
CREATE INDEX IF NOT EXISTS idx_skills_task_type ON skills(task_type);
CREATE INDEX IF NOT EXISTS idx_skills_origin ON skills(origin);
```

FTS5 availability note:

* If `skills_fts` cannot be created (FTS5 missing), `skill_search` MUST fall back to `regex` + `exact` and MUST report the effective `search_type` used.

---

### 3.5 Developer Registration: Skill Packs

Downstream developers must be able to ship skills intentionally (curated), without relying on automated learning.

#### Skill Pack definition

A **Skill Pack** is a folder or package resource containing skill files.

Supported file formats (v0.3):

* Markdown with YAML frontmatter (`*.skill.md`)
* YAML (`*.skill.yaml` / `*.skill.yml`)
* JSON (`*.skill.json`)
* JSONL (`*.skill.jsonl`) (each line one skill object)

**Required:** at least one of these formats.

#### SkillPackConfig

```python
class SkillPackConfig(BaseModel):
    name: str                       # pack name; used in skill naming
    path: str                       # filesystem path (or package resource path)
    format: Literal["md", "yaml", "json", "jsonl"] | None = None  # auto-detect if None
    scope_mode: Literal["project", "tenant", "global"] = "project"
    enabled: bool = True

    # whether to overwrite existing pack skills with same name if content changed
    update_existing_pack_skills: bool = True

    # optional pinning for directory exposure
    pinned_skill_names: list[str] = []
```

#### Loading behavior (required)

On planner init (or explicit call):

* parse pack files
* validate schema
* compute a `pack_content_hash` (sha256 of normalized fields)
* for each skill:

  * ensure `name` is set; if absent, generate `pack.<pack_name>.<slug(file)>`
  * upsert into store if:

    * new skill, or
    * existing skill is `origin="pack"` and content changed and `update_existing_pack_skills=True`
  * never overwrite learned skills

**Rationale:** pack skills are curated and should update cleanly across versions.

#### Minimum skill fields for packs

Required:

* `name` (or derivable)
* `trigger`
* `steps`

Recommended:

* `title`
* `task_type`
* `failure_modes`
* `tags`
* `preconditions`

Full schema in Appendix D.

---

### 3.6 Skill Directory Exposure (Names in Prompt)

This addresses your request: “LLM can have the names of skills and tools.”

**We make this optional and bounded.**

#### SkillsDirectoryConfig

```python
class SkillsDirectoryConfig(BaseModel):
    enabled: bool = True
    max_entries: int = 30  # hard cap in prompt
    include_fields: list[Literal["name", "title", "trigger", "task_type"]] = ["name", "title", "trigger"]
    selection_strategy: Literal["pinned_then_recent", "pinned_then_top"] = "pinned_then_recent"
```

#### Directory selection algorithm (default)

1. include pinned names from enabled skill packs
2. include most recently used skills (highest `last_used`) until cap
3. exclude disabled skills (if the store supports status fields; v2.12 MVP treats all pack skills as active)

Rendered in prompt as:

```
<skill_directory>
Known skills (use skill_get by name, or skill_search for discovery):
- pack.core.browser_auth.login_basic — "Login with username/password"
- pack.core.api.pagination.cursor_loop — "Handle cursor pagination"
</skill_directory>
```

**Important:** This is a *directory*, not the full content.

---

### 3.7 Skill Discovery Tools (Built-in)

When `skills.enabled=True`, expose these built-in tools (similar to `tool_search`):

* `skill_search`: broad search → returns names
* `skill_get`: targeted fetch by name → returns full content
* `skill_list`: controlled listing (paged / filtered) → returns names

Deferred to v2.13:

* `skill_report_outcome` (feedback loop)
* `skill_report_failure` / negative skill creation

#### `skill_search`

**Input**

```json
{
  "type": "object",
  "required": ["query"],
  "properties": {
    "query": { "type": "string" },
    "search_type": { "type": "string", "enum": ["fts", "regex", "exact"], "default": "fts" },
    "limit": { "type": "integer", "minimum": 1, "maximum": 20, "default": 8 },
    "task_type": { "type": "string", "enum": ["browser", "api", "code", "domain", "unknown"] }
  }
}
```

**Output**

```json
{
  "type": "object",
  "required": ["skills", "query", "search_type"],
  "properties": {
    "skills": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "trigger", "score"],
        "properties": {
          "name": { "type": "string" },
          "title": { "type": "string" },
           "trigger": { "type": "string" },
           "task_type": { "type": "string" },
           "score": { "type": "number" }
         }
       }
     }
   }
}
```

Score contract mirrors `tool_search`: normalized `[0,1]`, with the same FTS fallback behavior.

#### `skill_get`

**Input**

```json
{
  "type": "object",
  "required": ["names"],
  "properties": {
    "names": { "type": "array", "items": { "type": "string" }, "minItems": 1, "maxItems": 10 },
    "format": { "type": "string", "enum": ["raw", "injection"], "default": "injection" },
    "max_tokens": { "type": "integer", "minimum": 200, "maximum": 6000, "default": 1500 }
  }
}
```

**Output**

```json
{
  "type": "object",
  "required": ["skills", "formatted_context"],
  "properties": {
    "skills": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "trigger", "steps"],
        "properties": {
          "name": { "type": "string" },
          "title": { "type": "string" },
          "trigger": { "type": "string" },
          "steps": { "type": "array", "items": { "type": "string" } },
          "preconditions": { "type": "array", "items": { "type": "string" } },
          "failure_modes": { "type": "array", "items": { "type": "string" } }
        }
      }
    },
    "formatted_context": { "type": "string" }
  }
}
```

`formatted_context` should be token-budgeted and ready to inject.

#### `skill_list`

This is intentionally more structured than “dump all skills”.

**Input**: page + filters (task_type, origin, scope)
**Output**: list of names + short triggers.

---

### 3.8 Retrieval Pipeline (Pre-flight `get_relevant`)

The pre-flight injector remains the main path for most runs.

Required steps:

1. choose a fixed `top_k` + token budget from `SkillsConfig` (MVP; no complexity classifier)
2. retrieve candidates (FTS first; fall back to regex/exact if FTS5 is unavailable)
3. enforce scope and status filtering (no cross-scope leakage)
4. compress to budget (MVP: structured compression + truncation; optional LLM summarizer later)
5. format into an injection block suitable for system prompt inclusion

Deferred to v2.13: negative skills, clustering, typed decay, and effectiveness-based reranking.

---

### 3.9 Ingestion Pipeline (Post-flight Async)

Deferred to v2.13.

v2.12 MVP intentionally does not perform post-flight learning/ingestion.
See `docs/RFC/ToDo/RFC_SKILLS_LEARNING_V213.md`.

---

### 3.10 Typed Decay

Deferred to v2.13.

Typed decay is part of skills learning/ranking and will be specified in `docs/RFC/ToDo/RFC_SKILLS_LEARNING_V213.md`.

---

### 3.11 Scope and Privacy

Default scope mode: **project**.

Skills are retrievable only inside their scope. Downstream apps should supply scope fields via `tool_context`:

* `tenant_id`
* `project_id`
* `session_id` (for concurrency safety)

Depersonalization is on by default (Appendix C).

Note: In v2.12, skills primarily come from curated Skill Packs. Redaction still applies to injected/returned skill content as a safety net; learned-skill depersonalization is deferred to v2.13.

---

### 3.12 Skills ↔ Tools Synergy

Skills may mention tools that are deferred.

Tool reference safety (required):

* Skill content rendered into prompts (pre-flight injection or `skill_get`) MUST NOT leak denied or non-activatable tool names.
* If a skill references a tool that is not visible/activatable under the current run policies, the implementation MUST either:

  * omit the tool reference entirely, or
  * rewrite it into generic guidance (e.g., “use tool_search for the needed capability”).

We support:

1. **Hint-only (default)**
   Skills text says: “If tool X is not visible, use tool_search for ‘X’.”

2. **Auto-activate (optional)**
   Deferred to v2.13. See `docs/RFC/ToDo/RFC_SKILLS_LEARNING_V213.md`.

---

## Configuration Reference

### ToolSearchConfig

```python
class ToolSearchConfig(BaseModel):
    enabled: bool = False
    cache_dir: str = ".penguiflow"
    default_loading_mode: ToolLoadingMode = ToolLoadingMode.ALWAYS
    always_loaded_patterns: list[str] = ["tasks.*", "tool_search", "finish"]
    activation_scope: Literal["run", "session"] = "run"
    preferred_namespaces: list[str] = []
    fts_fallback_to_regex: bool = True
    enable_incremental_index: bool = True
    rebuild_cache_on_init: bool = False
    max_search_results: int = 10
```

### ToolExamplesConfig

```python
class ToolExamplesConfig(BaseModel):
    enabled: bool = True
    max_examples_per_tool: int = 3
    include_descriptions: bool = True
```

### SkillsConfig (v2.12 MVP)

```python
class SkillsConfig(BaseModel):
    enabled: bool = False

    # storage
    cache_dir: str = ".penguiflow"  # stores skills.db

    # retrieval / injection
    max_tokens: int = 2000
    summarize: bool = False  # MVP default: deterministic structured compression

    # privacy/scope
    redact_pii: bool = True
    scope_mode: Literal["project", "tenant", "global"] = "project"

    # skill packs
    skill_packs: list[SkillPackConfig] = []
    directory: SkillsDirectoryConfig = SkillsDirectoryConfig()

    # discovery behavior
    fts_fallback_to_regex: bool = True
```

---

## Events and Observability

Emit structured events:

### Tools

* `tool_search_query` `{query, requested_search_type, effective_search_type, results_count}`
* `tool_activated` `{tool_name, activation_scope, source, reason}`
* `tool_activation_denied` `{reason}`

### Skills

* `skill_pack_loaded` `{pack_name, skill_count, updated_count}`
* `skill_directory_rendered` `{count}`
* `skills_retrieved` `{skill_count, top_k_used, raw_tokens_est, final_tokens_est, was_summarized}`
* `skill_search_query` `{query, requested_search_type, effective_search_type, results_count}`
* `skill_get` `{names, returned_count, max_tokens, final_tokens_est}`
* `skill_list` `{filters, returned_count}`

Deferred to v2.13: ingestion/learning and feedback events.

---

## Implementation Plan and Phases

### Phase 1 — Tool Search + Deferred Activation (Core)

Deliverable:

* `tool_search` works end-to-end with safe filtering and activation-on-first-use.

Tasks:

1. Tool loading modes (`ALWAYS` / `DEFERRED`)
2. ToolSearchCache (SQLite FTS) + fingerprinting
3. `tool_search` built-in tool
4. Visible catalog separation per run
5. Activation on first use + events
6. Prompt guidance block

### Phase 2 — Tool Examples

Deliverable:

* examples can be declared, rendered, and participate in cache invalidation.

Tasks:

1. Add examples to tool decorator + NodeSpec metadata
2. Prompt rendering rules + max cap
3. Add examples to tool record hashing

### Phase 3 — Skills Local Provider + Skill Packs + Skill Discovery Tools

Deliverable:

* LocalSkillProvider supports:

  * pre-flight `get_relevant`,
  * Skill Packs import,
  * `skill_search` + `skill_get` + `skill_list`,
  * optional directory listing in prompt.

Tasks:

1. skills.db schema + LocalSkillProvider CRUD
2. pack loader (`SkillPackLoader`) + validation + upsert rules
3. implement built-in tools:

   * `skill_search`
   * `skill_get`
   * `skill_list`
4. retrieval pipeline + formatting + budget enforcement (MVP: structured compression)
5. directory rendering (bounded)
6. safety gating: scope enforcement + tool reference redaction

Deferred to v2.13: ingestion/learning, feedback tools, clustering, decay, and durability.
See `docs/RFC/ToDo/RFC_SKILLS_LEARNING_V213.md`.

---

## File-by-File Work List

### New files

* `penguiflow/planner/tool_search_cache.py`

* `penguiflow/planner/tool_search_tool.py`

* `penguiflow/skills/models.py`

* `penguiflow/skills/provider.py`

* `penguiflow/skills/local_store.py`

* `penguiflow/skills/pack_loader.py`   ✅ new in v0.3

* `penguiflow/skills/redaction.py`

* `penguiflow/skills/tools/skill_search_tool.py` ✅ new in v0.3

* `penguiflow/skills/tools/skill_get_tool.py` ✅ new in v0.3

* `penguiflow/skills/tools/skill_list_tool.py` ✅ new in v0.3

### Modified files (typical)

* `penguiflow/catalog.py` (loading_mode + examples)
* `penguiflow/planner/models.py` (configs)
* `penguiflow/planner/prompts.py` (render tool catalog + skill directory + prompt blocks)
* `penguiflow/planner/react.py` (wire features pre-flight)
* `penguiflow/planner/react_runtime.py` (activation-on-first-use)
* `tests/...` new unit/integration suites for both subsystems

---

## Testing Plan

### Unit

**Tools**

* cache init/migrations
* fingerprint incremental update
* scoring range `[0,1]`
* policy + visibility filtering
* activation-on-first-use

**Skills**

* pack loading:

  * file parsing + validation
  * upsert/update behavior for pack-origin
  * never overwrite learned skills
* `skill_search`:

  * returns names, stable score in `[0,1]`
* `skill_get`:

  * fetch by name, injection formatting, budget enforcement
* directory rendering:

  * bounded max entries
  * pinned + recent selection
* safety:

  * scope filtering
  * tool reference redaction

### Integration

* end-to-end run:

  * tool directory reduced with deferred tools
  * tool_search used → tool activated → executed
  * skill directory shows names
  * skill_search → skill_get used mid-run

### Performance

* `tool_search` p95 < 10ms at ~200 tools
* `skill_search` p95 < 10ms at ~500 skills (local sqlite)
* directory prompt addition bounded by `max_entries`

---

## Migration / Rollout

Defaults are off:

* no tool_search
* no skill system
* no directory

Recommended rollout:

1. enable tool_search but keep default loading ALWAYS → observe
2. switch to default DEFERRED with allowlisted always-loaded patterns
3. enable skills + Skill Packs (read-only) → observe prompt budget impact
4. optionally enable directory exposure (bounded)
5. (v2.13) enable learning/ingestion features

---

## Acceptance Criteria

### Tools

* deferred tools hidden by default
* `tool_search` returns only allowed + visible/activatable tools
* activation-on-first-use works
* concurrency safety documented and validated

### Skills

* devs can register Skill Packs and see them loaded into local store
* LLM can see skill names (directory) and can:

  * broad search via `skill_search`
  * targeted fetch via `skill_get`
* pre-flight injection stays within budget
* scope filtering prevents cross-tenant/project leakage
* skill rendering does not leak denied tool names (tool reference redaction)

---

## Open Questions / Future Work

Tracked for v2.13 in `docs/RFC/ToDo/RFC_SKILLS_LEARNING_V213.md`:

* skill learning/ingestion, dedup/merge, negative skills, clustering, typed decay, durability

* optional embedding index for skills (local) to improve semantic retrieval
* automatic cluster creation from co-occurrence mining
* external shared skills provider (MCP) (explicitly out of scope for v0.3)
* verification workflows for stale browser skills

---

## Appendix A: Prompt Blocks

### Tool discovery guidance

```
<tool_discovery>
You can discover additional tools using `tool_search`.
- Describe the capability you need.
- Use the returned tool name to call it.
Only tools permitted for this request will appear.
</tool_discovery>
```

### Skill discovery guidance

```
<skill_discovery>
You can discover and fetch skills:
- Use `skill_search` to find skills by capability (returns skill names).
- Use `skill_get` with a skill name for the full playbook.
</skill_discovery>
```

### Skill directory (optional)

```
<skill_directory>
Known skills (use skill_get by name; use skill_search for discovery):
- pack.core.browser_auth.login_basic — Login with username/password
- pack.core.browser_forms.date_picker_safe — Fill date picker safely
</skill_directory>
```

---

## Appendix B: Hashing and Token Estimation

### Tool record hash

`tool_record_hash = sha256(json.dumps(canonical_tool_metadata, sort_keys=True))`

### Skill content_hash (pack updates)

`content_hash = sha256(json.dumps(canonical_skill_fields, sort_keys=True))`

Used to detect changes in pack-provided skills and update the local store.

Deferred to v2.13: source_hash-based dedup/merge across learned skills.

### Token estimation (MVP acceptable)

* estimate tokens as `len(text) / 4`
* enforce budgets by summarization or structured compression

---

## Appendix C: Redaction Rules

Minimum patterns (recommended):

* emails → `[REDACTED_EMAIL]`
* phone numbers → `[REDACTED_PHONE]`
* bearer tokens / api keys → `[REDACTED_TOKEN]`
* URLs with user-specific query params → strip query portion where possible

Use conservative redaction to preserve procedural structure.

---

## Appendix D: Skill Pack Format Reference

### Option 1: Markdown with YAML frontmatter (`*.skill.md`)

```markdown
---
name: pack.core.browser_auth.login_basic
title: Basic login (username/password)
description: Standard website login without SSO.
task_type: browser
tags: [auth, login, browser]
trigger: Log into a website using username and password.
preconditions:
  - Credentials are available in tool_context or secret store.
steps:
  - Navigate to the login page.
  - Fill the username/email field.
  - Fill the password field.
  - Click “Sign in”.
  - Verify login succeeded by checking for a user avatar or logout button.
failure_modes:
  - Login form is inside an iframe.
  - CAPTCHA or bot detection blocks interaction.
  - 2FA prompt appears (use the 2FA skill).
cluster: Browser Authentication
tools:
  - tool: tool_search
    note: If browser tools are not visible, discover them.
---
```

Note:

* Fields like `cluster` and `tools` are accepted as metadata.
* Cluster expansion and any tool auto-activation are deferred to v2.13.
* In v2.12, any tool references rendered into prompts must be gated to avoid leaking denied/non-activatable tool names (see Section 3.12).

### Option 2: YAML (`*.skill.yaml`)

```yaml
name: pack.core.api.pagination.cursor_loop
title: Cursor pagination loop
task_type: api
tags: [pagination, cursor]
trigger: Retrieve all items from a cursor-paginated API.
steps:
  - Call the endpoint with an initial cursor (or none).
  - Append results to an accumulator list.
  - Read the next cursor/token from the response.
  - Repeat until the cursor/token is empty or missing.
failure_modes:
  - Rate limits require backoff between pages.
  - Cursor is nested in a sub-field.
```

### Validation rules (required)

* `trigger` must be non-empty
* `steps` must be a non-empty list of strings
* `name` must be unique (or generated deterministically)
* unknown fields must either:

  * be ignored safely, or
  * be stored in an `extra` JSON column (recommended)

---

### Notes on prior drafts used during authoring (not required for implementation)

Historical drafts and internal specs that informed v0.3:
