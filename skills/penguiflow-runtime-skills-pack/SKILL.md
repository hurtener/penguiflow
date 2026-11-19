---
name: penguiflow-runtime-skills-pack
description: Author "skill packs" — playbooks the PenguiFlow ReactPlanner retrieves at runtime via `skill_search`/`skill_get`/`skill_list` (NOT the same as the SKILL.md agent-tool descriptors in the project's `skills/` directory; these are runtime artifacts stored in a SQLite skill store). Configure `SkillsConfig(enabled=True, skill_packs=[SkillPackConfig(...)], top_k=..., redact_pii=True)`, pick a pack format (`*.skill.md`/`.yaml`/`.json`/`.jsonl`), declare applicability via `required_tool_names`/`required_namespaces`/`required_tags`, scope by tenant/project, compose with a runtime `skills_provider`/`skills_provider_factory`, optionally enable non-persisting `skill_propose` drafting and `SkillsDirectoryConfig` surfacing. Use when a user says "author a skill pack", "playbooks for my planner", "skill_search", "runtime skills provider", or names `SkillsConfig`.
---

# Authoring Runtime Skill Packs for the Planner

## Important: naming collision (read first)

This skill is about **planner-consumed skill packs** — curated playbooks that the LLM retrieves at runtime via `skill_search` / `skill_get` / `skill_list` tools. These are PenguiFlow runtime artifacts stored in a SQLite skill store and rendered into prompts.

These are **NOT** the same as the `SKILL.md` files inside the project's `skills/` directory. Those are **agent-tool skill descriptors** following the agentskills.io / Anthropic Skills bundle format (used by the AI tooling that reads this very file). The two share the word "skill" but live in completely different layers:

| Layer | What it is | Format | Audience |
|---|---|---|---|
| Agent skills (this directory) | Tool descriptors for AI assistants | `SKILL.md` + frontmatter + references/* + agents/openai.yaml | The AI assistant reading the skill |
| Planner skill packs (this skill) | Playbooks the ReactPlanner retrieves | `*.skill.md`/`.skill.yaml`/`.skill.json`/`.skill.jsonl` | The LLM driving the planner at runtime |

Don't try to reuse SKILL.md files as planner skill packs (or vice versa). Different schemas, different consumers.

## When to use
- Codify operational procedures ("how we handle an incident") as planner-retrievable playbooks.
- Standardize agent behavior across teams without copy-pasting prompt text.
- Surface domain conventions (titles, layouts, when to use which chart) to the planner.
- Author tenant- or persona-specific guidance gated by applicability.

## When NOT to use
- Long-term memory or knowledge base → not the right tool; build retrieval separately.
- Tools — skills are documents, they don't execute.
- Frequently-changing data — packs are versioned like code, not live data.
- Trying to override planner configuration knobs — that's [[penguiflow-reactplanner-config]].

## Hard boundaries
This skill covers **authoring and configuring** runtime skill packs and the providers that supply them. Planner knobs (always-visible tools, ToolPolicy, etc.) live in `penguiflow-reactplanner-config`. Rich-output integration with skills lives in `penguiflow-rich-output`.

## Workflow

### 1) Decide static packs vs runtime provider
| Choice | Use when |
|---|---|
| **Static packs** (`skill_packs=[...]`) | In-repo curated playbooks, versioned with code. |
| **Runtime provider** (`skills_provider=...`) | Per-user personas, dynamic content from a host service. |
| **Both** | Static packs as the baseline, runtime overlays for tenant-specific guidance. Runtime wins on name collision. |

### 2) Author a pack
Pick a format: `*.skill.md` (Markdown + YAML frontmatter, most readable), `*.skill.yaml`/`.yml`, `*.skill.json`, `*.skill.jsonl` (bulk import). Required frontmatter: `trigger` (when to use), `steps` (the playbook). Recommended: `name`, `title`, `task_type`, applicability (`required_tool_names`/`required_namespaces`/`required_tags`), `failure_modes`. Full schema and a runnable Markdown example in `references/pack-formats.md`.

### 3) Wire `SkillsConfig`
```python
SkillsConfig(
    enabled=True,                            # default False; must opt in
    cache_dir=".penguiflow",                 # library default; SQLite store location
    skill_packs=[SkillPackConfig(name="ops", path="skills/packs/ops")],
    top_k=6,                                 # default
    max_tokens=2000,                         # default (range 200-10000)
    summarize=False,                         # default
    redact_pii=True,                         # default
)
```
Planner gets `skill_search`/`skill_get`/`skill_list` always-visible plus pre-flight injection of `top_k` relevant skills.

### 4) (Optional) Wire a runtime provider
```python
class TenantSkillProvider(SkillProvider):
    # Six required methods — typed queries + keyword-only tool_context/capability_context.
    async def get_relevant(self, query, *, tool_context, capability_context=None): ...
    async def search(self, query, *, tool_context, capability_context=None): ...
    async def get_by_name(self, names, *, tool_context, capability_context=None): ...
    async def list(self, req, *, tool_context, capability_context=None): ...
    async def directory(self, config, *, tool_context, capability_context=None): ...
    async def format_for_injection(self, skills, *, max_tokens): ...

planner = ReactPlanner(
    ...,
    skills_provider=TenantSkillProvider(),      # or skills_provider_factory=Callable[[SkillsConfig], SkillProvider]
    skills=SkillsConfig(enabled=True, skill_packs=[...]),  # static packs compose
)
```

Runtime providers are Python-only — use for per-tenant skills loaded from a host service, user personalization, or composition with the host app's permission model. Static packs and runtime providers compose; runtime wins on name collision.

### 5) Scope per tenant/project
Pass `tenant_id`, `project_id`, `user_id` in `tool_context` — the provider scopes by these. Without them, scope filtering is permissive (fine for single-tenant, dangerous for multi-tenant).

### 6) Use applicability metadata
Per-skill `required_tool_names` / `required_namespaces` / `required_tags`. The planner checks against the request's allowed capability set (after tool policy/visibility) — skill surfaces only when **all** populated requirements are satisfied. Use for persona-style and capability-gated skills.

### 7) (Optional) `skill_propose` drafting and directory
`SkillsConfig(..., proposal={"enabled": True})` exposes a non-persisting drafting tool. `SkillsDirectoryConfig(enabled=True, include_fields=[...])` surfaces a compact directory for discoverability.

### 8) Treat packs as LLM-visible
Anything in a skill enters `llm_context`. **No secrets, no PII, no tenant-private identifiers.** `redact_pii=True` is a safety net, not a substitute for clean authoring.

## Troubleshooting (fast checks)
- **`skill_search is not configured`** — `SkillsConfig.enabled=False`, or no provider. Enable and verify packs load.
- **`skill_propose is not configured`** — `proposal.enabled=False`; enable it explicitly.
- **Pack ignored / no skills available** — path doesn't exist on the worker (container vs laptop), or YAML frontmatter invalid (empty `trigger`/`steps`).
- **Skill surfaces but mentions a forbidden tool** — applicability misconfigured; add `required_tool_names` or rewrite the skill to use `tool_search`.
- **Cross-tenant skill leak** — `tenant_id`/`project_id` not in `tool_context`; the provider has nothing to scope on.
- **Runtime provider not appearing** — confirm both `skills_provider` is injected AND `SkillsConfig.enabled=True` (passing a provider with `enabled=False` is a configuration error).
- **Top-k retrieval misses obvious skills** — bad `trigger` text; rewrite to mention the user's intent terms.
- **Skills bloat prompts** — lower `max_tokens` and `top_k`; enable `summarize=True`.

## Worked example
- No bundled example ships a skill pack; `tests/test_skills_pack_loader.py` and `tests/test_skills_runtime_provider.py` show canonical pack layouts and runtime-provider wiring.

## References (load only as needed)
- `references/pack-formats.md` — every supported format with full schemas, applicability fields, validation rules.
- `references/providers-and-scoping.md` — `SkillProvider` protocol, runtime providers, composition with local packs, tenant scoping, host-side ACL patterns.
- `references/retrieval-and-tools.md` — `skill_search`/`skill_get`/`skill_list`, pre-flight injection, directory rendering, tool-aware redaction, `skill_propose` lifecycle.
