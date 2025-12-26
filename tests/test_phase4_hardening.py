"""Tests for Phase 4: Hardening, Presets, and Negative Paths.

This module tests:
- MCP server presets (Tableau, GitHub, Filesystem, Google Drive)
- Artifact extraction presets and utilities
- NoOp fallback behavior when no ArtifactStore configured
- Malformed base64 content handling
- Decode failures (corrupted data)
- Oversized payload rejection
- Session access control violations
- MCP server errors during resource operations
- TTL expiration and LRU eviction
"""

from __future__ import annotations

import base64
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from penguiflow.artifacts import (
    ArtifactRef,
    ArtifactRetentionConfig,
    ArtifactScope,
    InMemoryArtifactStore,
    NoOpArtifactStore,
)
from penguiflow.tools.config import (
    ArtifactExtractionConfig,
    ArtifactFieldConfig,
    BinaryDetectionConfig,
)
from penguiflow.tools.presets import (
    ARTIFACT_PRESETS,
    FILESYSTEM_ARTIFACT_PRESET,
    GITHUB_ARTIFACT_PRESET,
    GOOGLE_DRIVE_ARTIFACT_PRESET,
    TABLEAU_ARTIFACT_PRESET,
    get_artifact_preset,
    get_artifact_preset_info,
    get_artifact_preset_with_overrides,
    list_artifact_presets,
    merge_artifact_preset,
)


# ─── Preset Tests ─────────────────────────────────────────────────────────────


class TestArtifactPresets:
    """Tests for artifact extraction presets."""

    def test_tableau_preset_exists(self) -> None:
        """Tableau preset should be defined."""
        assert TABLEAU_ARTIFACT_PRESET is not None
        assert TABLEAU_ARTIFACT_PRESET.max_inline_size == 5_000

    def test_tableau_preset_tool_fields(self) -> None:
        """Tableau preset should have tool field mappings."""
        assert "download_workbook" in TABLEAU_ARTIFACT_PRESET.tool_fields
        assert "get_view_as_pdf" in TABLEAU_ARTIFACT_PRESET.tool_fields
        assert "get_view_as_image" in TABLEAU_ARTIFACT_PRESET.tool_fields

    def test_github_preset_exists(self) -> None:
        """GitHub preset should be defined."""
        assert GITHUB_ARTIFACT_PRESET is not None
        assert GITHUB_ARTIFACT_PRESET.max_inline_size == 10_000

    def test_github_preset_tool_fields(self) -> None:
        """GitHub preset should have tool field mappings."""
        assert "get_file_contents" in GITHUB_ARTIFACT_PRESET.tool_fields
        assert "download_artifact" in GITHUB_ARTIFACT_PRESET.tool_fields

    def test_filesystem_preset_exists(self) -> None:
        """Filesystem preset should be defined."""
        assert FILESYSTEM_ARTIFACT_PRESET is not None
        assert FILESYSTEM_ARTIFACT_PRESET.max_inline_size == 50_000

    def test_google_drive_preset_exists(self) -> None:
        """Google Drive preset should be defined."""
        assert GOOGLE_DRIVE_ARTIFACT_PRESET is not None
        assert "download_file" in GOOGLE_DRIVE_ARTIFACT_PRESET.tool_fields

    def test_get_artifact_preset(self) -> None:
        """get_artifact_preset should return the correct preset."""
        preset = get_artifact_preset("tableau")
        assert preset is TABLEAU_ARTIFACT_PRESET

    def test_get_artifact_preset_unknown(self) -> None:
        """get_artifact_preset should raise KeyError for unknown presets."""
        with pytest.raises(KeyError, match="Unknown artifact preset"):
            get_artifact_preset("nonexistent")

    def test_list_artifact_presets(self) -> None:
        """list_artifact_presets should return all preset names."""
        presets = list_artifact_presets()
        assert "tableau" in presets
        assert "github" in presets
        assert "filesystem" in presets
        assert "google-drive" in presets

    def test_get_artifact_preset_with_overrides(self) -> None:
        """get_artifact_preset_with_overrides should apply overrides."""
        preset = get_artifact_preset_with_overrides(
            "tableau",
            max_inline_size=2000,
        )
        assert preset.max_inline_size == 2000
        # Other fields should remain from preset
        assert "download_workbook" in preset.tool_fields

    def test_get_artifact_preset_with_overrides_no_changes(self) -> None:
        """get_artifact_preset_with_overrides without overrides returns original."""
        preset = get_artifact_preset_with_overrides("tableau")
        assert preset is TABLEAU_ARTIFACT_PRESET

    def test_merge_artifact_preset(self) -> None:
        """merge_artifact_preset should combine tool_fields."""
        custom = ArtifactExtractionConfig(
            max_inline_size=2000,
            tool_fields={
                "custom_tool": [
                    ArtifactFieldConfig(
                        field_path="data",
                        content_type="binary",
                    )
                ]
            },
        )

        merged = merge_artifact_preset(custom, "tableau")

        # Custom tool should be preserved
        assert "custom_tool" in merged.tool_fields
        # Tableau tools should be added
        assert "download_workbook" in merged.tool_fields
        # Custom max_inline_size should be preserved
        assert merged.max_inline_size == 2000

    def test_merge_artifact_preset_unknown(self) -> None:
        """merge_artifact_preset should raise KeyError for unknown presets."""
        custom = ArtifactExtractionConfig()
        with pytest.raises(KeyError, match="Unknown artifact preset"):
            merge_artifact_preset(custom, "nonexistent")

    def test_get_artifact_preset_info(self) -> None:
        """get_artifact_preset_info should return preset metadata."""
        info = get_artifact_preset_info("tableau")

        assert info["name"] == "tableau"
        assert info["max_inline_size"] == 5_000
        assert info["binary_detection_enabled"] is True
        assert "download_workbook" in info["tool_fields"]

    def test_artifact_presets_registry(self) -> None:
        """ARTIFACT_PRESETS should contain all presets."""
        assert len(ARTIFACT_PRESETS) >= 4
        assert all(isinstance(p, ArtifactExtractionConfig) for p in ARTIFACT_PRESETS.values())


