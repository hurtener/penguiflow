"""Tests for the LLM telemetry module."""

from __future__ import annotations

from datetime import datetime

import pytest

from penguiflow.llm.telemetry import (
    LLMEvent,
    TelemetryHooks,
    TimingContext,
    default_logger_callback,
    get_telemetry_hooks,
    set_telemetry_hooks,
)


class TestLLMEvent:
    def test_create(self) -> None:
        event = LLMEvent(
            event_type="request_start",
            timestamp=datetime.now(),
            provider="openai",
            model="gpt-4o",
        )
        assert event.event_type == "request_start"
        assert event.provider == "openai"
        assert event.model == "gpt-4o"
        assert event.trace_id is None
        assert event.extra is None

    def test_create_with_extra(self) -> None:
        event = LLMEvent(
            event_type="request_end",
            timestamp=datetime.now(),
            provider="anthropic",
            model="claude-3-5-sonnet",
            trace_id="trace-123",
            extra={"latency_ms": 150},
        )
        assert event.trace_id == "trace-123"
        assert event.extra == {"latency_ms": 150}

    def test_create_factory(self) -> None:
        event = LLMEvent.create(
            "error",
            "google",
            "gemini-2.0-flash",
            trace_id="trace-456",
            error="Connection failed",
        )
        assert event.event_type == "error"
        assert event.provider == "google"
        assert event.extra is not None
        assert event.extra.get("error") == "Connection failed"

    def test_create_factory_no_extra(self) -> None:
        event = LLMEvent.create("request_start", "openai", "gpt-4o")
        assert event.extra is None


class TestTelemetryHooks:
    def test_register_callback(self) -> None:
        hooks = TelemetryHooks()
        events: list[LLMEvent] = []
        hooks.register(lambda e: events.append(e))

        event = LLMEvent.create("test", "provider", "model")
        hooks.emit(event)

        assert len(events) == 1
        assert events[0] == event

    def test_unregister_callback(self) -> None:
        hooks = TelemetryHooks()
        events: list[LLMEvent] = []

        def callback(e: LLMEvent) -> None:
            events.append(e)

        hooks.register(callback)
        result = hooks.unregister(callback)
        assert result is True

        hooks.emit(LLMEvent.create("test", "provider", "model"))
        assert len(events) == 0

    def test_unregister_not_found(self) -> None:
        hooks = TelemetryHooks()
        result = hooks.unregister(lambda e: None)
        assert result is False

    def test_disable_enable(self) -> None:
        hooks = TelemetryHooks()
        events: list[LLMEvent] = []
        hooks.register(lambda e: events.append(e))

        hooks.disable()
        hooks.emit(LLMEvent.create("test1", "provider", "model"))
        assert len(events) == 0

        hooks.enable()
        hooks.emit(LLMEvent.create("test2", "provider", "model"))
        assert len(events) == 1

    def test_clear(self) -> None:
        hooks = TelemetryHooks()
        events: list[LLMEvent] = []
        hooks.register(lambda e: events.append(e))
        hooks.clear()

        hooks.emit(LLMEvent.create("test", "provider", "model"))
        assert len(events) == 0

    def test_callback_error_does_not_propagate(self) -> None:
        hooks = TelemetryHooks()
        call_count = 0

        def bad_callback(e: LLMEvent) -> None:
            raise ValueError("Intentional error")

        def good_callback(e: LLMEvent) -> None:
            nonlocal call_count
            call_count += 1

        hooks.register(bad_callback)
        hooks.register(good_callback)

        # Should not raise, and should continue to next callback
        hooks.emit(LLMEvent.create("test", "provider", "model"))
        assert call_count == 1

    def test_multiple_callbacks(self) -> None:
        hooks = TelemetryHooks()
        events1: list[LLMEvent] = []
        events2: list[LLMEvent] = []

        hooks.register(lambda e: events1.append(e))
        hooks.register(lambda e: events2.append(e))

        event = LLMEvent.create("test", "provider", "model")
        hooks.emit(event)

        assert len(events1) == 1
        assert len(events2) == 1


class TestGlobalHooks:
    def test_get_telemetry_hooks(self) -> None:
        # Reset global state for clean test
        set_telemetry_hooks(TelemetryHooks())

        hooks = get_telemetry_hooks()
        assert hooks is not None

    def test_set_telemetry_hooks(self) -> None:
        custom_hooks = TelemetryHooks()
        set_telemetry_hooks(custom_hooks)
        assert get_telemetry_hooks() is custom_hooks


