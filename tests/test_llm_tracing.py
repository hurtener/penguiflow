"""Tests for the LLM call tracing seam."""

from __future__ import annotations

import logging
import sys
import types
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from penguiflow.llm.protocol import NativeLLMAdapter, create_native_adapter
from penguiflow.llm.tracing import (
    TRACING_ENV_VAR,
    LLMCallSpan,
    LoggingLLMTraceSink,
    MlflowLLMTraceSink,
    OpenTelemetryLLMTraceSink,
    resolve_trace_sink_from_env,
)
from penguiflow.llm.types import CompletionResponse, LLMMessage, TextPart, Usage


class RecordingSink:
    """Minimal LLMTraceSink capturing every span for assertions."""

    def __init__(self) -> None:
        self.calls: list[LLMCallSpan] = []

    @contextmanager
    def span(self, call: LLMCallSpan) -> Any:
        self.calls.append(call)
        import time

        started = time.perf_counter()
        try:
            yield None
        except BaseException as exc:
            call.error_type = type(exc).__name__
            call.error_message = str(exc)[:500]
            raise
        finally:
            call.latency_ms = (time.perf_counter() - started) * 1000


def _mock_provider() -> MagicMock:
    provider = MagicMock()
    provider.model = "test-model"
    provider.provider_name = "test"
    provider.complete = AsyncMock(
        return_value=CompletionResponse(
            message=LLMMessage(role="assistant", parts=[TextPart(text='{"result": "ok"}')]),
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        )
    )
    return provider


class TestAdapterTracing:
    @pytest.mark.asyncio
    async def test_successful_call_records_span(self) -> None:
        sink = RecordingSink()
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            adapter = NativeLLMAdapter("test-model", trace_sink=sink)
            content, cost = await adapter.complete(messages=[{"role": "user", "content": "Hello"}])

        assert content == '{"result": "ok"}'
        assert len(sink.calls) == 1
        call = sink.calls[0]
        assert call.provider == "test"
        assert call.model == "test-model"
        assert call.attempts == 1
        assert call.content_chars == len(content)
        assert call.input_tokens == 10
        assert call.output_tokens == 5
        assert call.cost_usd == cost
        assert call.latency_ms is not None
        assert call.error_type is None

    @pytest.mark.asyncio
    async def test_failed_call_records_error_and_propagates(self) -> None:
        sink = RecordingSink()
        provider = _mock_provider()
        provider.complete = AsyncMock(side_effect=ValueError("boom"))
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = provider
            adapter = NativeLLMAdapter("test-model", trace_sink=sink)
            with pytest.raises(ValueError, match="boom"):
                await adapter.complete(messages=[{"role": "user", "content": "Hello"}])

        assert len(sink.calls) == 1
        call = sink.calls[0]
        assert call.error_type == "ValueError"
        assert call.error_message == "boom"
        assert call.latency_ms is not None
        assert call.cost_usd is None

    @pytest.mark.asyncio
    async def test_no_sink_means_no_tracing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(TRACING_ENV_VAR, raising=False)
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            adapter = NativeLLMAdapter("test-model")
            assert adapter._trace_sink is None
            content, _ = await adapter.complete(messages=[{"role": "user", "content": "Hello"}])
        assert content == '{"result": "ok"}'

    def test_create_native_adapter_threads_sink(self) -> None:
        sink = RecordingSink()
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            adapter = create_native_adapter("test-model", trace_sink=sink)
        assert adapter._trace_sink is sink

    @pytest.mark.asyncio
    async def test_fallback_client_adapters_share_sink(self) -> None:
        """Adapters built by FallbackLLMClient inherit the trace sink, so
        failover produces spans attributed to each model actually called."""
        from penguiflow.llm.fallback import ModelFallbackConfig

        sink = RecordingSink()
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            client = create_native_adapter(
                "test-model",
                fallback=ModelFallbackConfig(models=["other-model"]),
                trace_sink=sink,
            )
            content, _ = await client.complete(messages=[{"role": "user", "content": "Hello"}])

        assert content == '{"result": "ok"}'
        assert len(sink.calls) == 1
        assert sink.calls[0].model == "test-model"


