import types

import pytest


@pytest.mark.asyncio
async def test_google_provider_streaming_emits_deltas_for_cumulative_text() -> None:
    from google.genai import types as genai_types

    from penguiflow.llm.providers.google import GoogleProvider

    provider = GoogleProvider("gemini-3-flash-preview", api_key="test")

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

    class _FakeModels:
        async def generate_content_stream(self, **_kwargs):  # type: ignore[no-untyped-def]
            async def _iter():
                for item in chunks:
                    yield item

            return _iter()

    provider._client = types.SimpleNamespace(  # type: ignore[attr-defined]
        aio=types.SimpleNamespace(models=_FakeModels())
    )

    deltas: list[str] = []
    done_flags: list[bool] = []

    def _on_event(event):  # type: ignore[no-untyped-def]
        if event.delta_text:
            deltas.append(event.delta_text)
        if event.done:
            done_flags.append(True)

    response = await provider._stream_completion(  # type: ignore[attr-defined]
        contents=[],
        config=genai_types.GenerateContentConfig(),
        on_stream_event=_on_event,
        timeout=5.0,
        cancel=None,
    )

    assert "".join(deltas) == '{"a":1}'
    assert response.message.text == '{"a":1}'
    assert done_flags == [True]


@pytest.mark.asyncio
async def test_google_provider_streaming_routes_thought_parts_to_reasoning() -> None:
    from google.genai import types as genai_types

    from penguiflow.llm.providers.google import GoogleProvider

    provider = GoogleProvider("gemini-3-flash-preview", api_key="test")

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

    class _FakeModels:
        async def generate_content_stream(self, **_kwargs):  # type: ignore[no-untyped-def]
            async def _iter():
                for item in chunks:
                    yield item

            return _iter()

    provider._client = types.SimpleNamespace(  # type: ignore[attr-defined]
        aio=types.SimpleNamespace(models=_FakeModels())
    )

    text_deltas: list[str] = []
    reasoning_deltas: list[str] = []

    def _on_event(event):  # type: ignore[no-untyped-def]
        if event.delta_text:
            text_deltas.append(event.delta_text)
        if event.delta_reasoning:
            reasoning_deltas.append(event.delta_reasoning)

    response = await provider._stream_completion(  # type: ignore[attr-defined]
        contents=[],
        config=genai_types.GenerateContentConfig(),
        on_stream_event=_on_event,
        timeout=5.0,
        cancel=None,
    )

    assert "".join(text_deltas) == "ANSWER"
    assert "".join(reasoning_deltas) == "THINK"
    assert response.message.text == "ANSWER"
    assert response.reasoning_content == "THINK"


def test_google_provider_non_stream_separates_thought_parts() -> None:
    from google.genai import types as genai_types

    from penguiflow.llm.providers.google import GoogleProvider

    provider = GoogleProvider("gemini-3-flash-preview", api_key="test")
    response = genai_types.GenerateContentResponse(
        candidates=[
            genai_types.Candidate(
                content=genai_types.Content(
                    role="model",
                    parts=[
                        genai_types.Part(text="THINK", thought=True),
                        genai_types.Part(thought=True, thought_signature=b"sig"),
                        genai_types.Part(text="ANSWER", thought=False),
                    ],
                )
            )
        ]
    )

    converted = provider._from_google_response(response)  # type: ignore[attr-defined]
    assert converted.message.text == "ANSWER"
    assert converted.reasoning_content == "THINK"

