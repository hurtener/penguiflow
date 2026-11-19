"""Real SDK clients with their network calls replaced, for provider tests.

Providers accept a pre-built SDK ``client``. Tests build a real client and
replace only the request method the provider calls, so the provider sees
the genuine client type and no request leaves the process.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google import genai
from openai import AsyncOpenAI

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest


def openai_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    post: Callable[..., object] | None = None,
    create: Callable[..., object] | None = None,
) -> AsyncOpenAI:
    """Return an ``AsyncOpenAI`` client with replaced request methods.

    Args:
        monkeypatch: Restores the client methods after the test.
        post: Replacement for the raw ``client.post``.
        create: Replacement for ``client.chat.completions.create``.
    """
    client = AsyncOpenAI(
        api_key="test-token", base_url="https://test.invalid/"
    )
    if post is not None:
        monkeypatch.setattr(client, "post", post)
    if create is not None:
        monkeypatch.setattr(client.chat.completions, "create", create)
    return client


def genai_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    generate_content_stream: Callable[..., object],
) -> genai.Client:
    """Return a google-genai client with a replaced async stream call."""
    client = genai.Client(api_key="test")
    monkeypatch.setattr(
        client.aio.models, "generate_content_stream", generate_content_stream
    )
    return client
