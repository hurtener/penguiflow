from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SyncResult:
    """Track sync outcomes so callers and tests can verify behavior."""

    created: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)
    conflicts: list[Path] = field(default_factory=list)


def discover_skill_dirs(source_root: Path) -> list[Path]:
    """Return skill bundle directories that contain a SKILL.md entrypoint."""

    if not source_root.exists():
        return []
    return sorted(
        [
            path
            for path in source_root.iterdir()
            if path.is_dir() and (path / "SKILL.md").is_file()
        ]
    )


def sync_skills(source_root: Path, target_root: Path, *, dry_run: bool, force: bool) -> SyncResult:
    """Symlink valid skill bundles into target, skipping unsafe conflicts by default."""

    result = SyncResult()
    bundles = discover_skill_dirs(source_root)
    if not dry_run:
        target_root.mkdir(parents=True, exist_ok=True)

    for bundle in bundles:
        target = target_root / bundle.name
        source = bundle.resolve()

        if target.is_symlink():
            if target.resolve() == source:
                result.unchanged.append(target)
                continue
            if not force:
                result.conflicts.append(target)
                continue
            if not dry_run:
                target.unlink()
                target.symlink_to(source)
            result.created.append(target)
            continue

        if target.exists():
            result.conflicts.append(target)
            continue

        if not dry_run:
            target.symlink_to(source)
        result.created.append(target)

    return result


def _default_source_root() -> Path:
    """Resolve repo skills dir relative to this script for portability."""

    return Path(__file__).resolve().parents[1] / "skills"


def parse_args() -> argparse.Namespace:
    """Parse minimal CLI flags needed for local and CI usage."""

    parser = argparse.ArgumentParser(description="Symlink PenguiFlow skills into ~/.agents/skills")
    parser.add_argument("--source", type=Path, default=_default_source_root())
    parser.add_argument("--target", type=Path, default=Path.home() / ".agents" / "skills")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    """Run sync and print a concise operational summary."""

    args = parse_args()
    result = sync_skills(args.source, args.target, dry_run=args.dry_run, force=args.force)

    print(f"source={args.source}")
    print(f"target={args.target}")
    print(f"created={len(result.created)} unchanged={len(result.unchanged)} conflicts={len(result.conflicts)}")

    for path in result.created:
        print(f"created: {path}")
    for path in result.unchanged:
        print(f"unchanged: {path}")
    for path in result.conflicts:
        print(f"conflict: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
