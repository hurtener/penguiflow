"""Model fallback with per-(model, key) cooldown for rate-limit resilience.

When a model returns HTTP 429, :class:`FallbackLLMClient` routes the call to
the next configured model (or the next API key for the same model) and places
the rate-limited ``(model, key)`` pair in a cooldown. Because each planner step
is a discrete ``complete()`` call and trajectory state lives in the planner,
this switch is transparent — a run continues uninterrupted on the fallback
model.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from typing_extensions import override

from .errors import LLMRateLimitError
from .providers.base import Provider
from .types import StreamEvent

if TYPE_CHECKING:
    from penguiflow.planner.models import JSONLLMClient

    from .profiles import ModelProfile
    from .protocol import NativeLLMAdapter, NativeToolCallResult
    from .types import (
        CancelToken,
        CompletionResponse,
        LLMRequest,
        StreamCallback,
        ToolSpec,
    )

_T = TypeVar("_T")
ChunkCallback = Callable[[str, bool], None]

logger = logging.getLogger("penguiflow.llm.fallback")

# An (model_id, key_index) pair — the unit that cooldown is tracked against.
Entry = tuple[str, int]


class CooldownStore:
    """Tracks cooldown expiry per ``(model, key_index)`` pair.

    The default implementation keeps state in memory for the lifetime of the
    instance (v1: one store shared by all clients of a single planner run).
    Subclass and override the methods to widen the scope (e.g. a process-wide
    singleton) without changing :class:`FallbackLLMClient`.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        """Create an empty cooldown store.

        Args:
            clock: Monotonic time source, in seconds; tests pass a fake one.
        """
        self._until: dict[Entry, float] = {}
        self._clock = clock

    def _now(self) -> float:
        return self._clock()

    def in_cooldown(self, entry: Entry) -> bool:
        """Whether ``entry`` is currently cooling down."""
        expiry = self._until.get(entry)
        return expiry is not None and expiry > self._now()

    def set_cooldown(self, entry: Entry, seconds: float) -> float:
        """Put ``entry`` in cooldown for ``seconds``; return the expiry."""
        expiry = self._now() + seconds
        self._until[entry] = expiry
        return expiry

    def seconds_until_earliest(self, entries: Sequence[Entry]) -> float | None:
        """Seconds until the soonest cooldown among ``entries`` expires.

        Returns ``None`` when at least one entry is already available.
        """
        now = self._now()
        soonest: float | None = None
        for entry in entries:
            expiry = self._until.get(entry)
            if expiry is None or expiry <= now:
                return None  # something is available right now
            remaining = expiry - now
            if soonest is None or remaining < soonest:
                soonest = remaining
        return soonest


@dataclass
class ModelFallbackConfig:
    """Developer-facing configuration for rate-limit model fallback.

    Args:
        models: Ordered fallback chain, highest priority first.
        api_keys: Optional key pool. ``None`` means each provider uses its
            main/env key. When provided, a 429 first rotates to the next key
            for the same model before advancing to the next model.
        cooldown_s: Cooldown applied to a ``(model, key)`` pair after a 429.
        max_wait_s: When every entry is cooling down, the longest a call will
            block waiting for the soonest cooldown to expire before failing.
    """

    models: list[str]
    api_keys: list[str] | None = None
    cooldown_s: float = 45.0
    max_wait_s: float = 30.0

    def __post_init__(self) -> None:
        """Validate the fallback chain, key pool and cooldown."""
        if not self.models:
            msg = "ModelFallbackConfig.models must not be empty"
            raise ValueError(msg)
        if self.api_keys is not None and not self.api_keys:
            msg = (
                "ModelFallbackConfig.api_keys must not be empty when provided"
            )
            raise ValueError(msg)
        if self.cooldown_s <= 0:
            msg = "ModelFallbackConfig.cooldown_s must be positive"
            raise ValueError(msg)

    @classmethod
    def from_env(
        cls,
        model_vars: Sequence[str],
        *,
        api_key_vars: Sequence[str] | None = None,
        cooldown_s: float = 45.0,
        max_wait_s: float = 30.0,
    ) -> ModelFallbackConfig:
        """Build a config from named environment variables.

        ``model_vars`` are read in order; missing/empty variables are skipped.
        """
        models = [os.environ[v] for v in model_vars if os.environ.get(v)]
        if not models:
            msg = f"No fallback models found in env vars: {list(model_vars)}"
            raise ValueError(msg)
        api_keys: list[str] | None = None
        if api_key_vars:
            api_keys = [
                os.environ[v] for v in api_key_vars if os.environ.get(v)
            ] or None
        return cls(
            models=models,
            api_keys=api_keys,
            cooldown_s=cooldown_s,
            max_wait_s=max_wait_s,
        )


