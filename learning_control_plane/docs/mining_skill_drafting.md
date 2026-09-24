# Constrained LLM skill drafting

`LlmSkillDrafter` is an optional adapter for the existing `CandidateMiner`
`drafter` callback. It turns one redacted `TracePattern` into a short advisory
skill; it cannot approve, deliver, or activate that skill.

## Provider input

The drafting prompt contains only the agent reference, execution fingerprint,
pattern key, success count, safe summaries, and investigation digests. It does
not contain a native trajectory, raw request, tool arguments, observations,
errors, final answers, or credentials.

The provider must return JSON with exactly four string fields:

```text
title
trigger
advisory_skill
rationale
```

## Local fail-closed validation

Every field must be non-empty, bounded in size, and free of executable code,
tool-call syntax, credential-like strings, permission escalation, and policy
bypass language. Callers may also supply known forbidden text for a run. Invalid
output raises an error, so no candidate is created.

The draft stores SHA-256 digests of both the exact provider prompt and the
validated draft fields for audit. The existing callback-based `CandidateMiner`
path remains supported.

Run the local fake-provider demonstration with:

```bash
uv run python examples/learning_control_plane_local_demo/skill_drafting_demo.py
```
