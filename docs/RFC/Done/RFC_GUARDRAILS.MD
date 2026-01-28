# RFC-001: Guardrails & Alignment Checks for PenguiFlow React Planner

**Subtitle:** Hybrid sync/async safety layer with extensibility hooks for jailbreak detection and enterprise guardrail integration.

---

## Metadata

| Field | Value |
|-------|-------|
| **RFC Number** | RFC-001 |
| **Status** | Final Draft v5 |
| **Authors** | PenguiFlow Core Team |
| **Created** | 2026-01-11 |
| **Revised** | 2026-01-16 |
| **Target Version** | PenguiFlow 2.12.0 |
| **New Modules** | `penguiflow.planner.guardrails`, `penguiflow.steering.guard_inbox` |
| **Modified Modules** | `penguiflow.steering`, `penguiflow.planner.react`, `penguiflow.planner.models` |

---

## 1. Executive Summary

This RFC introduces a **Hybrid Guardrail Architecture** for the PenguiFlow React Planner that combines:

1. **Synchronous fast rules** — Inline evaluation for blocking gates (tool allowlists, secrets, injection patterns)
2. **Asynchronous deep rules** — Via `SteeringGuardInbox` for expensive checks (PII detection, ML classifiers, external APIs)
3. **Unified Gateway** — Single API hiding sync/async complexity with extensibility hooks
4. **Pluggable classifiers** — Bring your own jailbreak detection (HuggingFace, custom)

### Design Principles

| Principle | Implementation |
|-----------|----------------|
| **Ship working defaults** | Regex patterns, tool allowlists, secret redaction out of the box |
| **Enable advanced use cases** | Hooks for ML classifiers, vendor APIs, custom policies |
| **No ML ops burden** | Library doesn't ship models; provides integration patterns |
| **Agent-aware context** | Standardized schema for tool risk, trust boundaries |

### What Ships in v0.12.0

| Component | Included | Notes |
|-----------|----------|-------|
| Hybrid sync/async architecture | ✅ | Core infrastructure |
| `SteeringGuardInbox` | ✅ | Async rule evaluation (multi-decision) |
| Built-in sync rules | ✅ | Tool allowlist, secrets, regex injection |
| `ContextSnapshotV1` | ✅ | Agent-aware context schema |
| `JailbreakIntent` taxonomy | ✅ | Standard labels for classifiers |
| `DecisionPolicy` protocol | ✅ | Pluggable resolution strategies |
| `RiskRouter` protocol | ✅ | Dynamic per-event routing |
| ML classifiers | ❌ | Examples provided, bring your own |

---

## 2. Architecture Overview

### 2.1 Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              ReactPlanner                                    │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │                       GuardrailGateway                                  ││
│  │                                                                         ││
│  │  ┌─────────────────────┐         ┌────────────────────────────────────┐││
│  │  │   SyncEvaluator     │         │   SteeringGuardInbox               │││
│  │  │   (in-process)      │         │   (async, decoupled)               │││
│  │  │                     │         │                                    │││
│  │  │  Built-in:          │         │  Plug in:                          │││
│  │  │  • ToolAllowlist    │         │  • ML classifiers (HuggingFace)    │││
│  │  │  • SecretRedaction  │         │  • Vendor APIs (NeMo, custom)      │││
│  │  │  • InjectionPattern │         │  • PII detection (Presidio)        │││
│  │  │  • SchemaValidation │         │  • Custom deep rules               │││
│  │  │                     │         │                                    │││
│  │  │  Latency: < 15ms    │         │  Latency: 50-500ms                 │││
│  │  └──────────┬──────────┘         └─────────────┬──────────────────────┘││
│  │             │                                  │                       ││
│  │             ▼                                  ▼                       ││
│  │  ┌─────────────────────────────────────────────────────────────────┐  ││
│  │  │                    Extensibility Hooks                           │  ││
│  │  │                                                                  │  ││
│  │  │  • DecisionPolicy  — Custom resolution (priority, voting, etc.) │  ││
│  │  │  • RiskRouter      — Dynamic routing per event/tool             │  ││
│  │  │  • ContextBuilder  — Agent-aware feature extraction             │  ││
│  │  └─────────────────────────────────────────────────────────────────┘  ││
│  └─────────────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Sync vs Async Decision Flow

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           Why Hybrid?                                     │
└──────────────────────────────────────────────────────────────────────────┘

ASYNC-ONLY RACE CONDITION (BAD):
────────────────────────────────────────────────────────────────────────────
Time ─►  T0          T1           T2          T3
         │           │            │           │
         Submit      Tool         STOP        Too late!
         to queue    executes     arrives     (damage done)
────────────────────────────────────────────────────────────────────────────

HYBRID SYNC GATE (GOOD):
────────────────────────────────────────────────────────────────────────────
Time ─►  T0          T1           T2
         │           │            │
         Sync check  STOP         Tool never
         (blocking)  returned     executes
────────────────────────────────────────────────────────────────────────────
```

| Requirement | Sync Only | Async Only | Hybrid ✓ |
|-------------|-----------|------------|----------|
| Block dangerous tools before execution | ✅ | ❌ Race condition | ✅ |
| Stop streaming mid-output | ✅ | ❌ Too late | ✅ |
| Scale expensive classifiers independently | ❌ Blocks hot path | ✅ | ✅ |
| Plug in external guardrail services | ❌ | ✅ | ✅ |

---

## 3. Data Models

### 3.1 Core Decision Types

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Protocol
from uuid import uuid4


class GuardrailAction(str, Enum):
    """Guardrail decision actions."""
    ALLOW = "ALLOW"
    REDACT = "REDACT"
    RETRY = "RETRY"
    PAUSE = "PAUSE"
    STOP = "STOP"


class GuardrailSeverity(str, Enum):
    """Decision severity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RuleCost(str, Enum):
    """Rule execution cost classification."""
    FAST = "fast"    # < 15ms, suitable for sync
    DEEP = "deep"    # > 50ms, should be async


@dataclass(slots=True, frozen=True)
class RedactionSpec:
    """Specification for content redaction."""
    path: str
    replacement: str = "[REDACTED]"
    entity_type: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None


@dataclass(slots=True, frozen=True)
class RetrySpec:
    """Specification for LLM/tool retry with corrective guidance."""
    max_attempts: int = 2
    corrective_message: str = ""


@dataclass(slots=True, frozen=True)
class PauseSpec:
    """Specification for human-in-the-loop pause."""
    scope: Literal["run", "step", "tool_call"] = "tool_call"
    approver_roles: tuple[str, ...] = ("admin",)
    prompt: str = ""
    timeout_s: float | None = 300.0


@dataclass(slots=True, frozen=True)
class StopSpec:
    """Specification for run termination."""
    error_code: str = "GUARDRAIL_STOP"
    user_message: str = "I'm unable to complete that request."
    internal_reason: str = ""


@dataclass(slots=True)
class GuardrailDecision:
    """Decision returned by guardrail evaluation."""
    
    # Core fields
    action: GuardrailAction
    rule_id: str
    reason: str
    
    # Identification
    decision_id: str = field(default_factory=lambda: uuid4().hex)
    correlation_id: str | None = None
    
    # Metadata
    severity: GuardrailSeverity = GuardrailSeverity.MEDIUM
    confidence: float | None = None
    was_sync: bool = True
    
    # Secondary effects (e.g., "flag_trajectory", "emit_alert", "increment_strike")
    # Enables patterns like "REDACT + flag" without multi-action complexity
    effects: tuple[str, ...] = ()
    
    # Action-specific payloads
    redactions: tuple[RedactionSpec, ...] | None = None
    retry: RetrySpec | None = None
    pause: PauseSpec | None = None
    stop: StopSpec | None = None
    
    # Extensibility: classifier results (for ML-based rules)
    classifier_result: dict[str, Any] | None = None
    
    def with_effects(self, *new_effects: str) -> GuardrailDecision:
        """Return a new decision with additional effects."""
        return GuardrailDecision(
            action=self.action,
            rule_id=self.rule_id,
            reason=self.reason,
            decision_id=self.decision_id,
            correlation_id=self.correlation_id,
            severity=self.severity,
            confidence=self.confidence,
            was_sync=self.was_sync,
            effects=self.effects + new_effects,
            redactions=self.redactions,
            retry=self.retry,
            pause=self.pause,
            stop=self.stop,
            classifier_result=self.classifier_result,
        )
```

