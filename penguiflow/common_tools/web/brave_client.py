"""Minimal async client for the Brave Search API."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .cache import TTLCache, make_cache_key


@dataclass(frozen=True, slots=True)
class BraveAuth:
    """Which header carries the Brave API key."""

    header: Literal["X-Subscription-Token", "Authorization"] = (
        "X-Subscription-Token"
    )


@dataclass(frozen=True, slots=True)
class BraveClientConfig:
    """Endpoint, timeout and auth settings for :class:`BraveClient`."""

    base_url: str = "https://api.search.brave.com"
    timeout_s: float = 20.0
    auth: BraveAuth = BraveAuth()


@dataclass(frozen=True, slots=True)
class LocationHeaders:
    """Location hints Brave uses to localize results."""

    lat: float | None = None
    long: float | None = None
    timezone: str | None = None
    city: str | None = None
    state: str | None = None
    state_name: str | None = None
    country: str | None = None
    postal_code: str | None = None

    def to_headers(self) -> dict[str, str]:
        """Return the location as Brave ``X-Loc-*`` request headers."""
        headers: dict[str, str] = {}
        if self.lat is not None:
            headers["X-Loc-Lat"] = str(self.lat)
        if self.long is not None:
            headers["X-Loc-Long"] = str(self.long)
        if self.timezone:
            headers["X-Loc-Timezone"] = str(self.timezone)
        if self.city:
            headers["X-Loc-City"] = str(self.city)
        if self.state:
            headers["X-Loc-State"] = str(self.state)
        if self.state_name:
            headers["X-Loc-State-Name"] = str(self.state_name)
        if self.country:
            headers["X-Loc-Country"] = str(self.country)
        if self.postal_code:
            headers["X-Loc-Postal-Code"] = str(self.postal_code)
        return headers


def resolve_brave_api_key(
    *,
    explicit: str | None,
    tool_context: Mapping[str, Any] | None,
    service: Literal["search", "answers"] = "search",
) -> str:
    """Resolve Brave API key for a given service.

    Some Brave subscriptions issue distinct tokens for different product groups
    (e.g., Answers vs Search). This resolver supports both.
    """
    if explicit:
        return explicit

    keys = (
        ("brave_answers_api_key", "brave_answer_api_key")
        if service == "answers"
        else ("brave_api_key",)
    )
    from_context = _context_api_key(tool_context, keys)
    if from_context:
        return from_context

    env_names = (
        (
            "BRAVE_ANSWERS_API_KEY",
            "BRAVE_ANSWER_API_KEY",
            "BRAVE_SEARCH_API_KEY",
            "BRAVE_API_KEY",
        )
        if service == "answers"
        else ("BRAVE_SEARCH_API_KEY", "BRAVE_API_KEY")
    )
    env = next(
        (os.environ[name] for name in env_names if os.environ.get(name)), None
    )
    if env and env.strip():
        return env.strip()

    if service == "answers":
        msg = (
            "Brave Answers API key required. Provide "
            "BraveWebConfig(answers_api_key=...), "
            "set tool_context['brave_answers_api_key'] (or "
            "tool_context['web']['brave_answers_api_key']), "
            "or set BRAVE_ANSWERS_API_KEY."
        )
    else:
        msg = (
            "Brave API key required. Provide BraveWebConfig(api_key=...), "
            "set tool_context['brave_api_key'], "
            "set tool_context['web']['brave_api_key'], or set "
            "BRAVE_SEARCH_API_KEY / BRAVE_API_KEY."
        )
    raise ValueError(msg)


def _context_api_key(
    tool_context: Mapping[str, Any] | None, keys: tuple[str, ...]
) -> str | None:
    """Find a key in ``tool_context`` or its nested ``web`` mapping."""
    if not tool_context:
        return None
    nested = tool_context.get("web")
    for source in (tool_context, nested):
        if not isinstance(source, Mapping):
            continue
        for key in keys:
            candidate = source.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


class BraveClient:
    """Async JSON client for the Brave Search API with an optional cache."""

    def __init__(
        self,
        *,
        config: BraveClientConfig,
        cache: TTLCache[str, Any] | None = None,
    ) -> None:
        """Configure the client and an optional response cache."""
        self._config = config
        self._cache = cache

    async def request_json(
        self,
        *,
        api_key: str,
        method: Literal["GET", "POST"],
        path: str,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        cache_key: str | None = None,
    ) -> object:
        """Send a request and return its JSON body, cached when keyed."""
        cached = (
            self._cache.get(cache_key) if (cache_key and self._cache) else None
        )
        if cached is not None:
            return cached

        try:
            import aiohttp
        except ModuleNotFoundError as exc:  # pragma: no cover
            msg = (
                "aiohttp is required for Brave web tools. Install "
                "penguiflow-lite[web]."
            )
            raise ModuleNotFoundError(msg) from exc

        url = self._config.base_url.rstrip("/") + path
        req_headers: dict[str, str] = {"Accept": "application/json"}
        if self._config.auth.header == "Authorization":
            req_headers["Authorization"] = f"Bearer {api_key}"
        else:
            req_headers["X-Subscription-Token"] = api_key
        if headers:
            req_headers.update({str(k): str(v) for k, v in headers.items()})

        timeout = aiohttp.ClientTimeout(total=float(self._config.timeout_s))
        query_params = (
            _normalize_query_params(params or {}) if method == "GET" else None
        )
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.request(
                method,
                url,
                params=query_params,
                json=dict(json_body or {}) if method == "POST" else None,
                headers=req_headers,
            ) as resp,
        ):
            data: object = await resp.json()
            if resp.status >= 400:
                raise RuntimeError(
                    format_brave_error(
                        "Brave API error", status=resp.status, payload=data
                    )
                )

        if cache_key and self._cache:
            self._cache.set(cache_key, data)
        return data

    def make_key(
        self,
        *,
        path: str,
        method: str,
        params: Mapping[str, Any] | None,
        body: Mapping[str, Any] | None,
        headers: Mapping[str, str] | None,
    ) -> str:
        """Build a cache key, dropping auth headers."""
        # Avoid secrets in keys: exclude auth headers; keep location +
        # request-shape.
        safe_headers = {}
        for k, v in (headers or {}).items():
            if k.lower() in {"authorization", "x-subscription-token"}:
                continue
            safe_headers[k] = v
        return make_cache_key(
            method,
            path,
            sorted((params or {}).items()),
            sorted((body or {}).items()),
            sorted(safe_headers.items()),
        )


def _normalize_query_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize query params for aiohttp/yarl.

    yarl rejects bool values in query strings (expects str/int/float).
    """
    normalized: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            normalized[str(key)] = "true" if value else "false"
            continue
        if isinstance(value, (str, int, float)):
            normalized[str(key)] = value
            continue
        if isinstance(value, (list, tuple)):
            out: list[Any] = []
            for item in value:
                if item is None:
                    continue
                if isinstance(item, bool):
                    out.append("true" if item else "false")
                elif isinstance(item, (str, int, float)):
                    out.append(item)
                else:
                    out.append(str(item))
            normalized[str(key)] = out
            continue
        normalized[str(key)] = str(value)
    return normalized


def format_brave_error(prefix: str, *, status: int, payload: object) -> str:
    """Format a Brave API error body, using its code and detail if any."""
    if isinstance(payload, Mapping):
        err = payload.get("error")
        if isinstance(err, Mapping):
            code = err.get("code")
            detail = err.get("detail")
            if isinstance(code, str) and isinstance(detail, str):
                return f"{prefix} {status} [{code}]: {detail}"
            if isinstance(detail, str):
                return f"{prefix} {status}: {detail}"
    return f"{prefix} {status}: {payload}"
