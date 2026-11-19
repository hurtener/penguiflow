"""Shared helpers for React planner modules."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import ValidationError


def _safe_json_dumps(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _serialize_validation_errors(exc: ValidationError) -> str:
    try:
        return json.dumps(exc.errors(), ensure_ascii=False, default=str)
    except Exception:
        return str(exc)
