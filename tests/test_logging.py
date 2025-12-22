"""Tests for the logging module."""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest

from penguiflow.logging import (
    ExtraFormatter,
    StructuredFormatter,
    configure_logging,
)


class TestStructuredFormatter:
    """Tests for StructuredFormatter."""

    def test_basic_format(self):
        """Test basic message formatting."""
        formatter = StructuredFormatter(include_timestamp=False)
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "INFO"
        assert data["logger"] == "test.logger"
        assert data["message"] == "Hello world"
        assert "timestamp" not in data

    def test_with_timestamp(self):
        """Test that timestamp is included when enabled."""
        formatter = StructuredFormatter(include_timestamp=True)
        record = logging.LogRecord(
            name="test",
            level=logging.DEBUG,
            pathname="test.py",
            lineno=1,
            msg="test message",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)

        assert "timestamp" in data
        # Should be ISO format
        assert "T" in data["timestamp"]

    def test_extra_fields(self):
        """Test that extra fields are captured."""
        formatter = StructuredFormatter(include_timestamp=False)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="action",
            args=(),
            exc_info=None,
        )
        # Add extra fields
        record.step = 5
        record.tool = "search"
        record.custom_data = {"nested": "value"}

        output = formatter.format(record)
        data = json.loads(output)

        assert "extra" in data
        assert data["extra"]["step"] == 5
        assert data["extra"]["tool"] == "search"
        assert data["extra"]["custom_data"] == {"nested": "value"}

    def test_non_serializable_value(self):
        """Test that non-serializable values fall back to str."""
        formatter = StructuredFormatter(include_timestamp=False)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test",
            args=(),
            exc_info=None,
        )
        # Add a non-serializable object
        record.my_object = object()

        output = formatter.format(record)
        data = json.loads(output)

        assert "extra" in data
        assert "my_object" in data["extra"]
        # Should be converted to string
        assert "<object object" in data["extra"]["my_object"]

    def test_exception_info(self):
        """Test that exception info is included."""
        formatter = StructuredFormatter(include_timestamp=False)
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="error occurred",
            args=(),
            exc_info=exc_info,
        )

        output = formatter.format(record)
        data = json.loads(output)

        assert "exception" in data
        assert "ValueError" in data["exception"]
        assert "test error" in data["exception"]

    def test_indent_option(self):
        """Test that indent option produces formatted JSON."""
        formatter = StructuredFormatter(include_timestamp=False, indent=2)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)

        # Indented JSON has newlines
        assert "\n" in output
        # Should still be valid JSON
        data = json.loads(output)
        assert data["message"] == "test"


class TestExtraFormatter:
    """Tests for ExtraFormatter."""

    def test_basic_format(self):
        """Test basic message formatting without extras."""
        formatter = ExtraFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test message",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)

        assert "test message" in output
        # No extras, no brackets
        assert "[" not in output

    def test_with_extra_fields(self):
        """Test that extra fields are appended in brackets."""
        formatter = ExtraFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="planner action",
            args=(),
            exc_info=None,
        )
        record.step = 3
        record.tool = "search"

        output = formatter.format(record)

        assert "planner action" in output
        assert "[" in output
        assert 'step=3' in output
        assert 'tool="search"' in output

    def test_long_string_truncation(self):
        """Test that long string values are truncated."""
        formatter = ExtraFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test",
            args=(),
            exc_info=None,
        )
        # Add a very long string
        record.long_value = "x" * 200

        output = formatter.format(record)

        # Should be truncated with ellipsis
        assert "..." in output
        # Original 200 chars should not all be present
        assert "x" * 200 not in output
        # But first 100 should be
        assert "x" * 100 in output

    def test_non_string_values(self):
        """Test that non-string values are formatted without quotes."""
        formatter = ExtraFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test",
            args=(),
            exc_info=None,
        )
        record.count = 42
        record.active = True

        output = formatter.format(record)

        assert "count=42" in output
        assert "active=True" in output

    def test_custom_format(self):
        """Test that custom format string is respected."""
        formatter = ExtraFormatter(fmt="%(levelname)s - %(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="warning message",
            args=(),
            exc_info=None,
        )
        record.code = "W001"

        output = formatter.format(record)

        assert output.startswith("WARNING - warning message")
        assert 'code="W001"' in output


class TestConfigureLogging:
    """Tests for configure_logging function."""

    def test_structured_mode(self):
        """Test structured JSON logging mode."""
        configure_logging(level="DEBUG", structured=True, logger_name="test_struct")
        logger = logging.getLogger("test_struct")

        # Capture output
        stream = StringIO()
        logger.handlers[0].stream = stream

        logger.info("test message", extra={"step": 1})

        output = stream.getvalue()
        data = json.loads(output.strip())

        assert data["message"] == "test message"
        assert data["extra"]["step"] == 1

    def test_extras_mode(self):
        """Test human-readable mode with extras."""
        configure_logging(
            level="DEBUG",
            structured=False,
            include_extras=True,
            logger_name="test_extras",
        )
        logger = logging.getLogger("test_extras")

        stream = StringIO()
        logger.handlers[0].stream = stream

        logger.info("action", extra={"tool": "search"})

        output = stream.getvalue()
        assert "action" in output
        assert 'tool="search"' in output

    def test_simple_mode(self):
        """Test simple logging mode without extras."""
        configure_logging(
            level="DEBUG",
            structured=False,
            include_extras=False,
            logger_name="test_simple",
        )
        logger = logging.getLogger("test_simple")

        stream = StringIO()
        logger.handlers[0].stream = stream

        logger.info("simple message", extra={"ignored": "value"})

        output = stream.getvalue()
        assert "simple message" in output
        # Extra should not appear
        assert "ignored" not in output

    def test_removes_existing_handlers(self):
        """Test that existing handlers are removed."""
        logger_name = "test_replace"
        logger = logging.getLogger(logger_name)

        # Add a dummy handler
        dummy_handler = logging.StreamHandler()
        logger.addHandler(dummy_handler)
        initial_count = len(logger.handlers)

        configure_logging(level="INFO", logger_name=logger_name)

        # Should have exactly one handler now
        assert len(logger.handlers) == 1
        assert logger.handlers[0] is not dummy_handler

    def test_propagation_disabled(self):
        """Test that propagation to root logger is disabled."""
        configure_logging(level="INFO", logger_name="test_propagate")
        logger = logging.getLogger("test_propagate")

        assert logger.propagate is False

    def test_level_as_string(self):
        """Test that level can be specified as string."""
        configure_logging(level="WARNING", logger_name="test_level_str")
        logger = logging.getLogger("test_level_str")

        assert logger.level == logging.WARNING

    def test_level_as_int(self):
        """Test that level can be specified as int."""
        configure_logging(level=logging.ERROR, logger_name="test_level_int")
        logger = logging.getLogger("test_level_int")

        assert logger.level == logging.ERROR
