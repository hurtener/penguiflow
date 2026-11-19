"""AWS Bedrock provider implementation.

Uses boto3 (>=1.42.0) for direct AWS API access via the Converse API.

Supported models (January 2026):
- Anthropic Claude: claude-opus-4-5, claude-sonnet-4-5, claude-haiku-4-5,
  claude-opus-4-1, claude-sonnet-4
- Amazon Nova: nova-premier, nova-pro, nova-lite, nova-micro, nova-2-lite
- Meta Llama: llama4-scout-17b, llama3-3-70b, llama3-2-90b, llama3-1-405b
- Mistral: mistral-large-3, pixtral-large, ministral-3-8b
- Cohere: command-r-plus, command-r
- DeepSeek: deepseek-r1
- Google: gemma-3-27b

Reference:
https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import TYPE_CHECKING, Any

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
    ImagePart,
    LLMMessage,
    LLMRequest,
    StreamEvent,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    Usage,
)
from ._params import resolve_temperature
from .base import Provider

if TYPE_CHECKING:
    from ..types import (
        CancelToken,
        ContentPart,
        StreamCallback,
        StructuredOutputSpec,
    )


def _map_client_error(exc: Exception) -> LLMError | None:
    """Map a botocore ``ClientError`` by its error code and HTTP status."""
    try:
        from botocore.exceptions import ClientError
    except ImportError:
        return None
    if not isinstance(exc, ClientError):
        return None

    error_code = exc.response.get("Error", {}).get("Code", "")
    status_code = exc.response.get("ResponseMetadata", {}).get(
        "HTTPStatusCode", 500
    )
    if error_code in ("AccessDeniedException", "UnauthorizedException"):
        return LLMAuthError(
            message=str(exc),
            provider="bedrock",
            status_code=status_code,
            raw=exc,
        )
    if error_code == "ThrottlingException":
        return LLMRateLimitError(
            message=str(exc), provider="bedrock", status_code=429, raw=exc
        )
    if error_code == "ValidationException":
        error_cls = (
            LLMContextLengthError
            if is_context_length_error(exc)
            else LLMInvalidRequestError
        )
        return error_cls(
            message=str(exc), provider="bedrock", status_code=400, raw=exc
        )
    if status_code >= 500:
        return LLMServerError(
            message=str(exc),
            provider="bedrock",
            status_code=status_code,
            raw=exc,
        )
    return None


_REASONING_TEXT_KEYS = ("text", "reasoningText", "thinkingText", "content")


def _reasoning_delta_text(reasoning: object) -> str | None:
    """Return the text of a Bedrock ``reasoningContent`` delta."""
    if isinstance(reasoning, str):
        return reasoning or None
    if isinstance(reasoning, dict):
        for key in _REASONING_TEXT_KEYS:
            value = reasoning.get(key)
            if isinstance(value, str) and value:
                return value
    return None


class _BedrockStreamState:
    """Accumulates Bedrock ``converse_stream`` events into a response."""

    def __init__(self, on_stream_event: StreamCallback) -> None:
        self._emit = on_stream_event
        self._text: list[str] = []
        self._reasoning: list[str] = []
        self._tool_calls: list[dict[str, str]] = []
        self._block_type: str | None = None
        self._usage: Usage | None = None
        self._finish_reason: str | None = None

    def handle(self, event: dict[str, Any]) -> None:
        """Fold one stream event into the accumulated response."""
        if "contentBlockDelta" in event:
            self._add_delta(event["contentBlockDelta"]["delta"])
        elif "contentBlockStart" in event:
            self._start_block(event["contentBlockStart"]["start"])
        elif "contentBlockStop" in event:
            self._block_type = None
        elif "messageStop" in event:
            self._finish_reason = event["messageStop"].get("stopReason")
        elif "metadata" in event and "usage" in event["metadata"]:
            usage = event["metadata"]["usage"]
            self._usage = Usage(
                input_tokens=usage.get("inputTokens", 0),
                output_tokens=usage.get("outputTokens", 0),
                total_tokens=usage.get("totalTokens", 0),
            )

    def _add_reasoning(self, text: str) -> None:
        self._reasoning.append(text)
        self._emit(StreamEvent(delta_reasoning=text))

    def _add_delta(self, delta: dict[str, Any]) -> None:
        if "text" in delta:
            if self._block_type == "reasoning":
                self._add_reasoning(delta["text"])
            else:
                self._text.append(delta["text"])
                self._emit(StreamEvent(delta_text=delta["text"]))
        elif "reasoningContent" in delta:
            text = _reasoning_delta_text(delta["reasoningContent"])
            if text:
                self._add_reasoning(text)
        # "toolUse" input deltas are not accumulated.

    def _start_block(self, start: dict[str, Any]) -> None:
        if "toolUse" in start:
            self._block_type = "toolUse"
            self._tool_calls.append(
                {
                    "id": start["toolUse"]["toolUseId"],
                    "name": start["toolUse"]["name"],
                    "input": "",
                }
            )
        elif "reasoningContent" in start:
            self._block_type = "reasoning"
        elif "text" in start:
            self._block_type = "text"

    def response(self) -> CompletionResponse:
        """Emit the ``done`` event and build the completion response."""
        parts: list[ContentPart] = []
        full_text = "".join(self._text)
        if full_text:
            parts.append(TextPart(text=full_text))
        parts.extend(
            ToolCallPart(
                name=call["name"],
                arguments_json=call.get("input", "{}"),
                call_id=call["id"],
            )
            for call in self._tool_calls
        )
        self._emit(
            StreamEvent(
                done=True, usage=self._usage, finish_reason=self._finish_reason
            )
        )
        return CompletionResponse(
            message=LLMMessage(role="assistant", parts=parts),
            usage=self._usage or Usage.zero(),
            raw_response=None,
            reasoning_content="".join(self._reasoning) or None,
            finish_reason=self._finish_reason,
        )


def _to_bedrock_messages(
    messages: tuple[Any, ...] | list[Any],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert typed messages to Bedrock Converse format.

    Returns:
        Tuple of (system_text, messages_list).
    """
    system_text: str | None = None
    result: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == "system":
            system_text = msg.text
            continue

        content: list[dict[str, Any]] = []

        for part in msg.parts:
            if isinstance(part, TextPart):
                content.append({"text": part.text})
            elif isinstance(part, ImagePart):
                content.append(
                    {
                        "image": {
                            "format": part.media_type.split("/")[-1],
                            "source": {
                                "bytes": part.data,
                            },
                        },
                    }
                )
            elif isinstance(part, ToolCallPart):
                content.append(
                    {
                        "toolUse": {
                            "toolUseId": part.call_id
                            or f"call_{len(content)}",
                            "name": part.name,
                            "input": json.loads(part.arguments_json)
                            if part.arguments_json
                            else {},
                        },
                    }
                )
            elif isinstance(part, ToolResultPart):
                content.append(
                    {
                        "toolResult": {
                            "toolUseId": part.call_id or "",
                            "content": [
                                {
                                    "json": json.loads(part.result_json)
                                    if part.result_json
                                    else {}
                                }
                            ],
                            "status": "error" if part.is_error else "success",
                        },
                    }
                )

        if content:
            role = (
                "user"
                if msg.role in ("user", "system", "tool")
                else "assistant"
            )
            result.append({"role": role, "content": content})

    return system_text, result


