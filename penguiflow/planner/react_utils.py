"""Shared helpers for React planner modules."""

from __future__ import annotations

import json
from typing import Any


def _safe_json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)
