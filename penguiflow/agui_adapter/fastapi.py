"""FastAPI integration helpers for AG-UI adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse


def create_agui_endpoint(
    adapter_run: Callable[[RunAgentInput], AsyncIterator[Any]],
) -> Callable[[RunAgentInput, Request], StreamingResponse]:
    """Create a FastAPI endpoint handler for an AG-UI adapter."""

    async def endpoint(input: RunAgentInput, request: Request) -> StreamingResponse:
        accept = request.headers.get("accept", "text/event-stream")
        encoder = EventEncoder(accept=accept)

        async def stream():
            async for event in adapter_run(input):
                yield encoder.encode(event)

        return StreamingResponse(
            stream(),
            media_type=encoder.get_content_type(),
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return endpoint  # type: ignore[return-value]


def add_agui_route(
    app: FastAPI,
    path: str,
    adapter_run: Callable[[RunAgentInput], AsyncIterator[Any]],
    **route_kwargs: Any,
) -> None:
    """Add an AG-UI route to a FastAPI app."""
    endpoint = create_agui_endpoint(adapter_run)

    @app.post(path, **route_kwargs)
    async def agui_route(input: RunAgentInput, request: Request) -> StreamingResponse:
        return await endpoint(input, request)  # type: ignore[misc]
