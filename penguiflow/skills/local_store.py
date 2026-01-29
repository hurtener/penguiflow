"""Local SQLite-backed skill store."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from .models import (
    SkillDefinition,
    SkillOrigin,
    SkillRecord,
    SkillScopeMode,
    SkillSearchType,
    SkillTaskType,
)

_SCHEMA_VERSION = 1


class LocalSkillStore:
    def __init__(
        self,
        *,
        db_path: str | Path,
        fts_fallback_to_regex: bool = True,
    ) -> None:
        self._db_path = Path(db_path)
        self._fts_fallback_to_regex = bool(fts_fallback_to_regex)
        self._fts_available: bool | None = None

    @property
    def fts_available(self) -> bool:
        if self._fts_available is None:
            self._ensure_schema()
        return bool(self._fts_available)

    def upsert_pack_skill(
        self,
        skill: SkillDefinition,
        *,
        pack_name: str,
        scope_mode: SkillScopeMode,
        update_existing: bool,
    ) -> tuple[bool, bool]:
        if not skill.name:
            raise ValueError("Skill name is required for upsert")
        now = int(time.time())
        content_hash = _hash_payload(_canonical_skill_payload(skill))
        payload = _serialise_skill_payload(skill)
        self._ensure_schema()

        inserted = False
        updated = False
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            row = conn.execute(
                "SELECT id, origin, content_hash FROM skills WHERE name = ?",
                (skill.name,),
            ).fetchone()
            if row is None:
                name_seed = (skill.name or "").encode("utf-8")
                skill_id = f"sk_{hashlib.sha256(name_seed).hexdigest()[:12]}"
                conn.execute(
                    """
                    INSERT INTO skills (
                        id,
                        scope_mode,
                        scope_tenant_id,
                        scope_project_id,
                        name,
                        title,
                        description,
                        trigger,
                        task_type,
                        tags,
                        steps,
                        preconditions,
                        failure_modes,
                        origin,
                        origin_ref,
                        content_hash,
                        created_at,
                        updated_at,
                        last_used,
                        use_count,
                        extra
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        skill_id,
                        scope_mode,
                        None,
                        None,
                        skill.name,
                        payload["title"],
                        payload["description"],
                        payload["trigger"],
                        payload["task_type"],
                        payload["tags"],
                        payload["steps"],
                        payload["preconditions"],
                        payload["failure_modes"],
                        "pack",
                        pack_name,
                        content_hash,
                        now,
                        now,
                        now,
                        0,
                        payload["extra"],
                    ),
                )
                inserted = True
            else:
                existing_origin = str(row[1])
                existing_hash = str(row[2])
                if existing_origin != "pack":
                    return False, False
                if existing_hash == content_hash or not update_existing:
                    return False, False
                conn.execute(
                    """
                    UPDATE skills SET
                        title = ?,
                        description = ?,
                        trigger = ?,
                        task_type = ?,
                        tags = ?,
                        steps = ?,
                        preconditions = ?,
                        failure_modes = ?,
                        origin_ref = ?,
                        content_hash = ?,
                        updated_at = ?,
                        extra = ?
                    WHERE name = ?
                    """,
                    (
                        payload["title"],
                        payload["description"],
                        payload["trigger"],
                        payload["task_type"],
                        payload["tags"],
                        payload["steps"],
                        payload["preconditions"],
                        payload["failure_modes"],
                        pack_name,
                        content_hash,
                        now,
                        payload["extra"],
                        skill.name,
                    ),
                )
                updated = True
        return inserted, updated

    def get_by_name(
        self,
        names: Sequence[str],
        *,
        scope_clause: str,
        scope_params: Sequence[Any],
    ) -> list[SkillRecord]:
        cleaned = [name for name in names if isinstance(name, str) and name.strip()]
        if not cleaned:
            return []
        placeholders = ", ".join("?" for _ in cleaned)
        clause = f"name IN ({placeholders})"
        if scope_clause:
            clause = f"({clause}) AND ({scope_clause})"
        sql = f"SELECT {_SKILL_COLUMNS} FROM skills WHERE {clause}"
        params = list(cleaned) + list(scope_params)
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        lookup = {row[4]: _row_to_skill(row) for row in rows}
        return [lookup[name] for name in cleaned if name in lookup]

    def search(
        self,
        query: str,
        *,
        search_type: SkillSearchType,
        limit: int,
        task_type: SkillTaskType | None,
        scope_clause: str,
        scope_params: Sequence[Any],
    ) -> tuple[list[dict[str, Any]], SkillSearchType]:
        cleaned_query = query.strip()
        if not cleaned_query:
            return [], search_type
        effective: SkillSearchType = search_type
        if search_type == "fts" and not self.fts_available:
            effective = "regex" if self._fts_fallback_to_regex else "exact"

        self._ensure_schema()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA case_sensitive_like = OFF")
            if effective == "fts":
                results, executed = _search_fts(conn, cleaned_query, task_type, scope_clause, scope_params)
                if not executed and cleaned_query:
                    if self._fts_fallback_to_regex:
                        effective = "regex"
                        results = _search_regex_exact(
                            conn, cleaned_query, "regex", task_type, scope_clause, scope_params
                        )
                    else:
                        effective = "exact"
                        results = _search_regex_exact(
                            conn, cleaned_query, "exact", task_type, scope_clause, scope_params
                        )
            else:
                results = _search_regex_exact(conn, cleaned_query, effective, task_type, scope_clause, scope_params)
        results = sorted(results, key=lambda item: (-float(item["score"]), len(item["name"]), item["name"]))
        limited = results[: max(int(limit), 1)]
        output = [
            {
                "name": item["name"],
                "title": item.get("title"),
                "trigger": item.get("trigger"),
                "task_type": item.get("task_type"),
                "score": float(item["score"]),
                "match_type": item["match_type"],
            }
            for item in limited
        ]
        return output, effective

    def list(
        self,
        *,
        page: int,
        page_size: int,
        task_type: SkillTaskType | None,
        origin: SkillOrigin | None,
        scope_clause: str,
        scope_params: Sequence[Any],
    ) -> tuple[list[SkillRecord], int]:
        page = max(int(page), 1)
        page_size = max(int(page_size), 1)
        filters: list[str] = []
        params: list[Any] = []
        if scope_clause:
            filters.append(f"({scope_clause})")
            params.extend(scope_params)
        if task_type:
            filters.append("task_type = ?")
            params.append(task_type)
        if origin:
            filters.append("origin = ?")
            params.append(origin)
        where = " AND ".join(filters) if filters else "1=1"
        limit = page_size
        offset = (page - 1) * page_size
        sql = f"SELECT {_SKILL_COLUMNS} FROM skills WHERE {where} ORDER BY name LIMIT ? OFFSET ?"
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(sql, params + [limit, offset]).fetchall()
            total = conn.execute(
                f"SELECT COUNT(1) FROM skills WHERE {where}",
                params,
            ).fetchone()[0]
        return [_row_to_skill(row) for row in rows], int(total)

    def list_recent(
        self,
        *,
        limit: int,
        exclude_names: Iterable[str],
        scope_clause: str,
        scope_params: Sequence[Any],
    ) -> Sequence[SkillRecord]:
        return self._list_ranked(
            order_by="last_used DESC",
            limit=limit,
            exclude_names=exclude_names,
            scope_clause=scope_clause,
            scope_params=scope_params,
        )

    def list_top(
        self,
        *,
        limit: int,
        exclude_names: Iterable[str],
        scope_clause: str,
        scope_params: Sequence[Any],
    ) -> Sequence[SkillRecord]:
        return self._list_ranked(
            order_by="use_count DESC",
            limit=limit,
            exclude_names=exclude_names,
            scope_clause=scope_clause,
            scope_params=scope_params,
        )

    def touch(self, names: Sequence[str]) -> None:
        if not names:
            return
        now = int(time.time())
        placeholders = ", ".join("?" for _ in names)
        sql = f"UPDATE skills SET last_used = ?, use_count = use_count + 1 WHERE name IN ({placeholders})"
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(sql, (now, *names))

    def _list_ranked(
        self,
        *,
        order_by: str,
        limit: int,
        exclude_names: Iterable[str],
        scope_clause: str,
        scope_params: Sequence[Any],
    ) -> Sequence[SkillRecord]:
        filters: list[str] = []
        params: list[Any] = []
        if scope_clause:
            filters.append(f"({scope_clause})")
            params.extend(scope_params)
        exclude = [name for name in exclude_names if name]
        if exclude:
            placeholders = ", ".join("?" for _ in exclude)
            filters.append(f"name NOT IN ({placeholders})")
            params.extend(exclude)
        where = " AND ".join(filters) if filters else "1=1"
        sql = f"SELECT {_SKILL_COLUMNS} FROM skills WHERE {where} ORDER BY {order_by} LIMIT ?"
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(sql, params + [max(int(limit), 1)]).fetchall()
        return [_row_to_skill(row) for row in rows]

    def _ensure_schema(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS skills (
                    id TEXT PRIMARY KEY,
                    scope_mode TEXT NOT NULL,
                    scope_tenant_id TEXT,
                    scope_project_id TEXT,
                    name TEXT NOT NULL UNIQUE,
                    title TEXT,
                    description TEXT,
                    trigger TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    steps TEXT NOT NULL,
                    preconditions TEXT NOT NULL,
                    failure_modes TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    origin_ref TEXT,
                    content_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_used INTEGER NOT NULL,
                    use_count INTEGER NOT NULL,
                    extra TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS skill_index_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_skills_scope ON skills(scope_mode, scope_tenant_id, scope_project_id)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_task_type ON skills(task_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_origin ON skills(origin)")
            self._fts_available = _ensure_fts(conn)
            _set_meta(conn, "schema_version", str(_SCHEMA_VERSION))


_SKILL_COLUMNS = (
    "id, scope_mode, scope_tenant_id, scope_project_id, name, title, description, trigger, task_type, tags, "
    "steps, preconditions, failure_modes, origin, origin_ref, content_hash, created_at, updated_at, last_used, "
    "use_count, extra"
)


def _ensure_fts(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts USING fts5(
                name,
                title,
                trigger,
                description,
                tags,
                content='skills',
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
        CREATE TRIGGER IF NOT EXISTS skills_ai AFTER INSERT ON skills BEGIN
            INSERT INTO skills_fts(rowid, name, title, trigger, description, tags)
            VALUES (new.rowid, new.name, new.title, new.trigger, new.description, new.tags);
        END;
        CREATE TRIGGER IF NOT EXISTS skills_ad AFTER DELETE ON skills BEGIN
            INSERT INTO skills_fts(skills_fts, rowid, name, title, trigger, description, tags)
            VALUES('delete', old.rowid, old.name, old.title, old.trigger, old.description, old.tags);
        END;
        CREATE TRIGGER IF NOT EXISTS skills_au AFTER UPDATE ON skills BEGIN
            INSERT INTO skills_fts(skills_fts, rowid, name, title, trigger, description, tags)
            VALUES('delete', old.rowid, old.name, old.title, old.trigger, old.description, old.tags);
            INSERT INTO skills_fts(rowid, name, title, trigger, description, tags)
            VALUES (new.rowid, new.name, new.title, new.trigger, new.description, new.tags);
        END;
        """
    )
    return True


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO skill_index_meta (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, value),
    )


def _canonical_skill_payload(skill: SkillDefinition) -> dict[str, Any]:
    payload = {
        "name": skill.name,
        "title": skill.title,
        "description": skill.description,
        "trigger": skill.trigger,
        "task_type": skill.task_type,
        "tags": list(skill.tags),
        "steps": list(skill.steps),
        "preconditions": list(skill.preconditions),
        "failure_modes": list(skill.failure_modes),
        "tools": skill.tools,
        "extra": skill.extra_payload(),
    }
    return payload


def _hash_payload(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _serialise_skill_payload(skill: SkillDefinition) -> dict[str, str]:
    extra = skill.extra_payload()
    if skill.tools is not None:
        # Persist structured tool hints in the JSON column (v2.12 MVP).
        # The SkillStore schema does not have a dedicated tools column.
        extra = dict(extra)
        extra["tools"] = skill.tools
    return {
        "title": skill.title or "",
        "description": skill.description or "",
        "trigger": skill.trigger,
        "task_type": skill.task_type or "unknown",
        "tags": json.dumps(skill.tags, ensure_ascii=False),
        "steps": json.dumps(skill.steps, ensure_ascii=False),
        "preconditions": json.dumps(skill.preconditions, ensure_ascii=False),
        "failure_modes": json.dumps(skill.failure_modes, ensure_ascii=False),
        "extra": json.dumps(extra, ensure_ascii=False),
    }


def _parse_json_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []


def _parse_json_dict(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _coerce_scope_mode(value: Any) -> SkillScopeMode:
    text = str(value).strip().lower() if value is not None else ""
    if text == "tenant":
        return "tenant"
    if text == "global":
        return "global"
    return "project"


def _coerce_task_type(value: Any) -> SkillTaskType:
    text = str(value).strip().lower() if value is not None else ""
    if text == "browser":
        return "browser"
    if text == "api":
        return "api"
    if text == "code":
        return "code"
    if text == "domain":
        return "domain"
    return "unknown"


def _coerce_origin(value: Any) -> SkillOrigin:
    text = str(value).strip().lower() if value is not None else ""
    if text == "learned":
        return "learned"
    return "pack"


def _row_to_skill(row: Sequence[Any]) -> SkillRecord:
    (
        skill_id,
        scope_mode,
        scope_tenant_id,
        scope_project_id,
        name,
        title,
        description,
        trigger,
        task_type,
        tags,
        steps,
        preconditions,
        failure_modes,
        origin,
        origin_ref,
        content_hash,
        created_at,
        updated_at,
        last_used,
        use_count,
        extra,
    ) = row
    return SkillRecord(
        id=str(skill_id),
        scope_mode=_coerce_scope_mode(scope_mode),
        scope_tenant_id=str(scope_tenant_id) if scope_tenant_id else None,
        scope_project_id=str(scope_project_id) if scope_project_id else None,
        name=str(name),
        title=str(title) if title else None,
        description=str(description) if description else None,
        trigger=str(trigger),
        task_type=_coerce_task_type(task_type),
        tags=_parse_json_list(str(tags or "[]")),
        steps=_parse_json_list(str(steps or "[]")),
        preconditions=_parse_json_list(str(preconditions or "[]")),
        failure_modes=_parse_json_list(str(failure_modes or "[]")),
        origin=_coerce_origin(origin),
        origin_ref=str(origin_ref) if origin_ref else None,
        content_hash=str(content_hash),
        created_at=int(created_at or 0),
        updated_at=int(updated_at or 0),
        last_used=int(last_used or 0),
        use_count=int(use_count or 0),
        extra=_parse_json_dict(str(extra or "{}")),
    )


def _search_fts(
    conn: sqlite3.Connection,
    query: str,
    task_type: SkillTaskType | None,
    scope_clause: str,
    scope_params: Sequence[Any],
) -> tuple[list[dict[str, Any]], bool]:
    tokens = _fts_tokens(query)
    if not tokens:
        return [], False
    strict_query = " ".join(tokens)
    relaxed_query = _fts_or_query(tokens)
    params: list[Any] = [strict_query]
    clause = ""
    if scope_clause:
        clause += f" AND ({scope_clause})"
        params.extend(scope_params)
    if task_type:
        clause += " AND skills.task_type = ?"
        params.append(task_type)
    sql = (
        "SELECT skills.name, skills.title, skills.trigger, skills.task_type, skills.tags, bm25(skills_fts) as rank "
        "FROM skills_fts JOIN skills ON skills_fts.rowid = skills.rowid "
        "WHERE skills_fts MATCH ?" + clause
    )

    def _run(match_query: str) -> list[tuple[Any, ...]] | None:
        try:
            return conn.execute(sql, [match_query, *params[1:]]).fetchall()
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "syntax error" in message or "fts5" in message:
                return None
            raise

    rows = _run(strict_query)
    if rows is None:
        return [], False
    if not rows:
        # AND queries are often too strict for fuzzy user text. Retry with OR.
        rows = _run(relaxed_query)
        if rows is None:
            return [], False
    if not rows:
        return [], True
    scored: list[dict[str, Any]] = []
    raw_scores: list[float] = []
    for name, title, trigger, task_type_value, tags, raw in rows:
        raw_value = float(raw) if raw is not None else 0.0
        raw_value = max(raw_value, 0.0)
        score_raw = 1.0 / (1.0 + raw_value)
        raw_scores.append(score_raw)
        scored.append(
            {
                "name": str(name),
                "title": str(title) if title else None,
                "trigger": str(trigger) if trigger else None,
                "task_type": str(task_type_value) if task_type_value else None,
                "tags": _parse_json_list(str(tags or "[]")),
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
        item.pop("score_raw", None)
    return scored, True


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


def _search_regex_exact(
    conn: sqlite3.Connection,
    query: str,
    search_type: SkillSearchType,
    task_type: SkillTaskType | None,
    scope_clause: str,
    scope_params: Sequence[Any],
) -> list[dict[str, Any]]:
    rows = _fetch_rows(conn, task_type, scope_clause, scope_params)
    if not rows:
        return []
    if search_type == "exact":
        return _score_exact(query, rows)
    return _score_regex(query, rows)


def _fetch_rows(
    conn: sqlite3.Connection,
    task_type: SkillTaskType | None,
    scope_clause: str,
    scope_params: Sequence[Any],
) -> list[tuple[str, str | None, str | None, str, str]]:
    filters: list[str] = []
    params: list[Any] = []
    if scope_clause:
        filters.append(f"({scope_clause})")
        params.extend(scope_params)
    if task_type:
        filters.append("task_type = ?")
        params.append(task_type)
    where = " AND ".join(filters) if filters else "1=1"
    sql = f"SELECT name, title, trigger, tags, task_type FROM skills WHERE {where}"
    return [
        (
            str(name),
            str(title) if title else None,
            str(trigger) if trigger else None,
            str(tags or "[]"),
            str(task_type_value or "unknown"),
        )
        for name, title, trigger, tags, task_type_value in conn.execute(sql, params)
    ]


def _score_exact(query: str, rows: list[tuple[str, str | None, str | None, str, str]]) -> list[dict[str, Any]]:
    query_lower = query.lower()
    results: list[dict[str, Any]] = []
    for name, title, trigger, tags_raw, task_type_value in rows:
        tags = _parse_json_list(tags_raw)
        if not _exact_match(query_lower, name, title, trigger, tags):
            continue
        results.append(
            {
                "name": name,
                "title": title,
                "trigger": trigger,
                "task_type": task_type_value,
                "match_type": "exact",
                "score": 1.0,
            }
        )
    return results


def _score_regex(query: str, rows: list[tuple[str, str | None, str | None, str, str]]) -> list[dict[str, Any]]:
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
    for name, title, trigger, tags_raw, task_type_value in rows:
        tags = _parse_json_list(tags_raw)
        score = _regex_score(regex, name, title, trigger, tags)
        if score is None:
            continue
        results.append(
            {
                "name": name,
                "title": title,
                "trigger": trigger,
                "task_type": task_type_value,
                "match_type": "regex",
                "score": score,
            }
        )
    return results


def _exact_match(
    query: str,
    name: str,
    title: str | None,
    trigger: str | None,
    tags: list[str],
) -> bool:
    if query == name.lower():
        return True
    if title and query == title.lower():
        return True
    if trigger and query == trigger.lower():
        return True
    return any(query == tag.lower() for tag in tags)


def _regex_score(
    regex: re.Pattern[str],
    name: str,
    title: str | None,
    trigger: str | None,
    tags: list[str],
) -> float | None:
    if regex.search(name):
        if regex.fullmatch(name):
            return 0.95
        if regex.match(name):
            return 0.90
        return 0.85
    tags_blob = " ".join(tags)
    search_space = " ".join([value for value in [title, trigger, tags_blob] if value])
    if regex.search(search_space):
        return 0.75
    return None


__all__ = ["LocalSkillStore"]
