"""Developer-facing debugging helpers for PenguiFlow."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .metrics import FlowEvent


def format_flow_event(event: FlowEvent) -> dict[str, Any]:
    """Return a structured payload ready for logging.

    The returned dictionary mirrors :meth:`FlowEvent.to_payload` and flattens any
    embedded ``FlowError`` payload so that log aggregators can index the error
    metadata (``flow_error_code``, ``flow_error_message``, ...).
    """

    payload = dict(event.to_payload())
    error_payload: Mapping[str, Any] | None = event.error_payload
    if error_payload is not None:
        # Preserve the original payload for downstream consumers.
        payload["flow_error"] = dict(error_payload)
        for key, value in error_payload.items():
            payload[f"flow_error_{key}"] = value
    return payload


__all__ = ["format_flow_event"]
