"""Mock LLM streaming example."""

from __future__ import annotations

import asyncio
from collections import defaultdict

from penguiflow import (
    Headers,
    Message,
    Node,
    NodePolicy,
    StreamChunk,
    chunk_to_ws_json,
    create,
    format_sse_event,
)


async def mock_llm(message: Message, ctx) -> None:
    """Emit streaming tokens for an incoming prompt."""

    prompt = str(message.payload)
    tokens = prompt.split()

    for idx, token in enumerate(tokens):
        await asyncio.sleep(0.05)
        done = idx == len(tokens) - 1
        text = token + (" " if not done else "")
        await ctx.emit_chunk(
            parent=message,
            text=text,
            done=done,
            meta={"token_index": idx},
        )


BUFFERS: defaultdict[str, list[str]] = defaultdict(list)


async def sse_sink(message: Message, _ctx) -> str | None:
    """Print Server-Sent Events payloads and return final text when complete."""

    chunk = message.payload
    assert isinstance(chunk, StreamChunk)

    buffer = BUFFERS[chunk.stream_id]
    buffer.append(chunk.text)

    print(format_sse_event(chunk), end="")
    if chunk.done:
        final_text = "".join(BUFFERS.pop(chunk.stream_id))
        print(chunk_to_ws_json(chunk))
        return final_text
    return None


async def main() -> None:
    llm_node = Node(mock_llm, name="mock_llm", policy=NodePolicy(validate="none"))
    sink_node = Node(sse_sink, name="sse_sink", policy=NodePolicy(validate="none"))
    flow = create(llm_node.to(sink_node))
    flow.run()

    message = Message(
        payload="Penguins huddle to stay warm",
        headers=Headers(tenant="demo"),
    )

    await flow.emit(message)
    final_text = await flow.fetch()
    print(f"final: {final_text}")

    await flow.stop()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