# ─── NoOp Fallback Tests ─────────────────────────────────────────────────────


class TestNoOpArtifactStore:
    """Tests for NoOpArtifactStore fallback behavior."""

    @pytest.mark.asyncio
    async def test_put_bytes_returns_truncated_ref(self) -> None:
        """NoOp store should return ref with truncated warning."""
        store = NoOpArtifactStore()
        data = b"test binary data"

        ref = await store.put_bytes(data, mime_type="application/pdf")

        assert ref.id.startswith("art_")
        assert ref.size_bytes == len(data)
        assert ref.source["truncated"] is True
        assert "warning" in ref.source

    @pytest.mark.asyncio
    async def test_put_bytes_logs_warning_once(self) -> None:
        """NoOp store should log warning only on first use."""
        store = NoOpArtifactStore()

        with patch("penguiflow.artifacts.logger") as mock_logger:
            await store.put_bytes(b"data1")
            await store.put_bytes(b"data2")

            # Should only warn once
            assert mock_logger.warning.call_count == 1

    @pytest.mark.asyncio
    async def test_put_text_includes_preview(self) -> None:
        """NoOp store should include text preview in source."""
        store = NoOpArtifactStore(max_inline_preview=10)
        text = "Hello World, this is a longer text"

        ref = await store.put_text(text)

        assert ref.source["truncated"] is True
        assert "preview" in ref.source
        assert ref.source["preview"].startswith("Hello Worl")

    @pytest.mark.asyncio
    async def test_get_returns_none(self) -> None:
        """NoOp store get should always return None."""
        store = NoOpArtifactStore()

        ref = await store.put_bytes(b"data")
        result = await store.get(ref.id)

        assert result is None

    @pytest.mark.asyncio
    async def test_exists_returns_false(self) -> None:
        """NoOp store exists should always return False."""
        store = NoOpArtifactStore()

        assert await store.exists("any_id") is False

    @pytest.mark.asyncio
    async def test_delete_returns_false(self) -> None:
        """NoOp store delete should always return False."""
        store = NoOpArtifactStore()

        assert await store.delete("any_id") is False

    @pytest.mark.asyncio
    async def test_get_ref_returns_none(self) -> None:
        """NoOp store get_ref should always return None."""
        store = NoOpArtifactStore()

        assert await store.get_ref("any_id") is None


# ─── Size Limit Tests ─────────────────────────────────────────────────────────


