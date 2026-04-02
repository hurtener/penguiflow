from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from penguiflow.artifacts import InMemoryArtifactStore
from penguiflow.common_tools.web import BraveWebConfig, build_web_tool_specs


class FakeToolCtx:
    def __init__(self) -> None:
        self._artifacts = InMemoryArtifactStore()
        self._llm_context: dict[str, Any] = {}
        self._tool_context: dict[str, Any] = {}
        self.emitted: list[dict[str, Any]] = []

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
        self.emitted.append(
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
        raise RuntimeError(f"pause not supported: {reason}")

    @property
    def kv(self):
        raise RuntimeError("kv not supported")


class _FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, _n: int):
        for c in self._chunks:
            yield c


class _FakeResp:
    def __init__(
        self,
        *,
        status: int,
        headers: dict[str, str] | None = None,
        json_body=None,
        text_body: str = "",
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._json_body = json_body
        self._text_body = text_body
        self.content = _FakeContent([])

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._json_body

    async def text(self, errors: str = "replace"):
        return self._text_body


class _FakeStreamResp(_FakeResp):
    def __init__(self, *, status: int, headers: dict[str, str] | None = None, chunks: list[bytes]) -> None:
        super().__init__(status=status, headers=headers or {}, json_body=None)
        self.content = _FakeContent(chunks)


class _FakeSession:
    def __init__(self, handler):
        self._handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def request(self, method: str, url: str, **kwargs):
        return self._handler(method, url, **kwargs)

    def get(self, url: str, **kwargs):
        return self._handler("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self._handler("POST", url, **kwargs)


@pytest.mark.asyncio
async def test_build_web_tool_specs_metadata() -> None:
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
async def test_web_search_missing_key_raises(monkeypatch) -> None:
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    specs = build_web_tool_specs(BraveWebConfig(api_key=None))
    by_name = {s.name: s for s in specs}
    ctx = FakeToolCtx()
    args = by_name["web_search"].args_model.model_validate({"query": "penguiflow"})
    with pytest.raises(ValueError):
        await by_name["web_search"].node.func(args, ctx)


@pytest.mark.asyncio
async def test_web_answer_accepts_singular_answers_env_alias(monkeypatch) -> None:
    monkeypatch.delenv("BRAVE_ANSWERS_API_KEY", raising=False)
    monkeypatch.setenv("BRAVE_ANSWER_API_KEY", "k_answers")
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    def handler(method: str, url: str, **kwargs):
        headers = kwargs.get("headers", {})
        assert headers.get("X-Subscription-Token") == "k_answers"
        payload = {"choices": [{"message": {"content": "ok"}}]}
        return _FakeResp(status=200, json_body=payload)

    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(handler))
    specs = build_web_tool_specs(BraveWebConfig(api_key=None))
    by_name = {s.name: s for s in specs}
    ctx = FakeToolCtx()
    args = by_name["web_answer"].args_model.model_validate({"query": "q", "stream": False})
    out = await by_name["web_answer"].node.func(args, ctx)
    parsed = by_name["web_answer"].out_model.model_validate(out)
    assert "ok" in parsed.answer_markdown


def test_web_answer_validation_errors_are_json_serializable() -> None:
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
async def test_web_context_parses_grounding_and_wraps(monkeypatch) -> None:
    payload = {
        "grounding": {"generic": [{"url": "https://example.com", "title": "Example", "snippets": ["a", "b"]}]},
        "sources": {"https://example.com": {"title": "Example", "hostname": "example.com", "age": ["x"]}},
    }

    def handler(method: str, url: str, **kwargs):
        assert "X-Subscription-Token" in kwargs.get("headers", {})
        return _FakeResp(status=200, json_body=payload)

    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(handler))
    specs = build_web_tool_specs(BraveWebConfig(api_key="k", max_preview_chars=200))
    by_name = {s.name: s for s in specs}
    ctx = FakeToolCtx()
    args = by_name["web_context"].args_model.model_validate({"query": "q"})
    out = await by_name["web_context"].node.func(args, ctx)
    parsed = by_name["web_context"].out_model.model_validate(out)
    assert parsed.grounding and parsed.grounding[0].url == "https://example.com"
    assert "UNTRUSTED EXTERNAL CONTENT" in parsed.context_markdown


