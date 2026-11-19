"""Tests for common tools web."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from penguiflow.common_tools.web import BraveWebConfig, build_web_tool_specs
from penguiflow.common_tools.web.fetch import WebFetchResult
from penguiflow.common_tools.web.specs import (
    WebAnswerResult,
    WebContextResult,
    WebSearchResult,
)

from .tool_context_doubles import FakeToolContext

if TYPE_CHECKING:
    pass
    from collections.abc import AsyncIterator
    from types import TracebackType
    from typing import Self


class _FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, _n: int) -> AsyncIterator[object]:
        for c in self._chunks:
            yield c


class _FakeResp:
    def __init__(
        self,
        *,
        status: int,
        headers: dict[str, str] | None = None,
        json_body: object = None,
        text_body: str = "",
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._json_body = json_body
        self._text_body = text_body
        self.text_errors: list[str] = []
        self.content = _FakeContent([])

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return False

    async def json(self) -> object:
        return self._json_body

    async def text(self, errors: str = "replace") -> str:
        self.text_errors.append(errors)
        return self._text_body


class _FakeStreamResp(_FakeResp):
    def __init__(
        self,
        *,
        status: int,
        headers: dict[str, str] | None = None,
        chunks: list[bytes],
    ) -> None:
        super().__init__(status=status, headers=headers or {}, json_body=None)
        self.content = _FakeContent(chunks)


_Handler = Callable[..., _FakeResp]
"""Answers a fake HTTP call: ``(method, url, **kwargs) -> response``."""


class _FakeSession:
    def __init__(self, handler: _Handler) -> None:
        self._handler = handler

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return False

    def request(self, method: str, url: str, **kwargs: object) -> _FakeResp:
        return self._handler(method, url, **kwargs)

    def get(self, url: str, **kwargs: object) -> _FakeResp:
        return self._handler("GET", url, **kwargs)

    def post(self, url: str, **kwargs: object) -> _FakeResp:
        return self._handler("POST", url, **kwargs)


@pytest.mark.asyncio
async def test_build_web_tool_specs_metadata() -> None:
    """Build web tool specs metadata."""
    specs = build_web_tool_specs(BraveWebConfig(api_key="k"))
    names = {s.name for s in specs}
    assert "web_search" in names
    assert "web_context" in names
    assert "web_answer" in names
    assert "web_fetch" in names
    for spec in specs:
        assert spec.side_effects == "external"
        assert spec.loading_mode.value == "deferred"
        assert (
            "Untrusted" in (spec.safety_notes or "")
            or "SSRF" in (spec.safety_notes or "")
            or spec.name == "web_fetch"
        )


@pytest.mark.asyncio
async def test_web_search_missing_key_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Web search missing key raises."""
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    specs = build_web_tool_specs(BraveWebConfig(api_key=None))
    by_name = {s.name: s for s in specs}
    ctx = FakeToolContext()
    args = by_name["web_search"].args_model.model_validate(
        {"query": "penguiflow"}
    )
    with pytest.raises(ValueError):
        await by_name["web_search"].node.func(args, ctx)


