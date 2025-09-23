"""Typed message models for PenguiFlow."""

from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import BaseModel, Field


class Headers(BaseModel):
    tenant: str
    topic: str | None = None
    priority: int = 0


class Message(BaseModel):
    payload: Any
    headers: Headers
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = Field(default_factory=time.time)
    deadline_s: float | None = None


__all__ = ["Headers", "Message"]
