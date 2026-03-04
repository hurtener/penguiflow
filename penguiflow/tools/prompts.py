"""MCP Prompts support for ToolNode.

This module implements MCP prompts protocol support including:
- Prompt listing and argument discovery
- Prompt execution with argument serialization
- Content serialization for prompt messages (text, image, audio, embedded resource)

See plan: MCP Prompts + Apps (Phase 3)
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "PromptArgumentInfo",
    "PromptInfo",
    "serialize_prompt_messages",
]

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------


class PromptArgumentInfo(BaseModel):
    """Information about a prompt argument."""

    name: str
    """Argument name."""

    description: str | None = None
    """Human-readable description."""

    required: bool = False
    """Whether this argument must be provided."""


class PromptInfo(BaseModel):
    """Information about an MCP prompt."""

    name: str
    """Prompt name (unique within server)."""

    description: str | None = None
    """Human-readable description of what this prompt provides."""

    arguments: list[PromptArgumentInfo] = Field(default_factory=list)
    """Arguments accepted by this prompt."""


# -----------------------------------------------------------------------------
# Content serialization helpers
# -----------------------------------------------------------------------------


def serialize_prompt_messages(messages: Any) -> list[dict[str, Any]]:
    """Serialize MCP PromptMessage list into plain dicts.

    Handles all MCP Content types:
    - TextContent -> {"type": "text", "text": ...}
    - ImageContent -> {"type": "image", "data": ..., "mimeType": ...}
    - AudioContent -> {"type": "audio", "data": ..., "mimeType": ...}
    - EmbeddedResource -> {"type": "resource", "resource": ...}

    Args:
        messages: List of mcp.types.PromptMessage objects.

    Returns:
        List of serialized message dicts with role and content.
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        role = getattr(msg, "role", "user")
        content = getattr(msg, "content", None)
        if content is None:
            continue

        serialized = _serialize_content(content)
        result.append({"role": str(role), **serialized})

    return result


def _serialize_content(content: Any) -> dict[str, Any]:
    """Serialize a single MCP Content item."""
    content_type = getattr(content, "type", None)

    if content_type == "text":
        return {"type": "text", "text": getattr(content, "text", "")}

    if content_type == "image":
        return {
            "type": "image",
            "data": getattr(content, "data", ""),
            "mimeType": getattr(content, "mimeType", "image/png"),
        }

    if content_type == "audio":
        return {
            "type": "audio",
            "data": getattr(content, "data", ""),
            "mimeType": getattr(content, "mimeType", "audio/wav"),
        }

    if content_type == "resource":
        resource = getattr(content, "resource", None)
        if resource is not None:
            return {
                "type": "resource",
                "resource": {
                    "uri": str(getattr(resource, "uri", "")),
                    "mimeType": getattr(resource, "mimeType", None),
                    "text": getattr(resource, "text", None),
                    "blob": getattr(resource, "blob", None),
                },
            }

    # Fallback: try to dump as dict
    if hasattr(content, "model_dump"):
        return content.model_dump()
    if isinstance(content, dict):
        return content

    return {"type": "unknown", "value": str(content)}
