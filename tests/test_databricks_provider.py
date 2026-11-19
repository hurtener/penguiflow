"""Databricks provider regression tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch
from urllib.parse import urljoin

import pytest

from .fake_sdk_clients import openai_client

if TYPE_CHECKING:
    from collections.abc import Callable


def test_databricks_base_url_keeps_endpoint_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure base_url/path joins don't drop the endpoint segment.

    Databricks requires /serving-endpoints/<endpoint>/invocations, and some
    HTTP clients join relative paths like urljoin().
    """
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.databricks.com")
    monkeypatch.setenv("DATABRICKS_TOKEN", "token")

    with patch("openai.AsyncOpenAI") as mock_client:
        from penguiflow.llm.providers.databricks import DatabricksProvider

        DatabricksProvider("my-endpoint")

    base_url = mock_client.call_args.kwargs["base_url"]
    assert base_url.endswith("/my-endpoint/")
    assert urljoin(base_url, "invocations").endswith(
        "/serving-endpoints/my-endpoint/invocations"
    )


@pytest.mark.asyncio
async def test_databricks_structured_output_streaming_degrades_to_single_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Streaming drops response_format, which Databricks rejects."""
    from types import SimpleNamespace

    from penguiflow.llm.providers.databricks import DatabricksProvider
    from penguiflow.llm.types import (
        LLMMessage,
        LLMRequest,
        StreamEvent,
        StructuredOutputSpec,
        TextPart,
    )

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"ok":true}', tool_calls=None
                ),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=10, completion_tokens=5, total_tokens=15
        ),
    )

    post_calls: list[tuple[str, object]] = []

    post_options: list[dict[str, object]] = []

    async def post(path: str, *, body: object, cast_to: object) -> object:
        post_options.append({"cast_to": cast_to})
        post_calls.append((path, body))
        return response

    provider = DatabricksProvider(
        "my-endpoint",
        host="https://example.databricks.com",
        token="token",
        timeout=5.0,
        profile=MagicMock(),
        client=openai_client(monkeypatch, post=post),
    )

    async def mock_stream_completion(
        params: dict[str, Any],
        on_stream_event: Callable[[StreamEvent], None],
        _cancel: object,
    ) -> object:
        # response_format should be dropped for streaming
        assert "response_format" not in params
        # and schema guidance should be injected
        assert params["messages"][0]["role"] == "system"
        assert "JSON Schema" in params["messages"][0]["content"]
        # Emit a minimal stream so the test can complete.
        on_stream_event(StreamEvent(delta_text='{"ok":true}'))
        on_stream_event(StreamEvent(done=True))
        return SimpleNamespace(
            message=LLMMessage(
                role="assistant", parts=[TextPart(text='{"ok":true}')]
            ),
            usage=SimpleNamespace(
                input_tokens=10, output_tokens=5, total_tokens=15
            ),
            reasoning_content=None,
            finish_reason="stop",
        )

    events: list[StreamEvent] = []

    def on_event(event: StreamEvent) -> None:
        events.append(event)

    req = LLMRequest(
        model="my-endpoint",
        messages=(LLMMessage(role="user", parts=[TextPart(text="hi")]),),
        structured_output=StructuredOutputSpec(
            name="x", json_schema={"type": "object"}, strict=True
        ),
    )

    with patch.object(provider, "stream_completion", mock_stream_completion):
        out = await provider.complete(
            req, stream=True, on_stream_event=on_event
        )
    assert post_calls == []
    assert any(e.delta_text == '{"ok":true}' for e in events)
    assert events[-1].done is True
    assert out.message.text == '{"ok":true}'
