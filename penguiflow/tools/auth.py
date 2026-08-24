"""OAuth manager and token storage for ToolNode."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Protocol


class TokenStore(Protocol):
    """Persistence contract for OAuth access tokens.

    Implement this protocol to back :class:`OAuthManager` with a durable
    store (e.g. a database or secrets manager) instead of the default
    :class:`InMemoryTokenStore`. Tokens are keyed by ``(user_id, provider)``.
    """

    async def store(self, user_id: str, provider: str, token: str, expires_at: float | None) -> None:
        """Persist an access token for a user/provider pair.

        Args:
            user_id: Identifier of the user the token belongs to.
            provider: OAuth provider name (matches
                :attr:`OAuthProviderConfig.name`).
            token: The access token value to store.
            expires_at: Unix timestamp (seconds) after which the token is
                considered expired, or ``None`` if it does not expire.
        """
        ...

    async def get(self, user_id: str, provider: str) -> str | None:
        """Retrieve a previously stored access token.

        Args:
            user_id: Identifier of the user the token belongs to.
            provider: OAuth provider name.

        Returns:
            The stored token, or ``None`` if absent or expired.
        """
        ...

    async def delete(self, user_id: str, provider: str) -> None:
        """Remove a stored access token, if present.

        Args:
            user_id: Identifier of the user the token belongs to.
            provider: OAuth provider name.
        """
        ...


class InMemoryTokenStore:
    """Simple in-memory :class:`TokenStore` for development and tests.

    Tokens are held only in process memory (a plain dict keyed by
    ``(user_id, provider)``) and are lost on process restart. Not suitable
    for production multi-worker deployments.
    """

    def __init__(self) -> None:
        self._tokens: dict[tuple[str, str], tuple[str, float | None]] = {}

    async def store(self, user_id: str, provider: str, token: str, expires_at: float | None) -> None:
        """Store a token in memory, overwriting any existing entry.

        Args:
            user_id: Identifier of the user the token belongs to.
            provider: OAuth provider name.
            token: The access token value to store.
            expires_at: Unix timestamp (seconds) after which the token is
                considered expired, or ``None`` if it does not expire.
        """
        self._tokens[(user_id, provider)] = (token, expires_at)

    async def get(self, user_id: str, provider: str) -> str | None:
        """Fetch a token, transparently evicting it if expired.

        Args:
            user_id: Identifier of the user the token belongs to.
            provider: OAuth provider name.

        Returns:
            The stored token, or ``None`` if absent or expired.
        """
        data = self._tokens.get((user_id, provider))
        if not data:
            return None
        token, expires_at = data
        if expires_at and time.time() > expires_at:
            await self.delete(user_id, provider)
            return None
        return token

    async def delete(self, user_id: str, provider: str) -> None:
        """Remove a stored token, if present.

        Args:
            user_id: Identifier of the user the token belongs to.
            provider: OAuth provider name.
        """
        self._tokens.pop((user_id, provider), None)


@dataclass
class OAuthProviderConfig:
    """Static configuration describing a single OAuth 2.0 authorization-code provider.

    One instance is registered per provider in ``OAuthManager.providers``, keyed
    by ``name``.

    Attributes:
        name: Unique provider key (matches the key used in
            ``OAuthManager.providers`` and the ``provider`` argument passed to
            :class:`TokenStore` methods).
        display_name: Human-readable provider name shown to end users during
            the HITL consent flow.
        auth_url: Provider's authorization endpoint (where the user is
            redirected to grant consent).
        token_url: Provider's token endpoint used to exchange an
            authorization code for an access token.
        client_id: OAuth client ID registered with the provider.
        client_secret: OAuth client secret registered with the provider.
        redirect_uri: Redirect URI registered with the provider that the
            authorization server will send the user back to.
        scopes: OAuth scopes to request during authorization. Defaults to
            an empty list (no scopes requested).
    """

    name: str
    display_name: str
    auth_url: str
    token_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: list[str] = field(default_factory=list)


@dataclass
class OAuthManager:
    """Manages user OAuth flows with HITL integration.

    Used by ``ToolNode`` when a tool source's ``auth_type`` is
    ``AuthType.OAUTH2_USER``: the manager checks the token store for a cached
    token, and if none is available builds an authorization URL for the
    caller to present to the user (typically via a HITL pause), then
    completes the flow via :meth:`handle_callback` once the provider
    redirects back with an authorization code.

    Attributes:
        providers: Mapping of provider name to its
            :class:`OAuthProviderConfig`.
        token_store: Backing :class:`TokenStore` used to cache and retrieve
            access tokens. Defaults to a fresh :class:`InMemoryTokenStore`.
    """

    providers: dict[str, OAuthProviderConfig]
    token_store: TokenStore = field(default_factory=InMemoryTokenStore)

    _pending: dict[str, dict[str, float | str]] = field(default_factory=dict, repr=False)

    async def get_token(self, user_id: str, provider: str) -> str | None:
        """Look up a cached access token for a user/provider pair.

        Args:
            user_id: Identifier of the user to look up.
            provider: OAuth provider name.

        Returns:
            The cached token, or ``None`` if none is stored or it expired.
        """
        return await self.token_store.get(user_id, provider)

    def get_auth_request(
        self,
        provider: str,
        user_id: str,
        trace_id: str,
    ) -> dict[str, str | list[str]]:
        """Build an authorization request for a HITL OAuth consent flow.

        Generates and tracks a random ``state`` value (pruning any expired
        pending requests first) and constructs the provider's authorization
        URL. The returned payload is typically surfaced to the user via
        ``ctx.pause()`` so they can complete consent out-of-band.

        Args:
            provider: OAuth provider name (must exist in ``self.providers``).
            user_id: Identifier of the user initiating the flow.
            trace_id: Trace ID of the run requesting authorization, used to
                resume the correct run in :meth:`handle_callback`.

        Returns:
            Dict with ``display_name``, ``auth_url`` (full authorization URL
            including the generated ``state``), ``scopes``, and ``state``.

        Raises:
            ValueError: If ``provider`` is not a known provider name.
        """
        config = self.providers.get(provider)
        if not config:
            raise ValueError(f"Unknown OAuth provider: {provider}")

        self._cleanup_pending()
        state = secrets.token_urlsafe(32)
        self._pending[state] = {
            "user_id": user_id,
            "trace_id": trace_id,
            "provider": provider,
            "created_at": time.time(),
        }

        params = {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "scope": " ".join(config.scopes),
            "state": state,
            "response_type": "code",
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())

        return {
            "display_name": config.display_name,
            "auth_url": f"{config.auth_url}?{query}",
            "scopes": config.scopes,
            "state": state,
        }

    async def handle_callback(self, code: str, state: str) -> tuple[str, str]:
        """Complete an OAuth authorization-code exchange and cache the token.

        Consumes the pending request created by :meth:`get_auth_request`,
        exchanges ``code`` for an access token at the provider's token
        endpoint, and stores the token in ``self.token_store`` for future
        :meth:`get_token` lookups.

        Args:
            code: Authorization code returned by the provider's redirect.
            state: The ``state`` value returned alongside the code; must
                match a pending request created within the last 10 minutes.

        Returns:
            Tuple of ``(user_id, trace_id)`` identifying which user and run
            initiated the flow, so the caller can resume the paused run.

        Raises:
            ValueError: If ``state`` is unknown/already consumed, the
                pending request has expired (older than 10 minutes), or the
                provider's token response contains an ``error``.
            RuntimeError: If ``aiohttp`` is not installed.
        """
        pending = self._pending.pop(state, None)
        if not pending:
            raise ValueError("Invalid or expired OAuth state")

        created_at = float(pending["created_at"])
        if time.time() - created_at > 600:
            raise ValueError("OAuth request expired")

        provider = str(pending["provider"])
        config = self.providers[provider]

        try:
            import aiohttp
        except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError("aiohttp is required for OAuth handling. Install penguiflow[planner].") from exc

        async with aiohttp.ClientSession() as session:
            async with session.post(
                config.token_url,
                data={
                    "client_id": config.client_id,
                    "client_secret": config.client_secret,
                    "code": code,
                    "redirect_uri": config.redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            ) as resp:
                result = await resp.json()

        if "error" in result:
            raise ValueError(f"OAuth error: {result.get('error_description', result['error'])}")

        expires_at = None
        if "expires_in" in result:
            expires_at = time.time() + result["expires_in"]

        user_id = str(pending["user_id"])
        trace_id = str(pending["trace_id"])

        await self.token_store.store(
            user_id,
            provider,
            result["access_token"],
            expires_at,
        )

        return user_id, trace_id

    def _cleanup_pending(self) -> None:
        """Prune expired pending OAuth states to avoid unbounded growth."""
        if not self._pending:
            return
        now = time.time()
        expired = [state for state, data in self._pending.items() if now - float(data.get("created_at", 0)) > 600]
        for state in expired:
            self._pending.pop(state, None)


__all__ = [
    "InMemoryTokenStore",
    "OAuthManager",
    "OAuthProviderConfig",
    "TokenStore",
]
