"""Traceable exception surface for PenguiFlow."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any


class FlowErrorCode(str, Enum):
    """Stable error codes surfaced by the runtime."""

    NODE_TIMEOUT = "NODE_TIMEOUT"
    NODE_EXCEPTION = "NODE_EXCEPTION"
    TRACE_CANCELLED = "TRACE_CANCELLED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    HOP_BUDGET_EXHAUSTED = "HOP_BUDGET_EXHAUSTED"
    TOKEN_BUDGET_EXHAUSTED = "TOKEN_BUDGET_EXHAUSTED"


class FlowError(Exception):
    """Wraps runtime failures with trace metadata for downstream handling."""

    __slots__ = (
        "trace_id",
        "node_name",
        "node_id",
        "code",
        "message",
        "original_exc",
        "metadata",
        "exception_type",
    )

    def __init__(
        self,
        *,
        trace_id: str | None,
        node_name: str | None,
        code: FlowErrorCode | str,
        message: str,
        original_exc: BaseException | None = None,
        node_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.trace_id = trace_id
        self.node_name = node_name
        self.node_id = node_id
        self.code = code.value if isinstance(code, FlowErrorCode) else str(code)
        self.message = message
        self.original_exc = original_exc
        self.metadata = dict(metadata or {})
        self.exception_type = (
            type(original_exc).__name__ if original_exc is not None else None
        )

    def __str__(self) -> str:  # pragma: no cover - debug helper
        trace = f" trace={self.trace_id}" if self.trace_id else ""
        node = f" node={self.node_name}" if self.node_name else ""
        return f"[{self.code}] {self.message}{trace}{node}".strip()

    def unwrap(self) -> BaseException | None:
        """Return the wrapped exception, if any."""

        return self.original_exc

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of the error."""

        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.trace_id is not None:
            payload["trace_id"] = self.trace_id
        if self.node_name is not None:
            payload["node_name"] = self.node_name
        if self.node_id is not None:
            payload["node_id"] = self.node_id
        if self.exception_type is not None:
            payload["exception_type"] = self.exception_type
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    @classmethod
    def from_exception(
        cls,
        *,
        trace_id: str | None,
        node_name: str | None,
        node_id: str | None,
        exc: BaseException,
        code: FlowErrorCode,
        message: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> FlowError:
        """Build a ``FlowError`` from an underlying exception."""

        error_message = message or str(exc) or exc.__class__.__name__
        return cls(
            trace_id=trace_id,
            node_name=node_name,
            node_id=node_id,
            code=code,
            message=error_message,
            original_exc=exc,
            metadata=metadata,
        )


__all__ = ["FlowError", "FlowErrorCode"]
