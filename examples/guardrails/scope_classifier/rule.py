"""Scope classifier guardrail rule (async, binary in/out)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from penguiflow.planner.guardrails import (
    ContextSnapshotV1,
    GuardrailAction,
    GuardrailDecision,
    GuardrailEvent,
    GuardrailSeverity,
    RetrySpec,
    RuleCost,
)


@dataclass
class ScopeClassifierRule:
    """Binary in-scope vs out-of-scope classifier for analytics assistants."""

    rule_id: str = "scope-classifier"
    version: str = "1.0.0"
    supports_event_types: frozenset[str] = frozenset({"llm_before"})
    cost: RuleCost = RuleCost.DEEP
    enabled: bool = True
    severity: GuardrailSeverity = GuardrailSeverity.MEDIUM

    model_path: Path | None = None
    threshold: float = 0.6

    _encoder: Any = field(default=None, repr=False)
    _classifier: Any = field(default=None, repr=False)

    def _load_model(self) -> bool:
        if self._encoder is not None and self._classifier is not None:
            return True
        if self.model_path is None or not self.model_path.exists():
            return False
        try:
            import joblib
            from sentence_transformers import SentenceTransformer
        except Exception:
            return False

        payload = joblib.load(self.model_path)
        encoder_name = payload.get("encoder")
        classifier = payload.get("classifier")
        if not encoder_name or classifier is None:
            return False
        self._encoder = SentenceTransformer(encoder_name)
        self._classifier = classifier
        threshold = payload.get("threshold")
        if isinstance(threshold, (int, float)):
            self.threshold = float(threshold)
        return True

    async def evaluate(
        self,
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> GuardrailDecision | None:
        if not self._load_model():
            return None
        text = event.text_content
        if not text:
            return None

        augmented = _augment_with_context(text, context_snapshot, event.payload)
        loop = asyncio.get_running_loop()
        embedding = await loop.run_in_executor(
            None,
            lambda: self._encoder.encode([augmented], normalize_embeddings=True),
        )
        proba = self._classifier.predict_proba(embedding)[0]
        in_scope_score = float(proba[1]) if len(proba) > 1 else float(proba[0])

        if in_scope_score < self.threshold:
            return GuardrailDecision(
                action=GuardrailAction.RETRY,
                rule_id=self.rule_id,
                reason=f"Out-of-scope request (score: {in_scope_score:.2f})",
                severity=self.severity,
                confidence=1.0 - in_scope_score,
                retry=RetrySpec(
                    corrective_message=(
                        "I'm focused on analytics reporting. Could you clarify which metrics, "
                        "dimensions, or charts you want?"
                    )
                ),
                effects=("flag_trajectory",),
                classifier_result={"score": in_scope_score},
            )
        return None


def _augment_with_context(text: str, context: ContextSnapshotV1, payload: dict[str, Any]) -> str:
    parts = [f"[USER] {text}"]
    last_assistant = payload.get("last_assistant")
    if isinstance(last_assistant, str) and last_assistant.strip():
        parts.append(f"[LAST_ASSISTANT] {last_assistant.strip()}")
    task_scope = payload.get("task_scope")
    if isinstance(task_scope, str) and task_scope.strip():
        parts.append(f"[TASK_SCOPE] {task_scope.strip()}")
    if context.available_tools:
        tools = ", ".join(tool.name for tool in context.available_tools)
        parts.append(f"[AVAILABLE_TOOLS] {tools}")
    return "\n".join(parts)