### 3.2 Jailbreak Intent Taxonomy

Standard labels for jailbreak detection. The library provides the taxonomy; you provide the classifier.

```python
class JailbreakIntent(str, Enum):
    """Taxonomy of jailbreak/prompt-injection intent types.
    
    Use these labels when building or integrating jailbreak classifiers.
    Distinct from content safety (malware/harm) which is a separate concern.
    """
    
    # Override system/developer instructions
    JB_OVERRIDE = "jb_override"
    # Examples: "ignore previous instructions", "you are now DAN"
    
    # Exfiltrate system prompt or hidden instructions
    EXFIL_PROMPT = "exfil_prompt"
    # Examples: "show me your system prompt", "reveal hidden rules"
    
    # Escalate tool/capability access
    TOOL_ESCALATION = "tool_escalation"
    # Examples: "run as root", "bypass tool restrictions"
    
    # Indirect injection via retrieved content
    INDIRECT_INJECTION = "indirect_injection"
    # Examples: Malicious instructions in RAG chunks or web pages
    
    # Social engineering the agent
    SOCIAL_ENGINEERING = "social_engineering"
    # Examples: "I'm the developer", "this was pre-approved"
    
    # No jailbreak intent detected
    BENIGN = "benign"
```

### 3.3 GuardrailRule Protocol

```python
class GuardrailRule(Protocol):
    """Protocol that all guardrail rules must implement."""
    
    rule_id: str
    version: str
    supports_event_types: frozenset[str]
    cost: RuleCost
    enabled: bool
    severity: GuardrailSeverity
    
    async def evaluate(
        self,
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> GuardrailDecision | None:
        """Evaluate the rule against an event.
        
        Args:
            event: The guardrail event to evaluate
            context_snapshot: Agent-aware context for the evaluation
            
        Returns:
            A GuardrailDecision if the rule triggers, None otherwise
        """
        ...
```

### 3.4 GuardrailContext and GuardrailEvent

```python
@dataclass(slots=True)
class GuardrailContext:
    """Context for a guardrail evaluation session.
    
    Tracks state across events within a single run.
    NOTE: Do not store per-event computed state here; use ContextSnapshotV1 instead.
    """
    
    run_id: str
    tenant_id: str | None = None
    persona_id: str | None = None
    
    # Tool context (available tools, pending calls, etc.)
    tool_context: dict[str, Any] = field(default_factory=dict)
    
    # Strike counts by category (for escalation policies)
    strike_counts: dict[str, int] = field(default_factory=dict)
    
    # Policy configuration (rule overrides, thresholds, etc.)
    policy_config: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GuardrailEvent:
    """Event submitted for guardrail evaluation."""
    
    event_type: str  # e.g., "llm_before", "tool_call_start", "llm_stream_chunk"
    run_id: str
    
    # Content
    text_content: str | None = None
    
    # Tool-specific fields
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    
    # Generic payload for extensibility
    payload: dict[str, Any] = field(default_factory=dict)
    
    # Timestamp
    timestamp: float = field(default_factory=lambda: __import__("time").time())
```

---

## 4. Agent-Aware Context Schema

Classifiers work better with context. This schema standardizes what your rules receive.

### 4.1 Context Snapshot

```python
class ToolRiskTier(str, Enum):
    """Risk classification for tools."""
    CRITICAL = "critical"  # Admin, delete, credential access
    HIGH = "high"          # Filesystem write, network, database modify
    MEDIUM = "medium"      # Read operations, search, retrieval
    LOW = "low"            # Display, format, calculate


class TrustBoundary(str, Enum):
    """Trust level of content source."""
    SYSTEM = "system"          # System prompt, developer instructions
    USER = "user"              # Direct user input
    RAG = "rag"                # Retrieved from vector store
    WEB = "web"                # Fetched from web
    TOOL_OUTPUT = "tool_output"


@dataclass(slots=True, frozen=True)
class ToolRiskInfo:
    """Risk metadata for a tool."""
    name: str
    risk_tier: ToolRiskTier


@dataclass(slots=True)
class ContextSnapshotV1:
    """Standardized context for guardrail evaluation.
    
    Provides agent-aware features that improve classifier accuracy.
    Passed explicitly to rules; not stored in GuardrailContext.
    """
    
    schema_version: Literal["1"] = "1"
    
    # Text content
    user_text: str = ""
    
    # Trust boundaries
    primary_source: TrustBoundary = TrustBoundary.USER
    contains_untrusted: bool = False
    
    # Tool context
    available_tools: tuple[ToolRiskInfo, ...] = ()
    current_tool: ToolRiskInfo | None = None
    max_tool_risk: ToolRiskTier = ToolRiskTier.LOW
    
    # Request characteristics (useful signals for classifiers)
    requests_system_info: bool = False
    requests_capability_change: bool = False
    
    # Session context
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
```

### 4.2 Context Builder

```python
class ContextSnapshotBuilder:
    """Builds ContextSnapshotV1 from planner state."""
    
    def __init__(
        self,
        tool_risk_registry: dict[str, ToolRiskTier] | None = None,
    ) -> None:
        self._tool_risks = tool_risk_registry or {}
        self._default_risk = ToolRiskTier.MEDIUM
    
    def build(
        self,
        event: GuardrailEvent,
        ctx: GuardrailContext,
    ) -> ContextSnapshotV1:
        """Build context snapshot from current state."""
        
        text = event.text_content or ""
        
        # Determine tool risk
        current_tool = None
        if event.tool_name:
            risk = self._tool_risks.get(event.tool_name, self._default_risk)
            current_tool = ToolRiskInfo(name=event.tool_name, risk_tier=risk)
        
        # Compute max risk from available tools
        max_risk = ToolRiskTier.LOW
        available = []
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
        return any(re.search(p, text, re.IGNORECASE) for p in patterns)
    
    def _check_capability_request(self, text: str) -> bool:
        patterns = [
            r"(remove|disable|bypass)\s*(your\s+)?(restrictions|limits)",
            r"unrestricted\s+mode",
        ]
        return any(re.search(p, text, re.IGNORECASE) for p in patterns)
```

---

## 5. Extensibility Hooks

### 5.1 Decision Policy

Customize how multiple rule decisions are resolved into a final decision.

```python
class DecisionPolicy(Protocol):
    """Protocol for custom decision resolution.
    
    Default: Priority-based (STOP > PAUSE > RETRY > REDACT > ALLOW)
    
    Custom examples:
    - Score aggregation across multiple classifiers
    - Two-person approval overrides
    - Tenant-specific policies
    """
    
    def resolve(
        self,
        decisions: list[GuardrailDecision],
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> GuardrailDecision:
        ...


class DefaultDecisionPolicy:
    """Priority-based resolution with redaction combining and effect merging."""
    
    def __init__(self, combine_effects: bool = True) -> None:
        self._combine_effects = combine_effects
    
    def resolve(
        self,
        decisions: list[GuardrailDecision],
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> GuardrailDecision:
        if not decisions:
            return GuardrailDecision(
                action=GuardrailAction.ALLOW,
                rule_id="__default__",
                reason="No rules triggered",
            )
        
        priority = {
            GuardrailAction.STOP: 5,
            GuardrailAction.PAUSE: 4,
            GuardrailAction.RETRY: 3,
            GuardrailAction.REDACT: 2,
            GuardrailAction.ALLOW: 1,
        }
        
        sorted_decisions = sorted(
            decisions,
            key=lambda d: (priority.get(d.action, 0), d.confidence or 0),
            reverse=True,
        )
        
        winner = sorted_decisions[0]
        
        # Combine redactions if multiple REDACT decisions
        if winner.action == GuardrailAction.REDACT:
            all_redactions: list[RedactionSpec] = []
            for d in sorted_decisions:
                if d.redactions:
                    all_redactions.extend(d.redactions)
            if all_redactions:
                winner = GuardrailDecision(
                    action=GuardrailAction.REDACT,
                    rule_id=winner.rule_id,
                    reason=f"Combined {len(all_redactions)} redactions",
                    severity=winner.severity,
                    redactions=tuple(all_redactions),
                    effects=winner.effects,
                )
        
        # Combine effects from all decisions
        if self._combine_effects:
            all_effects: set[str] = set()
            for d in sorted_decisions:
                all_effects.update(d.effects)
            if all_effects and all_effects != set(winner.effects):
                winner = winner.with_effects(*all_effects)
        
        return winner
```

