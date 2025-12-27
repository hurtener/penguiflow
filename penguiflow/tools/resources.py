"""MCP Resources support for ToolNode.

This module implements MCP resources protocol support including:
- Resource listing and templates
- Resource reading with caching
- Subscription management for resource updates
- Integration with ArtifactStore for binary content

See RFC: docs/RFC_MCP_BINARY_CONTENT_HANDLING.md (Phase 2)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from penguiflow.artifacts import ArtifactRef, ArtifactStore
    from penguiflow.planner.context import ToolContext

__all__ = [
    "ResourceCache",
    "ResourceInfo",
    "ResourceTemplateInfo",
    "ResourceContents",
    "ResourceCacheConfig",
]

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------


class ResourceInfo(BaseModel):
    """Information about an MCP resource."""

    uri: str
    """Unique resource identifier."""

    name: str | None = None
    """Human-readable name."""

    description: str | None = None
    """Description of the resource."""

    mime_type: str | None = None
    """MIME type of the resource content."""

    size_bytes: int | None = None
    """Size hint (if known)."""

    annotations: dict[str, Any] = Field(default_factory=dict)
    """Additional annotations from the server."""


class ResourceTemplateInfo(BaseModel):
    """Information about an MCP resource template."""

    uri_template: str
    """URI template with placeholders."""

    name: str | None = None
    """Human-readable name."""

    description: str | None = None
    """Description of the template."""

    mime_type: str | None = None
    """Expected MIME type of generated resources."""


class ResourceContents(BaseModel):
    """Contents returned from resources/read."""

    uri: str
    """Resource URI."""

    mime_type: str | None = None
    """MIME type of content."""

    text: str | None = None
    """Text content (for text resources)."""

    blob: str | None = None
    """Base64-encoded binary content (for binary resources)."""


class ResourceCacheConfig(BaseModel):
    """Configuration for resource caching."""

    enabled: bool = True
    """Enable caching of resource reads."""

    max_entries: int = 1000
    """Maximum number of cached entries."""

    ttl_seconds: int = 3600
    """Time-to-live for cached entries (1 hour default)."""

    inline_text_if_under_chars: int = 10_000
    """Inline text resources if under this size."""


# -----------------------------------------------------------------------------
# Cache Entry
# -----------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    """Internal cache entry for a resource."""

    uri: str
    artifact_ref: ArtifactRef | None = None
    inline_text: str | None = None
    created_at: float = 0.0
    last_accessed: float = 0.0


# -----------------------------------------------------------------------------
# ResourceCache
# -----------------------------------------------------------------------------


class ResourceCache:
    """Cache for MCP resource reads with ArtifactStore integration.

    Provides caching of resource reads to avoid repeated fetches from
    MCP servers. Binary content is stored in ArtifactStore; small text
    content may be inlined.

    The cache invalidates entries on `resources/updated` notifications
    from the MCP server.
    """

    def __init__(
        self,
        artifact_store: ArtifactStore,
        namespace: str,
        config: ResourceCacheConfig | None = None,
    ) -> None:
        """Initialize the resource cache.

        Args:
            artifact_store: Store for binary/large text content
            namespace: ToolNode namespace for artifact naming
            config: Cache configuration
        """
        self._artifact_store = artifact_store
        self._namespace = namespace
        self._config = config or ResourceCacheConfig()
        self._entries: dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()
        self._time_source = time.monotonic

    async def get_or_fetch(
        self,
        uri: str,
        read_fn: Any,  # Callable that reads the resource
        ctx: ToolContext,
    ) -> dict[str, Any]:
        """Get cached resource or fetch from server.

        Args:
            uri: Resource URI to fetch
            read_fn: Async function to read the resource if not cached
            ctx: Tool context

        Returns:
            Dict with either 'artifact' (ArtifactRef) or 'text' (inline content)
        """
        if not self._config.enabled:
            return await self._fetch_and_store(uri, read_fn, ctx, skip_cache=True)

        async with self._lock:
            entry = self._entries.get(uri)
            if entry is not None:
                # Check if artifact still exists
                if entry.artifact_ref is not None:
                    if await self._artifact_store.exists(entry.artifact_ref.id):
                        entry.last_accessed = self._time_source()
                        return {"artifact": entry.artifact_ref.model_dump()}
                    # Artifact expired/deleted - refetch
                    del self._entries[uri]
                elif entry.inline_text is not None:
                    entry.last_accessed = self._time_source()
                    return {"text": entry.inline_text}

        # Not in cache or expired - fetch
        return await self._fetch_and_store(uri, read_fn, ctx)

    async def _fetch_and_store(
        self,
        uri: str,
        read_fn: Any,
        ctx: ToolContext,
        *,
        skip_cache: bool = False,
    ) -> dict[str, Any]:
        """Fetch resource and store in cache."""
        import base64

        # Call the read function
        contents = await read_fn(uri)

        # Convert to ResourceContents if needed
        if isinstance(contents, dict):
            # Handle raw dict response
            resource_data = contents.get("contents", [contents])
            if isinstance(resource_data, list) and resource_data:
                content_item = resource_data[0]
            else:
                content_item = resource_data
        else:
            # Assume it's already a proper object
            content_item = contents

        # Extract content fields
        text = getattr(content_item, "text", None) or (
            content_item.get("text") if isinstance(content_item, dict) else None
        )
        blob = getattr(content_item, "blob", None) or (
            content_item.get("blob") if isinstance(content_item, dict) else None
        )
        mime_type = getattr(content_item, "mimeType", None) or (
            content_item.get("mimeType") if isinstance(content_item, dict) else None
        )

        now = self._time_source()
        entry = _CacheEntry(uri=uri, created_at=now, last_accessed=now)

        if blob is not None:
            # Binary content - always store as artifact
            try:
                data = base64.b64decode(blob)
                ref = await self._artifact_store.put_bytes(
                    data,
                    mime_type=mime_type or "application/octet-stream",
                    namespace=f"{self._namespace}.resource",
                )
                if not skip_cache:
                    entry.artifact_ref = ref
                    async with self._lock:
                        await self._maybe_evict()
                        self._entries[uri] = entry
                return {"artifact": ref.model_dump()}
            except Exception as e:
                logger.warning(f"Failed to decode/store resource blob: {e}")
                return {"error": str(e)}

        elif text is not None:
            # Text content - inline if small, otherwise store as artifact
            if len(text) <= self._config.inline_text_if_under_chars:
                if not skip_cache:
                    entry.inline_text = text
                    async with self._lock:
                        await self._maybe_evict()
                        self._entries[uri] = entry
                return {"text": text}
            else:
                # Large text - store as artifact
                ref = await self._artifact_store.put_text(
                    text,
                    mime_type=mime_type or "text/plain",
                    namespace=f"{self._namespace}.resource",
                )
                if not skip_cache:
                    entry.artifact_ref = ref
                    async with self._lock:
                        await self._maybe_evict()
                        self._entries[uri] = entry
                return {"artifact": ref.model_dump()}

        return {"error": "Resource has no content"}

    async def _maybe_evict(self) -> None:
        """Evict oldest entries if cache is full."""
        while len(self._entries) >= self._config.max_entries:
            # Find least recently accessed
            oldest_uri = min(
                self._entries.keys(),
                key=lambda u: self._entries[u].last_accessed,
            )
            del self._entries[oldest_uri]
            logger.debug(f"Evicted resource cache entry: {oldest_uri}")

    def invalidate(self, uri: str) -> bool:
        """Invalidate a cache entry.

        Called when receiving `resources/updated` notification.

        Args:
            uri: Resource URI to invalidate

        Returns:
            True if entry was invalidated, False if not found
        """
        if uri in self._entries:
            del self._entries[uri]
            logger.debug(f"Invalidated resource cache entry: {uri}")
            return True
        return False

    def invalidate_all(self) -> int:
        """Invalidate all cache entries.

        Returns:
            Number of entries invalidated
        """
        count = len(self._entries)
        self._entries.clear()
        logger.debug(f"Invalidated all resource cache entries ({count})")
        return count

    @property
    def size(self) -> int:
        """Number of entries in cache."""
        return len(self._entries)


# -----------------------------------------------------------------------------
# Subscription Manager
# -----------------------------------------------------------------------------


@dataclass
class ResourceSubscription:
    """Tracks a resource subscription."""

    uri: str
    callback: Any | None = None
    subscribed_at: float = 0.0


class ResourceSubscriptionManager:
    """Manages resource subscriptions for MCP servers.

    Handles subscribe/unsubscribe requests and routes
    `notifications/resources/updated` to the appropriate handlers.
    """

    def __init__(self, namespace: str) -> None:
        """Initialize subscription manager.

        Args:
            namespace: ToolNode namespace
        """
        self._namespace = namespace
        self._subscriptions: dict[str, ResourceSubscription] = {}
        self._lock = asyncio.Lock()

    async def subscribe(
        self,
        uri: str,
        subscribe_fn: Any,
        callback: Any | None = None,
    ) -> bool:
        """Subscribe to resource updates.

        Args:
            uri: Resource URI to subscribe to
            subscribe_fn: Async function to call MCP subscribe
            callback: Optional callback for updates

        Returns:
            True if subscription successful
        """
        async with self._lock:
            if uri in self._subscriptions:
                return True  # Already subscribed

            try:
                await subscribe_fn(uri)
                self._subscriptions[uri] = ResourceSubscription(
                    uri=uri,
                    callback=callback,
                    subscribed_at=asyncio.get_event_loop().time(),
                )
                logger.debug(f"Subscribed to resource: {uri}")
                return True
            except Exception as e:
                logger.warning(f"Failed to subscribe to resource {uri}: {e}")
                return False

    async def unsubscribe(
        self,
        uri: str,
        unsubscribe_fn: Any,
    ) -> bool:
        """Unsubscribe from resource updates.

        Args:
            uri: Resource URI to unsubscribe from
            unsubscribe_fn: Async function to call MCP unsubscribe

        Returns:
            True if unsubscription successful
        """
        async with self._lock:
            if uri not in self._subscriptions:
                return False

            try:
                await unsubscribe_fn(uri)
                del self._subscriptions[uri]
                logger.debug(f"Unsubscribed from resource: {uri}")
                return True
            except Exception as e:
                logger.warning(f"Failed to unsubscribe from resource {uri}: {e}")
                return False

    async def handle_update(self, uri: str) -> None:
        """Handle a resource update notification.

        Args:
            uri: URI of updated resource
        """
        async with self._lock:
            sub = self._subscriptions.get(uri)
            if sub is not None and sub.callback is not None:
                try:
                    result = sub.callback(uri)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.warning(f"Resource update callback failed for {uri}: {e}")

    @property
    def subscribed_uris(self) -> list[str]:
        """List of currently subscribed URIs."""
        return list(self._subscriptions.keys())

    def is_subscribed(self, uri: str) -> bool:
        """Check if subscribed to a resource."""
        return uri in self._subscriptions