class NativeAdapter(Protocol):
    """Adapter surface that :class:`FallbackLLMClient` fails over across."""

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: ChunkCallback | None = None,
        on_reasoning_chunk: ChunkCallback | None = None,
    ) -> tuple[str, float]:
        """Run a JSON completion and return ``(text, cost)``."""
        ...

    async def complete_with_tools(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[ToolSpec],
        stream: bool = False,
        on_stream_chunk: ChunkCallback | None = None,
        on_reasoning_chunk: ChunkCallback | None = None,
    ) -> NativeToolCallResult:
        """Run a completion with provider-native tool declarations."""
        ...


# Factories build one client for one model. They receive the model id, an
# ``api_key`` keyword and the wrapper's extra keyword arguments, which each
# factory validates itself.
AdapterFactory = Callable[..., NativeAdapter]
ClientFactory = Callable[..., "JSONLLMClient"]
ProviderFactory = Callable[..., Provider]


def _default_adapter_factory(
    model: str, *, api_key: str | None, **kwargs: object
) -> NativeLLMAdapter:
    """Build a :class:`NativeLLMAdapter` for ``model`` (deferred import)."""
    from .protocol import NativeLLMAdapter

    # The adapter validates the forwarded options at construction time.
    build: Callable[..., NativeLLMAdapter] = NativeLLMAdapter
    return build(model, api_key=api_key, **kwargs)