### 5.2 Risk Router

Customize async wait times and failure behavior per event/tool.

```python
@dataclass(slots=True, frozen=True)
class RiskRoutingDecision:
    """Routing decision for an event."""
    async_wait_ms: float = 0.0
    on_timeout: Literal["allow", "pause", "stop"] = "allow"
    required_async_rules: frozenset[str] = frozenset()
    skip_evaluation: bool = False


class RiskRouter(Protocol):
    """Protocol for dynamic risk-based routing.
    
    Default: Route by tool risk tier
    
    Custom examples:
    - Fail-closed for specific tenants
    - Skip evaluation for known-safe patterns
    - Require specific classifiers for high-risk tools
    """
    
    def route(
        self,
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> RiskRoutingDecision:
        ...


class DefaultRiskRouter:
    """Route by tool risk tier and context signals."""
    
    def __init__(
        self,
        high_risk_wait_ms: float = 200.0,
        medium_risk_wait_ms: float = 100.0,
        critical_fail_closed: bool = True,
    ) -> None:
        self._high_wait = high_risk_wait_ms
        self._medium_wait = medium_risk_wait_ms
        self._critical_fail_closed = critical_fail_closed
    
    def route(
        self,
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> RiskRoutingDecision:
        wait_ms = 0.0
        on_timeout: Literal["allow", "pause", "stop"] = "allow"
        required_rules: set[str] = set()
        
        # Route based on current tool
        if context_snapshot.current_tool:
            risk = context_snapshot.current_tool.risk_tier
            
            if risk == ToolRiskTier.CRITICAL:
                wait_ms = self._high_wait
                on_timeout = "stop" if self._critical_fail_closed else "pause"
            elif risk == ToolRiskTier.HIGH:
                wait_ms = self._high_wait
                on_timeout = "pause"
            elif risk == ToolRiskTier.MEDIUM:
                wait_ms = self._medium_wait
        
        # Also consider llm_before with high-risk tools available
        if event.event_type == "llm_before":
            if context_snapshot.max_tool_risk in (ToolRiskTier.CRITICAL, ToolRiskTier.HIGH):
                wait_ms = max(wait_ms, self._medium_wait)
                on_timeout = "pause" if on_timeout == "allow" else on_timeout
        
        # Require jailbreak classifiers for suspicious requests
        if context_snapshot.requests_system_info:
            required_rules.add("hf-jailbreak")
            wait_ms = max(wait_ms, self._medium_wait)
        
        if context_snapshot.requests_capability_change:
            required_rules.add("hf-jailbreak")
            wait_ms = max(wait_ms, self._high_wait)
            on_timeout = "pause" if on_timeout == "allow" else on_timeout
        
        return RiskRoutingDecision(
            async_wait_ms=wait_ms,
            on_timeout=on_timeout,
            required_async_rules=frozenset(required_rules),
        )
```

---

## 6. Built-in Rules

### 6.1 Tool Allowlist (Sync)

```python
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
```

### 6.2 Secret Redaction (Sync)

```python
@dataclass
class SecretRedactionRule:
    """Detect and redact secrets in output."""
    
    rule_id: str = "secret-redaction"
    version: str = "1.0.0"
    supports_event_types: frozenset[str] = frozenset({
        "llm_stream_chunk",
        "tool_call_result",
    })
    cost: RuleCost = RuleCost.FAST
    enabled: bool = True
    severity: GuardrailSeverity = GuardrailSeverity.HIGH
    
    patterns: dict[str, re.Pattern] = field(default_factory=dict)
    
    def __post_init__(self):
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
        
        redactions = []
        for secret_type, pattern in self.patterns.items():
            for match in pattern.finditer(text):
                redactions.append(RedactionSpec(
                    path="text",
                    replacement=f"[{secret_type}]",
                    entity_type=secret_type,
                    start_offset=match.start(),
                    end_offset=match.end(),
                ))
        
        if redactions:
            return GuardrailDecision(
                action=GuardrailAction.REDACT,
                rule_id=self.rule_id,
                reason=f"Found {len(redactions)} secret(s)",
                severity=self.severity,
                redactions=tuple(redactions),
            )
        
        return None
```

### 6.3 Injection Pattern Detection (Sync)

```python
@dataclass
class InjectionPatternRule:
    """Regex-based jailbreak pattern detection.
    
    These are hard blocks for unambiguous attack patterns.
    For ML-based detection, see Section 8 (Plugging in Classifiers).
    """
    
    rule_id: str = "injection-patterns"
    version: str = "1.0.0"
    supports_event_types: frozenset[str] = frozenset({"llm_before"})
    cost: RuleCost = RuleCost.FAST
    enabled: bool = True
    severity: GuardrailSeverity = GuardrailSeverity.CRITICAL
    
    patterns: tuple[tuple[str, JailbreakIntent], ...] = field(default_factory=tuple)
    
    def __post_init__(self):
        if not self.patterns:
            self.patterns = (
                # JB_OVERRIDE - instruction override
                (r"ignore\s+(all\s+)?previous\s+instructions", JailbreakIntent.JB_OVERRIDE),
                (r"disregard\s+(your\s+)?(instructions|rules)", JailbreakIntent.JB_OVERRIDE),
                (r"you\s+are\s+now\s+(in\s+)?(\w+\s+)?mode", JailbreakIntent.JB_OVERRIDE),
                (r"\bDAN\b.*mode", JailbreakIntent.JB_OVERRIDE),
                (r"jailbreak", JailbreakIntent.JB_OVERRIDE),
                
                # EXFIL_PROMPT - system prompt extraction
                (r"(show|reveal|print)\s+(your|the)\s+system\s*prompt", JailbreakIntent.EXFIL_PROMPT),
                (r"what\s+(is|are)\s+your\s+(system\s+)?instructions", JailbreakIntent.EXFIL_PROMPT),
                
                # TOOL_ESCALATION - privilege escalation
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
```

---

## 7. SteeringGuardInbox

### 7.1 Interface

```python
from abc import ABC, abstractmethod


class SteeringGuardInbox(ABC):
    """Interface for async guardrail evaluation.
    
    Implementations:
    - InMemoryGuardInbox: Single-process (default)
    - RedisGuardInbox: Distributed (enterprise)
    
    Note: Async evaluation runs N deep rules and returns N decisions,
    which are then resolved via DecisionPolicy (symmetric with sync).
    """
    
    @abstractmethod
    async def submit(self, event: SteeringGuardEvent) -> str:
        """Submit event for async evaluation. Returns correlation_id."""
        ...
    
    @abstractmethod
    async def await_response(
        self,
        correlation_id: str,
        timeout_s: float,
    ) -> SteeringGuardResponse:
        """Wait for response. Raises TimeoutError if exceeded."""
        ...
    
    @abstractmethod
    def drain_responses(self, run_id: str) -> list[SteeringGuardResponse]:
        """Drain pending responses for late decision handling."""
        ...


@dataclass(slots=True)
class SteeringGuardEvent:
    """Event submitted for async evaluation."""
    event_id: str = field(default_factory=lambda: uuid4().hex)
    correlation_id: str = field(default_factory=lambda: uuid4().hex)
    event_type: str = ""
    run_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    context_snapshot: dict[str, Any] = field(default_factory=dict)
    
    # Specifies which async rules must run (empty = all applicable)
    required_rules: frozenset[str] = frozenset()


@dataclass(slots=True)
class SteeringGuardResponse:
    """Response from async evaluation.
    
    Contains multiple decisions (one per deep rule that triggered),
    symmetric with sync evaluation.
    """
    correlation_id: str
    decisions: list[GuardrailDecision] = field(default_factory=list)
    error: str | None = None
```