class TestEnvResolution:
    def test_unset_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(TRACING_ENV_VAR, raising=False)
        assert resolve_trace_sink_from_env() is None

    @pytest.mark.parametrize("value", ["0", "false", "off", "none", "disabled", ""])
    def test_falsy_values_disable(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(TRACING_ENV_VAR, value)
        assert resolve_trace_sink_from_env() is None

    def test_log_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(TRACING_ENV_VAR, "log")
        assert isinstance(resolve_trace_sink_from_env(), LoggingLLMTraceSink)

    def test_mlflow_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(TRACING_ENV_VAR, "mlflow")
        assert isinstance(resolve_trace_sink_from_env(), MlflowLLMTraceSink)

    def test_unknown_value_is_ignored_with_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv(TRACING_ENV_VAR, "datadog")
        with caplog.at_level(logging.WARNING, logger="penguiflow.llm.tracing"):
            assert resolve_trace_sink_from_env() is None
        assert "datadog" in caplog.text

    @pytest.mark.asyncio
    async def test_env_var_enables_tracing_transparently(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(TRACING_ENV_VAR, "log")
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            adapter = NativeLLMAdapter("test-model")
            assert isinstance(adapter._trace_sink, LoggingLLMTraceSink)
            content, _ = await adapter.complete(messages=[{"role": "user", "content": "Hello"}])
        assert content == '{"result": "ok"}'


class TestLoggingSink:
    @pytest.mark.asyncio
    async def test_emits_log_record(self, caplog: pytest.LogCaptureFixture) -> None:
        sink = LoggingLLMTraceSink()
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            adapter = NativeLLMAdapter("test-model", trace_sink=sink)
            with caplog.at_level(logging.INFO, logger="penguiflow.llm.trace"):
                await adapter.complete(messages=[{"role": "user", "content": "Hello"}])

        records = [r for r in caplog.records if r.message == "llm_call"]
        assert len(records) == 1
        assert records[0].llm_model == "test-model"  # type: ignore[attr-defined]
        assert records[0].llm_provider == "test"  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_logs_on_error_too(self, caplog: pytest.LogCaptureFixture) -> None:
        sink = LoggingLLMTraceSink()
        provider = _mock_provider()
        provider.complete = AsyncMock(side_effect=ValueError("boom"))
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = provider
            adapter = NativeLLMAdapter("test-model", trace_sink=sink)
            with caplog.at_level(logging.INFO, logger="penguiflow.llm.trace"):
                with pytest.raises(ValueError):
                    await adapter.complete(messages=[{"role": "user", "content": "Hello"}])

        records = [r for r in caplog.records if r.message == "llm_call"]
        assert len(records) == 1
        assert records[0].llm_error_type == "ValueError"  # type: ignore[attr-defined]


class _FakeOtelSpan:
    def __init__(self, name: str, attributes: dict[str, Any]) -> None:
        self.name = name
        self.attributes = attributes

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        self.attributes.update(attributes)

    def set_status(self, _status: object) -> None:
        pass


class _FakeOtelTracer:
    def __init__(self, spans: list[_FakeOtelSpan]) -> None:
        self._spans = spans

    @contextmanager
    def start_as_current_span(self, name: str, *, kind: object, attributes: dict[str, Any]) -> Any:
        span = _FakeOtelSpan(name, attributes)
        self._spans.append(span)
        yield span


class TestOpenTelemetrySink:
    @pytest.mark.asyncio
    async def test_emits_metadata_only_genai_span(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spans: list[_FakeOtelSpan] = []
        monkeypatch.setattr(
            "penguiflow.llm.tracing.trace.get_tracer",
            lambda _name: _FakeOtelTracer(spans),
        )

        sink = OpenTelemetryLLMTraceSink()
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            adapter = NativeLLMAdapter("test-model", trace_sink=sink)
            content, _ = await adapter.complete(messages=[{"role": "user", "content": "secret prompt"}])

        assert content == '{"result": "ok"}'
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "gen_ai.chat"
        assert span.attributes == {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "test",
            "gen_ai.request.model": "test-model",
            "penguiflow.llm.stream": False,
            "penguiflow.llm.response_format": "text",
            "gen_ai.usage.input_tokens": 10,
            "gen_ai.usage.output_tokens": 5,
            "penguiflow.llm.cost_usd": 0.0,
            "penguiflow.llm.attempts": 1,
            "penguiflow.llm.content_chars": len(content),
            "penguiflow.llm.latency_ms": pytest.approx(0, abs=1000),
        }


class _FakeMlflowSpan:
    def __init__(self, name: str, span_type: str | None) -> None:
        self.name = name
        self.span_type = span_type
        self.attributes: dict[str, Any] = {}

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        self.attributes.update(attributes)


def _make_fake_mlflow(spans: list[_FakeMlflowSpan]) -> types.ModuleType:
    module = types.ModuleType("mlflow")

    @contextmanager
    def start_span(name: str, span_type: str | None = None) -> Any:
        span = _FakeMlflowSpan(name, span_type)
        spans.append(span)
        yield span

    module.start_span = start_span  # type: ignore[attr-defined]
    module.__version__ = "3.0.0-fake"
    return module


class TestMlflowSink:
    @pytest.mark.asyncio
    async def test_emits_mlflow_span(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spans: list[_FakeMlflowSpan] = []
        monkeypatch.setitem(sys.modules, "mlflow", _make_fake_mlflow(spans))

        sink = MlflowLLMTraceSink()
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            adapter = NativeLLMAdapter("test-model", trace_sink=sink)
            content, _ = await adapter.complete(messages=[{"role": "user", "content": "Hello"}])

        assert content == '{"result": "ok"}'
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "llm.complete"
        assert span.span_type == "LLM"
        assert span.attributes["model"] == "test-model"
        assert span.attributes["provider"] == "test"
        assert span.attributes["input_tokens"] == 10
        assert span.attributes["output_tokens"] == 5
        assert "latency_ms" in span.attributes

    @pytest.mark.asyncio
    async def test_missing_mlflow_degrades_to_noop(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A None entry in sys.modules makes `import mlflow` raise ImportError.
        monkeypatch.setitem(sys.modules, "mlflow", None)

        sink = MlflowLLMTraceSink()
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            adapter = NativeLLMAdapter("test-model", trace_sink=sink)
            with caplog.at_level(logging.WARNING, logger="penguiflow.llm.tracing"):
                content, _ = await adapter.complete(messages=[{"role": "user", "content": "Hello"}])

        assert content == '{"result": "ok"}'
        assert "MlflowLLMTraceSink disabled" in caplog.text
        assert sink._unavailable is True

    @pytest.mark.asyncio
    async def test_old_mlflow_without_tracing_degrades_to_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = types.ModuleType("mlflow")
        module.__version__ = "2.0.0-fake"  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "mlflow", module)

        sink = MlflowLLMTraceSink()
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            adapter = NativeLLMAdapter("test-model", trace_sink=sink)
            content, _ = await adapter.complete(messages=[{"role": "user", "content": "Hello"}])

        assert content == '{"result": "ok"}'
        assert sink._unavailable is True

    @pytest.mark.asyncio
    async def test_mlflow_span_records_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spans: list[_FakeMlflowSpan] = []
        monkeypatch.setitem(sys.modules, "mlflow", _make_fake_mlflow(spans))

        sink = MlflowLLMTraceSink()
        provider = _mock_provider()
        provider.complete = AsyncMock(side_effect=ValueError("boom"))
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = provider
            adapter = NativeLLMAdapter("test-model", trace_sink=sink)
            with pytest.raises(ValueError):
                await adapter.complete(messages=[{"role": "user", "content": "Hello"}])

        assert len(spans) == 1
        assert spans[0].attributes["error_type"] == "ValueError"
