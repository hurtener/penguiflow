"""Logging utilities for PenguiFlow with structured output support."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any


class StructuredFormatter(logging.Formatter):
    """
    A formatter that outputs JSON lines with all extra fields included.

    This is useful for debugging and log aggregation systems that expect
    structured JSON output.
    """

    # Fields that are part of the standard LogRecord and should be excluded
    # from the 'extra' output to avoid duplication
    STANDARD_FIELDS = frozenset({
        "name", "msg", "args", "created", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs",
        "pathname", "process", "processName", "relativeCreated",
        "stack_info", "exc_info", "exc_text", "thread", "threadName",
        "taskName", "message",
    })

    def __init__(self, include_timestamp: bool = True, indent: int | None = None) -> None:
        super().__init__()
        self._include_timestamp = include_timestamp
        self._indent = indent

    def format(self, record: logging.LogRecord) -> str:
        # Build the base log entry
        entry: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if self._include_timestamp:
            entry["timestamp"] = datetime.fromtimestamp(
                record.created, tz=UTC
            ).isoformat()

        # Extract extra fields (anything not in STANDARD_FIELDS)
        extra: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key not in self.STANDARD_FIELDS:
                # Try to serialize, fall back to str
                try:
                    json.dumps(value)
                    extra[key] = value
                except (TypeError, ValueError):
                    extra[key] = str(value)

        if extra:
            entry["extra"] = extra

        # Include exception info if present
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(entry, ensure_ascii=False, default=str, indent=self._indent)


class ExtraFormatter(logging.Formatter):
    """
    A formatter that appends extra fields to the standard log format.

    Output example:
        INFO:penguiflow.planner:planner_action [step=1, thought="...", next_node="search"]
    """

    STANDARD_FIELDS = StructuredFormatter.STANDARD_FIELDS

    def __init__(self, fmt: str | None = None, datefmt: str | None = None) -> None:
        super().__init__(fmt, datefmt)

    def format(self, record: logging.LogRecord) -> str:
        # Get the base formatted message
        base = super().format(record)

        # Extract extra fields
        extras: list[str] = []
        for key, value in record.__dict__.items():
            if key not in self.STANDARD_FIELDS:
                # Format value for display
                if isinstance(value, str):
                    if len(value) > 100:
                        value = value[:100] + "..."
                    extras.append(f'{key}="{value}"')
                else:
                    extras.append(f"{key}={value}")

        if extras:
            return f"{base} [{', '.join(extras)}]"
        return base


def configure_logging(
    level: int | str = logging.INFO,
    *,
    structured: bool = False,
    include_extras: bool = True,
    logger_name: str = "penguiflow",
) -> None:
    """
    Configure penguiflow logging with optional structured output.

    Parameters
    ----------
    level : int | str
        Log level (e.g., logging.DEBUG, "DEBUG", logging.INFO)
    structured : bool
        If True, output JSON lines. If False, use human-readable format.
    include_extras : bool
        If True (and structured=False), append extra fields to log lines.
    logger_name : str
        Logger name to configure. Default: "penguiflow"

    Examples
    --------
    >>> from penguiflow.logging import configure_logging
    >>> configure_logging(level="DEBUG", structured=True)

    >>> # For debugging arg-fill issues:
    >>> configure_logging(level="DEBUG", include_extras=True)
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)

    # Remove existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Create new handler with appropriate formatter
    handler = logging.StreamHandler()
    handler.setLevel(level)

    if structured:
        handler.setFormatter(StructuredFormatter())
    elif include_extras:
        handler.setFormatter(ExtraFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))

    logger.addHandler(handler)

    # Prevent propagation to root logger to avoid duplicate logs
    logger.propagate = False