def _from_bedrock_response(response: dict[str, Any]) -> CompletionResponse:
    """Convert Bedrock response to CompletionResponse."""
    parts: list[Any] = []
    reasoning_acc: list[str] = []

    output = response.get("output", {})
    message = output.get("message", {})

    for block in message.get("content", []):
        if "text" in block:
            parts.append(TextPart(text=block["text"]))
        elif "reasoningContent" in block:
            rc = block["reasoningContent"]
            if isinstance(rc, str) and rc:
                reasoning_acc.append(rc)
            elif isinstance(rc, dict):
                # Bedrock Converse uses different shapes depending on
                # model/provider.
                for key in (
                    "text",
                    "reasoningText",
                    "thinkingText",
                    "content",
                ):
                    val = rc.get(key)
                    if isinstance(val, str) and val:
                        reasoning_acc.append(val)
                        break
        elif "toolUse" in block:
            tu = block["toolUse"]
            parts.append(
                ToolCallPart(
                    name=tu["name"],
                    arguments_json=json.dumps(tu.get("input", {})),
                    call_id=tu.get("toolUseId"),
                )
            )

    usage_data = response.get("usage", {})
    usage = Usage(
        input_tokens=usage_data.get("inputTokens", 0),
        output_tokens=usage_data.get("outputTokens", 0),
        total_tokens=usage_data.get("totalTokens", 0),
    )

    return CompletionResponse(
        message=LLMMessage(role="assistant", parts=parts),
        usage=usage,
        raw_response=response,
        reasoning_content="".join(reasoning_acc) or None,
        finish_reason=response.get("stopReason"),
    )


