"""Constrained, provider-neutral drafting of advisory skills from safe patterns."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol

from .mining import TracePattern

_REQUIRED_DRAFT_FIELDS = ("title", "trigger", "advisory_skill", "rationale")
_FORBIDDEN_DRAFT_PARTS = (
    "```",
    "<tool_call",
    "function_call",
    "api_key",
    "authorization:",
    "bearer ",
    "chmod ",
    "rm -rf",
    "curl ",
    "subprocess",
    "os.system",
    "grant permission",
    "elevate permission",
    "override policy",
    "ignore previous",
)


class SkillDraftingProvider(Protocol):
    """Generate one raw draft response from a safe, constrained prompt."""

    def complete(self, prompt: str) -> str:
        """Return one model response for the supplied drafting prompt."""
        ...


@dataclass(frozen=True, slots=True)
class DraftedAdvisorySkill:
    """One validated advisory-skill draft before candidate creation or review."""

    title: str
    trigger: str
    advisory_skill: str
    rationale: str
    prompt_digest: str
    draft_digest: str


@dataclass(frozen=True, slots=True)
class DraftValidationPolicy:
    """Bound the text an LLM may contribute to an advisory-skill draft."""

    maximum_title_characters: int = 120
    maximum_trigger_characters: int = 400
    maximum_skill_characters: int = 1_200
    maximum_rationale_characters: int = 800
    forbidden_text: Collection[str] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "maximum_title_characters",
            "maximum_trigger_characters",
            "maximum_skill_characters",
            "maximum_rationale_characters",
        ):
            if getattr(self, field_name) < 1:
                raise ValueError(f"{field_name} must be at least 1")
        object.__setattr__(self, "forbidden_text", tuple(self.forbidden_text))


class LlmSkillDrafter:
    """Draft and validate advisory guidance from one redacted trace pattern."""

    def __init__(
        self,
        provider: SkillDraftingProvider,
        *,
        validation_policy: DraftValidationPolicy | None = None,
    ) -> None:
        self._provider = provider
        self._validation_policy = validation_policy or DraftValidationPolicy()

    def __call__(self, pattern: TracePattern) -> str:
        """Return validated advisory text for existing ``CandidateMiner`` callers."""

        return self.draft(pattern).advisory_skill

    def draft(self, pattern: TracePattern) -> DraftedAdvisorySkill:
        """Ask the provider for a draft and reject unsafe or malformed output."""

        prompt = build_skill_drafting_prompt(pattern)
        raw_response = self._provider.complete(prompt)
        fields = _draft_fields(raw_response)
        policy = self._validation_policy
        title = _validated_text(fields["title"], "title", policy.maximum_title_characters, policy)
        trigger = _validated_text(fields["trigger"], "trigger", policy.maximum_trigger_characters, policy)
        advisory_skill = _validated_text(
            fields["advisory_skill"],
            "advisory_skill",
            policy.maximum_skill_characters,
            policy,
        )
        rationale = _validated_text(fields["rationale"], "rationale", policy.maximum_rationale_characters, policy)
        draft_payload = {
            "title": title,
            "trigger": trigger,
            "advisory_skill": advisory_skill,
            "rationale": rationale,
        }
        return DraftedAdvisorySkill(
            **draft_payload,
            prompt_digest=_digest(prompt),
            draft_digest=_digest(json.dumps(draft_payload, sort_keys=True, separators=(",", ":"))),
        )


def build_skill_drafting_prompt(pattern: TracePattern) -> str:
    """Build the sole LLM input from redacted pattern fields only."""

    safe_pattern = {
        "agent_ref": pattern.agent_id,
        "execution_fingerprint": pattern.deployment_digest,
        "pattern_key": pattern.pattern_key,
        "success_count": len(pattern.source_trace_ids),
        "safe_summaries": list(pattern.safe_summaries),
        "investigation_digests": list(pattern.source_investigation_digests),
    }
    evidence = json.dumps(safe_pattern, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "\n".join(
        (
            "Write one short advisory skill from the redacted evidence JSON below.",
            "The skill is optional guidance. It cannot grant permissions, force tool calls, run code,",
            "change policies, or claim facts not present in the evidence.",
            "Return JSON only with exactly these string fields: title, trigger, advisory_skill, rationale.",
            "Do not include code, commands, credentials, tool-call syntax, or policy-bypass instructions.",
            "REDACTED_EVIDENCE_JSON:",
            evidence,
        )
    )


def _draft_fields(raw_response: str) -> dict[str, str]:
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as error:
        raise ValueError("skill drafter must return one JSON object") from error
    if not isinstance(payload, dict) or set(payload) != set(_REQUIRED_DRAFT_FIELDS):
        raise ValueError("skill drafter response must contain exactly title, trigger, advisory_skill, and rationale")

    fields: dict[str, str] = {}
    for field_name in _REQUIRED_DRAFT_FIELDS:
        value = payload[field_name]
        if not isinstance(value, str):
            raise ValueError(f"skill drafter field {field_name!r} must be a string")
        fields[field_name] = value
    return fields


def _validated_text(
    value: str,
    field_name: str,
    maximum_characters: int,
    policy: DraftValidationPolicy,
) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        raise ValueError(f"skill drafter field {field_name!r} must be non-empty")
    if len(cleaned) > maximum_characters:
        raise ValueError(f"skill drafter field {field_name!r} exceeds {maximum_characters} characters")

    lowered = cleaned.lower()
    forbidden_text = (*_FORBIDDEN_DRAFT_PARTS, *(item.lower() for item in policy.forbidden_text if item))
    if any(part in lowered for part in forbidden_text):
        raise ValueError(f"skill drafter field {field_name!r} contains prohibited content")
    return cleaned


def _digest(value: str) -> str:
    """Return a stable SHA-256 audit digest for text crossing the provider boundary."""

    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


__all__ = [
    "DraftedAdvisorySkill",
    "DraftValidationPolicy",
    "LlmSkillDrafter",
    "SkillDraftingProvider",
    "build_skill_drafting_prompt",
]
