"""Tests for rate-limit model fallback with cooldown."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pytest
from typing_extensions import override

from penguiflow.llm.errors import LLMInvalidRequestError, LLMRateLimitError
from penguiflow.llm.fallback import (
    CooldownStore,
    FallbackLLMClient,
    FallbackProvider,
    GenericFallbackLLMClient,
    ModelFallbackConfig,
    NativeAdapter,
)
from penguiflow.llm.profiles import ModelProfile
from penguiflow.llm.providers.base import Provider
from penguiflow.llm.types import (
    CompletionResponse,
    LLMMessage,
    LLMRequest,
    TextPart,
    ToolSpec,
    Usage,
)
from penguiflow.planner.models import ReasoningJSONLLMClient

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from penguiflow.llm.protocol import NativeToolCallResult
    from penguiflow.llm.types import CancelToken, StreamCallback


class _FakeClockStore(CooldownStore):
    """CooldownStore with a manually-advanced clock for deterministic tests."""

    def __init__(self) -> None:
        """Start the clock at t=1000."""
        self.t = 1000.0
        super().__init__(clock=lambda: self.t)


_Outcome = str | BaseException | dict[str, str]
"""A scripted reply: text, an error to raise, or a stream-then-raise spec."""

_Script = dict[tuple[str, str | None], list[_Outcome]]
"""Scripted outcomes per ``(model, api_key)``."""


class _FakeAdapter(NativeAdapter, ReasoningJSONLLMClient):
    """Adapter stub that replays a scripted list of outcomes."""

    def __init__(
        self, model: str, api_key: str | None, outcomes: list[_Outcome]
    ) -> None:
        """Replay ``outcomes`` for ``model`` called with ``api_key``."""
        self.model = model
        self.api_key = api_key
        self._outcomes = outcomes
        self.calls = 0

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
        on_reasoning_chunk: Callable[[str, bool], None] | None = None,
    ) -> tuple[str, float]:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, dict):
            # Stream the given text, then fail as a rate limit.
            if on_stream_chunk is not None:
                on_stream_chunk(outcome["stream_then_raise"], False)
            raise LLMRateLimitError(
                message="429", provider="x", status_code=429
            )
        if isinstance(outcome, BaseException):
            raise outcome
        return (outcome, 0.0)

    @override
    async def complete_with_tools(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[ToolSpec],
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
        on_reasoning_chunk: Callable[[str, bool], None] | None = None,
    ) -> NativeToolCallResult:
        msg = "complete_with_tools not expected"
        raise AssertionError(msg)


class _FakeGenericClient(_FakeAdapter):
    pass


class _FakeProvider(Provider):
    def __init__(
        self, model: str, api_key: str | None, outcomes: list[_Outcome]
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._outcomes = outcomes
        self.calls = 0
        self._profile = ModelProfile()

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def profile(self) -> ModelProfile:
        return self._profile

    @property
    def model(self) -> str:
        return self._model

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
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, str)
        return CompletionResponse(
            message=LLMMessage(
                role="assistant", parts=[TextPart(text=outcome)]
            ),
            usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


def _factory(script: _Script) -> Callable[..., _FakeAdapter]:
    """Build an adapter_factory replaying a per-model outcome script."""

    def factory(
        model: str, *, api_key: str | None = None, **_: object
    ) -> _FakeAdapter:
        return _FakeAdapter(
            model, api_key, list(script.get((model, api_key), ["ok"]))
        )

    return factory


def _provider_factory(script: _Script) -> Callable[..., _FakeProvider]:
    def factory(
        model: str, *, api_key: str | None = None, **_: object
    ) -> _FakeProvider:
        return _FakeProvider(
            model, api_key, list(script.get((model, api_key), ["ok"]))
        )

    return factory


_MSG = [{"role": "user", "content": "hi"}]


class TestModelFallbackConfig:
    """Tests for model fallback config."""

    def test_rejects_empty_models(self) -> None:
        """Rejects empty models."""
        with pytest.raises(ValueError):
            ModelFallbackConfig(models=[])

    def test_rejects_empty_api_keys(self) -> None:
        """Rejects empty API keys."""
        with pytest.raises(ValueError):
            ModelFallbackConfig(models=["a"], api_keys=[])

    def test_rejects_non_positive_cooldown(self) -> None:
        """Rejects non positive cooldown."""
        with pytest.raises(ValueError):
            ModelFallbackConfig(models=["a"], cooldown_s=0)

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """From env."""
        monkeypatch.setenv("FB_MODEL_A", "model-a")
        monkeypatch.setenv("FB_MODEL_B", "model-b")
        cfg = ModelFallbackConfig.from_env(
            ["FB_MODEL_A", "FB_MODEL_MISSING", "FB_MODEL_B"]
        )
        assert cfg.models == ["model-a", "model-b"]


class TestCooldownStore:
    """Tests for cooldown store."""

    def test_set_and_check(self) -> None:
        """Set and check."""
        store = _FakeClockStore()
        assert store.in_cooldown(("m", 0)) is False
        store.set_cooldown(("m", 0), 45.0)
        assert store.in_cooldown(("m", 0)) is True
        store.t += 46.0
        assert store.in_cooldown(("m", 0)) is False

    def test_seconds_until_earliest(self) -> None:
        """Seconds until earliest."""
        store = _FakeClockStore()
        store.set_cooldown(("a", 0), 45.0)
        store.set_cooldown(("b", 0), 10.0)
        assert store.seconds_until_earliest(
            [("a", 0), ("b", 0)]
        ) == pytest.approx(10.0)
        # An available entry yields None.
        assert store.seconds_until_earliest([("a", 0), ("c", 0)]) is None


def _generic_factory(script: _Script) -> Callable[..., _FakeGenericClient]:
    """Build a client_factory replaying a per-model outcome script."""

    def factory(
        model: str, *, api_key: str | None = None, **_: object
    ) -> _FakeGenericClient:
        return _FakeGenericClient(
            model, api_key, list(script.get((model, api_key), ["ok"]))
        )

    return factory


class TestFallbackLLMClient:
    """Tests for fallback LLM client."""

    @pytest.mark.asyncio
    async def test_uses_primary_when_healthy(self) -> None:
        """Uses primary when healthy."""
        cfg = ModelFallbackConfig(models=["primary", "backup"])
        client = FallbackLLMClient(
            "primary",
            cfg,
            adapter_factory=_factory({("primary", None): ["from-primary"]}),
        )
        text, _ = await client.complete(messages=_MSG)
        assert text == "from-primary"

    @pytest.mark.asyncio
    async def test_switches_to_fallback_on_429(self) -> None:
        """Switches to fallback on 429."""
        cfg = ModelFallbackConfig(models=["primary", "backup"])
        store = _FakeClockStore()
        client = FallbackLLMClient(
            "primary",
            cfg,
            cooldown_store=store,
            adapter_factory=_factory(
                {
                    ("primary", None): [
                        LLMRateLimitError(message="429", status_code=429)
                    ],
                    ("backup", None): ["from-backup"],
                }
            ),
        )
        text, _ = await client.complete(messages=_MSG)
        assert text == "from-backup"
        assert store.in_cooldown(("primary", 0)) is True
        assert store.in_cooldown(("backup", 0)) is False

    @pytest.mark.asyncio
    async def test_reverts_to_primary_after_cooldown(self) -> None:
        """Reverts to primary after cooldown."""
        cfg = ModelFallbackConfig(
            models=["primary", "backup"], cooldown_s=45.0
        )
        store = _FakeClockStore()
        client = FallbackLLMClient(
            "primary",
            cfg,
            cooldown_store=store,
            adapter_factory=_factory(
                {
                    ("primary", None): [
                        LLMRateLimitError(message="429", status_code=429),
                        "primary-again",
                    ],
                    ("backup", None): ["from-backup"],
                }
            ),
        )
        first, _ = await client.complete(messages=_MSG)
        assert first == "from-backup"
        # Advance past the primary's cooldown — it should be preferred again.
        store.t += 46.0
        second, _ = await client.complete(messages=_MSG)
        assert second == "primary-again"

    @pytest.mark.asyncio
    async def test_rotates_keys_within_model_first(self) -> None:
        """Rotates keys within model first."""
        cfg = ModelFallbackConfig(
            models=["primary", "backup"], api_keys=["k1", "k2"]
        )
        store = _FakeClockStore()
        client = FallbackLLMClient(
            "primary",
            cfg,
            cooldown_store=store,
            adapter_factory=_factory(
                {
                    ("primary", "k1"): [
                        LLMRateLimitError(message="429", status_code=429)
                    ],
                    ("primary", "k2"): ["primary-key2"],
                }
            ),
        )
        text, _ = await client.complete(messages=_MSG)
        # 429 on (primary, k1) rotates to (primary, k2) before any other model.
        assert text == "primary-key2"
        assert store.in_cooldown(("primary", 0)) is True
        assert store.in_cooldown(("primary", 1)) is False

    @pytest.mark.asyncio
    async def test_all_cooling_down_raises(self) -> None:
        """All cooling down raises."""
        cfg = ModelFallbackConfig(models=["primary", "backup"], max_wait_s=0.0)
        client = FallbackLLMClient(
            "primary",
            cfg,
            adapter_factory=_factory(
                {
                    ("primary", None): [
                        LLMRateLimitError(message="429", status_code=429)
                    ],
                    ("backup", None): [
                        LLMRateLimitError(message="429", status_code=429)
                    ],
                }
            ),
        )
        with pytest.raises(LLMRateLimitError):
            await client.complete(messages=_MSG)

    @pytest.mark.asyncio
    async def test_waits_for_cooldown_then_succeeds(self) -> None:
        """Waits for cooldown then succeeds."""
        cfg = ModelFallbackConfig(
            models=["primary"], cooldown_s=0.05, max_wait_s=2.0
        )
        client = FallbackLLMClient(
            "primary",
            cfg,
            adapter_factory=_factory(
                {
                    ("primary", None): [
                        LLMRateLimitError(message="429", status_code=429),
                        "recovered",
                    ]
                }
            ),
        )
        text, _ = await client.complete(messages=_MSG)
        assert text == "recovered"

    @pytest.mark.asyncio
    async def test_midstream_429_propagates(self) -> None:
        """Midstream 429 propagates."""
        cfg = ModelFallbackConfig(models=["primary", "backup"])
        client = FallbackLLMClient(
            "primary",
            cfg,
            adapter_factory=_factory(
                {
                    ("primary", None): [
                        {"stream_then_raise": "partial output"}
                    ],
                    ("backup", None): ["from-backup"],
                }
            ),
        )
        # Chunks already streamed -> cannot transparently switch.
        with pytest.raises(LLMRateLimitError):
            await client.complete(
                messages=_MSG, stream=True, on_stream_chunk=lambda *_: None
            )

    @pytest.mark.asyncio
    async def test_non_429_error_propagates(self) -> None:
        """Non 429 error propagates."""
        cfg = ModelFallbackConfig(models=["primary", "backup"])
        client = FallbackLLMClient(
            "primary",
            cfg,
            adapter_factory=_factory(
                {
                    ("primary", None): [
                        LLMInvalidRequestError(message="bad", status_code=400)
                    ]
                }
            ),
        )
        with pytest.raises(LLMInvalidRequestError):
            await client.complete(messages=_MSG)


class TestGenericFallbackLLMClient:
    """Tests for generic fallback LLM client."""

    @pytest.mark.asyncio
    async def test_switches_litellm_or_dspy_style_client_on_429(self) -> None:
        """Switches LiteLLM or DSPy style client on 429."""
        cfg = ModelFallbackConfig(models=["primary", "backup"])
        client = GenericFallbackLLMClient(
            "primary",
            cfg,
            client_factory=_generic_factory(
                {
                    ("primary", None): [
                        LLMRateLimitError(message="429", status_code=429)
                    ],
                    ("backup", None): ["backup-ok"],
                }
            ),
        )
        result = await client.complete(messages=_MSG)
        assert isinstance(result, tuple)
        text, _ = result
        assert text == "backup-ok"

    @pytest.mark.asyncio
    async def test_streaming_forwards_chunks_and_fails_over(self) -> None:
        # Primary 429s before emitting any chunk -> transparent failover; the
        # backup streams its chunk through the caller's callback.
        """Streaming forwards chunks and fails over."""
        cfg = ModelFallbackConfig(models=["primary", "backup"])
        client = GenericFallbackLLMClient(
            "primary",
            cfg,
            client_factory=_generic_factory(
                {
                    ("primary", None): [
                        LLMRateLimitError(message="429", status_code=429)
                    ],
                    ("backup", None): ["backup-ok"],
                }
            ),
        )
        chunks: list[tuple[str, bool]] = []
        result = await client.complete(
            messages=_MSG,
            stream=True,
            on_stream_chunk=lambda t, d: chunks.append((t, d)),
        )
        assert isinstance(result, tuple)
        text, _ = result
        assert text == "backup-ok"

    @pytest.mark.asyncio
    async def test_mid_stream_429_is_not_replayed(self) -> None:
        # Once output has streamed to the caller, a 429 cannot be transparently
        # retried: the error propagates (no mid-stream replay).
        """Mid stream 429 is not replayed."""
        cfg = ModelFallbackConfig(models=["primary", "backup"])
        client = GenericFallbackLLMClient(
            "primary",
            cfg,
            client_factory=_generic_factory(
                {
                    ("primary", None): [{"stream_then_raise": "partial"}],
                    ("backup", None): ["backup-ok"],
                }
            ),
        )
        seen: list[tuple[str, bool]] = []
        with pytest.raises(LLMRateLimitError):
            await client.complete(
                messages=_MSG,
                stream=True,
                on_stream_chunk=lambda t, d: seen.append((t, d)),
            )
        assert seen == [("partial", False)]


_REQUEST = LLMRequest(
    model="primary",
    messages=(LLMMessage(role="user", parts=[TextPart(text="hi")]),),
)


class TestFallbackProvider:
    """Tests for fallback provider."""

    @pytest.mark.asyncio
    async def test_provider_failover_on_429(self) -> None:
        """Provider failover on 429."""
        cfg = ModelFallbackConfig(models=["primary", "backup"])
        provider = FallbackProvider(
            "primary",
            cfg,
            provider_factory=_provider_factory(
                {
                    ("primary", None): [
                        LLMRateLimitError(message="429", status_code=429)
                    ],
                    ("backup", None): ["backup-response"],
                }
            ),
        )
        response = await provider.complete(_REQUEST)
        assert response.message.text == "backup-response"

    @pytest.mark.asyncio
    async def test_model_reports_active_model_for_pricing(self) -> None:
        # ``provider.model`` drives pricing/telemetry above this seam, so after
        # a failover it must report the model that actually answered, not the
        # primary.
        """Model reports active model for pricing."""
        cfg = ModelFallbackConfig(models=["primary", "backup"])
        provider = FallbackProvider(
            "primary",
            cfg,
            provider_factory=_provider_factory(
                {
                    ("primary", None): [
                        LLMRateLimitError(message="429", status_code=429)
                    ],
                    ("backup", None): ["backup-response"],
                }
            ),
        )
        assert provider.model == "primary"  # before any call
        await provider.complete(_REQUEST)
        assert (
            provider.model == "backup"
        )  # attributed to the model that answered
