"""Redaction helpers for skill content."""

from __future__ import annotations

import re
from collections.abc import Iterable

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s\-\.]?)?(?:\(?\d{3}\)?[\s\-\.]?)\d{3}[\s\-\.]?\d{4}")
_TOKEN_RE = re.compile(r"\b(?:bearer|api[_-]?key|token|sk)[\s:_-]*[A-Za-z0-9_\-]{8,}\b", re.IGNORECASE)
_URL_QUERY_RE = re.compile(r"(https?://[^\s?#]+)\?[^\s#]+")


def redact_pii(text: str) -> str:
    if not text:
        return text
    redacted = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    redacted = _PHONE_RE.sub("[REDACTED_PHONE]", redacted)
    redacted = _TOKEN_RE.sub("[REDACTED_TOKEN]", redacted)
    redacted = _URL_QUERY_RE.sub(r"\1", redacted)
    return redacted


def redact_tool_references(
    text: str,
    disallowed_tool_names: Iterable[str],
    *,
    tool_search_available: bool,
) -> str:
    names = [name for name in disallowed_tool_names if name]
    if not text or not names:
        return text
    escaped = sorted({re.escape(name) for name in names}, key=len, reverse=True)
    if not escaped:
        return text
    pattern = re.compile(r"(?<![A-Za-z0-9_.-])(" + "|".join(escaped) + r")(?![A-Za-z0-9_.-])")
    replacement = "a suitable tool (use tool_search)" if tool_search_available else "a suitable tool"
    return pattern.sub(replacement, text)


__all__ = ["redact_pii", "redact_tool_references"]