### 7.2 In-Memory Implementation

```python
class InMemoryGuardInbox(SteeringGuardInbox):
    """In-process async evaluation.
    
    NOTE: This implementation drains responses globally. Production
    implementations must track correlation IDs by run_id for proper
    isolation in concurrent scenarios.
    """
    
    def __init__(self, evaluator: AsyncRuleEvaluator) -> None:
        self._evaluator = evaluator
        self._pending: dict[str, asyncio.Future] = {}
        self._completed: dict[str, SteeringGuardResponse] = {}
    
    async def submit(self, event: SteeringGuardEvent) -> str:
        future: asyncio.Future = asyncio.Future()
        self._pending[event.correlation_id] = future
        asyncio.create_task(self._evaluate(event, future))
        return event.correlation_id
    
    async def _evaluate(
        self,
        event: SteeringGuardEvent,
        future: asyncio.Future,
    ) -> None:
        try:
            decisions = await self._evaluator.evaluate(event)
            response = SteeringGuardResponse(
                correlation_id=event.correlation_id,
                decisions=decisions,
            )
        except Exception as e:
            response = SteeringGuardResponse(
                correlation_id=event.correlation_id,
                error=str(e),
            )
        
        self._completed[event.correlation_id] = response
        self._pending.pop(event.correlation_id, None)
        
        if not future.done():
            future.set_result(response)
    
    async def await_response(
        self,
        correlation_id: str,
        timeout_s: float,
    ) -> SteeringGuardResponse:
        if correlation_id in self._completed:
            return self._completed[correlation_id]
        
        future = self._pending.get(correlation_id)
        if not future:
            raise KeyError(f"Unknown correlation_id: {correlation_id}")
        
        return await asyncio.wait_for(future, timeout=timeout_s)
    
    def drain_responses(self, run_id: str) -> list[SteeringGuardResponse]:
        # NOTE: Simple implementation drains globally.
        # Production: filter by run_id from a run_id -> [correlation_id] index.
        responses = list(self._completed.values())
        self._completed.clear()
        return responses
```

### 7.3 Async Rule Evaluator

```python
class AsyncRuleEvaluator:
    """Evaluates deep (async) rules for a submitted event."""
    
    def __init__(self, registry: RuleRegistry) -> None:
        self._registry = registry
    
    async def evaluate(self, event: SteeringGuardEvent) -> list[GuardrailDecision]:
        """Evaluate all applicable deep rules, filtered by required_rules if specified."""
        
        # Get applicable deep rules
        rules = self._registry.get_async_rules(event.event_type)
        
        # Filter by required_rules if specified
        if event.required_rules:
            rules = [r for r in rules if r.rule_id in event.required_rules]
        
        if not rules:
            return []
        
        # Reconstruct context snapshot
        context_snapshot = ContextSnapshotV1(**event.context_snapshot) if event.context_snapshot else ContextSnapshotV1()
        
        # Reconstruct event
        guardrail_event = GuardrailEvent(
            event_type=event.event_type,
            run_id=event.run_id,
            text_content=event.payload.get("text_content"),
            tool_name=event.payload.get("tool_name"),
            tool_args=event.payload.get("tool_args"),
            payload=event.payload,
        )
        
        # Evaluate all rules concurrently
        async def safe_eval(rule: GuardrailRule) -> GuardrailDecision | None:
            try:
                decision = await rule.evaluate(guardrail_event, context_snapshot)
                if decision:
                    decision.was_sync = False
                return decision
            except Exception:
                return None
        
        results = await asyncio.gather(*[safe_eval(r) for r in rules])
        return [d for d in results if d is not None]
```

---

## 8. Plugging in Jailbreak Classifiers

The architecture is designed to integrate ML classifiers without shipping them. Here are practical examples.

### 8.1 HuggingFace: Prompt Injection Models