class FallbackLLMClient:
    """A ``JSONLLMClient`` that fails over across models on rate limits.

    The effective chain leads with ``primary_model`` followed by the configured
    fallback models, so selection always prefers the primary and reverts to it
    once its cooldown expires.
    """

    def __init__(
        self,
        primary_model: str,
        config: ModelFallbackConfig,
        *,
        cooldown_store: CooldownStore | None = None,
        adapter_factory: AdapterFactory | None = None,
        default_api_key: str | None = None,
        **adapter_kwargs: object,
    ) -> None:
        """Wrap ``primary_model`` with failover across ``config``'s models."""
        self._model = primary_model
        self._config = config
        self._store = (
            cooldown_store if cooldown_store is not None else CooldownStore()
        )
        self._adapter_factory: AdapterFactory = (
            adapter_factory or _default_adapter_factory
        )
        self._default_api_key = default_api_key
        # Inner adapters must not retry 429s in-place — a rate limit should
        # trigger fast failover to the next model, not local backoff.
        self._adapter_kwargs = {
            **adapter_kwargs,
            "retry_rate_limit_errors": False,
        }

        # Effective chain: primary first, then fallbacks (de-duplicated).
        chain = [primary_model]
        chain.extend(m for m in config.models if m != primary_model)
        self._chain = chain

        n_keys = len(config.api_keys) if config.api_keys else 1
        # Entry order is model-major, key-minor: keys rotate within a model
        # before advancing to the next model.
        self._entries: list[Entry] = [
            (model, key_index)
            for model in chain
            for key_index in range(n_keys)
        ]
        self._adapters: dict[Entry, NativeAdapter] = {}

    # -- introspection ------------------------------------------------------

    @property
    def model(self) -> str:
        """The primary model identifier."""
        return self._model

    # -- internals ----------------------------------------------------------

    def _adapter(self, entry: Entry) -> NativeAdapter:
        """Lazily build (and cache) the adapter for an entry."""
        cached = self._adapters.get(entry)
        if cached is not None:
            return cached
        model, key_index = entry
        if self._config.api_keys:
            api_key: str | None = self._config.api_keys[key_index]
        else:
            api_key = self._default_api_key
        adapter = self._adapter_factory(
            model, api_key=api_key, **self._adapter_kwargs
        )
        self._adapters[entry] = adapter
        return adapter

    def _select(self) -> Entry | None:
        """First entry not currently in cooldown, in priority order."""
        for entry in self._entries:
            if not self._store.in_cooldown(entry):
                return entry
        return None

    # -- JSONLLMClient ------------------------------------------------------

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
        on_reasoning_chunk: Callable[[str, bool], None] | None = None,
    ) -> tuple[str, float]:
        """Run a completion, failing over to fallback models on 429."""

        async def call(
            adapter: NativeAdapter, stream_chunk: ChunkCallback | None
        ) -> tuple[str, float]:
            return await adapter.complete(
                messages=messages,
                response_format=response_format,
                stream=stream,
                on_stream_chunk=stream_chunk,
                on_reasoning_chunk=on_reasoning_chunk,
            )

        return await self._with_failover(
            call, stream=stream, on_stream_chunk=on_stream_chunk
        )

    async def complete_with_tools(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[ToolSpec],
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
        on_reasoning_chunk: Callable[[str, bool], None] | None = None,
    ) -> NativeToolCallResult:
        """Native tool-calling variant of ``complete``, same failover."""

        async def call(
            adapter: NativeAdapter, stream_chunk: ChunkCallback | None
        ) -> NativeToolCallResult:
            return await adapter.complete_with_tools(
                messages=messages,
                tools=tools,
                stream=stream,
                on_stream_chunk=stream_chunk,
                on_reasoning_chunk=on_reasoning_chunk,
            )

        return await self._with_failover(
            call, stream=stream, on_stream_chunk=on_stream_chunk
        )

    async def _with_failover(
        self,
        call: Callable[[NativeAdapter, ChunkCallback | None], Awaitable[_T]],
        *,
        stream: bool,
        on_stream_chunk: Callable[[str, bool], None] | None,
    ) -> _T:
        """Shared 429 failover: selection, cooldown and mid-stream rules."""
        chunks_emitted = 0

        def wrapped_stream_chunk(text: str, done: bool) -> None:
            nonlocal chunks_emitted
            if text:
                chunks_emitted += 1
            if on_stream_chunk is not None:
                on_stream_chunk(text, done)

        last_error: LLMRateLimitError | None = None
        waited = False

        while True:
            entry = self._select()
            if entry is None:
                # Every entry is cooling down — wait once for the soonest, then
                # retry; if still exhausted, surface the rate-limit error.
                wait_s = self._store.seconds_until_earliest(self._entries)
                if (
                    not waited
                    and wait_s is not None
                    and wait_s <= self._config.max_wait_s
                ):
                    waited = True
                    logger.warning(
                        "llm_fallback_all_cooling_down | waiting=%.1fs", wait_s
                    )
                    await asyncio.sleep(wait_s)
                    continue
                if last_error is not None:
                    raise last_error
                raise LLMRateLimitError(
                    message="all fallback models are in cooldown",
                    provider="fallback",
                    status_code=429,
                )

            model, key_index = entry
            adapter = self._adapter(entry)
            try:
                return await call(
                    adapter, wrapped_stream_chunk if stream else None
                )
            except LLMRateLimitError as exc:
                last_error = exc
                cooldown_s = max(
                    self._config.cooldown_s, exc.retry_after or 0.0
                )
                expiry = self._store.set_cooldown(entry, cooldown_s)
                logger.warning(
                    "llm_model_cooldown | model=%s key_index=%d "
                    "cooldown_s=%.1f "
                    "retry_after=%s expiry=%.1f",
                    model,
                    key_index,
                    cooldown_s,
                    exc.retry_after,
                    expiry,
                )
                if chunks_emitted > 0:
                    # Output already streamed to the user — cannot
                    # transparently switch. Propagate so the planner retries
                    # the step on a fresh model (the entry is now in cooldown).
                    raise
                # No output emitted yet: select the next entry and retry.
                continue


