"""Brave-powered web tools (opt-in).

This package provides NodeSpec factories for Brave Search API endpoints and a
companion `web_fetch` tool.
"""

from __future__ import annotations

from .specs import BraveWebConfig, build_web_tool_specs

__all__ = ["BraveWebConfig", "build_web_tool_specs"]

