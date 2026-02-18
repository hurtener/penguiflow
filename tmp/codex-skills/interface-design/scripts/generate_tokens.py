#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path


TOKENS: dict[str, dict[str, str]] = {
    "color": {
        "canvas": "#FAF7F2",
        "surface": "#F4EFE6",
        "surface_2": "#ECE6DC",
        "border": "#D8D0C4",
        "text": "#2B2723",
        "text_muted": "#6A625B",
        "accent": "#3B9C94",
        "accent_hover": "#348A83",
        "link": "#3D6A7E",
        "accent_warm": "#C97A64",
        "success": "#3F8E6B",
        "warning": "#C9943B",
        "error": "#B64A4A",
    },
    "radius": {"control": "10px", "card": "16px", "popover": "20px"},
    "shadow": {
        "sm": "0 1px 2px rgba(43, 39, 35, 0.06)",
        "md": "0 8px 24px rgba(43, 39, 35, 0.08)",
    },
    "space": {
        "1": "4px",
        "2": "8px",
        "3": "12px",
        "4": "16px",
        "5": "24px",
        "6": "32px",
        "7": "48px",
    },
    "font": {
        "sans": 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "Segoe UI", Inter, Roboto, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji"',
    },
}


def _css_name(prefix: str, group: str, key: str) -> str:
    group_part = group.replace("_", "-")
    key_part = key.replace("_", "-")
    return f"--{prefix}-{group_part}-{key_part}"


def render_css(tokens: dict[str, dict[str, str]], *, prefix: str) -> str:
    lines: list[str] = [":root {"]
    for group in sorted(tokens.keys()):
        group_tokens = tokens[group]
        for key in sorted(group_tokens.keys()):
            value = group_tokens[key]
            lines.append(f"  {_css_name(prefix, group, key)}: {value};")
        lines.append("")
    if lines[-1] == "":
        lines.pop()
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_json(tokens: dict[str, dict[str, str]], *, pretty: bool) -> str:
    if pretty:
        return json.dumps(tokens, indent=2, sort_keys=True) + "\n"
    return json.dumps(tokens, separators=(",", ":"), sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate Interface Design (Design Soul) starter tokens as CSS variables and/or JSON."
    )
    parser.add_argument(
        "--format",
        choices=("css", "json", "both"),
        default="css",
        help="Output format (default: css).",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="If provided, write tokens.css and/or tokens.json into this directory.",
    )
    parser.add_argument(
        "--prefix",
        default="ifd",
        help="CSS variable prefix (default: ifd).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    args = parser.parse_args()

    css = render_css(TOKENS, prefix=args.prefix)
    js = render_json(TOKENS, pretty=args.pretty)

    if args.out_dir:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        if args.format in ("css", "both"):
            (out_dir / "tokens.css").write_text(css, encoding="utf-8")
        if args.format in ("json", "both"):
            (out_dir / "tokens.json").write_text(js, encoding="utf-8")
        return 0

    if args.format == "css":
        print(css, end="")
        return 0
    if args.format == "json":
        print(js, end="")
        return 0

    print(css, end="")
    print()
    print(js, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

