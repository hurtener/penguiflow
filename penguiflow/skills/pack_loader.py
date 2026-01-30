"""Skill pack loader for local developer-provided skills."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped,unused-ignore]
from pydantic import ValidationError

from .models import SkillDefinition, SkillPackConfig, SkillPackFormat

logger = logging.getLogger("penguiflow.skills")

_SUPPORTED_FORMATS: dict[SkillPackFormat, tuple[str, ...]] = {
    "md": (".skill.md",),
    "yaml": (".skill.yaml", ".skill.yml"),
    "json": (".skill.json",),
    "jsonl": (".skill.jsonl",),
}


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "skill"


def _extract_frontmatter(text: str) -> dict[str, Any] | None:
    match = re.match(r"\s*---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if not match:
        return None
    payload = match.group(1)
    try:
        data = yaml.safe_load(payload)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _load_json(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON in {path}") from exc
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def _load_yaml(path: Path) -> list[dict[str, Any]]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Invalid YAML in {path}") from exc
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                skills.append(item)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSONL in {path}") from exc
    return skills


def _load_markdown(path: Path) -> list[dict[str, Any]]:
    try:
        data = _extract_frontmatter(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Failed to read markdown skill {path}") from exc
    return [data] if isinstance(data, dict) else []


def _iter_pack_files(pack: SkillPackConfig) -> list[Path]:
    root = Path(pack.path)
    if not root.exists():
        raise FileNotFoundError(f"Skill pack path does not exist: {pack.path}")
    if root.is_file():
        return [root]
    formats = _SUPPORTED_FORMATS.keys() if pack.format is None else (pack.format,)
    patterns: list[str] = []
    for fmt in formats:
        patterns.extend(_SUPPORTED_FORMATS.get(fmt, ()))
    files: list[Path] = []
    for suffix in patterns:
        files.extend(sorted(root.rglob(f"*{suffix}")))
    return sorted(set(files))


class SkillPackLoader:
    def load_pack(self, pack: SkillPackConfig) -> list[SkillDefinition]:
        if not pack.enabled:
            return []
        try:
            files = _iter_pack_files(pack)
        except FileNotFoundError:
            logger.warning("skill_pack_missing", extra={"pack": pack.name, "path": pack.path})
            return []

        loaded: list[SkillDefinition] = []
        seen_names: set[str] = set()

        for path in files:
            entries: list[dict[str, Any]] = []
            suffix = "".join(path.suffixes)
            fmt: SkillPackFormat | None = pack.format
            if fmt is None:
                for key, exts in _SUPPORTED_FORMATS.items():
                    if suffix in exts:
                        fmt = key
                        break
            if fmt == "md":
                entries = _load_markdown(path)
            elif fmt == "yaml":
                entries = _load_yaml(path)
            elif fmt == "json":
                entries = _load_json(path)
            elif fmt == "jsonl":
                entries = _load_jsonl(path)
            else:
                continue

            for idx, data in enumerate(entries):
                if not isinstance(data, dict):
                    continue
                name = data.get("name")
                if not isinstance(name, str) or not name.strip():
                    base = None
                    for field in ("title", "trigger"):
                        value = data.get(field)
                        if isinstance(value, str) and value.strip():
                            base = value
                            break
                    if base is None:
                        base = path.stem.replace(".skill", "")
                    slug = _slugify(base)
                    name = f"pack.{pack.name}.{slug}"
                name = str(name).strip()
                if name in seen_names:
                    name = f"{name}_{idx + 1}"
                seen_names.add(name)
                data["name"] = name
                try:
                    loaded.append(SkillDefinition.model_validate(data))
                except ValidationError:
                    logger.warning(
                        "skill_pack_invalid",
                        extra={"pack": pack.name, "path": str(path)},
                    )
        return loaded


__all__ = ["SkillPackLoader"]
