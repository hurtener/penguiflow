from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from penguiflow.streaming import stream_flow
from penguiflow.types import Headers, Message, StreamChunk


class _FetchResult:
    def __init__(self, payload: Any) -> None:
        self.payload = payload


class _DummyFlow:
    def __init__(self, payloads: Sequence[Any]) -> None:
        self._payloads = list(payloads)
        self.fetch_count = 0

    async def emit(self, parent_msg: Message, *, to=None) -> None:  # noqa: ANN001 - test stub
        _ = (parent_msg, to)

    async def fetch(self) -> Any:
        self.fetch_count += 1
        return _FetchResult(self._payloads.pop(0))


@pytest.mark.asyncio
async def test_stream_flow_stops_after_done_chunk_by_default() -> None:
    flow = _DummyFlow(
        [
            StreamChunk(stream_id="s", seq=1, text="hello", done=True),
            {"final": True},
        ]
    )
    msg = Message(payload="q", headers=Headers(tenant="t"))

    items: list[Any] = []
    async for item in stream_flow(flow, msg):
        items.append(item)

    assert items == [StreamChunk(stream_id="s", seq=1, text="hello", done=True)]
    assert flow.fetch_count == 1

