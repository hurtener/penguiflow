"""Tests for Google provider streaming."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from penguiflow.llm.providers.google import _from_google_response

from .fake_sdk_clients import genai_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from penguiflow.llm.types import StreamEvent


@pytest.mark.asyncio
async def test_google_provider_streaming_emits_deltas_for_cumulative_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google provider streaming emits deltas for cumulative text."""
    from google.genai import types as genai_types

    from penguiflow.llm.providers.google import GoogleProvider

    chunks = [
        genai_types.GenerateContentResponse(
            candidates=[
                genai_types.Candidate(
                    content=genai_types.Content(
                        role="model",
                        parts=[genai_types.Part(text='{"a"', thought=None)],
                    )
                )
            ]
        ),
        genai_types.GenerateContentResponse(
            candidates=[
                genai_types.Candidate(
                    content=genai_types.Content(
                        role="model",
                        parts=[genai_types.Part(text='{"a":1}', thought=None)],
                    )
                )
            ]
        ),
    ]

    async def generate_content_stream(
        **_kwargs: object,
    ) -> AsyncIterator[genai_types.GenerateContentResponse]:
        async def _iter() -> AsyncIterator[
            genai_types.GenerateContentResponse
        ]:
            for item in chunks:
                yield item

        return _iter()

    provider = GoogleProvider(
        "gemini-3-flash-preview",
        api_key="test",
        client=genai_client(
            monkeypatch, generate_content_stream=generate_content_stream
        ),
    )

    deltas: list[str] = []
    done_flags: list[bool] = []

    def _on_event(event: StreamEvent) -> None:
        if event.delta_text:
            deltas.append(event.delta_text)
        if event.done:
            done_flags.append(True)

    response = await provider.stream_completion(
        contents=[],
        config=genai_types.GenerateContentConfig(),
        on_stream_event=_on_event,
        cancel=None,
    )

    assert "".join(deltas) == '{"a":1}'
    assert response.message.text == '{"a":1}'
    assert done_flags == [True]


@pytest.mark.asyncio
async def test_google_provider_streaming_routes_thought_parts_to_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google provider streaming routes thought parts to reasoning."""
    from google.genai import types as genai_types

    from penguiflow.llm.providers.google import GoogleProvider

    chunks = [
        genai_types.GenerateContentResponse(
            candidates=[
                genai_types.Candidate(
                    content=genai_types.Content(
                        role="model",
                        parts=[
                            genai_types.Part(text="THINK", thought=True),
                            genai_types.Part(text="ANSWER", thought=False),
                        ],
                    )
                )
            ]
        )
    ]

    async def generate_content_stream(
        **_kwargs: object,
    ) -> AsyncIterator[genai_types.GenerateContentResponse]:
        async def _iter() -> AsyncIterator[
            genai_types.GenerateContentResponse
        ]:
            for item in chunks:
                yield item

        return _iter()

    provider = GoogleProvider(
        "gemini-3-flash-preview",
        api_key="test",
        client=genai_client(
            monkeypatch, generate_content_stream=generate_content_stream
        ),
    )

    text_deltas: list[str] = []
    reasoning_deltas: list[str] = []

    def _on_event(event: StreamEvent) -> None:
        if event.delta_text:
            text_deltas.append(event.delta_text)
        if event.delta_reasoning:
            reasoning_deltas.append(event.delta_reasoning)

    response = await provider.stream_completion(
        contents=[],
        config=genai_types.GenerateContentConfig(),
        on_stream_event=_on_event,
        cancel=None,
    )

    assert "".join(text_deltas) == "ANSWER"
    assert "".join(reasoning_deltas) == "THINK"
    assert response.message.text == "ANSWER"
    assert response.reasoning_content == "THINK"


def test_google_provider_non_stream_separates_thought_parts() -> None:
    """Google provider non stream separates thought parts."""
    from google.genai import types as genai_types

    response = genai_types.GenerateContentResponse(
        candidates=[
            genai_types.Candidate(
                content=genai_types.Content(
                    role="model",
                    parts=[
                        genai_types.Part(text="THINK", thought=True),
                        genai_types.Part(
                            thought=True, thought_signature=b"sig"
                        ),
                        genai_types.Part(text="ANSWER", thought=False),
                    ],
                )
            )
        ]
    )

    converted = _from_google_response(response)
    assert converted.message.text == "ANSWER"
    assert converted.reasoning_content == "THINK"
