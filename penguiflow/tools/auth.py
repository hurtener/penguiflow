"""OAuth manager and token storage for ToolNode."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Protocol


class TokenStore(Protocol):
    """Protocol for token persistence."""

    async def store(self, user_id: str, provider: str, token: str, expires_at: float | None) -> None:
        ...

    async def get(self, user_id: str, provider: str) -> str | None:
        ...

    async def delete(self, user_id: str, provider: str) -> None:
        ...


class InMemoryTokenStore:
    """Simple in-memory token store for development and tests."""

    def __init__(self) -> None:
        self._tokens: dict[tuple[str, str], tuple[str, float | None]] = {}

    async def store(self, user_id: str, provider: str, token: str, expires_at: float | None) -> None:
        self._tokens[(user_id, provider)] = (token, expires_at)

    async def get(self, user_id: str, provider: str) -> str | None:
        data = self._tokens.get((user_id, provider))
        if not data:
            return None
        token, expires_at = data
        if expires_at and time.time() > expires_at:
            await self.delete(user_id, provider)
            return None
        return token

    async def delete(self, user_id: str, provider: str) -> None:
        self._tokens.pop((user_id, provider), None)


@dataclass
class OAuthProviderConfig:
    """Configuration for an OAuth provider."""

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
    """Manages user OAuth flows with HITL integration."""

    providers: dict[str, OAuthProviderConfig]
    token_store: TokenStore = field(default_factory=InMemoryTokenStore)

    _pending: dict[str, dict[str, float | str]] = field(default_factory=dict, repr=False)

    async def get_token(self, user_id: str, provider: str) -> str | None:
        return await self.token_store.get(user_id, provider)

    def get_auth_request(
        self,
        provider: str,
        user_id: str,
        trace_id: str,
    ) -> dict[str, str | list[str]]:
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
        """Handle OAuth callback. Returns (user_id, trace_id) for resuming."""
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
