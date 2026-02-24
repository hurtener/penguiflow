# Skills (playbooks and retrieval)

## What it is / when to use it

“Skills” are **named playbooks** (human-authored or curated) that the planner can:

- retrieve automatically as “relevant skills” at the start of a run,
- discover by capability via `skill_search`,
- fetch in full via `skill_get`,
- list/paginate via `skill_list`.

Skills are designed for enterprise usage where you want:

- standardized, reviewable operational procedures (“how we do X here”),
- reuse across agents without copying prompt text,
- a safe way to expose internal process knowledge without adding new tools.

## Non-goals / boundaries

- Skills are not “tools”: they do not execute anything on their own.
- Skills are not long-term memory; they are curated documents stored in a local skill store.
- Skills are not guaranteed to be correct for every environment; treat them like runbooks that must be reviewed and maintained.

## Contract surface

### Enabling skills

Enable skills by passing `SkillsConfig(enabled=True, ...)` into `ReactPlanner(...)`:

- `ReactPlanner(..., skills=SkillsConfig(...))`

When enabled, ReactPlanner:

- creates a `LocalSkillProvider`,
- loads configured skill packs into a local SQLite skill store,
- injects skill discovery guidance into the system prompt,
- makes these always-visible tools available:
  - `skill_search`, `skill_get`, `skill_list`

### Skills configuration: `SkillsConfig`

Key knobs (from `penguiflow.skills.models.SkillsConfig`):

- `enabled`: master switch
- `cache_dir`: where the skill store SQLite DB lives
- `max_tokens`: cap for how much skill context can be injected per run
- `top_k`: how many relevant skills to retrieve automatically
- `summarize`: whether to summarize skill payloads to fit the budget
- `redact_pii`: redact PII in skill text before injection (recommended)
- `skill_packs`: list of `SkillPackConfig(name, path, format?, scope_mode?, ...)`
- `directory`: optional “known skills” directory rendering (useful for discoverability)
- `scope_mode`: default scope for learned skills (packs can declare scope too)

### Skill pack formats

The local pack loader supports:

- Markdown: `*.skill.md` (YAML frontmatter)
- YAML: `*.skill.yaml` / `*.skill.yml`
- JSON: `*.skill.json`
- JSONL: `*.skill.jsonl`

For Markdown, the file must contain YAML frontmatter with at least:

- `trigger: str` (when to use it)
- `steps: list[str]` (the playbook)

### Automatic injection and directory blocks

When enabled, the runtime may inject (as prompt metadata):

- `<skills_context>`: “relevant skills” (bounded by token budget)
- `<skill_directory>`: a compact directory of known skills (optional)

These are LLM-visible; do not put secrets in skills.

### Tool filtering interactions

Skill retrieval is **tool-aware**:

- skill text can be redacted to avoid naming disallowed tools
- if `tool_search` is available, skills can be rewritten to say “use tool_search” instead of naming a forbidden tool

This helps prevent capability leakage when tool visibility differs by tenant/user.

## Operational defaults (recommended)

- Keep skills enabled only when you have curated content:
  - `SkillsConfig(enabled=True, redact_pii=True, top_k=6, max_tokens=2000)`
- Store skill packs in-repo and version them like code.
- Use scope keys in `tool_context` for multi-tenant deployments:
  - `tenant_id`, `project_id` (used by the provider’s scoping filter)
- Enable the directory for discoverability in interactive UIs:
  - `SkillsDirectoryConfig(enabled=True, include_fields=["name","title","trigger"])`

## Failure modes & recovery

### `skill_search is not configured` / `skill_get is not configured`

**Likely cause**

- `SkillsConfig.enabled=False`, or no provider was created

**Fix**

- enable skills on the planner and ensure packs exist.

### Skill pack missing / empty

If a pack path doesn’t exist or contains no valid skills, it will be ignored.

**Fix**

- verify the pack path on the worker (container path differs from your laptop)
- validate YAML frontmatter (trigger non-empty, steps non-empty)

### Skills mention tools the user is not allowed to use

**Fix**

- ensure tool visibility/policy is configured correctly for that tenant
- keep `redact_pii=True` and avoid hard-coding sensitive tool names in skills
- prefer writing skills to say “use tool_search for capability X” unless a tool is truly stable/public

## Observability

Useful planner events:

- `skill_pack_loaded` (pack name, skill count)
- `skills_retrieved` (count, token estimates, was_summarized)
- `skill_search_query`, `skill_get`, `skill_list`
- `skill_directory_rendered` (entry count)

See **[Planner observability](observability.md)**.

## Security / multi-tenancy notes

- Treat skills as LLM-visible: never store secrets, credentials, or internal-only identifiers.
- Scope skills per tenant/project when applicable (use `tenant_id`/`project_id` in `tool_context`).
- Treat `cache_dir` as a data store (permissions, backups, and lifecycle matter if you have learned skills).

## Runnable example: load a temporary skill pack and call skill_search/skill_get

This example writes a `.skill.md` file to a temporary folder, enables skills, and uses a scripted client to exercise the skill tools.

```python
from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from penguiflow.planner import PlannerFinish, ReactPlanner
from penguiflow.planner.models import JSONLLMClient
from penguiflow.skills.models import SkillPackConfig, SkillsConfig


class ScriptedClient(JSONLLMClient):
    def __init__(self) -> None:
        self._step = 0

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str:
        del messages, response_format, stream, on_stream_chunk
        self._step += 1
        if self._step == 1:
            return json.dumps({"next_node": "skill_search", "args": {"query": "incident", "limit": 5}}, ensure_ascii=False)
        if self._step == 2:
            return json.dumps({"next_node": "skill_get", "args": {"names": ["pack.demo.handle_incident"]}}, ensure_ascii=False)
        return json.dumps({"next_node": "final_response", "args": {"answer": "done"}}, ensure_ascii=False)


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "handle_incident.skill.md").write_text(
            """---
name: pack.demo.handle_incident
title: Handle a production incident
trigger: When an on-call incident is declared and you need a repeatable response.
task_type: domain
steps:
  - Confirm impact and affected tenants/projects.
  - Identify the failing dependency and roll back if needed.
  - Mitigate user impact first, then diagnose root cause.
failure_modes:
  - If metrics are missing, check telemetry pipeline health first.
---
""",
            encoding="utf-8",
        )

        planner = ReactPlanner(
            llm_client=ScriptedClient(),
            catalog=[],  # skills tools are injected automatically when skills.enabled=True
            skills=SkillsConfig(
                enabled=True,
                cache_dir=str(root / ".cache"),
                skill_packs=[SkillPackConfig(name="demo", path=str(root))],
            ),
        )

        result = await planner.run("demo", tool_context={"session_id": "demo", "tenant_id": "t1", "project_id": "p1"})
        assert isinstance(result, PlannerFinish)
        print(result.reason)


if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting checklist

- Did you pass `skills=SkillsConfig(enabled=True, ...)`?
- Are your skill packs present on the runtime filesystem?
- Are you scoping skill access with `tenant_id` / `project_id` in multi-tenant deployments?
- Are you recording `skills_retrieved` and `skill_*` events?