Recommended models include:
- [`protectai/deberta-v3-base-prompt-injection-v2`](https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2)
- [`madhurjindal/Jailbreak-Detector-2-XL`](https://huggingface.co/madhurjindal/Jailbreak-Detector-2-XL)

```python
from dataclasses import dataclass, field
from typing import Any
import asyncio


@dataclass
class HuggingFaceJailbreakRule:
    """Jailbreak detection using ProtectAI's prompt injection model.
    
    Installation:
        pip install transformers torch
    
    Usage:
        registry.register(HuggingFaceJailbreakRule())
    """
    
    rule_id: str = "hf-jailbreak"
    version: str = "1.0.0"
    supports_event_types: frozenset[str] = frozenset({"llm_before"})
    cost: RuleCost = RuleCost.DEEP
    enabled: bool = True
    severity: GuardrailSeverity = GuardrailSeverity.CRITICAL
    
    model_name: str = "protectai/deberta-v3-base-prompt-injection-v2"
    threshold: float = 0.85
    
    # Device: -1 for CPU, 0+ for CUDA GPU index
    device: int = -1
    
    _pipeline: Any = field(default=None, repr=False)
    
    def _load_pipeline(self) -> bool:
        if self._pipeline is not None:
            return True
        
        try:
            from transformers import pipeline
            self._pipeline = pipeline(
                "text-classification",
                model=self.model_name,
                device=self.device,
            )
            return True
        except ImportError:
            return False
    
    async def evaluate(
        self,
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> GuardrailDecision | None:
        if not self._load_pipeline():
            return None  # Fail open if model unavailable
        
        text = event.text_content
        if not text or len(text) < 10:
            return None
        
        # Augment with context signals for better accuracy
        augmented_text = self._augment_with_context(text, context_snapshot)
        
        # Run inference in thread pool
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._pipeline(augmented_text, truncation=True, max_length=512),
        )
        
        # Model returns: [{"label": "INJECTION", "score": 0.98}]
        label = result[0]["label"]
        score = result[0]["score"]
        
        if label == "INJECTION" and score >= self.threshold:
            return GuardrailDecision(
                action=GuardrailAction.STOP,
                rule_id=self.rule_id,
                reason=f"Prompt injection detected (score: {score:.3f})",
                severity=GuardrailSeverity.CRITICAL,
                confidence=score,
                effects=("flag_trajectory", "increment_strike", "emit_alert"),
                classifier_result={
                    "model": self.model_name,
                    "label": label,
                    "score": score,
                    "intent": JailbreakIntent.JB_OVERRIDE.value,
                },
                stop=StopSpec(
                    error_code="JAILBREAK_ML_DETECTED",
                    user_message="I can't process that request.",
                ),
            )
        
        return None
    
    def _augment_with_context(
        self,
        text: str,
        context_snapshot: ContextSnapshotV1,
    ) -> str:
        """Augment input with context signals for better classifier accuracy."""
        features = []
        
        if context_snapshot.max_tool_risk.value in ("high", "critical"):
            features.append("[HIGH_RISK_TOOLS_AVAILABLE]")
        if context_snapshot.requests_system_info:
            features.append("[REQUESTS_SYSTEM_INFO]")
        if context_snapshot.previous_violations > 0:
            features.append(f"[PRIOR_VIOLATIONS:{context_snapshot.previous_violations}]")
        
        if features:
            return " ".join(features) + " " + text
        return text
```

**Registration:**

```python
from penguiflow.planner.guardrails import RuleRegistry, HuggingFaceJailbreakRule
import torch

registry = RuleRegistry()
registry.register(HuggingFaceJailbreakRule(
    threshold=0.85,
    device=0 if torch.cuda.is_available() else -1,
))
```

### 8.2 Custom Embedding + Classifier

For teams that want to train their own classifier:

```python
@dataclass
class CustomEmbeddingClassifierRule:
    """Custom jailbreak classifier using embeddings + sklearn.
    
    Training:
        1. Collect jailbreak/benign examples
        2. Embed with sentence-transformers
        3. Train LogisticRegression
        4. Save with joblib
    
    Usage:
        registry.register(CustomEmbeddingClassifierRule(
            model_path="/models/jailbreak-classifier.joblib"
        ))
    """
    
    rule_id: str = "custom-embedding-classifier"
    version: str = "1.0.0"
    supports_event_types: frozenset[str] = frozenset({"llm_before"})
    cost: RuleCost = RuleCost.FAST  # Embeddings are fast enough for sync
    enabled: bool = True
    severity: GuardrailSeverity = GuardrailSeverity.HIGH
    
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    model_path: str = ""
    threshold: float = 0.8
    
    _embedder: Any = field(default=None, repr=False)
    _classifier: Any = field(default=None, repr=False)
    
    def _load_models(self) -> bool:
        if self._embedder is not None:
            return True
        
        try:
            from sentence_transformers import SentenceTransformer
            import joblib
            
            self._embedder = SentenceTransformer(self.embedding_model)
            self._classifier = joblib.load(self.model_path)
            return True
        except Exception:
            return False
    
    async def evaluate(
        self,
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> GuardrailDecision | None:
        if not self._load_models():
            return None
        
        text = event.text_content
        if not text:
            return None
        
        # Embed and predict
        embedding = self._embedder.encode(text).reshape(1, -1)
        proba = self._classifier.predict_proba(embedding)[0]
        jailbreak_score = proba[1] if len(proba) > 1 else proba[0]
        
        if jailbreak_score >= self.threshold:
            return GuardrailDecision(
                action=GuardrailAction.STOP,
                rule_id=self.rule_id,
                reason=f"Custom classifier: jailbreak (score: {jailbreak_score:.3f})",
                severity=self.severity,
                confidence=jailbreak_score,
                effects=("flag_trajectory",),
                classifier_result={
                    "model": "custom-embedding",
                    "score": float(jailbreak_score),
                },
                stop=StopSpec(error_code="JAILBREAK_CUSTOM"),
            )
        
        return None
```

---

## 9. GuardrailGateway

### 9.1 Core Implementation

```python
@dataclass
class GatewayConfig:
    """Gateway configuration."""
    mode: Literal["shadow", "enforce"] = "enforce"
    sync_timeout_ms: float = 15.0
    sync_parallel: bool = True
    async_enabled: bool = True
    sync_fail_open: bool = False
    async_fail_open: bool = True


@dataclass
class GuardrailGateway:
    """Unified gateway with extensibility hooks.
    
    Key design decisions:
    - Context snapshot is passed explicitly, never stored in GuardrailContext
    - Both sync and async paths return list[GuardrailDecision] for symmetry
    - DecisionPolicy resolves all decisions uniformly
    """
    
    registry: RuleRegistry
    guard_inbox: SteeringGuardInbox
    config: GatewayConfig = field(default_factory=GatewayConfig)
    
    # Extensibility hooks
    decision_policy: DecisionPolicy = field(default_factory=DefaultDecisionPolicy)
    risk_router: RiskRouter = field(default_factory=DefaultRiskRouter)
    context_builder: ContextSnapshotBuilder = field(default_factory=ContextSnapshotBuilder)
    
    async def evaluate(
        self,
        ctx: GuardrailContext,
        event: GuardrailEvent,
    ) -> GuardrailDecision:
        """Evaluate with hybrid sync/async execution."""
        
        # Build context snapshot (passed explicitly, not stored in ctx)
        context_snapshot = self.context_builder.build(event, ctx)
        
        # Get routing decision
        routing = self.risk_router.route(event, context_snapshot)
        
        if routing.skip_evaluation:
            return GuardrailDecision(
                action=GuardrailAction.ALLOW,
                rule_id="__skip__",
                reason="Skipped by risk router",
            )
        
        # === SYNC EVALUATION ===
        sync_decisions = await self._evaluate_sync(event, context_snapshot)
        
        # Early exit on sync STOP
        sync_stops = [d for d in sync_decisions if d.action == GuardrailAction.STOP]
        if sync_stops:
            return self.decision_policy.resolve(sync_stops, event, context_snapshot)
        
        # === ASYNC SUBMISSION ===
        correlation_id: str | None = None
        if self.config.async_enabled:
            correlation_id = await self._submit_async(event, context_snapshot, routing)
        
        # === ASYNC WAIT (based on risk routing) ===
        async_decisions: list[GuardrailDecision] = []
        if correlation_id and routing.async_wait_ms > 0:
            async_decisions = await self._wait_for_async(
                correlation_id,
                timeout_ms=routing.async_wait_ms,
                on_timeout=routing.on_timeout,
            )
        
        # === RESOLVE FINAL DECISION ===
        all_decisions = sync_decisions + async_decisions
        
        if not all_decisions:
            return GuardrailDecision(
                action=GuardrailAction.ALLOW,
                rule_id="__default__",
                reason="No rules triggered",
            )
        
        final = self.decision_policy.resolve(all_decisions, event, context_snapshot)
        
        # Process effects
        self._process_effects(final, ctx)
        
        return final
    
    async def _evaluate_sync(
        self,
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> list[GuardrailDecision]:
        """Evaluate fast sync rules."""
        rules = self.registry.get_sync_rules(event.event_type)
        if not rules:
            return []
        
        async def safe_eval(rule: GuardrailRule) -> GuardrailDecision | None:
            try:
                return await asyncio.wait_for(
                    rule.evaluate(event, context_snapshot),
                    timeout=self.config.sync_timeout_ms / 1000,
                )
            except Exception:
                return None
        
        if self.config.sync_parallel:
            results = await asyncio.gather(*[safe_eval(r) for r in rules])
        else:
            results = [await safe_eval(r) for r in rules]
        
        return [d for d in results if d is not None]
    
    async def _submit_async(
        self,
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
        routing: RiskRoutingDecision,
    ) -> str:
        """Submit to async evaluation."""
        steering_event = SteeringGuardEvent(
            event_type=event.event_type,
            run_id=event.run_id,
            payload={
                "text_content": event.text_content,
                "tool_name": event.tool_name,
                "tool_args": event.tool_args,
                **event.payload,
            },
            context_snapshot=context_snapshot.to_dict(),
            required_rules=routing.required_async_rules,
        )
        return await self.guard_inbox.submit(steering_event)
    
    async def _wait_for_async(
        self,
        correlation_id: str,
        timeout_ms: float,
        on_timeout: Literal["allow", "pause", "stop"],
    ) -> list[GuardrailDecision]:
        """Wait for async with configurable timeout behavior."""
        try:
            response = await self.guard_inbox.await_response(
                correlation_id,
                timeout_s=timeout_ms / 1000,
            )
            # Mark all decisions as async
            for d in response.decisions:
                d.was_sync = False
            return response.decisions
        except asyncio.TimeoutError:
            if on_timeout == "stop":
                return [GuardrailDecision(
                    action=GuardrailAction.STOP,
                    rule_id="__timeout__",
                    reason="Async evaluation timed out (fail-closed)",
                    stop=StopSpec(error_code="GUARDRAIL_TIMEOUT"),
                )]
            elif on_timeout == "pause":
                return [GuardrailDecision(
                    action=GuardrailAction.PAUSE,
                    rule_id="__timeout__",
                    reason="Async evaluation timed out",
                    pause=PauseSpec(prompt="Safety check timed out"),
                )]
            return []
    
    def _process_effects(
        self,
        decision: GuardrailDecision,
        ctx: GuardrailContext,
    ) -> None:
        """Process side effects from decision."""
        for effect in decision.effects:
            if effect == "increment_strike":
                category = decision.rule_id.split("-")[0]
                ctx.strike_counts[category] = ctx.strike_counts.get(category, 0) + 1
            elif effect == "flag_trajectory":
                # Downstream: add to trajectory metadata
                pass
            elif effect == "emit_alert":
                # Downstream: send to alerting system
                pass
```

### 9.2 Rule Registry

```python
class RuleRegistry:
    """Registry for guardrail rules."""
    
    def __init__(self) -> None:
        self._sync_rules: list[GuardrailRule] = []
        self._async_rules: list[GuardrailRule] = []
    
    def register(self, rule: GuardrailRule) -> None:
        """Register a rule based on its cost."""
        if rule.cost == RuleCost.FAST:
            self._sync_rules.append(rule)
        else:
            self._async_rules.append(rule)
    
    def get_sync_rules(self, event_type: str) -> list[GuardrailRule]:
        """Get sync rules applicable to an event type."""
        return [
            r for r in self._sync_rules
            if r.enabled and event_type in r.supports_event_types
        ]
    
    def get_async_rules(self, event_type: str) -> list[GuardrailRule]:
        """Get async rules applicable to an event type."""
        return [
            r for r in self._async_rules
            if r.enabled and event_type in r.supports_event_types
        ]
```

---

## 10. Policy Pack Configuration

```yaml
# policies/default.yaml

policy_pack: "default"
version: "1.0.0"

gateway:
  mode: "enforce"
  sync:
    timeout_ms: 15
    parallel: true
    fail_open: false
  async:
    enabled: true
    fail_open: true

# Tool risk tiers (used by RiskRouter and ContextBuilder)
tool_risks:
  "filesystem.delete": "critical"
  "admin.execute": "critical"
  "filesystem.write": "high"
  "database.modify": "high"
  "filesystem.read": "medium"
  "__default__": "medium"

# Built-in sync rules
sync_rules:
  - id: "tool-allowlist"
    enabled: true
    config:
      denied_tools:
        - "admin.execute"
        - "filesystem.delete"

  - id: "secret-redaction"
    enabled: true

  - id: "injection-patterns"
    enabled: true

# Deep async rules (bring your own)
async_rules:
  - id: "hf-jailbreak"
    enabled: true
    config:
      model_name: "protectai/deberta-v3-base-prompt-injection-v2"
      threshold: 0.85

# Environment overrides
environments:
  dev:
    gateway:
      mode: "shadow"
  prod:
    gateway:
      mode: "enforce"
      sync:
        fail_open: false
```

---

## 11. Quick Start

### 11.1 Minimal Setup (Built-in Rules Only)

```python
from penguiflow.planner import ReactPlanner
from penguiflow.planner.guardrails import (
    GuardrailGateway,
    GatewayConfig,
    RuleRegistry,
    InMemoryGuardInbox,
    AsyncRuleEvaluator,
    ToolAllowlistRule,
    SecretRedactionRule,
    InjectionPatternRule,
)

# Create registry with built-in rules
registry = RuleRegistry()
registry.register(ToolAllowlistRule(denied_tools={"admin.execute"}))
registry.register(SecretRedactionRule())
registry.register(InjectionPatternRule())

# Create gateway
gateway = GuardrailGateway(
    registry=registry,
    guard_inbox=InMemoryGuardInbox(AsyncRuleEvaluator(registry)),
    config=GatewayConfig(mode="enforce"),
)

# Create planner with guardrails
planner = ReactPlanner(
    llm="gpt-4",
    nodes=[...],
    guardrail_gateway=gateway,
)
```

### 11.2 With HuggingFace Classifier

```python
from penguiflow.planner.guardrails import HuggingFaceJailbreakRule
import torch

# Add ML classifier (async/deep)
registry.register(HuggingFaceJailbreakRule(
    threshold=0.85,
    device=0 if torch.cuda.is_available() else -1,
))
```

### 11.3 Custom Risk Router

```python
from penguiflow.planner.guardrails import RiskRouter, RiskRoutingDecision

class StrictRiskRouter:
    """Fail-closed for all high-risk tools."""
    
    def route(self, event, context_snapshot) -> RiskRoutingDecision:
        if context_snapshot.max_tool_risk.value in ("high", "critical"):
            return RiskRoutingDecision(
                async_wait_ms=300,
                on_timeout="stop",  # Fail-closed
            )
        return RiskRoutingDecision(async_wait_ms=0)

gateway = GuardrailGateway(
    registry=registry,
    guard_inbox=inbox,
    risk_router=StrictRiskRouter(),
)
```

---

## 12. Deployment Patterns

### Pattern A: Embedded (Default)

```
┌─────────────────────────────────────────┐
│  PenguiFlow Instance                    │
│  ┌─────────────────────────────────────┐│
│  │ ReactPlanner                        ││
│  │   └── GuardrailGateway              ││
│  │         ├── SyncEvaluator           ││
│  │         └── InMemoryGuardInbox      ││
│  └─────────────────────────────────────┘│
└─────────────────────────────────────────┘

✓ Simple, no external dependencies
✓ Lowest latency
```

### Pattern B: Sidecar (Enterprise)

```
┌─────────────────────────────────┐    ┌─────────────────────────┐
│  Pod                            │    │  Guardrail Sidecar      │
│  ┌─────────────────────────────┐│    │  ┌─────────────────────┐│
│  │ ReactPlanner                │├────┤  │ ML Classifiers      ││
│  │   └── GuardrailGateway      ││gRPC│  │ Presidio            ││
│  │         └── GrpcGuardInbox ─┼┼────┤  │ External APIs       ││
│  └─────────────────────────────┘│    │  └─────────────────────┘│
└─────────────────────────────────┘    └─────────────────────────┘

✓ Resource isolation
✓ Independent scaling
```

---

## 13. Appendices

### Appendix A: Jailbreak Intent Reference

| Intent | Indicators | Default Action | Effects |
|--------|-----------|----------------|---------|
| `JB_OVERRIDE` | "ignore instructions", "DAN mode" | STOP | flag_trajectory, increment_strike |
| `EXFIL_PROMPT` | "show system prompt", "reveal rules" | STOP | flag_trajectory, increment_strike |
| `TOOL_ESCALATION` | "run as root", "bypass restrictions" | STOP | flag_trajectory, increment_strike |
| `INDIRECT_INJECTION` | Malicious text in RAG/web content | REDACT | flag_trajectory |
| `SOCIAL_ENGINEERING` | "I'm the developer", "pre-approved" | PAUSE | flag_trajectory |

### Appendix B: Recommended Off-the-Shelf Models

| Model | Type | Latency | Notes |
|-------|------|---------|-------|
| [`protectai/deberta-v3-base-prompt-injection-v2`](https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2) | HuggingFace | ~50ms | Production-ready, well-maintained |
| [`madhurjindal/Jailbreak-Detector-2-XL`](https://huggingface.co/madhurjindal/Jailbreak-Detector-2-XL) | HuggingFace | varies | Large model, strong recall |
| [NVIDIA NeMo Guardrails](https://github.com/NVIDIA/NeMo-Guardrails) | Framework | varies | Comprehensive, self-hosted |

### Appendix C: Tool Risk Tier Guidelines

| Tier | Capabilities | Default Wait | On Timeout |
|------|-------------|--------------|------------|
| CRITICAL | Admin, delete, credentials | 200ms | STOP |
| HIGH | Write, network, modify | 200ms | PAUSE |
| MEDIUM | Read, search, query | 100ms | ALLOW |
| LOW | Display, format | 0ms | ALLOW |

### Appendix D: Standard Effects

| Effect | Description | When to Use |
|--------|-------------|-------------|
| `flag_trajectory` | Mark trajectory for review | Any violation |
| `increment_strike` | Increment strike counter | Repeated violations |
| `emit_alert` | Send to alerting system | High-severity detections |
| `quarantine_session` | Flag session for review | Serious policy violations |

---

## 14. Changelog

### v5.0.0 (2026-01-16)
- **Breaking**: `SteeringGuardResponse.decision` → `SteeringGuardResponse.decisions: list` (async multi-decision support)
- **Breaking**: `GuardrailRule.evaluate()` now takes `(event, context_snapshot)` instead of `(ctx, event)`
- Added `GuardrailDecision.effects: tuple[str, ...]` for secondary effects (flag, alert, strike)
- Added `GuardrailRule` protocol definition
- Added `GuardrailContext` and `GuardrailEvent` definitions
- Added `AsyncRuleEvaluator` implementation
- Added `RuleRegistry` implementation
- `RiskRoutingDecision.required_async_rules` now actually filters async rules
- `DefaultRiskRouter` now considers `llm_before` with high-risk tools and suspicious request signals
- Fixed: Context snapshot no longer stored in `ctx.policy_config` (passed explicitly)
- Fixed: HuggingFace `device` parameter now uses int (-1 for CPU, 0+ for GPU)
- Fixed: Added missing `import re` in `ContextSnapshotBuilder`
- Added: Documentation note about `drain_responses` global behavior in `InMemoryGuardInbox`

### v4.0.0 (2026-01-14)
- Refocused on v2 core architecture with extensibility hooks
- Removed ML classifier implementations (now examples only)
- Added `DecisionPolicy` protocol for custom resolution
- Added `RiskRouter` protocol for dynamic routing
- Added `ContextSnapshotV1` for agent-aware context
- Added `JailbreakIntent` taxonomy (labels only)
- Added integration examples: HuggingFace, custom
- Simplified policy pack configuration

### v3.0.0 (2026-01-13)
- Added layered classifier architecture (scope-creeped, superseded by v4)

### v2.0.0 (2026-01-12)
- Introduced hybrid sync/async architecture
- Added SteeringGuardInbox

### v1.0.0 (2026-01-11)
- Initial RFC

---

**Document Version:** 5.0.0  
**Last Updated:** 2026-01-16  
**Status:** Final Draft - Ready for Architecture Review

---

## 15. Phased Implementation Plan (Opt-In, No Regressions)

This plan is designed to introduce guardrails safely without breaking existing behavior. Each phase is **opt-in** and must preserve current planner behavior when guardrails are not configured.

### Phase 0: Foundations (No-Op by Default)

**Goal:** Land core data models and gateway interfaces with zero runtime behavior change.

**Scope:**
- Add data models and protocols: `GuardrailDecision`, `GuardrailEvent`, `GuardrailContext`, `GuardrailRule`, `DecisionPolicy`, `RiskRouter`, `ContextSnapshotV1`, `RuleRegistry`.
- Introduce `GuardrailGateway` and `GatewayConfig` with defaults that yield ALLOW.
- Add `ContextSnapshotBuilder` with conservative defaults and minimal signals.
- Add `SteeringGuardInbox` interface and `InMemoryGuardInbox` implementation.

**Acceptance:**
- Planner runs identically when no gateway is injected.
- Unit tests for model serialization/deserialization and default resolution paths.
- No new dependencies required for core.

### Phase 1: Sync Rules + Planner Hooks (Opt-In, Safe Defaults)

**Goal:** Enable synchronous guardrails for pre-execution blocking and lightweight redaction.

**Scope:**
- Implement and register built-in sync rules: `ToolAllowlistRule`, `SecretRedactionRule`, `InjectionPatternRule`.
- Add planner event emission at key points:
  - `llm_before`
  - `tool_call_start`
  - `tool_call_result`
  - `llm_stream_chunk`
- Integrate `GuardrailGateway.evaluate` into planner pipeline, guarded by opt-in config.
- Implement redaction application for `llm_stream_chunk` and `tool_call_result` outputs.

**Acceptance:**
- `guardrail_gateway` is optional; no change to default planner behavior.
- Tool execution is blocked only when gateway is provided and rules trigger.
- Streaming outputs can be redacted without breaking stream framing.
- Negative-path tests: allowlist denial, injection pattern STOP, secret redaction in output.

### Phase 2: Async Path + Risk Routing (Opt-In, Fail-Open Default)

**Goal:** Add async rule execution with configurable waiting and timeout behavior.

**Scope:**
- Implement `AsyncRuleEvaluator` and async rule evaluation flow.
- Implement `DefaultRiskRouter` and `RiskRoutingDecision`.
- Add `SteeringGuardInbox.await_response` usage with routing-based wait time.
- Add optional timeout behaviors (allow/pause/stop) with safe defaults.
- Wire `drain_responses` to allow late decisions to be handled (best-effort).

**Acceptance:**
- Async eval does not block hot path unless configured to wait.
- Default behavior is fail-open for async timeouts.
- Tests for required rules filtering and timeout handling.

### Phase 3: Policy Packs + Configuration (Opt-In, Backwards-Compatible)

**Goal:** Provide optional policy packs without forcing new config surface area.

**Scope:**
- Introduce `policies/default.yaml` example and loader (optional).
- Allow explicit override of rule config, tool risk tiers, and gateway mode.
- Ensure policy configuration can be bypassed entirely by code.

**Acceptance:**
- Planner still runs with no policy pack.
- Invalid policy config fails fast with clear error messages when explicitly enabled.
- Tests for rule enable/disable and tool risk tier resolution.

### Phase 4: Enterprise Hooks + Integration Examples (Optional)

**Goal:** Provide integration examples without making them dependencies.

**Scope:**
- Document and optionally ship examples for external classifiers:
  - HuggingFace / Transformers (two prompt-injection models)
  - Custom embeddings + sklearn (binary in-scope/out-of-scope)
- Provide example `RiskRouter` policies (fail-closed for critical tools).
- Provide example `DecisionPolicy` (e.g., voting or tenant-specific).

**Acceptance:**
- No extra dependencies in core installation.
- Examples are optional and documented.

### Phase 4 Addendum: Example Design (Analytics Assistant)

This phase includes two opt-in example tracks, both intended to run locally
and load at agent startup without sidecars.

#### Example A: HuggingFace Prompt Injection (Async)

Provide an async guardrail rule with two model options:
- `protectai/deberta-v3-base-prompt-injection-v2`
- `madhurjindal/Jailbreak-Detector-2-XL`

Guidance:
- Configurable `threshold` and `device`.
- Augment input with context signals (tool risk, prior strikes).
- Default action: STOP on high-confidence injection.

#### Example B: Train-Your-Own Scope Classifier (Async, Binary)

Domain: **Analytics Assistant** (in-scope vs out-of-scope).

Goal: Avoid off-topic requests (e.g., weather) while supporting benign
continuations (e.g., "yes, go on") by enriching classifier input.

Workflow:
1. **Few-shot seeds**: user provides ~10–20 in-scope and out-of-scope examples.
2. **Dataset bootstrapping**: a local script uses the LLM to expand to
   ~100–200 examples.
3. **Local training**: embeddings + LogisticRegression (or LinearSVM).
4. **Runtime loading**: in-memory classifier at agent startup.

Classifier input should include minimal context to disambiguate short replies:
```
[USER] yes, go on
[LAST_ASSISTANT] ...summary of last response...
[TASK_SCOPE] analytics reporting, metrics, charts
[AVAILABLE_TOOLS] query_metrics, chart_line, table_summary
```

Decision behavior:
- Default action: RETRY with clarification guidance (not hard stop).
- Guidance should preserve developer voice by using the existing system prompt
  tone and a short clarification request or gentle out-of-scope refusal.

### Phase 5: Hardening + Observability (Optional, Non-Blocking)

**Goal:** Improve safety, auditability, and operational confidence.

**Scope:**
- Add telemetry hooks for decision counts, latency, and timeout rates.
- Standardize error codes and reporting surface for downstream integration.
- Add integration tests for multi-step planner flows with guardrails on.

**Acceptance:**
- No behavior changes unless telemetry hooks are configured.
- Coverage targets maintained; negative-path tests included for new features.

---

## 16. Risk & Concern Mitigations (Best Practices)

### 16.1 Event Coverage Consistency

**Concern:** Missing or inconsistent events reduce guardrail effectiveness.

**Mitigation:**
- Define a strict event contract for planner stages (`llm_before`, `tool_call_start`, `tool_call_result`, `llm_stream_chunk`).
- Add tests to ensure each stage emits an event when guardrails are enabled.
- Keep event payload minimal and stable; introduce new fields via `payload`.

### 16.2 Streaming Redaction Safety

**Concern:** Redacting stream chunks may break offsets or formatting.

**Mitigation:**
- Treat redactions as chunk-local, not global offsets, unless buffering is enabled.
- Prefer token/substring replacement without altering stream protocol framing.
- When offsets are required, buffer and re-emit with corrected offsets (opt-in).

### 16.3 Timeout and Fail-Closed Behavior

**Concern:** Aggressive fail-closed settings may block legitimate flows.

**Mitigation:**
- Default async timeout behavior is fail-open (`allow`).
- Provide `RiskRouter` examples for strict environments.
- Allow per-tenant or per-tool risk override.

### 16.4 Configuration Surface Area

**Concern:** Too many knobs can cause misconfiguration.

**Mitigation:**
- Keep defaults safe and minimal.
- Validate policy pack inputs with explicit schema.
- Ensure feature is entirely opt-in when no gateway is provided.

### 16.5 Classifier Stability and Schema Drift

**Concern:** External classifier integrations break when context schema changes.

**Mitigation:**
- Version `ContextSnapshotV1` and preserve backward compatibility.
- Add explicit conversion logic when extending the schema.
- Document schema changes in RFC changelog and release notes.

### 16.6 Runtime Performance Impact

**Concern:** Guardrails add latency to hot paths.

**Mitigation:**
- Restrict sync rules to fast operations only.
- Evaluate sync rules in parallel with a hard timeout.
- Push all heavy classifiers into async path by default.

### 16.7 Failures in Guardrail Subsystems

**Concern:** Guardrail errors should not crash runs unless configured.

**Mitigation:**
- Fail-open for async exceptions by default.
- Fail-open for sync exceptions only if explicitly configured.
- Provide clear error codes when failures should stop execution.

---

## 17. Operational Checklist (Tests + Rollout Gates)

This checklist keeps guardrails opt-in, predictable, and regression-free.

### 17.1 Tests (Must Have)

- **Unit**: Rule evaluation for built-ins (allowlist, redaction, injection).
- **Unit**: `DecisionPolicy` resolution ordering and effect merging.
- **Unit**: `RiskRouter` routing for risk tiers and suspicious prompts.
- **Unit**: `ContextSnapshotBuilder` signal extraction (system info, capability change).
- **Unit**: Async evaluator filtering (`required_rules`) and timeout behavior.
- **Integration**: Planner run with guardrails disabled (baseline behavior).
- **Integration**: Planner run with guardrails enabled (STOP, REDACT, PAUSE, RETRY).
- **Streaming**: Redaction applied to `llm_stream_chunk` without breaking framing.
- **Negative-path**: Async timeout in `allow`/`pause`/`stop` modes.

### 17.2 Rollout Gates (Per Phase)

- **Gate A (Phase 0)**: No behavior change without `guardrail_gateway`.
- **Gate B (Phase 1)**: Tool execution blocked only when explicit allow/deny rules applied.
- **Gate C (Phase 2)**: Async path does not block hot path unless router requests wait.
- **Gate D (Phase 3)**: Policy packs are optional and validated when enabled.
- **Gate E (Phase 4/5)**: External integrations are optional and non-core dependencies.

---

## 18. Concrete Task Breakdown (All Phases)

This section enumerates files, APIs, and tests required per phase. All paths are relative to repo root.

### Phase 0: Foundations

**Files (new/modified):**
- `penguiflow/planner/guardrails/__init__.py` (new)
- `penguiflow/planner/guardrails/models.py` (new): data models + enums
- `penguiflow/planner/guardrails/context.py` (new): `GuardrailContext`, `GuardrailEvent`, `ContextSnapshotV1`, builder
- `penguiflow/planner/guardrails/protocols.py` (new): `GuardrailRule`, `DecisionPolicy`, `RiskRouter`
- `penguiflow/planner/guardrails/registry.py` (new): `RuleRegistry`
- `penguiflow/planner/guardrails/gateway.py` (new): `GuardrailGateway`, `GatewayConfig`
- `penguiflow/steering/guard_inbox.py` (new): `SteeringGuardInbox`, `InMemoryGuardInbox`, `SteeringGuardEvent/Response`
- `penguiflow/planner/react.py` (modified): optional `guardrail_gateway` argument plumbed through

**APIs:**
- `GuardrailGateway.evaluate(ctx, event) -> GuardrailDecision`
- `RuleRegistry.register(rule)`
- `SteeringGuardInbox.submit/await_response/drain_responses`

**Tests:**
- `tests/guardrails/test_models.py` (new)
- `tests/guardrails/test_gateway_defaults.py` (new)
- `tests/guardrails/test_registry.py` (new)
- `tests/steering/test_guard_inbox.py` (new)

### Phase 1: Sync Rules + Planner Hooks

**Files (new/modified):**
- `penguiflow/planner/guardrails/rules.py` (new): built-in sync rules
- `penguiflow/planner/react.py` (modified): emit guardrail events + apply decisions
- `penguiflow/planner/models.py` (modified if event types or streaming hooks live here)

**APIs:**
- `ToolAllowlistRule`, `SecretRedactionRule`, `InjectionPatternRule`
- Redaction application helper (e.g., `apply_redactions(text, specs)`)

**Tests:**
- `tests/guardrails/test_rules_sync.py` (new)
- `tests/guardrails/test_redaction.py` (new)
- `tests/planner/test_guardrails_sync_integration.py` (new)

### Phase 2: Async Path + Risk Routing

**Files (new/modified):**
- `penguiflow/planner/guardrails/async_eval.py` (new): `AsyncRuleEvaluator`
- `penguiflow/planner/guardrails/routing.py` (new): `DefaultRiskRouter`, `RiskRoutingDecision`
- `penguiflow/planner/guardrails/gateway.py` (modified): async submission + wait path
- `penguiflow/steering/guard_inbox.py` (modified): timeout handling and draining

**APIs:**
- `AsyncRuleEvaluator.evaluate(event) -> list[GuardrailDecision]`
- `RiskRouter.route(event, context_snapshot) -> RiskRoutingDecision`

**Tests:**
- `tests/guardrails/test_async_evaluator.py` (new)
- `tests/guardrails/test_risk_router.py` (new)
- `tests/guardrails/test_gateway_async.py` (new)

### Phase 3: Policy Packs + Configuration

**Files (new/modified):**
- `penguiflow/planner/guardrails/config.py` (new): policy pack loader + validation
- `docs/RFC/ToDo/RFC_GUARDRAILS.MD` (modified, this file)
- `docs/policies/default.yaml` (new example; optional)

**APIs:**
- `load_policy_pack(path) -> GuardrailConfig`
- `apply_policy_config(registry, gateway, builder, router)`

**Tests:**
- `tests/guardrails/test_policy_pack.py` (new)
- `tests/guardrails/test_policy_validation.py` (new)

### Phase 4: Enterprise Hooks + Integration Examples

**Files (new/modified):**
- `examples/guardrails/huggingface/README.md` (new)
- `examples/guardrails/custom/README.md` (new)
- `examples/guardrails/scope_classifier/README.md` (new)
- `examples/guardrails/scope_classifier/train_classifier.py` (new)
- `examples/guardrails/scope_classifier/rule.py` (new)
- `docs/guardrails/INTEGRATIONS.md` (new)

**APIs:**
- None in core; examples only.

**Tests:**
- Optional smoke tests for example configs (skip if deps missing).

### Phase 5: Hardening + Observability

**Files (new/modified):**
- `penguiflow/planner/guardrails/telemetry.py` (new): hooks + counters
- `penguiflow/planner/guardrails/gateway.py` (modified): emit telemetry
- `docs/guardrails/OBSERVABILITY.md` (new)

**APIs:**
- `GuardrailTelemetryHook` protocol (optional)
- `GatewayConfig.telemetry_enabled` (optional)

**Tests:**
- `tests/guardrails/test_telemetry.py` (new)
- `tests/planner/test_guardrails_end_to_end.py` (new)