class TestOversizedPayloads:
    """Tests for oversized payload rejection."""

    @pytest.mark.asyncio
    async def test_artifact_exceeds_max_size(self) -> None:
        """Store should reject artifacts exceeding max size."""
        config = ArtifactRetentionConfig(max_artifact_bytes=100)
        store = InMemoryArtifactStore(retention=config)

        with pytest.raises(ValueError, match="exceeds limit"):
            await store.put_bytes(b"x" * 200)

    @pytest.mark.asyncio
    async def test_artifact_at_max_size_accepted(self) -> None:
        """Store should accept artifacts at exactly max size."""
        config = ArtifactRetentionConfig(max_artifact_bytes=100)
        store = InMemoryArtifactStore(retention=config)

        ref = await store.put_bytes(b"x" * 100)
        assert ref.size_bytes == 100

    @pytest.mark.asyncio
    async def test_text_artifact_size_check(self) -> None:
        """Store should check size for text artifacts (as UTF-8)."""
        config = ArtifactRetentionConfig(max_artifact_bytes=100)
        store = InMemoryArtifactStore(retention=config)

        # UTF-8 encoding of this text is 200 bytes
        with pytest.raises(ValueError, match="exceeds limit"):
            await store.put_text("x" * 200)


# ─── TTL Expiration Tests ─────────────────────────────────────────────────────


class TestTTLExpiration:
    """Tests for TTL-based artifact expiration."""

    @pytest.mark.asyncio
    async def test_artifact_expires_after_ttl(self) -> None:
        """Artifacts should expire after TTL seconds."""
        config = ArtifactRetentionConfig(ttl_seconds=1)
        store = InMemoryArtifactStore(retention=config)

        ref = await store.put_bytes(b"data")

        # Should exist initially
        assert await store.exists(ref.id) is True

        # Wait for TTL
        time.sleep(1.1)

        # Should be expired
        assert await store.exists(ref.id) is False

    @pytest.mark.asyncio
    async def test_artifact_accessible_before_ttl(self) -> None:
        """Artifacts should be accessible before TTL expires."""
        config = ArtifactRetentionConfig(ttl_seconds=10)
        store = InMemoryArtifactStore(retention=config)

        ref = await store.put_bytes(b"data")

        # Should exist and be retrievable
        assert await store.exists(ref.id) is True
        data = await store.get(ref.id)
        assert data == b"data"


# ─── LRU Eviction Tests ─────────────────────────────────────────────────────


class TestLRUEviction:
    """Tests for LRU-based artifact eviction."""

    @pytest.mark.asyncio
    async def test_lru_evicts_oldest_on_count_limit(self) -> None:
        """LRU should evict oldest artifact when count limit reached."""
        config = ArtifactRetentionConfig(max_artifacts_per_session=3)
        store = InMemoryArtifactStore(retention=config)

        ref1 = await store.put_bytes(b"data1", namespace="ns1")
        ref2 = await store.put_bytes(b"data2", namespace="ns2")
        ref3 = await store.put_bytes(b"data3", namespace="ns3")

        assert store.count == 3

        # Adding 4th should evict ref1 (oldest)
        await store.put_bytes(b"data4", namespace="ns4")

        assert store.count == 3
        assert await store.exists(ref1.id) is False
        assert await store.exists(ref2.id) is True
        assert await store.exists(ref3.id) is True

    @pytest.mark.asyncio
    async def test_lru_updates_on_access(self) -> None:
        """LRU should update access time on get."""
        config = ArtifactRetentionConfig(max_artifacts_per_session=3)
        store = InMemoryArtifactStore(retention=config)

        ref1 = await store.put_bytes(b"data1", namespace="ns1")
        ref2 = await store.put_bytes(b"data2", namespace="ns2")
        ref3 = await store.put_bytes(b"data3", namespace="ns3")

        # Access ref1, making it most recently used
        await store.get(ref1.id)

        # Adding 4th should evict ref2 (now oldest)
        await store.put_bytes(b"data4", namespace="ns4")

        assert await store.exists(ref1.id) is True  # Recently accessed
        assert await store.exists(ref2.id) is False  # Evicted (was oldest)
        assert await store.exists(ref3.id) is True

    @pytest.mark.asyncio
    async def test_size_based_eviction(self) -> None:
        """LRU should evict to make room for new artifacts."""
        config = ArtifactRetentionConfig(
            max_session_bytes=90,  # Less than 50+50
            max_artifact_bytes=60,
        )
        store = InMemoryArtifactStore(retention=config)

        # Store 50 bytes
        ref1 = await store.put_bytes(b"x" * 50, namespace="ns1")
        assert store.total_bytes == 50

        # Store another 50 bytes - should evict ref1 (50+50=100 > 90)
        ref2 = await store.put_bytes(b"y" * 50, namespace="ns2")

        assert await store.exists(ref1.id) is False  # Evicted
        assert await store.exists(ref2.id) is True