class BedrockProvider(Provider):
    """AWS Bedrock provider using boto3 (>=1.42.0).

    Supports models via the unified Converse API:
    - Anthropic Claude 4.5/4.1/4 (opus, sonnet, haiku)
    - Amazon Nova (premier, pro, lite, micro, nova-2-lite)
    - Meta Llama 4 (scout-17b) and Llama 3.x (405b, 90b, 70b)
    - Mistral (large-3, pixtral-large, ministral-3)
    - Cohere (command-r-plus, command-r, embed-v4)
    - DeepSeek (r1), Qwen3, Google Gemma 3

    Features:
    - Tool use via Converse API toolConfig
    - Streaming with usage tracking via converse_stream
    - Image/multimodal support for vision-capable models
    - Extended thinking (reasoning) for Claude 4+ models

    Example model IDs:
    - anthropic.claude-opus-4-5-20251101-v1:0
    - anthropic.claude-sonnet-4-5-20250929-v1:0
    - amazon.nova-pro-v1:0
    - meta.llama4-scout-17b-instruct-v1:0

    Reference:
    https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html
    """

    def __init__(
        self,
        model: str,
        *,
        region_name: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        aws_session_token: str | None = None,
        profile_name: str | None = None,
        profile: ModelProfile | None = None,
        timeout: float = 360.0,
    ) -> None:
        """Initialize the Bedrock provider.

        Args:
            model: Model identifier. Examples:
                - "anthropic.claude-opus-4-5-20251101-v1:0" (Claude Opus 4.5)
                - "anthropic.claude-sonnet-4-5-20250929-v1:0" (Claude Sonnet
                  4.5)
                - "anthropic.claude-haiku-4-5-20251001-v1:0" (Claude Haiku 4.5)
                - "amazon.nova-pro-v1:0" (Amazon Nova Pro)
                - "meta.llama4-scout-17b-instruct-v1:0" (Llama 4 Scout)
            region_name: AWS region (uses AWS_DEFAULT_REGION env var if not
                provided).
            aws_access_key_id: AWS access key ID.
            aws_secret_access_key: AWS secret access key.
            aws_session_token: AWS session token for temporary credentials.
            profile_name: AWS profile name for credentials.
            profile: Model profile override.
            timeout: Default timeout in seconds.

        Raises:
            ImportError: If boto3 (>=1.42.0) is not installed.
        """
        try:
            import boto3
            from botocore.config import Config
        except ImportError as e:
            msg = (
                "boto3 not installed. Install with: pip install boto3>=1.42.0"
            )
            raise ImportError(msg) from e

        self._model = model
        self._profile = profile or get_profile(model)
        self._timeout = timeout

        region_name = region_name or os.environ.get(
            "AWS_DEFAULT_REGION", "us-east-1"
        )

        config = Config(
            read_timeout=int(timeout),
            connect_timeout=60,
            retries={"max_attempts": 3},
        )

        session = boto3.Session(
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token,
            region_name=region_name,
            profile_name=profile_name,
        )

        self._client = session.client("bedrock-runtime", config=config)

    @property
    @override
    def provider_name(self) -> str:
        return "bedrock"

    @property
    @override
    def profile(self) -> ModelProfile:
        return self._profile

    @property
    @override
    def model(self) -> str:
        return self._model

    @property
    def timeout(self) -> float:
        """The default request timeout, in seconds."""
        return self._timeout

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
        if cancel and cancel.is_cancelled():
            raise LLMCancelledError(
                message="Request cancelled",
                provider="bedrock",
                retryable=False,
            )

        system_content, messages = _to_bedrock_messages(request.messages)
        params = self.build_params(request, system_content, messages)
        timeout = timeout_s or self._timeout

        loop = asyncio.get_event_loop()

        try:
            if stream and on_stream_event:
                try:
                    async with asyncio.timeout(timeout):
                        return await self.stream_completion(
                            params, on_stream_event, cancel
                        )
                except TimeoutError as e:
                    raise LLMTimeoutError(
                        message=f"Stream timed out after {timeout}s",
                        provider="bedrock",
                        raw=e,
                    ) from e

            async with asyncio.timeout(timeout):
                response = await loop.run_in_executor(
                    None,
                    lambda: self._client.converse(**params),
                )

            return _from_bedrock_response(response)

        except TimeoutError as e:
            raise LLMTimeoutError(
                message=f"Request timed out after {timeout}s",
                provider="bedrock",
                raw=e,
            ) from e
        except asyncio.CancelledError:
            raise LLMCancelledError(
                message="Request cancelled", provider="bedrock"
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
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.converse_stream(**params),
        )

        state = _BedrockStreamState(on_stream_event)
        for event in response.get("stream", []):
            if cancel and cancel.is_cancelled():
                raise LLMCancelledError(
                    message="Request cancelled",
                    provider="bedrock",
                    retryable=False,
                )
            state.handle(event)
        return state.response()

    def build_params(
        self,
        request: LLMRequest,
        system_content: str | None,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build Bedrock Converse API parameters from request."""
        inference_config: dict[str, Any] = {
            "maxTokens": request.max_tokens or 4096,
        }

        temp = resolve_temperature(
            self._profile,
            request.temperature,
            model=self._model,
            forced_off=self.temperature_unsupported,
        )
        if temp is not None:
            inference_config["temperature"] = temp

        params: dict[str, Any] = {
            "modelId": self._model,
            "messages": messages,
            "inferenceConfig": inference_config,
        }

        if system_content:
            params["system"] = [{"text": system_content}]

        if request.tools:
            params["toolConfig"] = {
                "tools": self._to_bedrock_tools(request.tools),
            }

        if request.tool_choice:
            if "toolConfig" not in params:
                params["toolConfig"] = {}
            params["toolConfig"]["toolChoice"] = {
                "tool": {"name": request.tool_choice}
            }

        # Handle structured output via forced tool
        if request.structured_output:
            params = self._add_structured_output(
                params, request.structured_output
            )

        if request.extra:
            # Sanitize extra params
            extra = dict(request.extra)
            extra.pop("reasoning_effort", None)  # Not supported
            params.update(extra)

        return params

    def _to_bedrock_tools(
        self, tools: tuple[Any, ...] | list[Any] | None
    ) -> list[dict[str, Any]]:
        """Convert typed tools to Bedrock format."""
        if not tools:
            return []

        return [
            {
                "toolSpec": {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": {"json": tool.json_schema},
                },
            }
            for tool in tools
        ]

    def _add_structured_output(
        self, params: dict[str, Any], structured_output: StructuredOutputSpec
    ) -> dict[str, Any]:
        """Add structured output via forced tool use."""
        tool_def = {
            "toolSpec": {
                "name": structured_output.name,
                "description": "Return structured data in the "
                "specified format.",
                "inputSchema": {"json": structured_output.json_schema},
            },
        }

        if "toolConfig" not in params:
            params["toolConfig"] = {"tools": []}
        params["toolConfig"]["tools"].append(tool_def)
        params["toolConfig"]["toolChoice"] = {
            "tool": {"name": structured_output.name}
        }

        return params

    def map_error(self, exc: Exception) -> LLMError:
        """Map boto3 exceptions to LLMError."""
        mapped = _map_client_error(exc)
        if mapped is not None:
            return mapped

        error_str = str(exc).lower()
        if "credential" in error_str or "access denied" in error_str:
            return LLMAuthError(
                message=str(exc),
                provider="bedrock",
                raw=exc,
            )

        if "throttl" in error_str or "rate" in error_str:
            return LLMRateLimitError(
                message=str(exc),
                provider="bedrock",
                raw=exc,
            )

        if is_context_length_error(exc):
            return LLMContextLengthError(
                message=str(exc),
                provider="bedrock",
                raw=exc,
            )

        return LLMError(
            message=str(exc),
            provider="bedrock",
            raw=exc,
        )
