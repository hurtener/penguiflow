"""Skill pack loader for local developer-provided skills."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from .models import SkillDefinition, SkillPackConfig, SkillPackFormat

if TYPE_CHECKING:
    from collections.abc import Callable

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
        msg = f"Invalid JSON in {path}"
        raise ValueError(msg) from exc
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def _load_yaml(path: Path) -> list[dict[str, Any]]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        msg = f"Invalid YAML in {path}"
        raise ValueError(msg) from exc
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
        msg = f"Invalid JSONL in {path}"
        raise ValueError(msg) from exc
    return skills


def _load_markdown(path: Path) -> list[dict[str, Any]]:
    try:
        data = _extract_frontmatter(path.read_text(encoding="utf-8"))
    except OSError as exc:
        msg = f"Failed to read markdown skill {path}"
        raise ValueError(msg) from exc
    return [data] if isinstance(data, dict) else []


def _iter_pack_files(pack: SkillPackConfig) -> list[Path]:
    root = Path(pack.path)
    if not root.exists():
        msg = f"Skill pack path does not exist: {pack.path}"
        raise FileNotFoundError(msg)
    if root.is_file():
        return [root]
    formats = (
        _SUPPORTED_FORMATS.keys() if pack.format is None else (pack.format,)
    )
    patterns: list[str] = []
    for fmt in formats:
        patterns.extend(_SUPPORTED_FORMATS.get(fmt, ()))
    files: list[Path] = []
    for suffix in patterns:
        files.extend(sorted(root.rglob(f"*{suffix}")))
    return sorted(set(files))


_LOADERS: dict[str, Callable[[Path], list[dict[str, Any]]]] = {
    "md": _load_markdown,
    "yaml": _load_yaml,
    "json": _load_json,
    "jsonl": _load_jsonl,
}


def _detect_format(
    path: Path, configured: SkillPackFormat | None
) -> SkillPackFormat | None:
    """Return the configured format, or infer it from the file suffix."""
    if configured is not None:
        return configured
    suffix = "".join(path.suffixes)
    for key, exts in _SUPPORTED_FORMATS.items():
        if suffix in exts:
            return key
    return None


def _default_skill_name(
    data: dict[str, Any], *, pack_name: str, path: Path
) -> str:
    """Name a skill from its title, trigger, or file name."""
    base = next(
        (
            value
            for value in (data.get("title"), data.get("trigger"))
            if isinstance(value, str) and value.strip()
        ),
        path.stem.replace(".skill", ""),
    )
    return f"pack.{pack_name}.{_slugify(base)}"


class SkillPackLoader:
    """Loads skill definitions from a pack of markdown/YAML/JSON files."""

    def load_pack(self, pack: SkillPackConfig) -> list[SkillDefinition]:
        """Load and validate every skill in ``pack``; skip invalid ones."""
        if not pack.enabled:
            return []
        try:
            files = _iter_pack_files(pack)
        except FileNotFoundError:
            logger.warning(
                "skill_pack_missing",
                extra={"pack": pack.name, "path": pack.path},
            )
            return []

        loaded: list[SkillDefinition] = []
        seen_names: set[str] = set()
        for path in files:
            fmt = _detect_format(path, pack.format)
            loader = _LOADERS.get(fmt) if fmt is not None else None
            if loader is None:
                continue
            for idx, data in enumerate(loader(path)):
                skill = self._load_entry(
                    data, idx=idx, pack=pack, path=path, seen_names=seen_names
                )
                if skill is not None:
                    loaded.append(skill)
        return loaded

    @staticmethod
    def _load_entry(
        data: object,
        *,
        idx: int,
        pack: SkillPackConfig,
        path: Path,
        seen_names: set[str],
    ) -> SkillDefinition | None:
        """Validate one entry, giving it a unique name."""
        if not isinstance(data, dict):
            return None
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            name = _default_skill_name(data, pack_name=pack.name, path=path)
        name = name.strip()
        if name in seen_names:
            name = f"{name}_{idx + 1}"
        seen_names.add(name)
        data["name"] = name
        try:
            return SkillDefinition.model_validate(data)
        except ValidationError:
            logger.warning(
                "skill_pack_invalid",
                extra={"pack": pack.name, "path": str(path)},
            )
            return None


__all__ = ["SkillPackLoader"]
