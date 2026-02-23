from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MKDOCS_YML = REPO_ROOT / "mkdocs.yml"


LINK_RE = re.compile(r"(!?\[[^\]]*\])\(([^)]+)\)")


@dataclass(frozen=True)
class LinkError:
    file: Path
    line_no: int
    target: str
    reason: str


def _iter_mkdocs_pages(nav: object) -> list[Path]:
    pages: list[Path] = []
    if isinstance(nav, list):
        for item in nav:
            pages.extend(_iter_mkdocs_pages(item))
        return pages
    if isinstance(nav, dict):
        for _, value in nav.items():
            pages.extend(_iter_mkdocs_pages(value))
        return pages
    if isinstance(nav, str) and nav.endswith(".md"):
        pages.append(REPO_ROOT / "docs" / nav)
    return pages


def _load_public_markdown_files() -> list[Path]:
    files: list[Path] = []

    files.append(REPO_ROOT / "README.md")

    if MKDOCS_YML.exists():
        config = yaml.safe_load(MKDOCS_YML.read_text(encoding="utf-8"))
        nav = config.get("nav", [])
        files.extend(_iter_mkdocs_pages(nav))

    files.extend(sorted(REPO_ROOT.glob("examples/**/README.md")))

    # De-dupe while preserving order.
    seen: set[Path] = set()
    out: list[Path] = []
    for p in files:
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        out.append(p)
    return out


def _is_external(target: str) -> bool:
    return target.startswith(("http://", "https://", "mailto:"))


def _should_ignore_target(target: str) -> bool:
    if not target:
        return True
    if target.startswith("#"):
        return True
    if _is_external(target):
        return True
    if target in {"TBD", "TODO"}:
        return True
    return False


def _resolve_target(md_file: Path, target: str) -> Path | None:
    target_no_anchor = target.split("#", 1)[0].strip()
    if not target_no_anchor:
        return None
    if target_no_anchor.startswith("/"):
        # Treat as site-relative; not validated by this script.
        return None
    return (md_file.parent / target_no_anchor).resolve()


def _check_file(md_file: Path) -> list[LinkError]:
    errors: list[LinkError] = []
    text = md_file.read_text(encoding="utf-8", errors="replace")
    for idx, line in enumerate(text.splitlines(), start=1):
        for _label, raw_target in LINK_RE.findall(line):
            target = raw_target.strip()
            if _should_ignore_target(target):
                continue

            resolved = _resolve_target(md_file, target)
            if resolved is None:
                continue

            if not resolved.exists():
                errors.append(LinkError(md_file, idx, target, "target does not exist"))
                continue

            # Allow linking to directories.
            # (Common for README links like `examples/`.)
    return errors


def main() -> int:
    files = _load_public_markdown_files()
    errors: list[LinkError] = []

    for md in files:
        if not md.exists():
            errors.append(LinkError(md, 0, "", "markdown file missing"))
            continue
        errors.extend(_check_file(md))

    if errors:
        print("Broken markdown links found:\n", file=sys.stderr)
        for e in errors:
            loc = f"{e.file.relative_to(REPO_ROOT)}:{e.line_no}" if e.line_no else str(e.file.relative_to(REPO_ROOT))
            print(f"- {loc}: {e.target} ({e.reason})", file=sys.stderr)
        print(f"\nTotal: {len(errors)} errors", file=sys.stderr)
        return 1

    print(f"OK: {len(files)} markdown files checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