@pytest.mark.asyncio
async def test_web_answer_accepts_singular_answers_env_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Web answer accepts singular answers env alias."""
    monkeypatch.delenv("BRAVE_ANSWERS_API_KEY", raising=False)
    monkeypatch.setenv("BRAVE_ANSWER_API_KEY", "k_answers")
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    def handler(_method: str, _url: str, **kwargs: object) -> _FakeResp:
        headers = kwargs.get("headers")
        assert isinstance(headers, dict)
        assert headers.get("X-Subscription-Token") == "k_answers"
        payload = {"choices": [{"message": {"content": "ok"}}]}
        return _FakeResp(status=200, json_body=payload)

    import aiohttp

    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *_a, **_k: _FakeSession(handler)
    )
    specs = build_web_tool_specs(BraveWebConfig(api_key=None))
    by_name = {s.name: s for s in specs}
    ctx = FakeToolContext()
    args = by_name["web_answer"].args_model.model_validate(
        {"query": "q", "stream": False}
    )
    out = await by_name["web_answer"].node.func(args, ctx)
    assert by_name["web_answer"].out_model is WebAnswerResult
    parsed = WebAnswerResult.model_validate(out)
    assert "ok" in parsed.answer_markdown


def test_web_answer_validation_errors_are_json_serializable() -> None:
    """Web answer validation errors are JSON serializable."""
    specs = build_web_tool_specs(BraveWebConfig(api_key="k"))
    by_name = {s.name: s for s in specs}

    with pytest.raises(ValidationError) as exc_info:
        by_name["web_answer"].args_model.model_validate(
            {"query": "q", "mode": "research", "stream": False}
        )

    errors = exc_info.value.errors()
    assert errors[0]["type"] == "invalid_web_answer_mode"
    assert json.loads(json.dumps(errors, ensure_ascii=False))[0]["msg"] == (
        "web_answer mode='research' requires stream=true"
    )


@pytest.mark.asyncio
async def test_web_context_parses_grounding_and_wraps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Web context parses grounding and wraps."""
    payload = {
        "grounding": {
            "generic": [
                {
                    "url": "https://example.com",
                    "title": "Example",
                    "snippets": ["a", "b"],
                }
            ]
        },
        "sources": {
            "https://example.com": {
                "title": "Example",
                "hostname": "example.com",
                "age": ["x"],
            }
        },
    }

    def handler(_method: str, _url: str, **kwargs: object) -> _FakeResp:
        headers = kwargs.get("headers")
        assert isinstance(headers, dict)
        assert "X-Subscription-Token" in headers
        return _FakeResp(status=200, json_body=payload)

    import aiohttp

    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *_a, **_k: _FakeSession(handler)
    )
    specs = build_web_tool_specs(
        BraveWebConfig(api_key="k", max_preview_chars=200)
    )
    by_name = {s.name: s for s in specs}
    ctx = FakeToolContext()
    args = by_name["web_context"].args_model.model_validate({"query": "q"})
    out = await by_name["web_context"].node.func(args, ctx)
    assert by_name["web_context"].out_model is WebContextResult
    parsed = WebContextResult.model_validate(out)
    assert (
        parsed.grounding and parsed.grounding[0].url == "https://example.com"
    )
    assert "UNTRUSTED EXTERNAL CONTENT" in parsed.context_markdown