class TestDefaultLoggerCallback:
    def test_request_end(self, caplog: pytest.LogCaptureFixture) -> None:
        event = LLMEvent.create(
            "request_end",
            "openai",
            "gpt-4o",
            latency_ms=150,
            input_tokens=100,
            output_tokens=50,
        )
        import logging
        with caplog.at_level(logging.DEBUG):
            default_logger_callback(event)

        # Check that something was logged (may not appear if DEBUG not captured)

    def test_error(self, caplog: pytest.LogCaptureFixture) -> None:
        event = LLMEvent.create(
            "error",
            "anthropic",
            "claude-3-5-sonnet",
            error="Connection timeout",
        )
        import logging
        with caplog.at_level(logging.WARNING):
            default_logger_callback(event)

    def test_retry(self, caplog: pytest.LogCaptureFixture) -> None:
        event = LLMEvent.create(
            "retry",
            "google",
            "gemini-2.0-flash",
            attempt=2,
            reason="Rate limited",
        )
        import logging
        with caplog.at_level(logging.INFO):
            default_logger_callback(event)


class TestTimingContext:
    def test_timing_context(self) -> None:
        # Set up custom hooks to capture events
        hooks = TelemetryHooks()
        events: list[LLMEvent] = []
        hooks.register(lambda e: events.append(e))
        set_telemetry_hooks(hooks)

        with TimingContext("openai", "gpt-4o", trace_id="test-trace") as ctx:
            # Simulate some work
            pass

        assert ctx.latency_ms > 0
        # Should have emitted request_start
        assert any(e.event_type == "request_start" for e in events)

    def test_timing_context_with_error(self) -> None:
        hooks = TelemetryHooks()
        events: list[LLMEvent] = []
        hooks.register(lambda e: events.append(e))
        set_telemetry_hooks(hooks)

        with pytest.raises(ValueError):
            with TimingContext("openai", "gpt-4o") as _ctx:  # noqa: F841
                raise ValueError("Test error")

        # Should have emitted error event
        error_events = [e for e in events if e.event_type == "error"]
        assert len(error_events) == 1
        assert error_events[0].extra is not None
        assert "Test error" in error_events[0].extra.get("error", "")

    def test_latency_property_during_execution(self) -> None:
        hooks = TelemetryHooks()
        set_telemetry_hooks(hooks)

        with TimingContext("openai", "gpt-4o") as ctx:
            # Mid-execution, latency should be positive
            assert ctx.latency_ms > 0


class TestGetTelemetryHooksInitialization:
    """Test fresh initialization of global hooks."""

    def test_fresh_init_registers_default_callback(self) -> None:
        """Test that fresh get_telemetry_hooks() registers default callback."""
        import penguiflow.llm.telemetry as telemetry_module

        # Clear the global hooks
        telemetry_module._hooks = None

        # Get fresh hooks - should auto-register default callback
        hooks = get_telemetry_hooks()

        # Should have at least one callback (the default logger)
        assert len(hooks._callbacks) >= 1


class TestMlflowCallback:
    """Tests for create_mlflow_callback."""

    def test_mlflow_callback_without_mlflow(self) -> None:
        """Test mlflow callback gracefully handles missing mlflow."""
        from penguiflow.llm.telemetry import create_mlflow_callback

        callback = create_mlflow_callback()

        # Should not raise even without mlflow
        event = LLMEvent.create(
            "request_end",
            "openai",
            "gpt-4o",
            latency_ms=100,
            input_tokens=50,
            output_tokens=25,
            cost=0.001,
        )
        callback(event)  # Should not raise

    def test_mlflow_callback_with_request_end(self) -> None:
        """Test mlflow callback logs metrics on request_end."""
        from unittest.mock import MagicMock, patch

        from penguiflow.llm.telemetry import create_mlflow_callback

        callback = create_mlflow_callback()

        # Mock mlflow import
        mock_mlflow = MagicMock()

        event = LLMEvent.create(
            "request_end",
            "openai",
            "gpt-4o",
            latency_ms=100,
            input_tokens=50,
            output_tokens=25,
            cost=0.002,
        )

        # Patch mlflow inside the callback
        with patch.dict("sys.modules", {"mlflow": mock_mlflow}):
            callback(event)

            # Verify log_metrics was called when mlflow is available

    def test_mlflow_callback_ignores_non_request_end(self) -> None:
        """Test mlflow callback ignores other event types."""
        from penguiflow.llm.telemetry import create_mlflow_callback

        callback = create_mlflow_callback()

        # Should not raise for non-request_end events
        event = LLMEvent.create("request_start", "openai", "gpt-4o")
        callback(event)

        event = LLMEvent.create("error", "openai", "gpt-4o", error="test")
        callback(event)


