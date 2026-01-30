from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from ..catalog import NodeSpec, ToolLoadingMode

_SCHEMA_VERSION = 1
_SIDE_EFFECT_RANK = {
    "pure": 0,
    "read": 1,
    "write": 2,
    "external": 3,
    "stateful": 4,
}


@dataclass(frozen=True, slots=True)
class _ToolRecord:
    name: str
    description: str
    tags: list[str]
    side_effects: str
    loading_mode: str
    record_hash: str


class ToolSearchCache:
    def __init__(
        self,
        *,
        cache_dir: str,
        preferred_namespaces: Sequence[str] | None = None,
        always_loaded_patterns: Sequence[str] | None = None,
        fts_fallback_to_regex: bool = True,
        enable_incremental_index: bool = True,
        rebuild_cache_on_init: bool = False,
        max_search_results: int = 10,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._db_path = self._cache_dir / "tool_cache.db"
        self._preferred_namespaces = list(preferred_namespaces or [])
        self._always_loaded_patterns = list(always_loaded_patterns or [])
        self._fts_fallback_to_regex = bool(fts_fallback_to_regex)
        self._enable_incremental_index = bool(enable_incremental_index)
        self._rebuild_cache_on_init = bool(rebuild_cache_on_init)
        self._max_search_results = int(max_search_results)
        if self._max_search_results <= 0:
            self._max_search_results = 10
        self._fts_available: bool | None = None

    @property
    def fts_available(self) -> bool:
        if self._fts_available is None:
            self._ensure_schema()
        return bool(self._fts_available)

    @property
    def db_path(self) -> Path:
        return self._db_path

    def tool_count(self) -> int:
        self._ensure_schema()
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute("SELECT COUNT(1) FROM tools").fetchone()
        return int(row[0] if row else 0)

    def sync_tools(self, specs: Sequence[NodeSpec]) -> None:
        records = [_tool_record(spec) for spec in specs]
        fingerprint = _catalog_fingerprint(records)

        self._ensure_schema()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            existing_fingerprint = _get_meta(conn, "catalog_fingerprint")
            if (
                existing_fingerprint == fingerprint
                and not self._rebuild_cache_on_init
                and self._enable_incremental_index
            ):
                return

            if self._rebuild_cache_on_init or not self._enable_incremental_index:
                conn.execute("DELETE FROM tools")
                to_upsert = records
                to_delete: Iterable[str] = []
            else:
                existing = {row[0]: row[1] for row in conn.execute("SELECT name, record_hash FROM tools")}
                desired = {record.name: record.record_hash for record in records}
                to_delete = set(existing) - set(desired)
                to_upsert = [record for record in records if existing.get(record.name) != record.record_hash]

            if to_delete:
                for name in to_delete:
                    conn.execute("DELETE FROM tools WHERE name = ?", (name,))

            if to_upsert:
                conn.executemany(
                    """
                    INSERT INTO tools (
                        name,
                        description,
                        tags,
                        side_effects,
                        loading_mode,
                        record_hash
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        description=excluded.description,
                        tags=excluded.tags,
                        side_effects=excluded.side_effects,
                        loading_mode=excluded.loading_mode,
                        record_hash=excluded.record_hash
                    """,
                    [
                        (
                            record.name,
                            record.description,
                            json.dumps(record.tags, ensure_ascii=False),
                            record.side_effects,
                            record.loading_mode,
                            record.record_hash,
                        )
                        for record in to_upsert
                    ],
                )

            _set_meta(conn, "catalog_fingerprint", fingerprint)
            _set_meta(conn, "schema_version", str(_SCHEMA_VERSION))

    def search(
        self,
        query: str,
        *,
        search_type: str,
        limit: int,
        include_always_loaded: bool,
        allowed_names: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        cleaned_query = query.strip()
        if not cleaned_query:
            return [], search_type

        effective_search_type = search_type
        if search_type == "fts" and not self.fts_available:
            effective_search_type = "regex" if self._fts_fallback_to_regex else "exact"

        limit = min(max(int(limit), 1), self._max_search_results)

        self._ensure_schema()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA case_sensitive_like = OFF")
            if effective_search_type == "fts":
                results, executed = self._search_fts(conn, cleaned_query, allowed_names)
                if not executed and cleaned_query:
                    # FTS query could not be executed (parser error / empty tokens) -> fail-soft.
                    if self._fts_fallback_to_regex:
                        effective_search_type = "regex"
                        results = self._search_regex_exact(conn, cleaned_query, "regex", allowed_names)
                    else:
                        effective_search_type = "exact"
                        results = self._search_regex_exact(conn, cleaned_query, "exact", allowed_names)
            else:
                results = self._search_regex_exact(
                    conn,
                    cleaned_query,
                    effective_search_type,
                    allowed_names,
                )

        filtered = [
            item
            for item in results
            if include_always_loaded
            or not _is_always_loaded(item["name"], item["loading_mode"], self._always_loaded_patterns)
        ]

        sorted_results = sorted(
            filtered,
            key=lambda item: (
                -float(item["score"]),
                item["namespace_rank"],
                item["side_effects_rank"],
                len(item["name"]),
                item["name"],
            ),
        )

        output: list[dict[str, Any]] = []
        for item in sorted_results[:limit]:
            output.append(
                {
                    "name": item["name"],
                    "description": item["description"],
                    "score": float(item["score"]),
                    "match_type": item["match_type"],
                    "loading_mode": item["loading_mode"],
                }
            )

        return output, effective_search_type

    def _ensure_schema(self) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tools (
                    name TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    side_effects TEXT NOT NULL,
                    loading_mode TEXT NOT NULL,
                    record_hash TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_index_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self._fts_available = self._ensure_fts(conn)

    def _ensure_fts(self, conn: sqlite3.Connection) -> bool:
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS tools_fts USING fts5(
                    name,
                    description,
                    tags,
                    content='tools',
                    content_rowid='rowid',
                    tokenize='porter unicode61'
                )
                """
            )
        except sqlite3.OperationalError as exc:
            if "fts5" in str(exc).lower():
                return False
            raise

        conn.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS tools_ai AFTER INSERT ON tools BEGIN
                INSERT INTO tools_fts(rowid, name, description, tags)
                VALUES (new.rowid, new.name, new.description, new.tags);
            END;
            CREATE TRIGGER IF NOT EXISTS tools_ad AFTER DELETE ON tools BEGIN
                INSERT INTO tools_fts(tools_fts, rowid, name, description, tags)
                VALUES('delete', old.rowid, old.name, old.description, old.tags);
            END;
            CREATE TRIGGER IF NOT EXISTS tools_au AFTER UPDATE ON tools BEGIN
                INSERT INTO tools_fts(tools_fts, rowid, name, description, tags)
                VALUES('delete', old.rowid, old.name, old.description, old.tags);
                INSERT INTO tools_fts(rowid, name, description, tags)
                VALUES (new.rowid, new.name, new.description, new.tags);
            END;
            """
        )
        return True

    def _search_fts(
        self,
        conn: sqlite3.Connection,
        query: str,
        allowed_names: set[str] | None,
    ) -> tuple[list[dict[str, Any]], bool]:
        tokens = _fts_tokens(query)
        if not tokens:
            return [], False
        strict_query = " ".join(tokens)
        relaxed_query = _fts_or_query(tokens)
        if allowed_names is not None and not allowed_names:
            return [], True
        clause = ""
        allowed_params: list[Any] = []
        if allowed_names:
            placeholders = ", ".join("?" for _ in allowed_names)
            clause = f" AND tools.name IN ({placeholders})"
            allowed_params.extend(sorted(allowed_names))

        sql = (
            "SELECT tools.name, tools.description, tools.tags, tools.side_effects, tools.loading_mode, "
            "bm25(tools_fts) as rank "
            "FROM tools_fts JOIN tools ON tools_fts.rowid = tools.rowid "
            "WHERE tools_fts MATCH ?" + clause
        )

        def _run(match_query: str) -> list[tuple[Any, ...]] | None:
            try:
                return conn.execute(sql, [match_query, *allowed_params]).fetchall()
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "syntax error" in message or "fts5" in message:
                    return None
                raise

        rows = _run(strict_query)
        if rows is None:
            return [], False
        if not rows:
            # AND queries are often too strict. Retry with OR for better recall.
            rows = _run(relaxed_query)
            if rows is None:
                return [], False
        if not rows:
            return [], True

        scored: list[dict[str, Any]] = []
        raw_scores: list[float] = []
        for name, description, tags, side_effects, loading_mode, raw in rows:
            raw_value = float(raw) if raw is not None else 0.0
            raw_value = max(raw_value, 0.0)
            score_raw = 1.0 / (1.0 + raw_value)
            raw_scores.append(score_raw)
            scored.append(
                {
                    "name": name,
                    "description": description or "",
                    "tags": _parse_tags(tags),
                    "side_effects": side_effects or "pure",
                    "loading_mode": loading_mode or ToolLoadingMode.ALWAYS.value,
                    "match_type": "fts",
                    "score_raw": score_raw,
                }
            )

        min_score = min(raw_scores)
        max_score = max(raw_scores)
        for item in scored:
            if max_score == min_score:
                score = 0.5
            else:
                score = (item["score_raw"] - min_score) / (max_score - min_score)
            item["score"] = max(0.0, min(1.0, score))
            item["namespace_rank"] = _namespace_rank(item["name"], self._preferred_namespaces)
            item["side_effects_rank"] = _SIDE_EFFECT_RANK.get(item["side_effects"], 99)
            item.pop("score_raw", None)
        return scored, True

    def _search_regex_exact(
        self,
        conn: sqlite3.Connection,
        query: str,
        search_type: str,
        allowed_names: set[str] | None,
    ) -> list[dict[str, Any]]:
        rows = _fetch_tool_rows(conn, allowed_names)
        if not rows:
            return []

        if search_type == "exact":
            return _score_exact(query, rows, self._preferred_namespaces)
        return _score_regex(query, rows, self._preferred_namespaces)


def _coerce_fts_query(value: str) -> str:
    """Convert arbitrary user text into a safe FTS5 MATCH query.

    SQLite FTS5 MATCH has its own query grammar; raw user input may contain
    punctuation (e.g. '!') that triggers parser errors.
    """

    return " ".join(_fts_tokens(value))


def _fts_tokens(value: str) -> list[str]:
    # Treat underscores and punctuation as separators.
    tokens = re.findall(r"[A-Za-z0-9]+", value or "")
    return [token for token in tokens if token]


def _fts_or_query(tokens: Sequence[str]) -> str:
    # Quote each token so operators don't leak in.
    return " OR ".join(f'"{token}"' for token in tokens)


def _tool_record(spec: NodeSpec) -> _ToolRecord:
    loading_mode = spec.loading_mode
    if isinstance(loading_mode, str):
        loading_mode = ToolLoadingMode(loading_mode)
    tags = list(spec.tags)
    for token in _fts_tokens(spec.name):
        lowered = token.lower()
        if lowered not in tags:
            tags.append(lowered)
        if len(tags) >= 24:
            break

    metadata = {
        "name": spec.name,
        "description": spec.desc,
        "tags": tags,
        "side_effects": spec.side_effects,
        "loading_mode": loading_mode.value,
        "examples": spec.examples_payload(),
    }
    record_hash = _hash_payload(metadata)
    return _ToolRecord(
        name=spec.name,
        description=spec.desc,
        tags=tags,
        side_effects=spec.side_effects,
        loading_mode=loading_mode.value,
        record_hash=record_hash,
    )


def _catalog_fingerprint(records: Sequence[_ToolRecord]) -> str:
    payload = {record.name: record.record_hash for record in records}
    return _hash_payload(payload)


def _hash_payload(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM tool_index_meta WHERE key = ?", (key,)).fetchone()
    if row:
        return str(row[0])
    return None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO tool_index_meta (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, value),
    )


def _fetch_tool_rows(
    conn: sqlite3.Connection,
    allowed_names: set[str] | None,
) -> list[tuple[str, str, str, str, str]]:
    if allowed_names is not None and not allowed_names:
        return []
    clause = ""
    params: list[Any] = []
    if allowed_names:
        placeholders = ", ".join("?" for _ in allowed_names)
        clause = f" WHERE name IN ({placeholders})"
        params.extend(sorted(allowed_names))
    sql = f"SELECT name, description, tags, side_effects, loading_mode FROM tools{clause}"
    return [
        (
            str(name),
            str(description or ""),
            str(tags or "[]"),
            str(side_effects or "pure"),
            str(loading_mode or ToolLoadingMode.ALWAYS.value),
        )
        for name, description, tags, side_effects, loading_mode in conn.execute(sql, params)
    ]


def _score_exact(
    query: str,
    rows: list[tuple[str, str, str, str, str]],
    preferred_namespaces: Sequence[str],
) -> list[dict[str, Any]]:
    query_lower = query.lower()
    results: list[dict[str, Any]] = []
    for name, description, tags_raw, side_effects, loading_mode in rows:
        tags = _parse_tags(tags_raw)
        if not _exact_match(query_lower, name, description, tags):
            continue
        results.append(
            {
                "name": name,
                "description": description,
                "tags": tags,
                "side_effects": side_effects,
                "loading_mode": loading_mode,
                "match_type": "exact",
                "score": 1.0,
                "namespace_rank": _namespace_rank(name, preferred_namespaces),
                "side_effects_rank": _SIDE_EFFECT_RANK.get(side_effects, 99),
            }
        )
    return results


def _score_regex(
    query: str,
    rows: list[tuple[str, str, str, str, str]],
    preferred_namespaces: Sequence[str],
) -> list[dict[str, Any]]:
    regex: re.Pattern[str] | None = None
    try:
        regex = re.compile(query, re.IGNORECASE)
    except re.error:
        regex = None

    # Natural language queries with whitespace are rarely intended as literal regex.
    if regex is None or any(ch.isspace() for ch in query):
        tokens = _fts_tokens(query)
        if not tokens:
            return []
        safe = "|".join(re.escape(token) for token in tokens)
        try:
            regex = re.compile(safe, re.IGNORECASE)
        except re.error:
            return []

    results: list[dict[str, Any]] = []
    for name, description, tags_raw, side_effects, loading_mode in rows:
        tags = _parse_tags(tags_raw)
        score = _regex_score(regex, name, description, tags)
        if score is None:
            continue
        results.append(
            {
                "name": name,
                "description": description,
                "tags": tags,
                "side_effects": side_effects,
                "loading_mode": loading_mode,
                "match_type": "regex",
                "score": score,
                "namespace_rank": _namespace_rank(name, preferred_namespaces),
                "side_effects_rank": _SIDE_EFFECT_RANK.get(side_effects, 99),
            }
        )
    return results


def _exact_match(query: str, name: str, description: str, tags: list[str]) -> bool:
    if query == name.lower():
        return True
    if query == description.lower():
        return True
    return any(query == tag.lower() for tag in tags)


def _regex_score(
    regex: re.Pattern[str],
    name: str,
    description: str,
    tags: list[str],
) -> float | None:
    if regex.search(name):
        if regex.fullmatch(name):
            return 0.95
        if regex.match(name):
            return 0.90
        return 0.85
    tags_blob = " ".join(tags)
    if regex.search(description) or regex.search(tags_blob):
        return 0.75
    return None


def _parse_tags(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []


def _namespace_rank(name: str, preferred: Sequence[str]) -> int:
    if not preferred:
        return 0
    namespace = name.split(".", 1)[0] if "." in name else name
    try:
        return preferred.index(namespace)
    except ValueError:
        return len(preferred) + 1


def _is_always_loaded(name: str, loading_mode: str, patterns: Sequence[str]) -> bool:
    if loading_mode == ToolLoadingMode.ALWAYS.value:
        return True
    if not patterns:
        return False
    return any(fnmatch(name, pattern) for pattern in patterns)