@pytest.mark.asyncio
async def test_web_search_normalizes_bool_query_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Web search normalizes bool query params."""
    observed_params: dict[str, Any] = {}

    def handler(_method: str, _url: str, **kwargs: object) -> _FakeResp:
        nonlocal observed_params
        params = kwargs.get("params", {})
        if isinstance(params, dict):
            observed_params = dict(params)
        payload = {
            "web": {
                "results": [{"url": "https://example.com", "title": "Example"}]
            }
        }
        return _FakeResp(status=200, json_body=payload)

    import aiohttp

    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *_a, **_k: _FakeSession(handler)
    )
    specs = build_web_tool_specs(BraveWebConfig(api_key="k"))
    by_name = {s.name: s for s in specs}
    ctx = FakeToolContext()
    args = by_name["web_search"].args_model.model_validate(
        {"query": "penguin flow"}
    )
    out = await by_name["web_search"].node.func(args, ctx)
    assert by_name["web_search"].out_model is WebSearchResult
    parsed = WebSearchResult.model_validate(out)
    assert (
        parsed.web_results
        and parsed.web_results[0].url == "https://example.com"
    )
    assert observed_params["spellcheck"] in {"true", "false"}
    assert observed_params["operators"] in {"true", "false"}
    assert observed_params["include_fetch_metadata"] in {"true", "false"}


@pytest.mark.asyncio
async def test_web_answer_streaming_emits_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two SSE data lines + DONE
    """Web answer streaming emits chunks."""
    chunks = [
        b'data: {"choices":[{"delta":{"content":"Hello "}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"world"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]

    def handler(_method: str, _url: str, **_kwargs: object) -> _FakeStreamResp:
        return _FakeStreamResp(status=200, chunks=chunks)

    import aiohttp

    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *_a, **_k: _FakeSession(handler)
    )
    specs = build_web_tool_specs(BraveWebConfig(api_key="k"))
    by_name = {s.name: s for s in specs}
    ctx = FakeToolContext()
    ctx.tool_context["_current_tool_call_id"] = "call_1"
    args = by_name["web_answer"].args_model.model_validate(
        {"query": "q", "mode": "single", "stream": True}
    )
    out = await by_name["web_answer"].node.func(args, ctx)
    assert by_name["web_answer"].out_model is WebAnswerResult
    parsed = WebAnswerResult.model_validate(out)
    assert "Hello world" in parsed.answer_markdown
    assert len(ctx.chunks) >= 2


@pytest.mark.asyncio
async def test_web_fetch_rejects_localhost() -> None:
    """Web fetch rejects localhost."""
    specs = build_web_tool_specs(BraveWebConfig(api_key="k"))
    by_name = {s.name: s for s in specs}
    ctx = FakeToolContext()
    args = by_name["web_fetch"].args_model.model_validate(
        {"url": "http://localhost/test"}
    )
    with pytest.raises(ValueError):
        await by_name["web_fetch"].node.func(args, ctx)


@pytest.mark.asyncio
async def test_web_fetch_binary_stores_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Web fetch binary stores artifact."""
    body = b"\x89PNG\r\n\x1a\n" + b"x" * 16

    def handler(_method: str, _url: str, **_kwargs: object) -> _FakeStreamResp:
        return _FakeStreamResp(
            status=200, headers={"Content-Type": "image/png"}, chunks=[body]
        )

    import aiohttp

    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *_a, **_k: _FakeSession(handler)
    )
    specs = build_web_tool_specs(BraveWebConfig(api_key="k"))
    by_name = {s.name: s for s in specs}
    ctx = FakeToolContext()
    args = by_name["web_fetch"].args_model.model_validate(
        {"url": "https://example.com/image.png"}
    )
    out = await by_name["web_fetch"].node.func(args, ctx)
    assert by_name["web_fetch"].out_model is WebFetchResult
    parsed = WebFetchResult.model_validate(out)
    assert parsed.artifact is not None
    assert parsed.markdown_preview is None


@pytest.mark.asyncio
async def test_web_fetch_html_converts_markdown_and_truncates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Web fetch HTML converts markdown and truncates."""
    html = b"<html><body><h1>Hello</h1><p>world</p></body></html>"

    def handler(_method: str, _url: str, **_kwargs: object) -> _FakeStreamResp:
        return _FakeStreamResp(
            status=200, headers={"Content-Type": "text/html"}, chunks=[html]
        )

    # Fake markitdown module to avoid depending on its internal converters.
    class _FakeConvertResult:
        def __init__(self, text: str) -> None:
            self.text_content = text

    class _FakeMarkItDown:
        def __init__(self, enable_plugins: bool = False) -> None:
            self._enable_plugins = enable_plugins

        def convert(self, _path: str) -> _FakeConvertResult:
            return _FakeConvertResult("X" * 1000)

    fake_module = SimpleNamespace(MarkItDown=_FakeMarkItDown)
    monkeypatch.setitem(sys.modules, "markitdown", fake_module)

    import aiohttp

    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *_a, **_k: _FakeSession(handler)
    )
    specs = build_web_tool_specs(BraveWebConfig(api_key="k"))
    by_name = {s.name: s for s in specs}
    ctx = FakeToolContext()
    args = by_name["web_fetch"].args_model.model_validate(
        {"url": "https://example.com", "max_preview_chars": 200}
    )
    out = await by_name["web_fetch"].node.func(args, ctx)
    assert by_name["web_fetch"].out_model is WebFetchResult
    parsed = WebFetchResult.model_validate(out)
    assert parsed.truncated is True
    assert parsed.artifact is not None
    assert "UNTRUSTED EXTERNAL CONTENT" in (parsed.markdown_preview or "")
