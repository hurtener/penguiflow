"""Framework-neutral, redacted evidence records for the learning control plane."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

_SENSITIVE_ATTRIBUTE_PARTS = frozenset(
    {
        "api_key",
        "authorization",
        "content",
        "cookie",
        "credential",
        "input",
        "message",
        "output",
        "password",
        "prompt",
        "secret",
        "token",
    }
)


def _require_non_empty(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be non-empty")
    return cleaned


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_ATTRIBUTE_PARTS)


def redact_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe evidence payload without content or credential fields.

    Evidence records are intentionally metadata-first. The LCP should link to an
    access-controlled source trace rather than copy prompts, tool payloads, or
    secret-bearing values into telemetry systems.
    """

    redacted: dict[str, Any] = {}
    for raw_key, value in attributes.items():
        key = str(raw_key).strip()
        if not key or _is_sensitive_key(key):
            continue
        try:
            json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            redacted[key] = str(value)
        else:
            redacted[key] = value
    return redacted


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    """Version-pinned identity for an evidence record."""

    agent_id: str
    deployment_digest: str
    trace_id: str | None = None
    evaluation_id: str | None = None
    candidate_id: str | None = None
    dataset_version: str | None = None
    metric_version: str | None = None
    policy_version: str | None = None
    scope_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "agent_id", _require_non_empty(self.agent_id, "agent_id"))
        object.__setattr__(self, "deployment_digest", _require_non_empty(self.deployment_digest, "deployment_digest"))


@dataclass(frozen=True, slots=True)
class EvidenceEvent:
    """A correlation record emitted outside the production agent request path."""

    event_type: str
    context: EvidenceContext
    attributes: Mapping[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: f"ev_{uuid4().hex}")
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", _require_non_empty(self.event_type, "event_type"))
        object.__setattr__(self, "event_id", _require_non_empty(self.event_id, "event_id"))
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        object.__setattr__(self, "attributes", redact_attributes(self.attributes))

    def record(self) -> dict[str, Any]:
        """Produce the redacted, JSON-serialisable record stored by evidence sinks."""

        payload = asdict(self.context)
        payload.update(
            {
                "event_id": self.event_id,
                "event_type": self.event_type,
                "occurred_at": self.occurred_at.isoformat(),
                "attributes": dict(self.attributes),
            }
        )
        return payload

    def telemetry_attributes(self) -> dict[str, str | bool | float | int]:
        """Flatten immutable identifiers for OpenTelemetry-compatible attributes."""

        flattened: dict[str, str | bool | float | int] = {
            "lcp.event_id": self.event_id,
            "lcp.event_type": self.event_type,
            "lcp.occurred_at": self.occurred_at.isoformat(),
        }
        for key, value in asdict(self.context).items():
            if value is not None:
                flattened[f"lcp.{key}"] = str(value)
        for key, value in self.attributes.items():
            if isinstance(value, (str, bool, float, int)):
                flattened[f"lcp.attr.{key}"] = value
        return flattened


__all__ = ["EvidenceContext", "EvidenceEvent", "redact_attributes"]
