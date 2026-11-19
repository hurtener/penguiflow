"""Web tools example: fetch, search and answer from the web."""

from __future__ import annotations

import asyncio
import os
import sys

from penguiflow.artifacts import InMemoryArtifactStore
from penguiflow.common_tools.web import build_web_tool_specs
from penguiflow.sessions.tool_jobs import ToolJobContext


async def main() -> None:
    # A standalone ToolContext: tools run here without a planner.
    """Run the example."""
    ctx = ToolJobContext(
        llm_context={}, tool_context={}, artifacts=InMemoryArtifactStore()
    )
    specs = build_web_tool_specs()
    by_name = {spec.name: spec for spec in specs}

    sys.stdout.write("1) web_fetch example.com -> markdown preview\n")
    fetch = by_name["web_fetch"]
    fetch_args = fetch.args_model.model_validate(
        {"url": "https://example.com", "max_preview_chars": 800}
    )
    fetch_out = await fetch.node.func(fetch_args, ctx)
    sys.stdout.write(
        f"{fetch.out_model.model_validate(fetch_out).model_dump()}\n"
    )

    brave_key = os.environ.get("BRAVE_SEARCH_API_KEY") or os.environ.get(
        "BRAVE_API_KEY"
    )
    if not brave_key:
        sys.stdout.write(
            "\n2) Brave tools skipped (set BRAVE_SEARCH_API_KEY to enable).\n"
        )
        return

    sys.stdout.write("\n2) web_context: grounding snippets for a query\n")
    web_context = by_name["web_context"]
    ctx.tool_context["brave_api_key"] = brave_key
    context_args = web_context.args_model.model_validate(
        {"query": "penguiflow react planner tool catalog"}
    )
    context_out = await web_context.node.func(context_args, ctx)
    sys.stdout.write(
        f"{web_context.out_model.model_validate(context_out).model_dump()}\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
