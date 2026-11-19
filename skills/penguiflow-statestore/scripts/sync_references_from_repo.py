#!/usr/bin/env python3
"""Sync this skill's reference docs from a local PenguiFlow repo checkout.

This skill bundles snapshots of key PenguiFlow documents so downstream agents
can work from a self-contained spec. When PenguiFlow changes, rerun this script
to refresh the bundled references.

Example (from the PenguiFlow repo root):

    python3 ~/.codex/skills/penguiflow-statestore/scripts/sync_references_from_repo.py --repo .
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def _detect_repo_root(candidate: Path) -> Path | None:
    required = [
        candidate / "docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md",
        candidate / "docs/tools/statestore-guide.md",
        candidate / "docs/tools/artifacts-and-resources.md",
    ]
    if all(path.exists() for path in required):
        return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync PenguiFlow StateStore skill references from a repo checkout.")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Path to PenguiFlow repo root (defaults to CWD).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned copies without writing files.")
    args = parser.parse_args()

    repo_root = _detect_repo_root(args.repo.resolve())
    if repo_root is None:
        print("ERROR: --repo does not look like a PenguiFlow repo root.", flush=True)
        print(f"Checked: {args.repo.resolve()}", flush=True)
        return 2

    skill_dir = Path(__file__).resolve().parents[1]
    refs_dir = skill_dir / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)

    copies = [
        (
            repo_root / "docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md",
            refs_dir / "statestore-implementation-spec.md",
        ),
        (
            repo_root / "docs/tools/statestore-guide.md",
            refs_dir / "statestore-production-guide.md",
        ),
        (
            repo_root / "docs/tools/artifacts-and-resources.md",
            refs_dir / "artifacts-and-resources.md",
        ),
        # references/distributed-execution.md is maintained in this skill (no repo source).
    ]

    for src, dest in copies:
        if not src.exists():
            print(f"ERROR: missing source file: {src}", flush=True)
            return 2
        if args.dry_run:
            print(f"[DRY] {src} -> {dest}", flush=True)
            continue
        shutil.copy2(src, dest)
        print(f"[OK] {src} -> {dest}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

