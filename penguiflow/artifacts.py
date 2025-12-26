"""
Artifact storage for binary and large text content.

This module provides the ArtifactStore protocol and implementations for storing
binary content (PDFs, images, etc.) and large text out-of-band, keeping only
compact ArtifactRef references in LLM context.

See RFC: docs/RFC_MCP_BINARY_CONTENT_HANDLING.md
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass

__all__ = [
    "ArtifactRef",
    "ArtifactScope",
    "ArtifactStore",
    "ArtifactRetentionConfig",
    "NoOpArtifactStore",
    "InMemoryArtifactStore",
    "discover_artifact_store",
]

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------


class ArtifactScope(BaseModel):
    """Scoping information for access control.

    The host (HTTP layer) enforces access control based on these fields.
    ArtifactStore implementations store this metadata but don't enforce access.
    """

    tenant_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    trace_id: str | None = None


class ArtifactRef(BaseModel):
    """Compact reference to a stored artifact.

    This is the only artifact-related data that should appear in LLM context.
    Never include raw bytes or base64 in observations - only ArtifactRef.
    """

    id: str
    """Unique artifact identifier (typically namespace + content hash)."""

    mime_type: str | None = None
    """MIME type of the content (e.g., 'application/pdf', 'image/png')."""

    size_bytes: int | None = None
    """Size of the artifact in bytes."""

    filename: str | None = None
    """Original or suggested filename for downloads."""

    sha256: str | None = None
    """SHA-256 hash of the content for integrity verification."""

    scope: ArtifactScope | None = None
    """Scoping information for access control."""

    source: dict[str, Any] = Field(default_factory=dict)
    """Additional metadata (tool name, warnings, preview, etc.)."""


class ArtifactRetentionConfig(BaseModel):
    """Retention policy for artifacts."""

    # Time-to-live
    ttl_seconds: int = 3600
    """Artifacts expire after this many seconds. Default: 1 hour."""

    # Size limits
    max_artifact_bytes: int = 50 * 1024 * 1024
    """Maximum size per artifact. Default: 50MB."""

    max_session_bytes: int = 500 * 1024 * 1024
    """Maximum total bytes per session. Default: 500MB."""

    max_trace_bytes: int = 100 * 1024 * 1024
    """Maximum total bytes per trace. Default: 100MB."""

    # Count limits
    max_artifacts_per_trace: int = 100
    """Maximum artifacts per trace."""

    max_artifacts_per_session: int = 1000
    """Maximum artifacts per session."""

    # Cleanup behavior
    cleanup_strategy: Literal["lru", "fifo", "none"] = "lru"
    """Strategy for evicting artifacts when limits are reached."""


# -----------------------------------------------------------------------------
# Protocol
# -----------------------------------------------------------------------------


@runtime_checkable
class ArtifactStore(Protocol):
    """Protocol for binary/large-text artifact storage.

    Implementations must be async-safe. The protocol is designed to be
    backend-agnostic - implementations can use memory, disk, S3, etc.
    """

    async def put_bytes(
        self,
        data: bytes,
        *,
        mime_type: str | None = None,
        filename: str | None = None,
        namespace: str | None = None,
        scope: ArtifactScope | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Store binary data and return a compact reference.

        Args:
            data: The binary content to store.
            mime_type: MIME type of the content.
            filename: Suggested filename for downloads.
            namespace: Namespace prefix for the artifact ID (e.g., tool name).
            scope: Scoping information for access control.
            meta: Additional metadata to include in the reference.

        Returns:
            ArtifactRef with the artifact ID and metadata.
        """
        ...

    async def put_text(
        self,
        text: str,
        *,
        mime_type: str = "text/plain",
        filename: str | None = None,
        namespace: str | None = None,
        scope: ArtifactScope | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Store large text and return a compact reference.

        Args:
            text: The text content to store.
            mime_type: MIME type (default: text/plain).
            filename: Suggested filename for downloads.
            namespace: Namespace prefix for the artifact ID.
            scope: Scoping information for access control.
            meta: Additional metadata to include in the reference.

        Returns:
            ArtifactRef with the artifact ID and metadata.
        """
        ...

    async def get(self, artifact_id: str) -> bytes | None:
        """Retrieve artifact bytes by ID.

        Args:
            artifact_id: The artifact ID from an ArtifactRef.

        Returns:
            The binary content, or None if not found or expired.
        """
        ...

    async def get_ref(self, artifact_id: str) -> ArtifactRef | None:
        """Retrieve artifact metadata by ID.

        Args:
            artifact_id: The artifact ID.

        Returns:
            The ArtifactRef with metadata, or None if not found.
        """
        ...

    async def delete(self, artifact_id: str) -> bool:
        """Delete an artifact.

        Args:
            artifact_id: The artifact ID to delete.

        Returns:
            True if deleted, False if not found.
        """
        ...

    async def exists(self, artifact_id: str) -> bool:
        """Check if an artifact exists.

        Args:
            artifact_id: The artifact ID to check.

        Returns:
            True if the artifact exists and hasn't expired.
        """
        ...


# -----------------------------------------------------------------------------
# Discovery
# -----------------------------------------------------------------------------


def discover_artifact_store(state_store: Any) -> ArtifactStore | None:
    """Attempt to discover ArtifactStore from state_store via duck-typing.

    Checks for:
    1. state_store.artifact_store attribute (preferred)
    2. state_store implementing ArtifactStore protocol directly

    Args:
        state_store: A StateStore or similar object that might provide artifacts.

    Returns:
        An ArtifactStore if discovered, None otherwise.
    """
    # Option 1: Explicit attribute
    if hasattr(state_store, "artifact_store"):
        candidate = state_store.artifact_store
        if isinstance(candidate, ArtifactStore):
            return candidate

    # Option 2: State store implements ArtifactStore directly
    if isinstance(state_store, ArtifactStore):
        return state_store

    return None


# -----------------------------------------------------------------------------
# Implementations
# -----------------------------------------------------------------------------


def _generate_artifact_id(
    data: bytes,
    namespace: str | None = None,
) -> str:
    """Generate a content-addressed artifact ID.

    Format: {namespace}_{sha256[:12]} or art_{sha256[:12]}
    """
    content_hash = hashlib.sha256(data).hexdigest()[:12]
    prefix = namespace or "art"
    return f"{prefix}_{content_hash}"


class NoOpArtifactStore:
    """Fallback store that logs warnings but doesn't persist content.

    Used when no real ArtifactStore is configured. Returns truncated
    references with warnings so the system continues to function.
    """

    def __init__(self, max_inline_preview: int = 500) -> None:
        self._max_preview = max_inline_preview
        self._warned = False

    async def put_bytes(
        self,
        data: bytes,
        *,
        mime_type: str | None = None,
        filename: str | None = None,
        namespace: str | None = None,
        scope: ArtifactScope | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Store binary data (no-op: logs warning, returns truncated ref)."""
        if not self._warned:
            logger.warning(
                "No ArtifactStore configured. Binary content will not be stored. "
                "Configure artifact_store= in ReactPlanner for full binary support."
            )
            self._warned = True

        content_hash = hashlib.sha256(data).hexdigest()
        artifact_id = _generate_artifact_id(data, namespace)

        source = dict(meta or {})
        source.update(
            {
                "warning": "Content not stored (no ArtifactStore configured)",
                "truncated": True,
                "original_size": len(data),
            }
        )

        return ArtifactRef(
            id=artifact_id,
            mime_type=mime_type,
            size_bytes=len(data),
            filename=filename,
            sha256=content_hash,
            scope=scope,
            source=source,
        )

    async def put_text(
        self,
        text: str,
        *,
        mime_type: str = "text/plain",
        filename: str | None = None,
        namespace: str | None = None,
        scope: ArtifactScope | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Store large text (no-op: logs warning, returns truncated ref with preview)."""
        if not self._warned:
            logger.warning(
                "No ArtifactStore configured. Large text will be truncated. "
                "Configure artifact_store= in ReactPlanner for full text storage."
            )
            self._warned = True

        data = text.encode("utf-8")
        content_hash = hashlib.sha256(data).hexdigest()
        artifact_id = _generate_artifact_id(data, namespace)

        # Generate preview
        preview = text[: self._max_preview]
        if len(text) > self._max_preview:
            preview += f"\n... [{len(text) - self._max_preview} more chars truncated]"

        source = dict(meta or {})
        source.update(
            {
                "warning": "Content truncated (no ArtifactStore configured)",
                "truncated": True,
                "preview": preview,
                "original_size": len(text),
            }
        )

        return ArtifactRef(
            id=artifact_id,
            mime_type=mime_type,
            size_bytes=len(data),
            filename=filename,
            sha256=content_hash,
            scope=scope,
            source=source,
        )

    async def get(self, artifact_id: str) -> bytes | None:
        """Cannot retrieve from no-op store."""
        return None

    async def get_ref(self, artifact_id: str) -> ArtifactRef | None:
        """Cannot retrieve metadata from no-op store."""
        return None

    async def delete(self, artifact_id: str) -> bool:
        """No-op delete always returns False."""
        return False

    async def exists(self, artifact_id: str) -> bool:
        """No-op store never has artifacts."""
        return False


class _StoredArtifact(BaseModel):
    """Internal representation of a stored artifact."""

    ref: ArtifactRef
    data: bytes
    created_at: float


class InMemoryArtifactStore:
    """In-memory artifact store for development and testing.

    Implements LRU eviction and TTL expiration. Suitable for Playground
    and test environments. Not suitable for production (no persistence).
    """

    def __init__(
        self,
        retention: ArtifactRetentionConfig | None = None,
        scope_filter: ArtifactScope | None = None,
    ) -> None:
        """Initialize the in-memory store.

        Args:
            retention: Retention policy configuration.
            scope_filter: If provided, all artifacts are scoped to this.
        """
        self._retention = retention or ArtifactRetentionConfig()
        self._scope_filter = scope_filter
        self._artifacts: OrderedDict[str, _StoredArtifact] = OrderedDict()
        self._total_bytes = 0

    async def put_bytes(
        self,
        data: bytes,
        *,
        mime_type: str | None = None,
        filename: str | None = None,
        namespace: str | None = None,
        scope: ArtifactScope | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Store binary data in memory."""
        # Check size limit
        if len(data) > self._retention.max_artifact_bytes:
            raise ValueError(
                f"Artifact size ({len(data)} bytes) exceeds limit "
                f"({self._retention.max_artifact_bytes} bytes)"
            )

        # Generate ID and hash
        content_hash = hashlib.sha256(data).hexdigest()
        artifact_id = _generate_artifact_id(data, namespace)

        # Check for deduplication
        if artifact_id in self._artifacts:
            # Update access time (LRU)
            self._artifacts.move_to_end(artifact_id)
            return self._artifacts[artifact_id].ref

        # Merge scope
        effective_scope = scope or self._scope_filter

        # Create reference
        ref = ArtifactRef(
            id=artifact_id,
            mime_type=mime_type,
            size_bytes=len(data),
            filename=filename,
            sha256=content_hash,
            scope=effective_scope,
            source=dict(meta or {}),
        )

        # Evict if necessary
        await self._evict_if_needed(len(data))

        # Store
        self._artifacts[artifact_id] = _StoredArtifact(
            ref=ref,
            data=data,
            created_at=time.time(),
        )
        self._total_bytes += len(data)

        return ref

    async def put_text(
        self,
        text: str,
        *,
        mime_type: str = "text/plain",
        filename: str | None = None,
        namespace: str | None = None,
        scope: ArtifactScope | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Store text as UTF-8 bytes."""
        data = text.encode("utf-8")
        return await self.put_bytes(
            data,
            mime_type=mime_type,
            filename=filename,
            namespace=namespace,
            scope=scope,
            meta=meta,
        )

    async def get(self, artifact_id: str) -> bytes | None:
        """Retrieve artifact bytes by ID."""
        await self._expire_old_artifacts()

        stored = self._artifacts.get(artifact_id)
        if stored is None:
            return None

        # Update access time (LRU)
        self._artifacts.move_to_end(artifact_id)
        return stored.data

    async def get_ref(self, artifact_id: str) -> ArtifactRef | None:
        """Retrieve artifact metadata by ID."""
        await self._expire_old_artifacts()

        stored = self._artifacts.get(artifact_id)
        if stored is None:
            return None

        return stored.ref

    async def delete(self, artifact_id: str) -> bool:
        """Delete an artifact."""
        stored = self._artifacts.pop(artifact_id, None)
        if stored is None:
            return False

        self._total_bytes -= len(stored.data)
        return True

    async def exists(self, artifact_id: str) -> bool:
        """Check if an artifact exists."""
        await self._expire_old_artifacts()
        return artifact_id in self._artifacts

    async def _expire_old_artifacts(self) -> None:
        """Remove expired artifacts based on TTL."""
        if self._retention.ttl_seconds <= 0:
            return

        now = time.time()
        expired = []

        for artifact_id, stored in self._artifacts.items():
            if now - stored.created_at > self._retention.ttl_seconds:
                expired.append(artifact_id)

        for artifact_id in expired:
            await self.delete(artifact_id)

    async def _evict_if_needed(self, incoming_bytes: int) -> None:
        """Evict artifacts if limits would be exceeded."""
        if self._retention.cleanup_strategy == "none":
            return

        # Check count limit
        while len(self._artifacts) >= self._retention.max_artifacts_per_session:
            if self._retention.cleanup_strategy == "lru":
                # Remove least recently used (first item in OrderedDict)
                oldest_id = next(iter(self._artifacts))
                await self.delete(oldest_id)
            elif self._retention.cleanup_strategy == "fifo":
                oldest_id = next(iter(self._artifacts))
                await self.delete(oldest_id)

        # Check size limit
        target_bytes = self._retention.max_session_bytes - incoming_bytes
        while self._total_bytes > target_bytes and self._artifacts:
            oldest_id = next(iter(self._artifacts))
            await self.delete(oldest_id)

    def clear(self) -> None:
        """Clear all artifacts (for testing/cleanup)."""
        self._artifacts.clear()
        self._total_bytes = 0

    @property
    def total_bytes(self) -> int:
        """Total bytes currently stored."""
        return self._total_bytes

    @property
    def count(self) -> int:
        """Number of artifacts currently stored."""
        return len(self._artifacts)
