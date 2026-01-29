# RFC: Skills Learning, Evolution, and Durability (Tentative v2.13)

> **Status:** Draft
> **Version:** 0.1
> **Target Release:** Penguiflow v2.13 (tentative)
> **Created:** 2026-01-28
> **Owner:** Penguiflow Core Team
> **License:** MIT

This RFC defines the **advanced skills** features explicitly deferred from `docs/RFC/ToDo/RFC_TOOL_AND_SKILL_SEARCH.md`.

It builds on the v2.12 MVP skills system:

* Skill Packs (developer-curated skills)
* Local SkillStore (SQLite)
* pre-flight bounded injection (`Skills.get_relevant`)
* in-flight discovery (`skill_search`, `skill_get`, `skill_list`)
* optional bounded directory (names only)

v2.13 adds **learning, ranking, and durability**.

---

## Executive Summary

Skills become more than a curated directory:

1. **Post-flight learning (auto-ingest)**
   Extract new candidate skills from successful trajectories (or failures), depersonalize/redact, and persist them asynchronously.

2. **Skill evolution**
   Deduplicate and merge near-duplicate skills, track lineage, and support negative skills that encode “don’t do this” warnings.

3. **Long-term quality controls**
   Typed decay/staleness for time-sensitive skills (especially browser automation), feedback loops (helpful/harmful), and optional clustering.

4. **Durability (optional)**
   Snapshot/export/import the skill store and optionally persist ingestion jobs via StateStore tasks.

---

## Goals

* Learn reusable skills from real usage without blocking user responses.
* Keep the skill store clean (dedup/merge), safe (redaction, scoped), and useful (ranking/decay).
* Support “warnings as skills” (negative skills) to reduce repeated failures.
* Provide optional durability hooks without forcing distributed coordination.

## Non-Goals (v2.13)

* A shared remote skills service as the default path (may be future work).
* Mandatory embeddings for skills retrieval (optional only).

---

## Part 1: Post-flight Auto-Ingest (Learning)

### 1.1 Ingestion trigger sources

Candidate extraction may be triggered by:

* `PlannerFinish` (success)
* certain failure classes (e.g., repeated tool errors with a stable fix)
* explicit developer calls (host app provides a SkillSubmission)

### 1.2 Ingestion is async + non-blocking

Hard requirement: ingestion MUST NOT block the main answer.

Two modes:

* `fire_and_forget` (default): background `asyncio.create_task()` best-effort
* `tasks` (optional): persisted ingestion jobs via StateStore task primitives (feature-detected)

### 1.3 Depersonalization and redaction

Learning MUST apply conservative redaction rules (see Appendix C in the v2.12 RFC) and must default to **depersonalized** skill text.

---

## Part 2: Dedup/Merge + Lineage

### 2.1 Canonical hashes

Introduce a stable `source_hash` used to detect near-duplicates:

`source_hash = sha256(trigger_norm + "\n" + "\n".join(steps_norm) + "\n" + task_type)`

### 2.2 Merge policy

When a new candidate matches an existing skill above a threshold:

* merge into the existing skill (update steps, preconditions, failure modes)
* preserve warnings and safety constraints
* record lineage (`merged_from`, `source_hashes`, `origin_ref`)
* never overwrite pack skills; learned skills can merge into learned skills

---

## Part 3: Negative Skills

Negative skills encode “avoid this approach” patterns.

Creation sources:

* repeated failures that share a stable signature
* explicit `skill_report_failure`

Retrieval behavior:

* injected alongside positive skills when relevant
* clearly labeled as warnings

---

## Part 4: Clusters (Optional)

Clusters group skills by theme (e.g., “Browser Authentication”).

v2.13 supports:

* pack-defined clusters (explicit)
* optional automatic clustering from co-occurrence mining (future extension)
* cluster expansion during retrieval (bounded)

---

## Part 5: Typed Decay + Staleness Warnings

Decay half-life by task type:

* browser: 7 days
* api: 90 days
* code: 180 days
* domain: 365 days
* unknown: 30 days

`decay = 2 ** (-(days_since_last_used / half_life_days))`

Decay influences ranking and may add staleness warnings to injected context.

---

## Part 6: Skills ↔ Tools Synergy (Optional)

When enabled, skill retrieval may return `suggested_tools: [tool_name...]`.

Activation constraints:

* must respect ToolPolicy + ToolVisibilityPolicy
* must respect activation scope and concurrency safety
* must cap auto-activations per run (default 0)

---

## Part 7: Durability (Optional)

Provide optional durability hooks:

* snapshot/export/import skills.db via ArtifactStore (if available)
* ingestion jobs persisted via StateStore tasks (if available)

The default remains local-first without distributed coherence.

---

## Configuration (Tentative)

This RFC extends `SkillsConfig` with a learning-focused block (name TBD):

```python
class SkillsLearningConfig(BaseModel):
    enabled: bool = False

    # ingestion
    auto_ingest: bool = True
    ingest_mode: Literal["fire_and_forget", "tasks"] = "fire_and_forget"
    auto_create_negative_on_failure: bool = True

    # dedup/merge
    enable_merge: bool = True
    merge_threshold: float = 0.90

    # retrieval enrichment
    include_negative: bool = True
    expand_clusters: bool = True
    typed_decay: bool = True

    # tool synergy
    auto_activate_tools: bool = False
    max_auto_activated_tools: int = 3
```

---

## Events (Tentative)

* `skill_ingest_queued` `{mode}`
* `skill_ingested` `{skill_id, name, action}`
* `skill_merged` `{into_skill_id, merged_skill_id}`
* `skill_negative_created` `{skill_id, name}`
* `skill_decay_applied` `{skill_id, decay, task_type}`
* `skills_cluster_expanded` `{cluster_id, added_count}`
* `skills_suggested_tools` `{tool_names, activated_count}`

---

## Implementation Plan (High-level)

1. Add SkillStore columns + tables required for lineage/feedback/clusters.
2. Implement ingestion extractors (trajectory → candidate skill).
3. Implement redaction + depersonalization hardening.
4. Implement merge/dedup, negative skills, and typed decay.
5. Add optional tool synergy.
6. Add optional durability adapters.

---

## Open Questions

* What is the minimum safe schema for “learned” skills to avoid accidental secret retention?
* How should we gate learning in multi-tenant apps (opt-in per tenant/project)?
* Should feedback be exposed as tools to the LLM, or only to host apps/UIs?