class _FallbackExecutor:
    """Shared 429 failover engine for client and provider wrappers."""

    def __init__(
        self,
        primary_model: str,
        config: ModelFallbackConfig,
        *,
        cooldown_store: CooldownStore | None = None,
    ) -> None:
        self._model = primary_model
        self._config = config
        self._store = (
            cooldown_store if cooldown_store is not None else CooldownStore()
        )

        chain = [primary_model]
        chain.extend(m for m in config.models if m != primary_model)
        self._chain = chain

        n_keys = len(config.api_keys) if config.api_keys else 1
        self._entries: list[Entry] = [
            (model, key_index)
            for model in chain
            for key_index in range(n_keys)
        ]

    @property
    def model(self) -> str:
        return self._model

    def resolve_api_key(
        self, key_index: int, default_api_key: str | None
    ) -> str | None:
        if self._config.api_keys:
            return self._config.api_keys[key_index]
        return default_api_key

    def _select(self) -> Entry | None:
        for entry in self._entries:
            if not self._store.in_cooldown(entry):
                return entry
        return None

    async def run(
        self,
        call: Callable[[Entry], Awaitable[_T]],
        *,
        streamed_output_emitted: Callable[[], bool] | None = None,
    ) -> _T:
        last_error: LLMRateLimitError | None = None
        waited = False

        while True:
            entry = self._select()
            if entry is None:
                wait_s = self._store.seconds_until_earliest(self._entries)
                if (
                    not waited
                    and wait_s is not None
                    and wait_s <= self._config.max_wait_s
                ):
                    waited = True
                    logger.warning(
                        "llm_fallback_all_cooling_down | waiting=%.1fs", wait_s
                    )
                    await asyncio.sleep(wait_s)
                    continue
                if last_error is not None:
                    raise last_error
                raise LLMRateLimitError(
                    message="all fallback models are in cooldown",
                    provider="fallback",
                    status_code=429,
                )

            model, key_index = entry
            try:
                return await call(entry)
            except LLMRateLimitError as exc:
                last_error = exc
                cooldown_s = max(
                    self._config.cooldown_s, exc.retry_after or 0.0
                )
                expiry = self._store.set_cooldown(entry, cooldown_s)
                logger.warning(
                    "llm_model_cooldown | model=%s key_index=%d "
                    "cooldown_s=%.1f "
                    "retry_after=%s expiry=%.1f",
                    model,
                    key_index,
                    cooldown_s,
                    exc.retry_after,
                    expiry,
                )
                if (
                    streamed_output_emitted is not None
                    and streamed_output_emitted()
                ):
                    raise
                continue


