from __future__ import annotations

import asyncio
import os
from typing import Any

from penguiflow.artifacts import InMemoryArtifactStore
from penguiflow.common_tools.web import build_web_tool_specs


class MiniToolCtx:
    def __init__(self) -> None:
        self._artifacts = InMemoryArtifactStore()
        self._llm_context: dict[str, Any] = {}
        self._tool_context: dict[str, Any] = {}
        self.chunks: list[dict[str, Any]] = []

    @property
    def llm_context(self):
        return self._llm_context

    @property
    def tool_context(self):
        return self._tool_context

    @property
    def artifacts(self):
        return self._artifacts

    async def emit_chunk(self, stream_id: str, seq: int, text: str, *, done: bool = False, meta=None) -> None:
        self.chunks.append(
            {
                "stream_id": stream_id,
                "seq": seq,
                "text": text,
                "done": done,
                "meta": meta or {},
            }
        )

    async def emit_artifact(
        self,
        stream_id: str,
        chunk: Any,
        *,
        done: bool = False,
        artifact_type=None,
        meta=None,
    ) -> None:
        return None

    async def pause(self, reason: str, payload=None):
        raise RuntimeError(f"pause not supported in this example: {reason}")

    @property
    def kv(self):
        raise RuntimeError("kv not supported in this example")


async def main() -> None:
    ctx = MiniToolCtx()
    specs = build_web_tool_specs()
    by_name = {spec.name: spec for spec in specs}

    print("1) web_fetch example.com -> markdown preview")
    fetch = by_name["web_fetch"]
    fetch_args = fetch.args_model.model_validate({"url": "https://example.com", "max_preview_chars": 800})
    fetch_out = await fetch.node.func(fetch_args, ctx)
    print(fetch.out_model.model_validate(fetch_out).model_dump())

    brave_key = os.environ.get("BRAVE_SEARCH_API_KEY") or os.environ.get("BRAVE_API_KEY")
    if not brave_key:
        print("\n2) Brave tools skipped (set BRAVE_SEARCH_API_KEY to enable).")
        return

    print("\n2) web_context: grounding snippets for a query")
    web_context = by_name["web_context"]
    ctx.tool_context["brave_api_key"] = brave_key
    context_args = web_context.args_model.model_validate({"query": "penguiflow react planner tool catalog"})
    context_out = await web_context.node.func(context_args, ctx)
    print(web_context.out_model.model_validate(context_out).model_dump())


if __name__ == "__main__":
    asyncio.run(main())
