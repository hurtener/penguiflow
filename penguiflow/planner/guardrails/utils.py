"""Guardrail helper utilities."""

from __future__ import annotations

from .models import RedactionSpec


def apply_redactions_to_text(text: str, redactions: tuple[RedactionSpec, ...]) -> str:
    """Apply redaction specs to a text string using offsets when available."""

    if not redactions:
        return text

    replacements: list[tuple[int, int, str]] = []
    for spec in redactions:
        if spec.start_offset is None or spec.end_offset is None:
            continue
        start = max(0, spec.start_offset)
        end = max(start, spec.end_offset)
        replacements.append((start, end, spec.replacement))

    if not replacements:
        return text

    replacements.sort(key=lambda item: item[0], reverse=True)
    redacted = text
    for start, end, replacement in replacements:
        redacted = f"{redacted[:start]}{replacement}{redacted[end:]}"
    return redacted


__all__ = ["apply_redactions_to_text"]
