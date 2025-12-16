"""Tests for penguiflow/tools/auth.py OAuth manager."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from penguiflow.tools.auth import (
    InMemoryTokenStore,
    OAuthManager,
    OAuthProviderConfig,
)

# ─── InMemoryTokenStore tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_token_store_store_and_get():
    """Token store should store and retrieve tokens."""
    store = InMemoryTokenStore()
    await store.store("user1", "github", "token123", None)
    token = await store.get("user1", "github")
    assert token == "token123"


@pytest.mark.asyncio
async def test_token_store_get_nonexistent():
    """Token store should return None for nonexistent tokens."""
    store = InMemoryTokenStore()
    token = await store.get("user1", "github")
    assert token is None


@pytest.mark.asyncio
async def test_token_store_delete():
    """Token store should delete tokens."""
    store = InMemoryTokenStore()
    await store.store("user1", "github", "token123", None)
    await store.delete("user1", "github")
    token = await store.get("user1", "github")
    assert token is None


@pytest.mark.asyncio
async def test_token_store_delete_nonexistent():
    """Token store should handle delete of nonexistent token."""
    store = InMemoryTokenStore()
    await store.delete("user1", "github")  # Should not raise


@pytest.mark.asyncio
async def test_token_store_expired_token():
    """Token store should return None for expired tokens."""
    store = InMemoryTokenStore()
    # Store with expired timestamp
    await store.store("user1", "github", "token123", time.time() - 100)
    token = await store.get("user1", "github")
    assert token is None
    # Token should be deleted after expiration check
    assert ("user1", "github") not in store._tokens


@pytest.mark.asyncio
async def test_token_store_not_expired_token():
    """Token store should return token if not expired."""
    store = InMemoryTokenStore()
    # Store with future expiration
    await store.store("user1", "github", "token123", time.time() + 3600)
    token = await store.get("user1", "github")
    assert token == "token123"


# ─── OAuthProviderConfig tests ───────────────────────────────────────────────


def test_oauth_provider_config_defaults():
    """OAuthProviderConfig should have correct defaults."""
    config = OAuthProviderConfig(
        name="github",
        display_name="GitHub",
        auth_url="https://auth",
        token_url="https://token",
        client_id="id",
        client_secret="secret",
        redirect_uri="https://redirect",
    )
    assert config.scopes == []


def test_oauth_provider_config_with_scopes():
    """OAuthProviderConfig should accept scopes."""
    config = OAuthProviderConfig(
        name="github",
        display_name="GitHub",
        auth_url="https://auth",
        token_url="https://token",
        client_id="id",
        client_secret="secret",
        redirect_uri="https://redirect",
        scopes=["repo", "user"],
    )
    assert config.scopes == ["repo", "user"]


# ─── OAuthManager tests ──────────────────────────────────────────────────────


def build_provider():
    return OAuthProviderConfig(
        name="github",
        display_name="GitHub",
        auth_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        client_id="test_client_id",
        client_secret="test_secret",
        redirect_uri="https://example.com/callback",
        scopes=["repo", "user"],
    )


@pytest.mark.asyncio
async def test_oauth_manager_get_token():
    """OAuthManager should delegate to token store."""
    store = InMemoryTokenStore()
    await store.store("user1", "github", "stored_token", None)
    manager = OAuthManager(providers={"github": build_provider()}, token_store=store)
    token = await manager.get_token("user1", "github")
    assert token == "stored_token"


def test_oauth_manager_get_auth_request():
    """OAuthManager should generate auth request with state."""
    manager = OAuthManager(providers={"github": build_provider()})
    request = manager.get_auth_request("github", "user1", "trace1")

    assert request["display_name"] == "GitHub"
    assert "auth_url" in request
    assert "client_id=test_client_id" in request["auth_url"]
    assert "scope=repo" in request["auth_url"]  # Scopes joined with space
    assert "state" in request
    assert request["scopes"] == ["repo", "user"]
    # State should be stored in pending
    assert request["state"] in manager._pending


def test_oauth_manager_get_auth_request_unknown_provider():
    """OAuthManager should raise for unknown provider."""
    manager = OAuthManager(providers={"github": build_provider()})
    with pytest.raises(ValueError, match="Unknown OAuth provider"):
        manager.get_auth_request("slack", "user1", "trace1")


def test_oauth_manager_cleanup_pending():
    """OAuthManager should cleanup expired pending states."""
    manager = OAuthManager(providers={"github": build_provider()})

    # Add expired state
    old_state = "expired_state"
    manager._pending[old_state] = {
        "user_id": "user1",
        "trace_id": "trace1",
        "provider": "github",
        "created_at": time.time() - 700,  # > 600s TTL
    }

    # Add valid state
    valid_state = "valid_state"
    manager._pending[valid_state] = {
        "user_id": "user2",
        "trace_id": "trace2",
        "provider": "github",
        "created_at": time.time(),
    }

    # New auth request triggers cleanup
    manager.get_auth_request("github", "user3", "trace3")

    assert old_state not in manager._pending
    assert valid_state in manager._pending


def test_oauth_manager_cleanup_empty_pending():
    """OAuthManager cleanup should handle empty pending dict."""
    manager = OAuthManager(providers={"github": build_provider()})
    manager._cleanup_pending()  # Should not raise


@pytest.mark.asyncio
async def test_oauth_manager_handle_callback_invalid_state():
    """OAuthManager should reject invalid state."""
    manager = OAuthManager(providers={"github": build_provider()})
    with pytest.raises(ValueError, match="Invalid or expired OAuth state"):
        await manager.handle_callback("code123", "invalid_state")


@pytest.mark.asyncio
async def test_oauth_manager_handle_callback_expired_state():
    """OAuthManager should reject expired state."""
    manager = OAuthManager(providers={"github": build_provider()})
    state = "expired_state"
    manager._pending[state] = {
        "user_id": "user1",
        "trace_id": "trace1",
        "provider": "github",
        "created_at": time.time() - 700,  # > 600s TTL
    }
    with pytest.raises(ValueError, match="OAuth request expired"):
        await manager.handle_callback("code123", state)


@pytest.mark.asyncio
async def test_oauth_manager_handle_callback_success():
    """OAuthManager should exchange code for token."""
    pytest.importorskip("aiohttp")
    manager = OAuthManager(providers={"github": build_provider()})

    # Generate auth request to get valid state
    request = manager.get_auth_request("github", "user1", "trace1")
    state = request["state"]

    # Mock aiohttp response
    mock_response = AsyncMock()
    mock_response.json = AsyncMock(
        return_value={
            "access_token": "new_access_token",
            "token_type": "bearer",
            "expires_in": 3600,
        }
    )
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        user_id, trace_id = await manager.handle_callback("auth_code", state)

    assert user_id == "user1"
    assert trace_id == "trace1"

    # Token should be stored
    token = await manager.get_token("user1", "github")
    assert token == "new_access_token"


@pytest.mark.asyncio
async def test_oauth_manager_handle_callback_oauth_error():
    """OAuthManager should handle OAuth error response."""
    pytest.importorskip("aiohttp")
    manager = OAuthManager(providers={"github": build_provider()})

    request = manager.get_auth_request("github", "user1", "trace1")
    state = request["state"]

    mock_response = AsyncMock()
    mock_response.json = AsyncMock(
        return_value={
            "error": "access_denied",
            "error_description": "User denied access",
        }
    )
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        with pytest.raises(ValueError, match="OAuth error: User denied access"):
            await manager.handle_callback("auth_code", state)


@pytest.mark.asyncio
async def test_oauth_manager_handle_callback_error_without_description():
    """OAuthManager should handle OAuth error without description."""
    pytest.importorskip("aiohttp")
    manager = OAuthManager(providers={"github": build_provider()})

    request = manager.get_auth_request("github", "user1", "trace1")
    state = request["state"]

    mock_response = AsyncMock()
    mock_response.json = AsyncMock(return_value={"error": "server_error"})
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        with pytest.raises(ValueError, match="OAuth error: server_error"):
            await manager.handle_callback("auth_code", state)


@pytest.mark.asyncio
async def test_oauth_manager_handle_callback_no_expires():
    """OAuthManager should handle token without expires_in."""
    pytest.importorskip("aiohttp")
    manager = OAuthManager(providers={"github": build_provider()})

    request = manager.get_auth_request("github", "user1", "trace1")
    state = request["state"]

    mock_response = AsyncMock()
    mock_response.json = AsyncMock(
        return_value={
            "access_token": "token_no_expiry",
            "token_type": "bearer",
        }
    )
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        user_id, trace_id = await manager.handle_callback("auth_code", state)

    assert user_id == "user1"
    token = await manager.get_token("user1", "github")
    assert token == "token_no_expiry"
