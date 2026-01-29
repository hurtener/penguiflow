"""Guardrail context and event models."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


@dataclass(slots=True)
class GuardrailContext:
    """Context for a guardrail evaluation session."""

    run_id: str
    tenant_id: str | None = None
    persona_id: str | None = None
    tool_context: dict[str, Any] = field(default_factory=dict)
    strike_counts: dict[str, int] = field(default_factory=dict)
    policy_config: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GuardrailEvent:
    """Event submitted for guardrail evaluation."""

    event_type: str
    run_id: str
    text_content: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=lambda: __import__("time").time())


class ToolRiskTier(str, Enum):
    """Risk classification for tools."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TrustBoundary(str, Enum):
    """Trust level of content source."""

    SYSTEM = "system"
    USER = "user"
    RAG = "rag"
    WEB = "web"
    TOOL_OUTPUT = "tool_output"


@dataclass(slots=True, frozen=True)
class ToolRiskInfo:
    """Risk metadata for a tool."""

    name: str
    risk_tier: ToolRiskTier


@dataclass(slots=True)
class ContextSnapshotV1:
    """Standardized context for guardrail evaluation."""

    schema_version: Literal["1"] = "1"
    user_text: str = ""
    primary_source: TrustBoundary = TrustBoundary.USER
    contains_untrusted: bool = False
    available_tools: tuple[ToolRiskInfo, ...] = ()
    current_tool: ToolRiskInfo | None = None
    max_tool_risk: ToolRiskTier = ToolRiskTier.LOW
    requests_system_info: bool = False
    requests_capability_change: bool = False
    previous_violations: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize for external classifiers."""

        return {
            "schema_version": self.schema_version,
            "user_text": self.user_text,
            "primary_source": self.primary_source.value,
            "contains_untrusted": self.contains_untrusted,
            "max_tool_risk": self.max_tool_risk.value,
            "current_tool": self.current_tool.name if self.current_tool else None,
            "requests_system_info": self.requests_system_info,
            "requests_capability_change": self.requests_capability_change,
            "previous_violations": self.previous_violations,
        }


class ContextSnapshotBuilder:
    """Builds ContextSnapshotV1 from planner state."""

    def __init__(self, tool_risk_registry: dict[str, ToolRiskTier] | None = None) -> None:
        self._tool_risks = tool_risk_registry or {}
        self._default_risk = ToolRiskTier.MEDIUM

    def build(self, event: GuardrailEvent, ctx: GuardrailContext) -> ContextSnapshotV1:
        """Build context snapshot from current state."""

        text = event.text_content or ""
        current_tool = None
        if event.tool_name:
            risk = self._tool_risks.get(event.tool_name, self._default_risk)
            current_tool = ToolRiskInfo(name=event.tool_name, risk_tier=risk)

        max_risk = ToolRiskTier.LOW
        available: list[ToolRiskInfo] = []
        for tool_name in ctx.tool_context.get("available_tools", []):
            risk = self._tool_risks.get(tool_name, self._default_risk)
            available.append(ToolRiskInfo(name=tool_name, risk_tier=risk))
            if self._risk_order(risk) > self._risk_order(max_risk):
                max_risk = risk

        return ContextSnapshotV1(
            user_text=text,
            primary_source=self._infer_source(event),
            contains_untrusted=ctx.tool_context.get("contains_untrusted", False),
            available_tools=tuple(available),
            current_tool=current_tool,
            max_tool_risk=max_risk,
            requests_system_info=self._check_system_info_request(text),
            requests_capability_change=self._check_capability_request(text),
            previous_violations=sum(ctx.strike_counts.values()),
        )

    def set_tool_risks(self, tool_risk_registry: dict[str, ToolRiskTier]) -> None:
        """Replace the tool risk registry."""

        self._tool_risks = dict(tool_risk_registry)

    def _risk_order(self, risk: ToolRiskTier) -> int:
        return ["low", "medium", "high", "critical"].index(risk.value)

    def _infer_source(self, event: GuardrailEvent) -> TrustBoundary:
        if event.event_type == "tool_call_result":
            return TrustBoundary.TOOL_OUTPUT
        return TrustBoundary.USER

    def _check_system_info_request(self, text: str) -> bool:
        patterns = [
            r"system\s*prompt",
            r"(show|reveal|print)\s*(your|the)\s*(instructions|rules|prompt)",
            r"what\s+are\s+your\s+instructions",
        ]
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)

    def _check_capability_request(self, text: str) -> bool:
        patterns = [
            r"(remove|disable|bypass)\s*(your\s+)?(restrictions|limits)",
            r"unrestricted\s+mode",
        ]
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


__all__ = [
    "ContextSnapshotBuilder",
    "ContextSnapshotV1",
    "GuardrailContext",
    "GuardrailEvent",
    "ToolRiskInfo",
    "ToolRiskTier",
    "TrustBoundary",
]