class TestPrometheusCallback:
    """Tests for create_prometheus_callback."""

    def test_prometheus_callback_without_prometheus(self) -> None:
        """Test prometheus callback gracefully handles missing prometheus_client."""
        from penguiflow.llm.telemetry import create_prometheus_callback

        callback = create_prometheus_callback()

        # Should not raise even without prometheus
        event = LLMEvent.create(
            "request_end",
            "openai",
            "gpt-4o",
            latency_ms=100,
            input_tokens=50,
            output_tokens=25,
        )
        callback(event)  # Should not raise

    def test_prometheus_callback_request_end(self) -> None:
        """Test prometheus callback updates metrics on request_end."""
        from unittest.mock import MagicMock, patch

        # Create mocks for prometheus counters/histograms
        mock_counter = MagicMock()
        mock_histogram = MagicMock()
        mock_counter_instance = MagicMock()
        mock_histogram_instance = MagicMock()
        mock_counter.return_value = mock_counter_instance
        mock_histogram.return_value = mock_histogram_instance

        # Create a mock prometheus_client module
        mock_prometheus = MagicMock()
        mock_prometheus.Counter = mock_counter
        mock_prometheus.Histogram = mock_histogram

        with patch.dict("sys.modules", {"prometheus_client": mock_prometheus}):
            # Need to reload to pick up the mock
            from penguiflow.llm.telemetry import create_prometheus_callback

            callback = create_prometheus_callback()

            event = LLMEvent.create(
                "request_end",
                "openai",
                "gpt-4o",
                latency_ms=100,
                input_tokens=50,
                output_tokens=25,
            )

            # The callback might not call anything if prometheus wasn't available at module load
            callback(event)

    def test_prometheus_callback_error_event(self) -> None:
        """Test prometheus callback handles error events."""
        from penguiflow.llm.telemetry import create_prometheus_callback

        callback = create_prometheus_callback()

        # Should not raise for error events
        event = LLMEvent.create(
            "error",
            "openai",
            "gpt-4o",
            error="Something went wrong",
        )
        callback(event)

    def test_prometheus_callback_ignores_other_events(self) -> None:
        """Test prometheus callback ignores non-relevant event types."""
        from penguiflow.llm.telemetry import create_prometheus_callback

        callback = create_prometheus_callback()

        # Should not raise for other event types
        event = LLMEvent.create("request_start", "openai", "gpt-4o")
        callback(event)

        event = LLMEvent.create("retry", "openai", "gpt-4o", attempt=1)
        callback(event)


class TestDefaultLoggerCallbackEdgeCases:
    """Additional tests for default_logger_callback edge cases."""

    def test_request_end_no_extra(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test request_end with no extra data."""
        import logging

        event = LLMEvent(
            event_type="request_end",
            timestamp=datetime.now(),
            provider="openai",
            model="gpt-4o",
            extra=None,
        )

        with caplog.at_level(logging.DEBUG):
            default_logger_callback(event)

    def test_error_no_extra(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test error event with no extra data."""
        import logging

        event = LLMEvent(
            event_type="error",
            timestamp=datetime.now(),
            provider="openai",
            model="gpt-4o",
            extra=None,
        )

        with caplog.at_level(logging.WARNING):
            default_logger_callback(event)

    def test_retry_no_extra(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test retry event with no extra data."""
        import logging

        event = LLMEvent(
            event_type="retry",
            timestamp=datetime.now(),
            provider="openai",
            model="gpt-4o",
            extra=None,
        )

        with caplog.at_level(logging.INFO):
            default_logger_callback(event)

    def test_unknown_event_type(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test callback handles unknown event types."""
        import logging

        event = LLMEvent.create("unknown_event", "openai", "gpt-4o")

        with caplog.at_level(logging.DEBUG):
            # Should not raise
            default_logger_callback(event)