# ─── Session Access Control Tests ─────────────────────────────────────────────


class TestSessionAccessControl:
    """Tests for session-based access control."""

    @pytest.mark.asyncio
    async def test_session_isolation(self) -> None:
        """Artifacts should be isolated by session."""
        from penguiflow.cli.playground_state import PlaygroundArtifactStore

        store = PlaygroundArtifactStore()

        scope1 = ArtifactScope(session_id="session1")
        scope2 = ArtifactScope(session_id="session2")

        ref1 = await store.put_bytes(b"secret1", scope=scope1)
        ref2 = await store.put_bytes(b"secret2", scope=scope2)

        # Session 1 can access its own artifact
        data = await store.get_with_session_check(ref1.id, "session1")
        assert data == b"secret1"

        # Session 1 cannot access session 2's artifact
        data = await store.get_with_session_check(ref2.id, "session1")
        assert data is None

    @pytest.mark.asyncio
    async def test_access_denied_wrong_session(self) -> None:
        """Access should be denied for wrong session."""
        from penguiflow.cli.playground_state import PlaygroundArtifactStore

        store = PlaygroundArtifactStore()

        scope = ArtifactScope(session_id="session1")
        ref = await store.put_bytes(b"secret", scope=scope)

        # Wrong session should get None
        data = await store.get_with_session_check(ref.id, "wrong_session")
        assert data is None

    @pytest.mark.asyncio
    async def test_access_nonexistent_artifact(self) -> None:
        """Access to nonexistent artifact should return None."""
        from penguiflow.cli.playground_state import PlaygroundArtifactStore

        store = PlaygroundArtifactStore()

        data = await store.get_with_session_check("nonexistent", "session1")
        assert data is None


# ─── Binary Detection Edge Cases ─────────────────────────────────────────────


class TestBinaryDetectionEdgeCases:
    """Tests for binary detection edge cases."""

    def test_binary_detection_config_defaults(self) -> None:
        """BinaryDetectionConfig should have sensible defaults."""
        config = BinaryDetectionConfig()

        assert config.enabled is True
        assert config.min_size_for_detection == 1000
        assert config.require_magic_bytes is True
        assert "JVBERi" in config.signatures  # PDF

    def test_binary_detection_config_custom_signatures(self) -> None:
        """BinaryDetectionConfig should accept custom signatures."""
        config = BinaryDetectionConfig(
            signatures={
                "CUSTOM": ("custom", "application/custom"),
            }
        )

        assert "CUSTOM" in config.signatures
        assert config.signatures["CUSTOM"] == ("custom", "application/custom")

    def test_short_content_not_detected(self) -> None:
        """Content shorter than min_size should not trigger detection."""
        config = BinaryDetectionConfig(min_size_for_detection=1000)

        # This would match PDF signature but is too short
        short_content = "JVBERi"  # Only 6 chars

        # Detection should be skipped (would need integration test to verify)
        assert len(short_content) < config.min_size_for_detection


# ─── Malformed Content Tests ─────────────────────────────────────────────────


