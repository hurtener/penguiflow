"""Show constrained skill drafting without a live model provider."""

from __future__ import annotations

import json

from learning_control_plane.mining import TracePattern
from learning_control_plane.skill_drafting import LlmSkillDrafter


class DemoProvider:
    """Return a fixed model-shaped response for local inspection."""

    def complete(self, prompt: str) -> str:
        del prompt
        return json.dumps(
            {
                "title": "Verify refund status",
                "trigger": "Use when a customer asks about a refund.",
                "advisory_skill": "Check the billing system before explaining the refund status.",
                "rationale": "Repeated successful investigations verified the authoritative record first.",
            }
        )


def main() -> None:
    """Draft one advisory skill from a safe trace pattern using the fake provider."""

    pattern = TracePattern(
        agent_id="support-agent",
        deployment_digest="sha256:bundle-v1",
        pattern_key="billing-refund",
        source_trace_ids=("trace-1", "trace-2"),
        source_investigation_digests=("sha256:investigation-1", "sha256:investigation-2"),
        safe_summaries=(
            "Verified the refund status with the billing system.",
            "Verified the refund status with the billing system.",
        ),
    )
    draft = LlmSkillDrafter(DemoProvider()).draft(pattern)
    print(
        json.dumps(
            {
                "title": draft.title,
                "trigger": draft.trigger,
                "advisory_skill": draft.advisory_skill,
                "rationale": draft.rationale,
                "prompt_digest": draft.prompt_digest,
                "draft_digest": draft.draft_digest,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