@pytest.mark.asyncio
async def test_web_search_normalizes_bool_query_params(monkeypatch) -> None:
    observed_params: dict[str, Any] = {}

    def handler(method: str, url: str, **kwargs):
        nonlocal observed_params
        params = kwargs.get("params", {})
        if isinstance(params, dict):
            observed_params = dict(params)
        payload = {"web": {"results": [{"url": "https://example.com", "title": "Example"}]}}
        return _FakeResp(status=200, json_body=payload)

    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(handler))
    specs = build_web_tool_specs(BraveWebConfig(api_key="k"))
    by_name = {s.name: s for s in specs}
    ctx = FakeToolCtx()
    args = by_name["web_search"].args_model.model_validate({"query": "penguin flow"})
    out = await by_name["web_search"].node.func(args, ctx)
    parsed = by_name["web_search"].out_model.model_validate(out)
    assert parsed.web_results and parsed.web_results[0].url == "https://example.com"
    assert observed_params["spellcheck"] in {"true", "false"}
    assert observed_params["operators"] in {"true", "false"}
    assert observed_params["include_fetch_metadata"] in {"true", "false"}


@pytest.mark.asyncio
async def test_web_answer_streaming_emits_chunks(monkeypatch) -> None:
    # Two SSE data lines + DONE
    chunks = [
        b'data: {"choices":[{"delta":{"content":"Hello "}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"world"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]

    def handler(method: str, url: str, **kwargs):
        return _FakeStreamResp(status=200, chunks=chunks)

    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(handler))
    specs = build_web_tool_specs(BraveWebConfig(api_key="k"))
    by_name = {s.name: s for s in specs}
    ctx = FakeToolCtx()
    ctx.tool_context["_current_tool_call_id"] = "call_1"
    args = by_name["web_answer"].args_model.model_validate({"query": "q", "mode": "single", "stream": True})
    out = await by_name["web_answer"].node.func(args, ctx)
    parsed = by_name["web_answer"].out_model.model_validate(out)
    assert "Hello world" in parsed.answer_markdown
    assert len(ctx.emitted) >= 2


@pytest.mark.asyncio
async def test_web_fetch_rejects_localhost() -> None:
    specs = build_web_tool_specs(BraveWebConfig(api_key="k"))
    by_name = {s.name: s for s in specs}
    ctx = FakeToolCtx()
    args = by_name["web_fetch"].args_model.model_validate({"url": "http://localhost/test"})
    with pytest.raises(ValueError):
        await by_name["web_fetch"].node.func(args, ctx)


@pytest.mark.asyncio
async def test_web_fetch_binary_stores_artifact(monkeypatch) -> None:
    body = b"\x89PNG\r\n\x1a\n" + b"x" * 16

    def handler(method: str, url: str, **kwargs):
        resp = _FakeStreamResp(status=200, headers={"Content-Type": "image/png"}, chunks=[body])
        return resp

    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(handler))
    specs = build_web_tool_specs(BraveWebConfig(api_key="k"))
    by_name = {s.name: s for s in specs}
    ctx = FakeToolCtx()
    args = by_name["web_fetch"].args_model.model_validate({"url": "https://example.com/image.png"})
    out = await by_name["web_fetch"].node.func(args, ctx)
    parsed = by_name["web_fetch"].out_model.model_validate(out)
    assert parsed.artifact is not None
    assert parsed.markdown_preview is None


@pytest.mark.asyncio
async def test_web_fetch_html_converts_markdown_and_truncates(monkeypatch) -> None:
    html = b"<html><body><h1>Hello</h1><p>world</p></body></html>"

    def handler(method: str, url: str, **kwargs):
        return _FakeStreamResp(status=200, headers={"Content-Type": "text/html"}, chunks=[html])

    # Fake markitdown module to avoid depending on its internal converters.
    class _FakeConvertResult:
        def __init__(self, text: str) -> None:
            self.text_content = text

    class _FakeMarkItDown:
        def __init__(self, enable_plugins: bool = False) -> None:
            self._enable_plugins = enable_plugins

        def convert(self, _path: str):
            return _FakeConvertResult("X" * 1000)

    fake_module = SimpleNamespace(MarkItDown=_FakeMarkItDown)
    monkeypatch.setitem(sys.modules, "markitdown", fake_module)

    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(handler))
    specs = build_web_tool_specs(BraveWebConfig(api_key="k"))
    by_name = {s.name: s for s in specs}
    ctx = FakeToolCtx()
    args = by_name["web_fetch"].args_model.model_validate({"url": "https://example.com", "max_preview_chars": 200})
    out = await by_name["web_fetch"].node.func(args, ctx)
    parsed = by_name["web_fetch"].out_model.model_validate(out)
    assert parsed.truncated is True
    assert parsed.artifact is not None
    assert "UNTRUSTED EXTERNAL CONTENT" in (parsed.markdown_preview or "")
