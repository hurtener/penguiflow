"""Slice-0 input contracts for query suites and curated trace ids."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_query_suite(path: str | Path) -> dict[str, Any]:
    """Load and minimally validate a query suite for eval runs."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    queries = payload.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError("query_suite must include non-empty queries")
    for row in queries:
        if not isinstance(row, dict):
            raise ValueError("query row must be an object")
        split = row.get("split")
        if split not in {"val", "test"}:
            raise ValueError(f"query split must be val/test, got {split!r}")
    return payload


def load_trace_ids(path: str | Path) -> list[str]:
    """Load curated trace ids, preserving order while removing duplicates."""

    ordered: list[str] = []
    seen: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        trace_id = line.strip()
        if not trace_id or trace_id in seen:
            continue
        seen.add(trace_id)
        ordered.append(trace_id)
    return ordered
