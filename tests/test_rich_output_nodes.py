from __future__ import annotations

import pytest

from penguiflow.rich_output.runtime import RichOutputConfig, configure_rich_output, reset_runtime
from penguiflow.rich_output.nodes import render_component, ui_form
from penguiflow.rich_output.tools import RenderComponentArgs, UIFormArgs


class PauseSignal(Exception):
    def __init__(self, payload: dict) -> None:
        super().__init__("paused")
        self.payload = payload


class DummyContext:
    def __init__(self) -> None:
        self.tool_context: dict = {}
        self.emitted: list[dict] = []

    async def emit_artifact(
        self,
        stream_id: str,
        chunk: dict,
        *,
        done: bool = False,
        artifact_type: str | None = None,
        meta: dict | None = None,
    ) -> None:
        self.emitted.append(
            {
                "stream_id": stream_id,
                "chunk": chunk,
                "done": done,
                "artifact_type": artifact_type,
                "meta": meta,
            }
        )

    async def pause(self, reason: str, payload: dict | None = None):
        raise PauseSignal(payload or {})


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    reset_runtime()


@pytest.mark.asyncio
async def test_render_component_emits_artifact() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["markdown"], max_payload_bytes=2000, max_total_bytes=2000)
    )
    ctx = DummyContext()
    args = RenderComponentArgs(component="markdown", props={"content": "Hello"})
    result = await render_component(args, ctx)
    assert result.ok is True
    assert ctx.emitted
    emitted = ctx.emitted[0]
    assert emitted["artifact_type"] == "ui_component"
    assert emitted["chunk"]["component"] == "markdown"


@pytest.mark.asyncio
async def test_ui_form_pauses_with_payload() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["form"], max_payload_bytes=2000, max_total_bytes=2000)
    )
    ctx = DummyContext()
    args = UIFormArgs(fields=[{"name": "title", "type": "text"}])
    with pytest.raises(PauseSignal) as exc:
        await ui_form(args, ctx)
    assert exc.value.payload["component"] == "form"
    assert exc.value.payload["tool"] == "ui_form"
