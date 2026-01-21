"""Built-in guardrail rules."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .context import ContextSnapshotV1, GuardrailEvent
from .models import (
    GuardrailAction,
    GuardrailDecision,
    GuardrailSeverity,
    RedactionSpec,
    RuleCost,
    StopSpec,
)


class JailbreakIntent(str, Enum):
    """Taxonomy of jailbreak/prompt-injection intent types."""

    JB_OVERRIDE = "jb_override"
    EXFIL_PROMPT = "exfil_prompt"
    TOOL_ESCALATION = "tool_escalation"
    INDIRECT_INJECTION = "indirect_injection"
    SOCIAL_ENGINEERING = "social_engineering"
    BENIGN = "benign"


@dataclass
class ToolAllowlistRule:
    """Block unauthorized tool execution."""

    rule_id: str = "tool-allowlist"
    version: str = "1.0.0"
    supports_event_types: frozenset[str] = frozenset({"tool_call_start"})
    cost: RuleCost = RuleCost.FAST
    enabled: bool = True
    severity: GuardrailSeverity = GuardrailSeverity.CRITICAL
    denied_tools: frozenset[str] = frozenset()
    allowed_tools: frozenset[str] | None = None

    async def evaluate(
        self,
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> GuardrailDecision | None:
        tool_name = event.tool_name
        if not tool_name:
            return None

        if tool_name in self.denied_tools:
            return GuardrailDecision(
                action=GuardrailAction.STOP,
                rule_id=self.rule_id,
                reason=f"Tool '{tool_name}' is denied",
                severity=GuardrailSeverity.CRITICAL,
                stop=StopSpec(error_code="TOOL_DENIED"),
            )

        if self.allowed_tools and tool_name not in self.allowed_tools:
            return GuardrailDecision(
                action=GuardrailAction.STOP,
                rule_id=self.rule_id,
                reason=f"Tool '{tool_name}' not in allowlist",
                severity=GuardrailSeverity.HIGH,
                stop=StopSpec(error_code="TOOL_NOT_ALLOWED"),
            )

        return None


@dataclass
class SecretRedactionRule:
    """Detect and redact secrets in output."""

    rule_id: str = "secret-redaction"
    version: str = "1.0.0"
    supports_event_types: frozenset[str] = frozenset(
        {
            "llm_stream_chunk",
            "tool_call_result",
        }
    )
    cost: RuleCost = RuleCost.FAST
    enabled: bool = True
    severity: GuardrailSeverity = GuardrailSeverity.HIGH
    patterns: dict[str, re.Pattern[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.patterns:
            self.patterns = {
                "OPENAI_KEY": re.compile(r"sk-[a-zA-Z0-9]{20,}"),
                "ANTHROPIC_KEY": re.compile(r"sk-ant-[a-zA-Z0-9\-]{20,}"),
                "AWS_KEY": re.compile(r"AKIA[0-9A-Z]{16}"),
                "GITHUB_TOKEN": re.compile(r"ghp_[a-zA-Z0-9]{36}"),
            }

    async def evaluate(
        self,
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> GuardrailDecision | None:
        text = event.text_content
        if not text:
            return None

        redactions: list[RedactionSpec] = []
        for secret_type, pattern in self.patterns.items():
            for match in pattern.finditer(text):
                redactions.append(
                    RedactionSpec(
                        path="text",
                        replacement=f"[{secret_type}]",
                        entity_type=secret_type,
                        start_offset=match.start(),
                        end_offset=match.end(),
                    )
                )

        if redactions:
            return GuardrailDecision(
                action=GuardrailAction.REDACT,
                rule_id=self.rule_id,
                reason=f"Found {len(redactions)} secret(s)",
                severity=self.severity,
                redactions=tuple(redactions),
            )

        return None


@dataclass
class InjectionPatternRule:
    """Regex-based jailbreak pattern detection."""

    rule_id: str = "injection-patterns"
    version: str = "1.0.0"
    supports_event_types: frozenset[str] = frozenset({"llm_before"})
    cost: RuleCost = RuleCost.FAST
    enabled: bool = True
    severity: GuardrailSeverity = GuardrailSeverity.CRITICAL
    patterns: tuple[tuple[str, JailbreakIntent], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.patterns:
            self.patterns = (
                (r"ignore\s+(all\s+)?previous\s+instructions", JailbreakIntent.JB_OVERRIDE),
                (r"disregard\s+(your\s+)?(instructions|rules)", JailbreakIntent.JB_OVERRIDE),
                (r"you\s+are\s+now\s+(in\s+)?(\w+\s+)?mode", JailbreakIntent.JB_OVERRIDE),
                (r"\bDAN\b.*mode", JailbreakIntent.JB_OVERRIDE),
                (r"jailbreak", JailbreakIntent.JB_OVERRIDE),
                (r"(show|reveal|print)\s+(your|the)\s+system\s*prompt", JailbreakIntent.EXFIL_PROMPT),
                (r"what\s+(is|are)\s+your\s+(system\s+)?instructions", JailbreakIntent.EXFIL_PROMPT),
                (r"(run|execute)\s+(as\s+)?(root|admin|sudo)", JailbreakIntent.TOOL_ESCALATION),
                (r"bypass\s+(tool\s+)?restrictions", JailbreakIntent.TOOL_ESCALATION),
            )

    async def evaluate(
        self,
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> GuardrailDecision | None:
        text = event.text_content
        if not text:
            return None

        for pattern, intent in self.patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return GuardrailDecision(
                    action=GuardrailAction.STOP,
                    rule_id=self.rule_id,
                    reason=f"Jailbreak pattern [{intent.value}]: '{match.group(0)[:50]}'",
                    severity=GuardrailSeverity.CRITICAL,
                    confidence=1.0,
                    effects=("flag_trajectory", "increment_strike"),
                    classifier_result={"intent": intent.value, "method": "regex"},
                    stop=StopSpec(
                        error_code=f"JAILBREAK_{intent.value.upper()}",
                        user_message="I can't process that request.",
                    ),
                )

        return None


__all__ = [
    "InjectionPatternRule",
    "JailbreakIntent",
    "SecretRedactionRule",
    "ToolAllowlistRule",
]
