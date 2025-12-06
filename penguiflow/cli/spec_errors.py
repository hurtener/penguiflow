"""Error helpers for spec parsing and validation."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

SpecPathComponent = str | int
SpecPath = tuple[SpecPathComponent, ...]


@dataclass(frozen=True)
class SpecErrorDetail:
    """Represents a single actionable spec error."""

    message: str
    path: SpecPath = ()
    line: int | None = None
    suggestion: str | None = None


class SpecValidationError(ValueError):
    """Raised when a spec fails validation or parsing."""

    def __init__(self, source: str | Path, errors: Iterable[SpecErrorDetail]) -> None:
        self.source = Path(source)
        self.errors = list(errors)
        super().__init__(format_spec_errors(self.source, self.errors))


def _format_path(path: Sequence[SpecPathComponent]) -> str:
    if not path:
        return "<root>"
    parts: list[str] = []
    for idx, component in enumerate(path):
        if isinstance(component, int):
            parts.append(f"[{component}]")
            continue
        if idx == 0:
            parts.append(str(component))
        else:
            parts.append(f".{component}")
    return "".join(parts)


def format_spec_errors(source: Path, errors: Sequence[SpecErrorDetail]) -> str:
    """Render spec errors with file and line context."""

    lines: list[str] = []
    for error in errors:
        prefix = source.as_posix()
        if error.line is not None:
            prefix = f"{prefix}:{error.line}"
        path = _format_path(error.path)
        lines.append(f"{prefix} {path} - {error.message}")
        if error.suggestion:
            lines.append(f"  Suggestion: {error.suggestion}")
    return "\n".join(lines)


__all__ = [
    "SpecErrorDetail",
    "SpecPath",
    "SpecPathComponent",
    "SpecValidationError",
    "format_spec_errors",
]
