"""Databricks provider implementation.

Uses the OpenAI SDK for Databricks Foundation Model APIs (January 2026).

Supported model families via Databricks serving endpoints:
- OpenAI GPT-5 series (gpt-5-2, gpt-5-1, gpt-5, gpt-5-mini, gpt-5-nano)
- Anthropic Claude series (claude-opus-4-5, claude-sonnet-4-5, claude-haiku-4-5, claude-sonnet-4, claude-opus-4-1)
- Google Gemini series (gemini-3-flash, gemini-3-pro, gemini-2-5-pro, gemini-2-5-flash, gemma-3-12b)
- Meta Llama series (llama-4-maverick, meta-llama-3-3-70b-instruct, meta-llama-3-1-405b-instruct)
- Alibaba Qwen series (qwen3-next-80b-a3b-instruct)

Reference: https://docs.databricks.com/en/machine-learning/model-serving/score-foundation-models.html
Databricks SDK: https://github.com/databricks/databricks-sdk-py (v0.77.0+)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import TYPE_CHECKING, Any

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
    ContentPart,
    LLMMessage,
    LLMRequest,
    StreamEvent,
    StructuredOutputSpec,
    TextPart,
    ToolCallPart,
    Usage,
)
from .base import OpenAICompatibleProvider

if TYPE_CHECKING:
    from ..types import CancelToken, StreamCallback


class DatabricksProvider(OpenAICompatibleProvider):
    """Databricks provider using OpenAI-compatible API.

    Uses the Databricks Foundation Model APIs which provide an OpenAI-compatible
    interface. Supports structured outputs via constrained decoding and function
    calling (GA as of 2025).

    Available model families (January 2026):
    - OpenAI GPT-5 series: databricks-gpt-5-2, databricks-gpt-5-1, databricks-gpt-5,
      databricks-gpt-5-mini, databricks-gpt-5-nano, databricks-gpt-oss-120b, databricks-gpt-oss-20b
    - Anthropic Claude: databricks-claude-opus-4-5, databricks-claude-sonnet-4-5,
      databricks-claude-haiku-4-5, databricks-claude-sonnet-4, databricks-claude-opus-4-1
    - Google Gemini: databricks-gemini-3-flash, databricks-gemini-3-pro,
      databricks-gemini-2-5-pro, databricks-gemini-2-5-flash, databricks-gemma-3-12b
    - Meta Llama: databricks-llama-4-maverick, databricks-meta-llama-3-3-70b-instruct,
      databricks-meta-llama-3-1-405b-instruct, databricks-meta-llama-3-1-8b-instruct
    - Alibaba Qwen: databricks-qwen3-next-80b-a3b-instruct

    Handles Databricks-specific quirks:
    - Nested JSON error messages
    - Reasoning controls vary by model family (reasoning_effort vs thinking budget)
    - Schema limitations (no anyOf/oneOf/allOf/$ref/pattern, max 64 keys)
    - Function calling limited to 32 functions, 16 keys per schema

    Reference: https://docs.databricks.com/en/machine-learning/model-serving/score-foundation-models.html
    """

    # Maximum limits per Databricks docs
    MAX_SCHEMA_KEYS = 64
    MAX_TOOLS = 32
    MAX_TOOL_SCHEMA_KEYS = 16
    DEFAULT_VISIBLE_OUTPUT_TOKENS_WITH_THINKING = 4096

    def __init__(
        self,
        model: str,
        *,
        host: str | None = None,
        token: str | None = None,
        profile: ModelProfile | None = None,
        timeout: float = 120.0,
    ):
        """Initialize the Databricks provider.

        Args:
            model: Model identifier (e.g., "databricks/databricks-claude-sonnet-4-5").
                   The "databricks/" prefix will be stripped to get the endpoint name.
            host: Databricks workspace host (uses DATABRICKS_HOST env var if not provided).
            token: Databricks access token (uses DATABRICKS_TOKEN env var if not provided).
                   OAuth tokens are recommended for production use.
            profile: Model profile override.
            timeout: Default timeout in seconds.
        """
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise ImportError("OpenAI SDK not installed. Install with: pip install openai>=1.50.0") from e

        # Strip "databricks/" prefix if present to get the endpoint name
        self._original_model = model
        if model.startswith("databricks/"):
            self._endpoint = model[len("databricks/") :]
        else:
            self._endpoint = model
        self._model = self._endpoint  # For compatibility with base class

        self._profile = profile or get_profile(model)
        self._timeout = timeout

        host = host or os.environ.get("DATABRICKS_HOST")
        token = token or os.environ.get("DATABRICKS_TOKEN")

        if not host or not token:
            raise ValueError(
                "Databricks host and token required. Set DATABRICKS_HOST and "
                "DATABRICKS_TOKEN environment variables or pass explicitly."
            )

        # Normalize host
        if host.startswith("https://"):
            host = host[8:]
        if host.startswith("http://"):
            host = host[7:]
        host = host.rstrip("/")

        # Databricks native API endpoint.
        #
        # Important: Keep a trailing slash so relative path joins (e.g. "invocations")
        # do not drop the endpoint segment (urljoin semantics).
        #
        # Format: https://{host}/serving-endpoints/{endpoint}/invocations
        base_url = f"https://{host}/serving-endpoints/{self._endpoint}/"

        self._client = AsyncOpenAI(
            api_key=token,
            base_url=base_url,
            timeout=timeout,
        )

    @property
    def provider_name(self) -> str:
        return "databricks"

    @property
    def profile(self) -> ModelProfile:
        return self._profile

    @property
    def model(self) -> str:
        return self._model

    def validate_request(self, request: LLMRequest) -> None:
        """Validate request against Databricks limits."""
        if request.tools and len(request.tools) > self.MAX_TOOLS:
            raise LLMInvalidRequestError(
                message=f"Databricks supports max {self.MAX_TOOLS} tools, got {len(request.tools)}",
                provider="databricks",
            )

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
            raise LLMCancelledError(message="Request cancelled", provider="databricks")

        self.validate_request(request)
        params = self._build_params(request)
        timeout = timeout_s or self._timeout

        try:
            if stream and on_stream_event:
                # Databricks Model Serving rejects structured output with streaming:
                # 400 INVALID_PARAMETER_VALUE: "Structured output is not currently supported with streaming."
                #
                # LiteLLM users still expect streaming to work for JSON outputs, so we
                # drop response_format during streaming and instead provide best-effort
                # prompt guidance to emit JSON (schema-guided if available).
                if request.structured_output is not None and "response_format" in params:
                    params = dict(params)
                    params.pop("response_format", None)
                    schema = request.structured_output.json_schema
                    schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
                    guidance = (
                        "Return a single valid JSON object that matches this JSON Schema:\n"
                        f"{schema_json}\n"
                        "Respond with JSON only."
                    )
                    params["messages"] = [{"role": "system", "content": guidance}] + list(params["messages"])

                return await self._stream_completion(params, on_stream_event, timeout, cancel)

            # Use client.post() directly to hit invocations endpoint
            # (chat.completions.create() would append /chat/completions which Databricks doesn't support)
            async with asyncio.timeout(timeout):
                response = await self._client.post(
                    "invocations",
                    body=params,
                    cast_to=ChatCompletion,
                )

            message, usage = self._from_openai_response(response)

            return CompletionResponse(
                message=message,
                usage=usage,
                raw_response=response,
                reasoning_content=self._extract_openai_reasoning_content(response.choices[0].message),
                finish_reason=response.choices[0].finish_reason,
            )

        except TimeoutError as e:
            raise LLMTimeoutError(
                message=f"Request timed out after {timeout}s",
                provider="databricks",
                raw=e,
            ) from e
        except asyncio.CancelledError:
            raise LLMCancelledError(message="Request cancelled", provider="databricks") from None
        except Exception as e:
            raise self._map_error(e) from e

    async def _stream_completion(
        self,
        params: dict[str, Any],
        on_stream_event: StreamCallback,
        timeout: float,
        cancel: CancelToken | None,
    ) -> CompletionResponse:
        """Handle streaming completion."""
        from openai import AsyncStream
        from openai.types.chat import ChatCompletionChunk

        params["stream"] = True
        stream_options = dict(params.get("stream_options") or {})
        stream_options.setdefault("include_usage", True)
        params["stream_options"] = stream_options

        text_acc: list[str] = []
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        usage: Usage | None = None
        finish_reason: str | None = None
        reasoning_acc: list[str] = []

        try:
            async with asyncio.timeout(timeout):
                # Use client.post() directly to hit invocations endpoint with streaming
                stream: AsyncStream[ChatCompletionChunk] = await self._client.post(
                    "invocations",
                    body=params,
                    cast_to=ChatCompletionChunk,
                    stream=True,
                    stream_cls=AsyncStream[ChatCompletionChunk],
                )
                async for chunk in stream:
                    if cancel and cancel.is_cancelled():
                        raise LLMCancelledError(message="Request cancelled", provider="databricks")

                    if not chunk.choices:
                        # Usage chunk at the end (if supported)
                        if hasattr(chunk, "usage") and chunk.usage:
                            usage = Usage(
                                input_tokens=chunk.usage.prompt_tokens,
                                output_tokens=chunk.usage.completion_tokens,
                                total_tokens=chunk.usage.total_tokens,
                            )
                        continue

                    delta = chunk.choices[0].delta
                    finish_reason = chunk.choices[0].finish_reason

                    # Handle text content
                    delta_content = getattr(delta, "content", None)
                    if isinstance(delta_content, str) and delta_content:
                        text_acc.append(delta_content)
                        on_stream_event(StreamEvent(delta_text=delta_content))
                    elif isinstance(delta_content, list):
                        for item in delta_content:
                            if isinstance(item, str) and item:
                                text_acc.append(item)
                                on_stream_event(StreamEvent(delta_text=item))
                                continue
                            if not isinstance(item, dict):
                                continue
                            item_type = item.get("type")
                            item_text = item.get("text")
                            if item_type in ("text", "output_text") and isinstance(item_text, str) and item_text:
                                text_acc.append(item["text"])
                                on_stream_event(StreamEvent(delta_text=item["text"]))
                            elif item_type in ("reasoning", "thinking", "thought"):
                                summary = item.get("summary")
                                if isinstance(summary, list):
                                    for s in summary:
                                        if isinstance(s, dict) and isinstance(s.get("text"), str) and s["text"]:
                                            reasoning_acc.append(s["text"])
                                            on_stream_event(StreamEvent(delta_reasoning=s["text"]))
                                elif isinstance(item.get("text"), str) and item["text"]:
                                    reasoning_acc.append(item["text"])
                                    on_stream_event(StreamEvent(delta_reasoning=item["text"]))

                    # If we already parsed reasoning blocks from a list-shaped delta content,
                    # don't double-emit via the generic OpenAI delta extractor.
                    if not isinstance(delta_content, list):
                        delta_reasoning = self._extract_openai_delta_reasoning(delta)
                        if delta_reasoning:
                            reasoning_acc.append(delta_reasoning)
                            on_stream_event(StreamEvent(delta_reasoning=delta_reasoning))

                    # Handle tool calls
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {
                                    "id": tc.id or "",
                                    "name": "",
                                    "arguments": "",
                                }
                            if tc.function:
                                if tc.function.name:
                                    tool_calls_acc[idx]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls_acc[idx]["arguments"] += tc.function.arguments

        except TimeoutError as e:
            raise LLMTimeoutError(
                message=f"Stream timed out after {timeout}s",
                provider="databricks",
                raw=e,
            ) from e

        # Build final message
        parts: list[ContentPart] = []
        full_text = "".join(text_acc)
        if full_text:
            parts.append(TextPart(text=full_text))

        for idx in sorted(tool_calls_acc.keys()):
            tc_data = tool_calls_acc[idx]
            parts.append(
                ToolCallPart(
                    name=tc_data["name"],
                    arguments_json=tc_data["arguments"],
                    call_id=tc_data["id"],
                )
            )

        on_stream_event(StreamEvent(done=True, usage=usage, finish_reason=finish_reason))

        return CompletionResponse(
            message=LLMMessage(role="assistant", parts=parts),
            usage=usage or Usage.zero(),
            raw_response=None,
            reasoning_content="".join(reasoning_acc) or None,
            finish_reason=finish_reason,
        )

    def _build_params(self, request: LLMRequest) -> dict[str, Any]:
        """Build Databricks API parameters from request."""
        params: dict[str, Any] = {
            "model": self._model,
            "messages": self._to_openai_messages(request.messages),
            "temperature": request.temperature,
        }

        if request.max_tokens is not None:
            params["max_tokens"] = request.max_tokens

        if request.tools:
            params["tools"] = self._to_openai_tools(request.tools)

        if request.tool_choice:
            params["tool_choice"] = {
                "type": "function",
                "function": {"name": request.tool_choice},
            }

        # Handle structured output via constrained decoding
        if request.structured_output:
            params["response_format"] = self._to_databricks_response_format(request.structured_output)

        # Handle extra parameters with Databricks-specific sanitization/mapping
        if request.extra:
            extra = dict(request.extra)

            reasoning_effort = extra.pop("reasoning_effort", None)
            if isinstance(reasoning_effort, str) and reasoning_effort:
                model_id = self._model

                # Claude + Gemini 2.5 use a "thinking" budget (hybrid reasoning).
                if model_id.startswith("databricks-claude-") or model_id.startswith("databricks-gemini-2-5-"):
                    # Respect an explicit thinking config if the caller provided one.
                    if "thinking" not in extra and "thinking" not in params:
                        effort = reasoning_effort.strip().lower()
                        if effort not in ("none", "off", "disabled", "false", "0"):
                            budget_tokens = self._thinking_budget_tokens_for_effort(effort)
                            if budget_tokens > 0:
                                params["thinking"] = {"type": "enabled", "budget_tokens": budget_tokens}

                # GPT OSS + Gemini 3 accept reasoning_effort directly.
                elif model_id.startswith("databricks-gpt-oss-") or model_id.startswith("databricks-gemini-3-"):
                    params["reasoning_effort"] = reasoning_effort

                # GPT-5 family also accepts reasoning_effort, but the allowed values vary;
                # pass through as-is when supplied.
                elif model_id.startswith("databricks-gpt-5"):
                    params["reasoning_effort"] = reasoning_effort

            params.update(extra)

        self._ensure_thinking_budget_and_max_tokens(
            params,
            request_max_tokens=request.max_tokens,
        )
        return params

    def _ensure_thinking_budget_and_max_tokens(
        self,
        params: dict[str, Any],
        *,
        request_max_tokens: int | None,
    ) -> None:
        """Ensure Databricks thinking budgets satisfy max_tokens constraints.

        Databricks (and upstream Claude/Gemini semantics) require:
        - thinking.budget_tokens < max_tokens
        If the caller doesn't set max_tokens, Databricks may default to a low value,
        which can break extended thinking.
        """
        thinking = params.get("thinking")
        if not isinstance(thinking, dict):
            return

        budget_tokens = thinking.get("budget_tokens")
        if not isinstance(budget_tokens, int) or budget_tokens <= 0:
            params.pop("thinking", None)
            return

        max_tokens = params.get("max_tokens")
        profile_max = getattr(self._profile, "max_output_tokens", None)
        profile_max_int = profile_max if isinstance(profile_max, int) and profile_max > 0 else None

        # If max_tokens isn't provided, choose a sensible default that leaves room
        # for visible output in addition to internal thinking.
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            desired = budget_tokens + self.DEFAULT_VISIBLE_OUTPUT_TOKENS_WITH_THINKING
            if profile_max_int is not None:
                desired = min(desired, profile_max_int)
            if desired <= budget_tokens:
                desired = budget_tokens + 1
                if profile_max_int is not None and desired > profile_max_int:
                    budget_tokens = max(profile_max_int - 1, 0)
                    desired = profile_max_int
            if budget_tokens <= 0:
                params.pop("thinking", None)
                return
            thinking["budget_tokens"] = budget_tokens
            params["max_tokens"] = desired
            return

        # If the caller set max_tokens but it's <= budget, shrink budget to fit.
        if max_tokens <= budget_tokens:
            # User intent is typically "give me up to max_tokens of visible output"
            # plus up to budget_tokens of internal thinking; Databricks enforces the
            # constraint by requiring max_tokens > budget_tokens.
            if request_max_tokens is not None:
                desired = max_tokens + budget_tokens
                if profile_max_int is not None:
                    desired = min(desired, profile_max_int)
                params["max_tokens"] = desired
                if desired <= budget_tokens:
                    budget_tokens = max(desired - 1, 0)
                    if budget_tokens <= 0:
                        params.pop("thinking", None)
                        return
                    thinking["budget_tokens"] = budget_tokens
            else:
                budget_tokens = max(max_tokens - 1, 0)
                if budget_tokens <= 0:
                    params.pop("thinking", None)
                    return
                thinking["budget_tokens"] = budget_tokens

    def _thinking_budget_tokens_for_effort(self, effort: str) -> int:
        """Map reasoning effort tiers to Databricks 'thinking' budget_tokens.

        Databricks docs warn that thinking budgets above 32k may vary; we cap at 32k.
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

    def _to_databricks_response_format(self, structured_output: StructuredOutputSpec) -> dict[str, Any]:
        """Convert structured output spec to Databricks response_format."""
        return {
            "type": "json_schema",
            "json_schema": {
                "name": structured_output.name,
                "schema": structured_output.json_schema,
                "strict": structured_output.strict,
            },
        }

    def _map_error(self, exc: Exception) -> LLMError:
        """Map Databricks/OpenAI SDK exceptions to LLMError."""
        # Try to extract nested error message
        clean_message = self._extract_databricks_error(exc)

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
                if is_context_length_error(exc) or is_context_length_error(clean_message):
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

    def _extract_databricks_error(self, exc: Exception) -> str:
        """Extract clean error message from Databricks nested JSON format.

        Databricks wraps errors in a DatabricksException with nested JSON:
        {"error_code":"BAD_REQUEST","message":"{\\"message\\":\\"Input is too long.\\"}"}
        """
        error_str = str(exc)

        # Try to extract nested JSON message
        # Pattern: {"error_code":"BAD_REQUEST","message":"{"message":"..."}"}
        try:
            json_match = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', error_str)
            if json_match:
                inner_msg = json_match.group(1)
                inner_msg = inner_msg.replace('\\"', '"').replace("\\\\", "\\")

                # Check for double-nested JSON
                inner_json = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', inner_msg)
                if inner_json:
                    return inner_json.group(1).replace('\\"', '"')

                return inner_msg
        except Exception:
            pass

        return error_str
