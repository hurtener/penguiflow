"""Databricks provider implementation.

Uses the OpenAI SDK for Databricks Foundation Model APIs (January 2026).

Supported model families via Databricks serving endpoints:
- OpenAI GPT-5 series (gpt-5-2, gpt-5-1, gpt-5, gpt-5-mini, gpt-5-nano)
- Anthropic Claude series (claude-opus-4-5, claude-sonnet-4-5,
  claude-haiku-4-5, claude-sonnet-4, claude-opus-4-1)
- Google Gemini series (gemini-3-flash, gemini-3-pro, gemini-2-5-pro,
  gemini-2-5-flash, gemma-3-12b)
- Meta Llama series (llama-4-maverick, meta-llama-3-3-70b-instruct,
  meta-llama-3-1-405b-instruct)
- Alibaba Qwen series (qwen3-next-80b-a3b-instruct)

Reference:
https://docs.databricks.com/en/machine-learning/model-serving/score-foundation-models.html
Databricks SDK: https://github.com/databricks/databricks-sdk-py (v0.77.0+)
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from typing_extensions import override

from ..errors import (
    LLMAuthError,
    LLMCancelledError,
    LLMContextLengthError,
    LLMError,
    LLMInvalidRequestError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    is_context_length_error,
)
from ..profiles import ModelProfile, get_profile
from ..types import (
    CompletionResponse,
    LLMRequest,
    StructuredOutputSpec,
)
from ._params import resolve_temperature
from .base import (
    _REASONING_BLOCK_TYPES,
    OpenAICompatibleProvider,
    OpenAIStreamAccumulator,
    _extract_openai_delta_reasoning,
    _extract_openai_reasoning_content,
    _from_openai_response,
    _summary_texts,
    _text_of,
    _to_openai_messages,
    _to_openai_tools,
)

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from openai.types.chat.chat_completion_chunk import ChoiceDelta

    from ..types import CancelToken, StreamCallback

DatabricksTokenProvider = Callable[[], str | Awaitable[str]]
DatabricksAuthMode = str


logger = logging.getLogger("penguiflow.llm.providers.databricks")


_EFFORT_OFF_VALUES = frozenset({"none", "off", "disabled", "false", "0"})


def _apply_adaptive_effort(
    params: dict[str, Any], reasoning_effort: str, reasoning_display: object
) -> None:
    """Request adaptive thinking with an ``output_config.effort`` level."""
    effort = reasoning_effort.strip().lower()
    if effort in _EFFORT_OFF_VALUES:
        params["thinking"] = {"type": "disabled"}
        return
    if "thinking" not in params:
        thinking: dict[str, Any] = {"type": "adaptive"}
        if reasoning_display in ("summarized", "omitted"):
            thinking["display"] = reasoning_display
        params["thinking"] = thinking
    output_config = params.get("output_config")
    if not isinstance(output_config, dict):
        output_config = {}
    output_config.setdefault("effort", effort)
    params["output_config"] = output_config


def _thinking_budget(
    params: dict[str, Any], thinking: dict[str, Any]
) -> int | None:
    """Return a positive thinking budget, or ``None`` when there is none.

    Enabled thinking without a budget is dropped; non-budgeted modes such as
    ``{"type": "adaptive"}`` are kept.
    """
    budget_tokens = thinking.get("budget_tokens")
    if isinstance(budget_tokens, int) and budget_tokens > 0:
        return budget_tokens
    if str(thinking.get("type", "")).strip().lower() == "enabled":
        params.pop("thinking", None)
    return None


def _default_thinking_max_tokens(
    budget_tokens: int, profile_max: int | None, *, visible_tokens: int
) -> tuple[int, int]:
    """Return ``(budget, max_tokens)`` when the caller set no max_tokens."""
    desired = budget_tokens + visible_tokens
    if profile_max is not None:
        desired = min(desired, profile_max)
    if desired <= budget_tokens:
        desired = budget_tokens + 1
        if profile_max is not None and desired > profile_max:
            budget_tokens = max(profile_max - 1, 0)
            desired = profile_max
    return budget_tokens, desired


class _DatabricksStreamAccumulator(OpenAIStreamAccumulator):
    """Stream accumulator that also reads list-shaped Databricks content."""

    def __init__(
        self,
        on_stream_event: StreamCallback,
        *,
        reasoning_of: Callable[[object], str | None],
        model: str,
    ) -> None:
        super().__init__(on_stream_event, reasoning_of=reasoning_of)
        self._model = model
        self._saw_redacted_reasoning = False

    @override
    def _add_delta(self, delta: ChoiceDelta) -> None:
        content = getattr(delta, "content", None)
        if isinstance(content, list):
            # Reasoning comes from the content blocks, so the generic OpenAI
            # delta extractor is skipped to avoid emitting it twice.
            for item in content:
                self._add_block(item)
            return
        if isinstance(content, str) and content:
            self.add_text(content)
        reasoning = self._reasoning_of(delta)
        if reasoning:
            self.add_reasoning(reasoning)

    def _add_block(self, item: object) -> None:
        if isinstance(item, str):
            if item:
                self.add_text(item)
            return
        if not isinstance(item, dict):
            return
        item_type = item.get("type")
        if item_type in ("text", "output_text"):
            text = _text_of(item.get("text"))
            if text:
                self.add_text(text)
        elif item_type in _REASONING_BLOCK_TYPES:
            self._add_reasoning_block(item)

    def _add_reasoning_block(self, item: dict[str, Any]) -> None:
        summaries = _summary_texts(item.get("summary"))
        for summary in summaries:
            self.add_reasoning(summary)
        if summaries:
            return
        text = _text_of(item.get("text"))
        if text:
            self.add_reasoning(text)
        elif not self._saw_redacted_reasoning:
            # Verified live 2026-06-12 (Opus 4.7 AND 4.8, adaptive thinking):
            # the route returns reasoning blocks whose summary_text is EMPTY
            # with only a cryptographic signature - thinking happens
            # server-side but its content is redacted. Nothing visible to
            # emit; there is no output_config.summary knob (400) to change it.
            self._saw_redacted_reasoning = True
            logger.info(
                "databricks_reasoning_redacted_by_route",
                extra={"model": self._model},
            )


def _thinking_budget_tokens_for_effort(effort: str) -> int:
    """Map reasoning effort tiers to Databricks 'thinking' budget_tokens.

    Databricks docs warn that thinking budgets above 32k may vary; we cap
    at 32k.
    """
    effort_norm = effort.strip().lower()
    if effort_norm in ("low", "minimal"):
        return 4096
    if effort_norm in ("medium", "default"):
        return 16384
    if effort_norm in ("high", "max"):
        return 32768
    # Unknown value: be conservative.
    return 4096


def _extract_databricks_error(exc: Exception) -> str:
    r"""Extract clean error message from Databricks nested JSON format.

    Databricks wraps errors in a DatabricksException with nested JSON:
    {"error_code":"BAD_REQUEST","message":"{\\"message\\":\\"Input is too
    long.\\"}"}
    """
    error_str = str(exc)

    # Try to extract nested JSON message
    # Pattern: {"error_code":"BAD_REQUEST","message":"{"message":"..."}"}
    try:
        json_match = re.search(
            r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', error_str
        )
        if json_match:
            inner_msg = json_match.group(1)
            inner_msg = inner_msg.replace('\\"', '"').replace("\\\\", "\\")

            # Check for double-nested JSON
            inner_json = re.search(
                r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', inner_msg
            )
            if inner_json:
                return inner_json.group(1).replace('\\"', '"')

            return inner_msg
    except Exception:
        pass

    return error_str


class DatabricksProvider(OpenAICompatibleProvider):
    """Databricks provider using OpenAI-compatible API.

    Uses the Databricks Foundation Model APIs which provide an
    OpenAI-compatible interface. Supports structured outputs via constrained
    decoding and function calling (GA as of 2025).

    Available model families (January 2026):
    - OpenAI GPT-5 series: databricks-gpt-5-5-pro, databricks-gpt-5-5,
      databricks-gpt-5-4-mini, databricks-gpt-5-4-nano, databricks-gpt-5-2,
      databricks-gpt-5-1, databricks-gpt-5, databricks-gpt-5-mini,
      databricks-gpt-5-nano, databricks-gpt-oss-120b, databricks-gpt-oss-20b
    - Anthropic Claude: databricks-claude-opus-4-7, databricks-claude-opus-4-5,
      databricks-claude-sonnet-4-5, databricks-claude-haiku-4-5,
      databricks-claude-sonnet-4, databricks-claude-opus-4-1
    - Google Gemini: databricks-gemini-3-5-flash, databricks-gemini-3-flash,
      databricks-gemini-3-pro, databricks-gemini-2-5-pro,
      databricks-gemini-2-5-flash, databricks-gemma-3-12b
    - Meta Llama: databricks-llama-4-maverick,
      databricks-meta-llama-3-3-70b-instruct,
      databricks-meta-llama-3-1-405b-instruct,
      databricks-meta-llama-3-1-8b-instruct
    - Alibaba Qwen: databricks-qwen3-next-80b-a3b-instruct

    Handles Databricks-specific quirks:
    - Nested JSON error messages
    - Reasoning controls vary by model family (reasoning_effort vs thinking
      budget)
    - Schema limitations (no anyOf/oneOf/allOf/$ref/pattern, max 64 keys)
    - Function calling limited to 32 functions, 16 keys per schema

    Reference:
    https://docs.databricks.com/en/machine-learning/model-serving/score-foundation-models.html
    """

    # Maximum limits per Databricks docs
    MAX_SCHEMA_KEYS = 64
    MAX_TOOLS = 32
    MAX_TOOL_SCHEMA_KEYS = 16
    DEFAULT_VISIBLE_OUTPUT_TOKENS_WITH_THINKING = 4096

    @staticmethod
    def _normalize_host_from_host_or_api_base(value: str) -> str:
        """Normalize Databricks host from host or API base alias."""
        raw = value.strip()
        if not raw:
            return ""

        # Accept host-only values and full URLs transparently.
        if not re.match(r"^https?://", raw, flags=re.IGNORECASE):
            raw = f"https://{raw}"

        parsed = urlparse(raw)
        host = parsed.netloc.strip()
        path = (parsed.path or "").rstrip("/")

        # DATABRICKS_API_BASE aliases are often configured as:
        # https://<workspace>/serving-endpoints or
        # https://<workspace>/serving-endpoints/<endpoint>
        if "/serving-endpoints" in path:
            path = path.split("/serving-endpoints", 1)[0]

        if path and path != "/":
            host = f"{host}{path}".strip("/")

        return host.rstrip("/")

    def __init__(
        self,
        model: str,
        *,
        host: str | None = None,
        token: str | None = None,
        api_key: str | None = None,
        token_provider: DatabricksTokenProvider | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        oauth_scopes: str | None = None,
        authorization_details: str | None = None,
        profile: ModelProfile | None = None,
        timeout: float = 360.0,
        client: AsyncOpenAI | None = None,
    ) -> None:
        """Initialize the Databricks provider.

        Args:
            model: Model identifier (e.g.,
                "databricks/databricks-claude-sonnet-4-5").
                   The "databricks/" prefix will be stripped to get the
                   endpoint name.
            host: Databricks workspace host (uses DATABRICKS_HOST or
                DATABRICKS_API_BASE if not provided).
            token: Databricks access token (uses DATABRICKS_TOKEN or
                DATABRICKS_API_KEY if not provided).
                   OAuth tokens are recommended for production use.
            api_key: Alias for token, for consistency with other providers.
            token_provider: Callable that returns a fresh Databricks bearer
                token for each request.
            client_id: Databricks service principal client ID (uses
                DATABRICKS_CLIENT_ID if not provided).
            client_secret: Databricks service principal OAuth secret (uses
                DATABRICKS_CLIENT_SECRET if not provided).
            oauth_scopes: OAuth scopes for service principal auth. Defaults to
                Databricks SDK behavior.
            authorization_details: Optional OAuth authorization details for
                serving endpoints that require them.
            profile: Model profile override.
            timeout: Default timeout in seconds.
            client: Pre-built SDK client to use instead of creating one.
        """
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            msg = (
                "OpenAI SDK not installed. Install with: pip install "
                "openai>=1.50.0"
            )
            raise ImportError(msg) from e

        # Strip "databricks/" prefix if present to get the endpoint name
        self._original_model = model
        if model.startswith("databricks/"):
            self._endpoint = model[len("databricks/") :]
        else:
            self._endpoint = model
        self._model = self._endpoint  # For compatibility with base class

        self._profile = profile or get_profile(model)
        self._timeout = timeout

        host = (
            host
            or os.environ.get("DATABRICKS_HOST")
            or os.environ.get("DATABRICKS_API_BASE")
        )
        static_token = token or api_key
        client_id = client_id or os.environ.get("DATABRICKS_CLIENT_ID")
        client_secret = client_secret or os.environ.get(
            "DATABRICKS_CLIENT_SECRET"
        )
        oauth_scopes = (
            oauth_scopes
            or os.environ.get("DATABRICKS_OAUTH_SCOPES")
            or os.environ.get("DATABRICKS_SCOPES")
        )
        authorization_details = authorization_details or os.environ.get(
            "DATABRICKS_AUTHORIZATION_DETAILS"
        )

        if not host or not self._has_auth_config(
            static_token, token_provider, client_id, client_secret
        ):
            msg = (
                "Databricks host and auth required. Set DATABRICKS_HOST or "
                "DATABRICKS_API_BASE, "
                "plus one of DATABRICKS_TOKEN/DATABRICKS_API_KEY or "
                "DATABRICKS_CLIENT_ID/DATABRICKS_CLIENT_SECRET, or pass host "
                "and token/api_key, "
                "token_provider, or client_id/client_secret explicitly."
            )
            raise ValueError(msg)

        # Normalize host from either direct host or API base alias.
        host = self._normalize_host_from_host_or_api_base(host)
        self._host = host
        self._workspace_url = f"https://{host}"
        self._static_token = static_token
        self._token_provider = token_provider
        self._client_id = client_id
        self._client_secret = client_secret
        self._oauth_scopes = oauth_scopes
        self._authorization_details = authorization_details
        self._databricks_config: Any | None = None
        self._auth_mode: DatabricksAuthMode
        if token_provider is not None:
            self._auth_mode = "token_provider"
        elif client_id and client_secret:
            self._auth_mode = "service_principal"
        else:
            self._auth_mode = "env_token"

        # Databricks native API endpoint.
        #
        # Important: Keep a trailing slash so relative path joins (e.g.
        # "invocations") do not drop the endpoint segment (urljoin semantics).
        #
        # Format: https://{host}/serving-endpoints/{endpoint}/invocations
        base_url = f"https://{host}/serving-endpoints/{self._endpoint}/"

        api_key_arg: str | DatabricksTokenProvider
        if static_token is not None:
            api_key_arg = static_token
        else:
            api_key_arg = self._resolve_auth_token

        self._client = (
            client
            if client is not None
            else AsyncOpenAI(
                api_key=api_key_arg,
                base_url=base_url,
                timeout=timeout,
            )
        )

    @property
    def endpoint(self) -> str:
        """The serving endpoint name, without the ``databricks/`` prefix."""
        return self._endpoint

    @property
    def original_model(self) -> str:
        """The model identifier as passed to the constructor."""
        return self._original_model

    @property
    @override
    def provider_name(self) -> str:
        return "databricks"

    @property
    @override
    def profile(self) -> ModelProfile:
        return self._profile

    @property
    @override
    def model(self) -> str:
        return self._model

    @property
    def client(self) -> AsyncOpenAI:
        """The SDK client the provider sends requests with."""
        return self._client

    @property
    def timeout(self) -> float:
        """The default request timeout, in seconds."""
        return self._timeout

    @staticmethod
    def _has_auth_config(
        static_token: str | None,
        token_provider: DatabricksTokenProvider | None,
        client_id: str | None,
        client_secret: str | None,
    ) -> bool:
        """Return whether auth material exists now or can be resolved later."""
        return bool(
            static_token
            or token_provider
            or os.environ.get("DATABRICKS_TOKEN")
            or os.environ.get("DATABRICKS_API_KEY")
            or (client_id and client_secret)
        )

    async def _resolve_auth_token(self) -> str:
        """Resolve a fresh bearer token for long-lived provider instances."""
        if self._auth_mode == "token_provider":
            if self._token_provider is None:
                msg = (
                    "Databricks token_provider auth mode is missing a "
                    "token_provider"
                )
                raise ValueError(msg)
            token = self._token_provider()
            if inspect.isawaitable(token):
                token = await token
            if not isinstance(token, str) or not token:
                msg = "Databricks token_provider returned an empty token"
                raise ValueError(msg)
            return token

        if self._auth_mode == "service_principal":
            return await self._resolve_service_principal_token()

        env_token = os.environ.get("DATABRICKS_TOKEN") or os.environ.get(
            "DATABRICKS_API_KEY"
        )
        if env_token:
            return env_token

        msg = (
            "Databricks auth token unavailable. Set "
            "DATABRICKS_TOKEN/DATABRICKS_API_KEY "
            "or DATABRICKS_CLIENT_ID/DATABRICKS_CLIENT_SECRET."
        )
        raise ValueError(msg)

    async def _resolve_service_principal_token(self) -> str:
        """Get or refresh a service-principal OAuth token via the SDK."""
        if self._databricks_config is None:
            try:
                from databricks.sdk.config import Config
            except ImportError as e:
                msg = (
                    "Databricks service-principal auth requires "
                    "databricks-sdk. "
                    "Install with: pip install databricks-sdk>=0.77.0"
                )
                raise ImportError(msg) from e

            config_kwargs: dict[str, Any] = {
                "host": self._workspace_url,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            }
            if self._oauth_scopes:
                config_kwargs["scopes"] = self._oauth_scopes
            if self._authorization_details:
                config_kwargs["authorization_details"] = (
                    self._authorization_details
                )
            self._databricks_config = Config(**config_kwargs)

        headers = await asyncio.to_thread(self._databricks_config.authenticate)
        authorization = str(headers.get("Authorization", ""))
        if not authorization.lower().startswith("bearer "):
            msg = "Databricks SDK did not return a bearer Authorization header"
            raise ValueError(msg)
        token = authorization[len("Bearer ") :].strip()
        if not token:
            msg = "Databricks SDK returned an empty bearer token"
            raise ValueError(msg)
        return token

    def validate_request(self, request: LLMRequest) -> None:
        """Validate request against Databricks limits."""
        if request.tools and len(request.tools) > self.MAX_TOOLS:
            raise LLMInvalidRequestError(
                message=(
                    f"Databricks supports max {self.MAX_TOOLS} tools, got "
                    f"{len(request.tools)}"
                ),
                provider="databricks",
            )

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
        """Execute a completion request."""
        from openai.types.chat import ChatCompletion

        if cancel and cancel.is_cancelled():
            raise LLMCancelledError(
                message="Request cancelled",
                provider="databricks",
                retryable=False,
            )

        self.validate_request(request)
        params = self.build_params(request)
        timeout = timeout_s or self._timeout

        try:
            if stream and on_stream_event:
                # Databricks Model Serving rejects structured output with
                # streaming:
                # 400 INVALID_PARAMETER_VALUE: "Structured output is not
                # currently supported with streaming."
                #
                # LiteLLM users still expect streaming to work for JSON
                # outputs, so we drop response_format during streaming and
                # instead provide best-effort prompt guidance to emit JSON
                # (schema-guided if available).
                if (
                    request.structured_output is not None
                    and "response_format" in params
                ):
                    params = dict(params)
                    params.pop("response_format", None)
                    schema = request.structured_output.json_schema
                    schema_json = json.dumps(
                        schema, ensure_ascii=False, separators=(",", ":")
                    )
                    guidance = (
                        "Return a single valid JSON object that matches this "
                        "JSON Schema:\n"
                        f"{schema_json}\n"
                        "Respond with JSON only."
                    )
                    params["messages"] = [
                        {"role": "system", "content": guidance},
                        *list(params["messages"]),
                    ]

                try:
                    async with asyncio.timeout(timeout):
                        return await self.stream_completion(
                            params, on_stream_event, cancel
                        )
                except TimeoutError as e:
                    raise LLMTimeoutError(
                        message=f"Stream timed out after {timeout}s",
                        provider="databricks",
                        raw=e,
                    ) from e

            # Use client.post() directly to hit invocations endpoint
            # (chat.completions.create() would append /chat/completions which
            # Databricks doesn't support)
            async with asyncio.timeout(timeout):
                response = await self._client.post(
                    "invocations",
                    body=params,
                    cast_to=ChatCompletion,
                )

            message, usage = _from_openai_response(response)

            return CompletionResponse(
                message=message,
                usage=usage,
                raw_response=response,
                reasoning_content=_extract_openai_reasoning_content(
                    response.choices[0].message
                ),
                finish_reason=response.choices[0].finish_reason,
            )

        except TimeoutError as e:
            raise LLMTimeoutError(
                message=f"Request timed out after {timeout}s",
                provider="databricks",
                raw=e,
            ) from e
        except asyncio.CancelledError:
            raise LLMCancelledError(
                message="Request cancelled", provider="databricks"
            ) from None
        except Exception as e:
            raise self.map_error(e) from e

    async def stream_completion(
        self,
        params: dict[str, Any],
        on_stream_event: StreamCallback,
        cancel: CancelToken | None,
    ) -> CompletionResponse:
        """Handle streaming completion."""
        from openai import AsyncStream
        from openai.types.chat import ChatCompletionChunk

        params["stream"] = True
        stream_options = dict(params.get("stream_options") or {})
        stream_options.setdefault("include_usage", True)
        params["stream_options"] = stream_options
        accumulator = _DatabricksStreamAccumulator(
            on_stream_event,
            reasoning_of=_extract_openai_delta_reasoning,
            model=self._model,
        )

        # Use client.post() directly to hit invocations endpoint with
        # streaming
        stream: AsyncStream[ChatCompletionChunk] = await self._client.post(
            "invocations",
            body=params,
            cast_to=ChatCompletionChunk,
            stream=True,
            stream_cls=AsyncStream[ChatCompletionChunk],
        )
        async for chunk in stream:
            if cancel and cancel.is_cancelled():
                raise LLMCancelledError(
                    message="Request cancelled",
                    provider="databricks",
                    retryable=False,
                )
            accumulator.add(chunk)

        return accumulator.response()

    def build_params(self, request: LLMRequest) -> dict[str, Any]:
        """Build Databricks API parameters from request."""
        params: dict[str, Any] = {
            "model": self._model,
            "messages": _to_openai_messages(request.messages),
        }

        temp = resolve_temperature(
            self._profile,
            request.temperature,
            model=self._model,
            forced_off=self.temperature_unsupported,
        )
        if temp is not None:
            params["temperature"] = temp

        if request.max_tokens is not None:
            params["max_tokens"] = request.max_tokens

        if request.tools:
            params["tools"] = _to_openai_tools(request.tools)

        if request.tool_choice:
            params["tool_choice"] = {
                "type": "function",
                "function": {"name": request.tool_choice},
            }

        # Handle structured output via constrained decoding
        if request.structured_output:
            params["response_format"] = self._to_databricks_response_format(
                request.structured_output
            )

        # Handle extra parameters with Databricks-specific sanitization/mapping
        if request.extra:
            extra = dict(request.extra)

            reasoning_effort = extra.pop("reasoning_effort", None)
            reasoning_display = extra.pop("reasoning_display", None)
            if isinstance(reasoning_effort, str) and reasoning_effort:
                self._apply_reasoning_effort(
                    params,
                    reasoning_effort,
                    reasoning_display=reasoning_display,
                    explicit_thinking="thinking" in extra,
                )

            for param in self._profile.unsupported_request_params:
                extra.pop(param, None)
            params.update(extra)

        self.ensure_thinking_budget_and_max_tokens(
            params,
            request_max_tokens=request.max_tokens,
        )
        return params

    def _apply_reasoning_effort(
        self,
        params: dict[str, Any],
        reasoning_effort: str,
        *,
        reasoning_display: object,
        explicit_thinking: bool,
    ) -> None:
        """Express ``reasoning_effort`` in this model's request style."""
        style = self._resolve_reasoning_request_style()
        if style == "adaptive_effort" and not explicit_thinking:
            # Adaptive thinking + output_config.effort (Claude Opus 4.7/4.8).
            _apply_adaptive_effort(params, reasoning_effort, reasoning_display)
        elif style == "thinking_budget":
            # Thinking budget (older Claude models and Gemini 2.5). Respect
            # an explicit thinking config if the caller provided one.
            effort = reasoning_effort.strip().lower()
            if (
                not explicit_thinking
                and "thinking" not in params
                and effort not in _EFFORT_OFF_VALUES
            ):
                budget_tokens = _thinking_budget_tokens_for_effort(effort)
                if budget_tokens > 0:
                    params["thinking"] = {
                        "type": "enabled",
                        "budget_tokens": budget_tokens,
                    }
        elif style == "reasoning_effort":
            # Direct passthrough (GPT OSS, Gemini 3, GPT-5 family; allowed
            # values vary per model).
            params["reasoning_effort"] = reasoning_effort

    def _profile_max_output_tokens(self) -> int | None:
        """Return the profile's positive ``max_output_tokens``, if any."""
        profile_max = getattr(self._profile, "max_output_tokens", None)
        if isinstance(profile_max, int) and profile_max > 0:
            return profile_max
        return None

    def ensure_thinking_budget_and_max_tokens(
        self,
        params: dict[str, Any],
        *,
        request_max_tokens: int | None,
    ) -> None:
        """Ensure Databricks thinking budgets satisfy max_tokens constraints.

        Databricks (and upstream Claude/Gemini semantics) require:
        - thinking.budget_tokens < max_tokens
        If the caller doesn't set max_tokens, Databricks may default to a low
        value, which can break extended thinking.
        """
        thinking = params.get("thinking")
        if not isinstance(thinking, dict):
            return

        budget_tokens = _thinking_budget(params, thinking)
        if budget_tokens is None:
            return

        max_tokens = params.get("max_tokens")
        profile_max_int = self._profile_max_output_tokens()

        if not isinstance(max_tokens, int) or max_tokens <= 0:
            # Without max_tokens, choose a default that leaves room for
            # visible output in addition to internal thinking.
            budget_tokens, desired = _default_thinking_max_tokens(
                budget_tokens,
                profile_max_int,
                visible_tokens=self.DEFAULT_VISIBLE_OUTPUT_TOKENS_WITH_THINKING,
            )
            if budget_tokens > 0:
                params["max_tokens"] = desired
        elif max_tokens > budget_tokens:
            return
        elif request_max_tokens is not None:
            # The caller set max_tokens <= budget. Their intent is typically
            # "up to max_tokens of visible output" plus up to budget_tokens
            # of thinking; Databricks requires max_tokens > budget_tokens.
            desired = max_tokens + budget_tokens
            if profile_max_int is not None:
                desired = min(desired, profile_max_int)
            params["max_tokens"] = desired
            if desired > budget_tokens:
                return
            budget_tokens = max(desired - 1, 0)
        else:
            # Shrink the budget to fit under max_tokens.
            budget_tokens = max(max_tokens - 1, 0)

        if budget_tokens <= 0:
            params.pop("thinking", None)
            return
        thinking["budget_tokens"] = budget_tokens

    _REASONING_REQUEST_STYLES = (
        "adaptive_effort",
        "thinking_budget",
        "reasoning_effort",
    )

    def _resolve_reasoning_request_style(self) -> str | None:
        """Resolve how a reasoning-effort request is expressed for this model.

        The model profile is authoritative
        (``ModelProfile.reasoning_request_style``); model-name heuristics are
        the fallback for unprofiled models. Unknown profile values fall back to
        the heuristics rather than failing.
        """
        style = getattr(self._profile, "reasoning_request_style", None)
        if isinstance(style, str) and style in self._REASONING_REQUEST_STYLES:
            return style

        model_id = self._model
        # Claude Opus 4.7+ rejects thinking budgets ("thinking.type.enabled is
        # not supported for this model") in favour of adaptive thinking.
        if model_id.startswith(
            ("databricks-claude-opus-4-7", "databricks-claude-opus-4-8")
        ):
            return "adaptive_effort"
        if model_id.startswith(
            ("databricks-claude-", "databricks-gemini-2-5-")
        ):
            return "thinking_budget"
        if model_id.startswith(
            ("databricks-gpt-oss-", "databricks-gemini-3-", "databricks-gpt-5")
        ):
            return "reasoning_effort"
        return None

    def _to_databricks_response_format(
        self, structured_output: StructuredOutputSpec
    ) -> dict[str, Any]:
        """Convert structured output spec to Databricks response_format."""
        return {
            "type": "json_schema",
            "json_schema": {
                "name": structured_output.name,
                "schema": structured_output.json_schema,
                "strict": structured_output.strict,
            },
        }

    def map_error(self, exc: Exception) -> LLMError:
        """Map Databricks/OpenAI SDK exceptions to LLMError."""
        # Try to extract nested error message
        clean_message = _extract_databricks_error(exc)

        try:
            from openai import (
                APIConnectionError,
                APIStatusError,
                AuthenticationError,
                BadRequestError,
                RateLimitError,
            )

            if isinstance(exc, AuthenticationError):
                return LLMAuthError(
                    message=clean_message,
                    provider="databricks",
                    status_code=401,
                    raw=exc,
                )

            if isinstance(exc, RateLimitError):
                return LLMRateLimitError(
                    message=clean_message,
                    provider="databricks",
                    status_code=429,
                    raw=exc,
                )

            if isinstance(exc, BadRequestError):
                if is_context_length_error(exc) or is_context_length_error(
                    clean_message
                ):
                    return LLMContextLengthError(
                        message=clean_message,
                        provider="databricks",
                        status_code=400,
                        raw=exc,
                    )
                return LLMInvalidRequestError(
                    message=clean_message,
                    provider="databricks",
                    status_code=400,
                    raw=exc,
                )

            if isinstance(exc, APIStatusError):
                status = getattr(exc, "status_code", 500)
                if status >= 500:
                    return LLMServerError(
                        message=clean_message,
                        provider="databricks",
                        status_code=status,
                        raw=exc,
                    )
                return LLMInvalidRequestError(
                    message=clean_message,
                    provider="databricks",
                    status_code=status,
                    raw=exc,
                )

            if isinstance(exc, APIConnectionError):
                return LLMServerError(
                    message=clean_message,
                    provider="databricks",
                    raw=exc,
                )

        except ImportError:
            pass

        # Check for context length in extracted message
        if is_context_length_error(clean_message):
            return LLMContextLengthError(
                message=clean_message,
                provider="databricks",
                raw=exc,
            )

        return LLMError(
            message=clean_message,
            provider="databricks",
            raw=exc,
        )
