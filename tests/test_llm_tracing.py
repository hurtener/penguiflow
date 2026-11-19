"""Tests for the LLM call tracing seam."""

from __future__ import annotations

import logging
import sys
import types
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from penguiflow.llm.protocol import NativeLLMAdapter, create_native_adapter
from penguiflow.llm.tracing import (
    TRACING_ENV_VAR,
    LLMCallSpan,
    LoggingLLMTraceSink,
    MlflowLLMTraceSink,
    resolve_trace_sink_from_env,
)
from penguiflow.llm.types import (
    CompletionResponse,
    LLMMessage,
    TextPart,
    Usage,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


class RecordingSink:
    """Minimal LLMTraceSink capturing every span for assertions."""

    def __init__(self) -> None:
        """Start with no recorded state."""
        self.calls: list[LLMCallSpan] = []

    @contextmanager
    def span(self, call: LLMCallSpan) -> Iterator[None]:
        """Record ``call`` and its latency and error, if any."""
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
            message=LLMMessage(
                role="assistant", parts=[TextPart(text='{"result": "ok"}')]
            ),
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        )
    )
    return provider


class TestAdapterTracing:
    """Tests for adapter tracing."""

    @pytest.mark.asyncio
    async def test_successful_call_records_span(self) -> None:
        """Successful call records span."""
        sink = RecordingSink()
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            adapter = NativeLLMAdapter("test-model", trace_sink=sink)
            content, cost = await adapter.complete(
                messages=[{"role": "user", "content": "Hello"}]
            )

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
        """Failed call records error and propagates."""
        sink = RecordingSink()
        provider = _mock_provider()
        provider.complete = AsyncMock(side_effect=ValueError("boom"))
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = provider
            adapter = NativeLLMAdapter("test-model", trace_sink=sink)
            with pytest.raises(ValueError, match="boom"):
                await adapter.complete(
                    messages=[{"role": "user", "content": "Hello"}]
                )

        assert len(sink.calls) == 1
        call = sink.calls[0]
        assert call.error_type == "ValueError"
        assert call.error_message == "boom"
        assert call.latency_ms is not None
        assert call.cost_usd is None

    @pytest.mark.asyncio
    async def test_no_sink_means_no_tracing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No sink means no tracing."""
        monkeypatch.delenv(TRACING_ENV_VAR, raising=False)
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            adapter = NativeLLMAdapter("test-model")
            assert adapter.trace_sink is None
            content, _ = await adapter.complete(
                messages=[{"role": "user", "content": "Hello"}]
            )
        assert content == '{"result": "ok"}'

    def test_create_native_adapter_threads_sink(self) -> None:
        """Create native adapter threads sink."""
        sink = RecordingSink()
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            adapter = create_native_adapter("test-model", trace_sink=sink)
        assert isinstance(adapter, NativeLLMAdapter)
        assert adapter.trace_sink is sink

    @pytest.mark.asyncio
    async def test_fallback_client_adapters_share_sink(self) -> None:
        """Adapters built by FallbackLLMClient inherit the trace sink.

        Failover then produces spans attributed to each model called.
        """
        from penguiflow.llm.fallback import ModelFallbackConfig

        sink = RecordingSink()
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            client = create_native_adapter(
                "test-model",
                fallback=ModelFallbackConfig(models=["other-model"]),
                trace_sink=sink,
            )
            content, _ = await client.complete(
                messages=[{"role": "user", "content": "Hello"}]
            )

        assert content == '{"result": "ok"}'
        assert len(sink.calls) == 1
        assert sink.calls[0].model == "test-model"


class TestEnvResolution:
    """Tests for env resolution."""

    def test_unset_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unset disables."""
        monkeypatch.delenv(TRACING_ENV_VAR, raising=False)
        assert resolve_trace_sink_from_env() is None

    @pytest.mark.parametrize(
        "value", ["0", "false", "off", "none", "disabled", ""]
    )
    def test_falsy_values_disable(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """Falsy values disable."""
        monkeypatch.setenv(TRACING_ENV_VAR, value)
        assert resolve_trace_sink_from_env() is None

    def test_log_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Log value."""
        monkeypatch.setenv(TRACING_ENV_VAR, "log")
        assert isinstance(resolve_trace_sink_from_env(), LoggingLLMTraceSink)

    def test_mlflow_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MLflow value."""
        monkeypatch.setenv(TRACING_ENV_VAR, "mlflow")
        assert isinstance(resolve_trace_sink_from_env(), MlflowLLMTraceSink)

    def test_unknown_value_is_ignored_with_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Unknown value is ignored with warning."""
        monkeypatch.setenv(TRACING_ENV_VAR, "datadog")
        with caplog.at_level(logging.WARNING, logger="penguiflow.llm.tracing"):
            assert resolve_trace_sink_from_env() is None
        assert "datadog" in caplog.text

    @pytest.mark.asyncio
    async def test_env_var_enables_tracing_transparently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Env var enables tracing transparently."""
        monkeypatch.setenv(TRACING_ENV_VAR, "log")
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            adapter = NativeLLMAdapter("test-model")
            assert isinstance(adapter.trace_sink, LoggingLLMTraceSink)
            content, _ = await adapter.complete(
                messages=[{"role": "user", "content": "Hello"}]
            )
        assert content == '{"result": "ok"}'


class TestLoggingSink:
    """Tests for logging sink."""

    @pytest.mark.asyncio
    async def test_emits_log_record(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Emits log record."""
        sink = LoggingLLMTraceSink()
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            adapter = NativeLLMAdapter("test-model", trace_sink=sink)
            with caplog.at_level(logging.INFO, logger="penguiflow.llm.trace"):
                await adapter.complete(
                    messages=[{"role": "user", "content": "Hello"}]
                )

        records = [r for r in caplog.records if r.message == "llm_call"]
        assert len(records) == 1
        assert vars(records[0])["llm_model"] == "test-model"
        assert vars(records[0])["llm_provider"] == "test"

    @pytest.mark.asyncio
    async def test_logs_on_error_too(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Logs on error too."""
        sink = LoggingLLMTraceSink()
        provider = _mock_provider()
        provider.complete = AsyncMock(side_effect=ValueError("boom"))
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = provider
            adapter = NativeLLMAdapter("test-model", trace_sink=sink)
            with (
                caplog.at_level(logging.INFO, logger="penguiflow.llm.trace"),
                pytest.raises(ValueError),
            ):
                await adapter.complete(
                    messages=[{"role": "user", "content": "Hello"}]
                )

        records = [r for r in caplog.records if r.message == "llm_call"]
        assert len(records) == 1
        assert vars(records[0])["llm_error_type"] == "ValueError"


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
    def start_span(
        name: str, span_type: str | None = None
    ) -> Iterator[_FakeMlflowSpan | None]:
        span = _FakeMlflowSpan(name, span_type)
        spans.append(span)
        yield span

    module.__dict__.update(start_span=start_span, __version__="3.0.0-fake")
    return module


class TestMlflowSink:
    """Tests for mlflow sink."""

    @pytest.mark.asyncio
    async def test_emits_mlflow_span(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Emits MLflow span."""
        spans: list[_FakeMlflowSpan] = []
        monkeypatch.setitem(sys.modules, "mlflow", _make_fake_mlflow(spans))

        sink = MlflowLLMTraceSink()
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            adapter = NativeLLMAdapter("test-model", trace_sink=sink)
            content, _ = await adapter.complete(
                messages=[{"role": "user", "content": "Hello"}]
            )

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
        """Missing MLflow degrades to noop."""
        monkeypatch.setitem(sys.modules, "mlflow", None)

        sink = MlflowLLMTraceSink()
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            adapter = NativeLLMAdapter("test-model", trace_sink=sink)
            with caplog.at_level(
                logging.WARNING, logger="penguiflow.llm.tracing"
            ):
                content, _ = await adapter.complete(
                    messages=[{"role": "user", "content": "Hello"}]
                )

        assert content == '{"result": "ok"}'
        assert "MlflowLLMTraceSink disabled" in caplog.text
        assert sink.available is False

    @pytest.mark.asyncio
    async def test_old_mlflow_without_tracing_degrades_to_noop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Old MLflow without tracing degrades to noop."""
        module = types.ModuleType("mlflow")
        module.__dict__.update(__version__="2.0.0-fake")
        monkeypatch.setitem(sys.modules, "mlflow", module)

        sink = MlflowLLMTraceSink()
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = _mock_provider()
            adapter = NativeLLMAdapter("test-model", trace_sink=sink)
            content, _ = await adapter.complete(
                messages=[{"role": "user", "content": "Hello"}]
            )

        assert content == '{"result": "ok"}'
        assert sink.available is False

    @pytest.mark.asyncio
    async def test_mlflow_span_records_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MLflow span records error."""
        spans: list[_FakeMlflowSpan] = []
        monkeypatch.setitem(sys.modules, "mlflow", _make_fake_mlflow(spans))

        sink = MlflowLLMTraceSink()
        provider = _mock_provider()
        provider.complete = AsyncMock(side_effect=ValueError("boom"))
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = provider
            adapter = NativeLLMAdapter("test-model", trace_sink=sink)
            with pytest.raises(ValueError):
                await adapter.complete(
                    messages=[{"role": "user", "content": "Hello"}]
                )

        assert len(spans) == 1
        assert spans[0].attributes["error_type"] == "ValueError"
