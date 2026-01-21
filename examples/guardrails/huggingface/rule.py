"""HuggingFace prompt-injection guardrail rule (async)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from penguiflow.planner.guardrails import (
    ContextSnapshotV1,
    GuardrailAction,
    GuardrailDecision,
    GuardrailEvent,
    GuardrailSeverity,
    JailbreakIntent,
    RuleCost,
    StopSpec,
)


@dataclass
class HuggingFaceJailbreakRule:
    """Jailbreak detection using HuggingFace text-classification pipelines."""

    rule_id: str = "hf-jailbreak"
    version: str = "1.0.0"
    supports_event_types: frozenset[str] = frozenset({"llm_before"})
    cost: RuleCost = RuleCost.DEEP
    enabled: bool = True
    severity: GuardrailSeverity = GuardrailSeverity.CRITICAL

    model_name: str = "protectai/deberta-v3-base-prompt-injection-v2"
    threshold: float = 0.85
    device: int = -1

    _pipeline: Any = field(default=None, repr=False)

    def _load_pipeline(self) -> bool:
        if self._pipeline is not None:
            return True
        try:
            from transformers import pipeline
        except Exception:
            return False
        self._pipeline = pipeline(
            "text-classification",
            model=self.model_name,
            device=self.device,
        )
        return True

    async def evaluate(
        self,
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> GuardrailDecision | None:
        if not self._load_pipeline():
            return None

        text = event.text_content
        if not text or len(text) < 10:
            return None

        augmented = _augment_with_context(text, context_snapshot)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._pipeline(augmented, truncation=True, max_length=512),
        )

        label = str(result[0].get("label") or "").upper()
        score = float(result[0].get("score", 0.0))
        if _is_injection_label(label) and score >= self.threshold:
            return GuardrailDecision(
                action=GuardrailAction.STOP,
                rule_id=self.rule_id,
                reason=f"Prompt injection detected (score: {score:.3f})",
                severity=self.severity,
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


def _is_injection_label(label: str) -> bool:
    if label in {"INJECTION", "JAILBREAK", "MALICIOUS", "PROMPT_INJECTION"}:
        return True
    if label in {"BENIGN", "SAFE", "NORMAL"}:
        return False
    if label == "LABEL_1":
        return True
    if label == "LABEL_0":
        return False
    return False


def _augment_with_context(text: str, context: ContextSnapshotV1) -> str:
    features = []
    if context.max_tool_risk.value in ("high", "critical"):
        features.append("[HIGH_RISK_TOOLS_AVAILABLE]")
    if context.requests_system_info:
        features.append("[REQUESTS_SYSTEM_INFO]")
    if context.previous_violations > 0:
        features.append(f"[PRIOR_VIOLATIONS:{context.previous_violations}]")
    if features:
        return " ".join(features) + " " + text
    return text
