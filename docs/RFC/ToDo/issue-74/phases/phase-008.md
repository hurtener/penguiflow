# Phase 008: Tests for `ScopedArtifacts` -- Immutability, Upload, Download, and Metadata

## Objective
Add tests for the `ScopedArtifacts` facade covering immutability guarantees, the `upload` method (bytes and str, mime defaults, namespace, meta, scope injection), the `download` method (same/different scope, unscoped artifacts, nonexistent), and the `get_metadata` method. All tests go in `tests/test_artifacts.py`.

## Tasks
1. Add `TestScopedArtifactsImmutability` test class
2. Add `TestScopedArtifactsUpload` test class
3. Add `TestScopedArtifactsDownload` test class
4. Add `TestScopedArtifactsGetMetadata` test class

## Detailed Steps

### Step 1: Add `ScopedArtifacts` import
Ensure `ScopedArtifacts` is imported at the top of `tests/test_artifacts.py`:
```python
from penguiflow.artifacts import (
    ...,
    ScopedArtifacts,
)
```

### Step 2: Add the four test classes
Append after the classes added in Phase 007.

## Required Code

```python
# Target file: tests/test_artifacts.py
# Add ScopedArtifacts to the imports from penguiflow.artifacts.
# Then append these test classes after the Phase 007 classes:


class TestScopedArtifactsImmutability:
    """Tests for ScopedArtifacts immutability."""

    def _make_facade(self, **kwargs) -> ScopedArtifacts:
        defaults = dict(tenant_id=None, user_id=None, session_id=None, trace_id=None)
        defaults.update(kwargs)
        return ScopedArtifacts(InMemoryArtifactStore(), **defaults)

    def test_cannot_reassign_store(self) -> None:
        """Reassigning _store raises AttributeError."""
        facade = self._make_facade()
        with pytest.raises(AttributeError, match="immutable"):
            facade._store = InMemoryArtifactStore()

    def test_cannot_reassign_scope(self) -> None:
        """Reassigning _scope raises AttributeError."""
        facade = self._make_facade()
        with pytest.raises(AttributeError, match="immutable"):
            facade._scope = ArtifactScope()

    def test_cannot_reassign_read_scope(self) -> None:
        """Reassigning _read_scope raises AttributeError."""
        facade = self._make_facade()
        with pytest.raises(AttributeError, match="immutable"):
            facade._read_scope = ArtifactScope()

    def test_cannot_add_new_attribute(self) -> None:
        """Adding a new attribute raises AttributeError."""
        facade = self._make_facade()
        with pytest.raises(AttributeError):
            facade.foo = "bar"

    def test_scope_property_returns_correct_value(self) -> None:
        """scope property returns the ArtifactScope passed at construction."""
        facade = self._make_facade(tenant_id="t1", session_id="s1", trace_id="tr1")
        assert facade.scope.tenant_id == "t1"
        assert facade.scope.session_id == "s1"
        assert facade.scope.trace_id == "tr1"
        assert facade.scope.user_id is None


class TestScopedArtifactsUpload:
    """Tests for ScopedArtifacts.upload()."""

    @pytest.mark.asyncio
    async def test_upload_bytes(self) -> None:
        """upload(bytes) stores via put_bytes and returns ArtifactRef with full scope."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr1"
        )
        ref = await facade.upload(b"pdf data", mime_type="application/pdf", filename="report.pdf")

        assert ref.mime_type == "application/pdf"
        assert ref.filename == "report.pdf"
        assert ref.scope is not None
        assert ref.scope.tenant_id == "t1"
        assert ref.scope.trace_id == "tr1"
        # Verify data was actually stored
        data = await store.get(ref.id)
        assert data == b"pdf data"

    @pytest.mark.asyncio
    async def test_upload_str(self) -> None:
        """upload(str) stores via put_text, defaults mime_type to text/plain."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id="t1", user_id=None, session_id="s1", trace_id=None
        )
        ref = await facade.upload("hello world")

        assert ref.mime_type == "text/plain"
        assert ref.scope is not None
        assert ref.scope.tenant_id == "t1"
        data = await store.get(ref.id)
        assert data == b"hello world"

    @pytest.mark.asyncio
    async def test_upload_str_custom_mime(self) -> None:
        """upload(str, mime_type=...) respects the provided mime type."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id=None, user_id=None, session_id=None, trace_id=None
        )
        ref = await facade.upload("col1,col2\n1,2", mime_type="text/csv")
        assert ref.mime_type == "text/csv"

    @pytest.mark.asyncio
    async def test_upload_passes_namespace(self) -> None:
        """upload(bytes, namespace=...) forwards namespace to the store."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id=None, user_id=None, session_id=None, trace_id=None
        )
        ref = await facade.upload(b"data", namespace="my_tool")
        assert ref.id.startswith("my_tool_")

    @pytest.mark.asyncio
    async def test_upload_passes_meta(self) -> None:
        """upload(bytes, meta=...) forwards meta to the store."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id=None, user_id=None, session_id=None, trace_id=None
        )
        ref = await facade.upload(b"data", meta={"key": "val"})
        assert ref.source.get("key") == "val"

    @pytest.mark.asyncio
    async def test_upload_injects_full_scope(self) -> None:
        """Upload injects full scope including trace_id."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr1"
        )
        ref = await facade.upload(b"data")

        assert ref.scope is not None
        assert ref.scope.tenant_id == "t1"
        assert ref.scope.user_id == "u1"
        assert ref.scope.session_id == "s1"
        assert ref.scope.trace_id == "tr1"


class TestScopedArtifactsDownload:
    """Tests for ScopedArtifacts.download()."""

    @pytest.mark.asyncio
    async def test_download_same_scope(self) -> None:
        """Upload via facade, then download returns the bytes."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr1"
        )
        ref = await facade.upload(b"secret data")
        result = await facade.download(ref.id)
        assert result == b"secret data"

    @pytest.mark.asyncio
    async def test_download_different_trace_same_session(self) -> None:
        """Different trace_id but same tenant/user/session: download succeeds."""
        store = InMemoryArtifactStore()
        facade1 = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr1"
        )
        ref = await facade1.upload(b"data")

        facade2 = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr2"
        )
        result = await facade2.download(ref.id)
        assert result == b"data"

    @pytest.mark.asyncio
    async def test_download_different_tenant_denied(self) -> None:
        """Different tenant_id: download returns None."""
        store = InMemoryArtifactStore()
        facade1 = ScopedArtifacts(
            store, tenant_id="t1", user_id=None, session_id=None, trace_id=None
        )
        ref = await facade1.upload(b"data")

        facade2 = ScopedArtifacts(
            store, tenant_id="t2", user_id=None, session_id=None, trace_id=None
        )
        result = await facade2.download(ref.id)
        assert result is None

    @pytest.mark.asyncio
    async def test_download_different_session_denied(self) -> None:
        """Different session_id: download returns None."""
        store = InMemoryArtifactStore()
        facade1 = ScopedArtifacts(
            store, tenant_id=None, user_id=None, session_id="s1", trace_id=None
        )
        ref = await facade1.upload(b"data")

        facade2 = ScopedArtifacts(
            store, tenant_id=None, user_id=None, session_id="s2", trace_id=None
        )
        result = await facade2.download(ref.id)
        assert result is None

    @pytest.mark.asyncio
    async def test_download_unscoped_artifact_allowed(self) -> None:
        """Artifacts with scope=None are accessible to any facade."""
        store = InMemoryArtifactStore()
        # Store directly with no scope
        ref = await store.put_bytes(b"open data", namespace="ns1")
        assert ref.scope is None

        facade = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr1"
        )
        result = await facade.download(ref.id)
        assert result == b"open data"

    @pytest.mark.asyncio
    async def test_download_nonexistent_returns_none(self) -> None:
        """download() for nonexistent ID returns None."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id=None, user_id=None, session_id=None, trace_id=None
        )
        result = await facade.download("nonexistent_id")
        assert result is None


class TestScopedArtifactsGetMetadata:
    """Tests for ScopedArtifacts.get_metadata()."""

    @pytest.mark.asyncio
    async def test_get_metadata_returns_ref(self) -> None:
        """Upload, then get_metadata returns the ArtifactRef with correct fields."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id="t1", user_id=None, session_id="s1", trace_id="tr1"
        )
        ref = await facade.upload(b"data", mime_type="application/pdf", filename="doc.pdf")

        metadata = await facade.get_metadata(ref.id)
        assert metadata is not None
        assert metadata.id == ref.id
        assert metadata.mime_type == "application/pdf"
        assert metadata.filename == "doc.pdf"
        assert metadata.scope is not None
        assert metadata.scope.tenant_id == "t1"

    @pytest.mark.asyncio
    async def test_get_metadata_different_trace_allowed(self) -> None:
        """Different trace, same tenant/user/session: returns ref."""
        store = InMemoryArtifactStore()
        facade1 = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr1"
        )
        ref = await facade1.upload(b"data")

        facade2 = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr2"
        )
        metadata = await facade2.get_metadata(ref.id)
        assert metadata is not None
        assert metadata.id == ref.id

    @pytest.mark.asyncio
    async def test_get_metadata_different_tenant_denied(self) -> None:
        """Different tenant_id: returns None."""
        store = InMemoryArtifactStore()
        facade1 = ScopedArtifacts(
            store, tenant_id="t1", user_id=None, session_id=None, trace_id=None
        )
        ref = await facade1.upload(b"data")

        facade2 = ScopedArtifacts(
            store, tenant_id="t2", user_id=None, session_id=None, trace_id=None
        )
        metadata = await facade2.get_metadata(ref.id)
        assert metadata is None

    @pytest.mark.asyncio
    async def test_get_metadata_nonexistent_returns_none(self) -> None:
        """get_metadata() for unknown ID returns None."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id=None, user_id=None, session_id=None, trace_id=None
        )
        result = await facade.get_metadata("nonexistent_id")
        assert result is None
```

## Exit Criteria (Success)
- [ ] `TestScopedArtifactsImmutability` class exists with 5 test methods, all passing
- [ ] `TestScopedArtifactsUpload` class exists with 6 test methods, all passing
- [ ] `TestScopedArtifactsDownload` class exists with 6 test methods, all passing
- [ ] `TestScopedArtifactsGetMetadata` class exists with 4 test methods, all passing
- [ ] `uv run ruff check tests/test_artifacts.py` passes
- [ ] `uv run pytest tests/test_artifacts.py -x -q` passes

## Implementation Notes
- All tests use `InMemoryArtifactStore` as the backing store for `ScopedArtifacts`.
- The immutability tests verify that `__setattr__` raises `AttributeError`.
- The download/get_metadata tests verify that `_check_scope` correctly:
  - Allows access when trace differs but tenant/user/session match
  - Denies access when tenant or session differs
  - Allows access to unscoped artifacts (`scope=None`)
- The upload tests verify that scope (including trace_id) is injected into stored artifacts.
- These tests depend on Phase 003 (`ScopedArtifacts` class must exist).

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow
uv run ruff check tests/test_artifacts.py
uv run pytest tests/test_artifacts.py -x -q -v
```