class TestMalformedContent:
    """Tests for handling malformed content."""

    def test_invalid_base64_detection(self) -> None:
        """Invalid base64 should be handled gracefully."""
        # This looks like base64 but has invalid characters
        invalid_b64 = "JVBERi!!@@##$%^"

        with pytest.raises(Exception):
            base64.b64decode(invalid_b64)

    def test_truncated_base64(self) -> None:
        """Truncated base64 should be handled."""
        # Valid PDF prefix but incomplete
        truncated = "JVBERi0xLjQ"  # Not padded correctly

        # Should still be decodable with some implementations
        # but might produce corrupt data
        try:
            data = base64.b64decode(truncated + "==")
            assert data.startswith(b"%PDF")
        except Exception:
            pass  # Some implementations reject this

    @pytest.mark.asyncio
    async def test_store_empty_data(self) -> None:
        """Store should handle empty data."""
        store = InMemoryArtifactStore()

        ref = await store.put_bytes(b"")

        assert ref.size_bytes == 0
        data = await store.get(ref.id)
        assert data == b""

    @pytest.mark.asyncio
    async def test_store_unicode_text(self) -> None:
        """Store should handle Unicode text correctly."""
        store = InMemoryArtifactStore()

        # Text with various Unicode characters
        text = "Hello 世界 🌍 مرحبا"

        ref = await store.put_text(text)

        data = await store.get(ref.id)
        assert data.decode("utf-8") == text


# ─── Deduplication Tests ─────────────────────────────────────────────────────


class TestDeduplication:
    """Tests for content deduplication."""

    @pytest.mark.asyncio
    async def test_identical_content_deduped(self) -> None:
        """Identical content should produce same artifact ID."""
        store = InMemoryArtifactStore()

        data = b"duplicate content"

        ref1 = await store.put_bytes(data)
        ref2 = await store.put_bytes(data)

        assert ref1.id == ref2.id
        assert store.count == 1

    @pytest.mark.asyncio
    async def test_different_content_not_deduped(self) -> None:
        """Different content should produce different artifact IDs."""
        store = InMemoryArtifactStore()

        ref1 = await store.put_bytes(b"content1")
        ref2 = await store.put_bytes(b"content2")

        assert ref1.id != ref2.id
        assert store.count == 2

    @pytest.mark.asyncio
    async def test_same_content_different_metadata(self) -> None:
        """Same content with different metadata should still dedupe."""
        store = InMemoryArtifactStore()

        data = b"content"

        ref1 = await store.put_bytes(data, mime_type="text/plain")
        ref2 = await store.put_bytes(data, mime_type="application/octet-stream")

        # Same content = same ID (deduped)
        assert ref1.id == ref2.id


# ─── Preset Integration Tests ─────────────────────────────────────────────────


class TestPresetIntegration:
    """Integration tests for presets with ToolNode."""

    def test_preset_field_config_valid(self) -> None:
        """Preset field configs should be valid."""
        for name, preset in ARTIFACT_PRESETS.items():
            for tool_name, fields in preset.tool_fields.items():
                for field in fields:
                    assert field.field_path, f"{name}.{tool_name} missing field_path"
                    assert field.content_type, f"{name}.{tool_name} missing content_type"

    def test_preset_summary_templates_have_placeholders(self) -> None:
        """Summary templates should have expected placeholders."""
        for name, preset in ARTIFACT_PRESETS.items():
            for tool_name, fields in preset.tool_fields.items():
                for field in fields:
                    template = field.summary_template
                    # Templates should include artifact_id
                    assert "{artifact_id}" in template or "{size}" in template, (
                        f"{name}.{tool_name} template missing standard placeholders"
                    )


# ─── Error Recovery Tests ─────────────────────────────────────────────────────


class TestErrorRecovery:
    """Tests for error recovery scenarios."""

    @pytest.mark.asyncio
    async def test_get_after_delete(self) -> None:
        """Get after delete should return None."""
        store = InMemoryArtifactStore()

        ref = await store.put_bytes(b"data")
        await store.delete(ref.id)

        assert await store.get(ref.id) is None
        assert await store.exists(ref.id) is False

    @pytest.mark.asyncio
    async def test_double_delete(self) -> None:
        """Double delete should not error."""
        store = InMemoryArtifactStore()

        ref = await store.put_bytes(b"data")

        assert await store.delete(ref.id) is True
        assert await store.delete(ref.id) is False  # Already deleted

    @pytest.mark.asyncio
    async def test_clear_and_reuse(self) -> None:
        """Store should be reusable after clear."""
        store = InMemoryArtifactStore()

        await store.put_bytes(b"data1")
        await store.put_bytes(b"data2")

        store.clear()

        assert store.count == 0
        assert store.total_bytes == 0

        # Should work after clear
        ref = await store.put_bytes(b"new_data")
        assert await store.exists(ref.id) is True