class GenericFallbackLLMClient:
    """Fallback wrapper for PenguiFlow-managed non-native JSON clients."""

    def __init__(
        self,
        primary_model: str,
        config: ModelFallbackConfig,
        *,
        client_factory: ClientFactory,
        cooldown_store: CooldownStore | None = None,
        default_api_key: str | None = None,
        **client_kwargs: object,
    ) -> None:
        """Wrap ``primary_model`` clients with failover across ``config``."""
        self._executor = _FallbackExecutor(
            primary_model, config, cooldown_store=cooldown_store
        )
        self._client_factory = client_factory
        self._default_api_key = default_api_key
        self._client_kwargs = client_kwargs
        self._clients: dict[Entry, JSONLLMClient] = {}

    @property
    def model(self) -> str:
        """The primary model identifier."""
        return self._executor.model

    def _client(self, entry: Entry) -> JSONLLMClient:
        cached = self._clients.get(entry)
        if cached is not None:
            return cached
        model, key_index = entry
        api_key = self._executor.resolve_api_key(
            key_index, self._default_api_key
        )
        client = self._client_factory(
            model, api_key=api_key, **self._client_kwargs
        )
        self._clients[entry] = client
        return client

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
        on_reasoning_chunk: Callable[[str, bool], None] | None = None,
    ) -> str | tuple[str, float]:
        """Run a JSON completion, failing over to fallback models on 429."""
        chunks_emitted = 0

        def wrapped_stream_chunk(text: str, done: bool) -> None:
            nonlocal chunks_emitted
            if text:
                chunks_emitted += 1
            if on_stream_chunk is not None:
                on_stream_chunk(text, done)

        async def call(entry: Entry) -> str | tuple[str, float]:
            client = self._client(entry)
            kwargs: dict[str, Any] = {
                "messages": messages,
                "response_format": response_format,
                "stream": stream,
                "on_stream_chunk": wrapped_stream_chunk if stream else None,
            }
            if on_reasoning_chunk is not None:
                kwargs["on_reasoning_chunk"] = on_reasoning_chunk
            return await client.complete(**kwargs)

        return await self._executor.run(
            call, streamed_output_emitted=lambda: chunks_emitted > 0
        )


class FallbackProvider(Provider):
    """Provider wrapper that fails over across models on 429."""

    def __init__(
        self,
        primary_model: str,
        config: ModelFallbackConfig,
        *,
        provider_factory: ProviderFactory,
        cooldown_store: CooldownStore | None = None,
        default_api_key: str | None = None,
        **provider_kwargs: object,
    ) -> None:
        """Wrap ``primary_model`` providers with failover across ``config``."""
        self._executor = _FallbackExecutor(
            primary_model, config, cooldown_store=cooldown_store
        )
        self._provider_factory = provider_factory
        self._default_api_key = default_api_key
        self._provider_kwargs = provider_kwargs
        self._providers: dict[Entry, Provider] = {}
        self._primary_entry: Entry = (primary_model, 0)
        # The model actually used by the most recent attempt. Drives pricing
        # and telemetry above this seam (e.g. ``call_with_retry`` reads
        # ``provider.model`` to attribute cost), so it must reflect the
        # failover target, not the primary.
        self._active_model = primary_model

    @property
    @override
    def provider_name(self) -> str:
        return self._provider(self._primary_entry).provider_name

    @property
    @override
    def profile(self) -> ModelProfile:
        return self._provider(self._primary_entry).profile

    @property
    @override
    def model(self) -> str:
        return self._active_model

    def _provider(self, entry: Entry) -> Provider:
        cached = self._providers.get(entry)
        if cached is not None:
            return cached
        model, key_index = entry
        api_key = self._executor.resolve_api_key(
            key_index, self._default_api_key
        )
        provider = self._provider_factory(
            model, api_key=api_key, **self._provider_kwargs
        )
        self._providers[entry] = provider
        return provider

    @override
    async def complete(
        self,
        request: LLMRequest,
        *,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
        stream: bool = False,
        on_stream_event: StreamCallback | None = None,
    ) -> CompletionResponse:
        chunks_emitted = 0

        def wrapped_stream_event(event: StreamEvent) -> None:
            nonlocal chunks_emitted
            if isinstance(event, StreamEvent) and (
                event.delta_text
                or event.delta_reasoning
                or event.delta_tool_call is not None
            ):
                chunks_emitted += 1
            if on_stream_event is not None:
                on_stream_event(event)

        async def call(entry: Entry) -> CompletionResponse:
            self._active_model = entry[0]
            provider = self._provider(entry)
            return await provider.complete(
                request,
                timeout_s=timeout_s,
                cancel=cancel,
                stream=stream,
                on_stream_event=wrapped_stream_event
                if stream
                else on_stream_event,
            )

        return await self._executor.run(
            call, streamed_output_emitted=lambda: chunks_emitted > 0
        )


__all__ = [
    "CooldownStore",
    "FallbackLLMClient",
    "FallbackProvider",
    "GenericFallbackLLMClient",
    "ModelFallbackConfig",
]
