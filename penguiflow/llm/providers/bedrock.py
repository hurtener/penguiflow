"""AWS Bedrock provider implementation.

Uses boto3 (>=1.42.0) for direct AWS API access via the Converse API.

Supported models (January 2026):
- Anthropic Claude: claude-opus-4-5, claude-sonnet-4-5, claude-haiku-4-5, claude-opus-4-1, claude-sonnet-4
- Amazon Nova: nova-premier, nova-pro, nova-lite, nova-micro, nova-2-lite
- Meta Llama: llama4-scout-17b, llama3-3-70b, llama3-2-90b, llama3-1-405b
- Mistral: mistral-large-3, pixtral-large, ministral-3-8b
- Cohere: command-r-plus, command-r
- DeepSeek: deepseek-r1
- Google: gemma-3-27b

Reference: https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html
"""

from __future__ import annotations

import asyncio
import json
import os
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
    ImagePart,
    LLMMessage,
    LLMRequest,
    StreamEvent,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    Usage,
)
from .base import Provider

if TYPE_CHECKING:
    from ..types import CancelToken, StreamCallback


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

    Reference: https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html
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
    ):
        """Initialize the Bedrock provider.

        Args:
            model: Model identifier. Examples:
                - "anthropic.claude-opus-4-5-20251101-v1:0" (Claude Opus 4.5)
                - "anthropic.claude-sonnet-4-5-20250929-v1:0" (Claude Sonnet 4.5)
                - "anthropic.claude-haiku-4-5-20251001-v1:0" (Claude Haiku 4.5)
                - "amazon.nova-pro-v1:0" (Amazon Nova Pro)
                - "meta.llama4-scout-17b-instruct-v1:0" (Llama 4 Scout)
            region_name: AWS region (uses AWS_DEFAULT_REGION env var if not provided).
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
            raise ImportError(
                "boto3 not installed. Install with: pip install boto3>=1.42.0"
            ) from e

        self._model = model
        self._profile = profile or get_profile(model)
        self._timeout = timeout

        region_name = region_name or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

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
    def provider_name(self) -> str:
        return "bedrock"

    @property
    def profile(self) -> ModelProfile:
        return self._profile

    @property
    def model(self) -> str:
        return self._model

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
            raise LLMCancelledError(message="Request cancelled", provider="bedrock")

        system_content, messages = self._to_bedrock_messages(request.messages)
        params = self._build_params(request, system_content, messages)
        timeout = timeout_s or self._timeout

        loop = asyncio.get_event_loop()

        try:
            if stream and on_stream_event:
                return await self._stream_completion(params, on_stream_event, timeout, cancel)

            async with asyncio.timeout(timeout):
                response = await loop.run_in_executor(
                    None,
                    lambda: self._client.converse(**params),
                )

            return self._from_bedrock_response(response)

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
            raise self._map_error(e) from e

    async def _stream_completion(
        self,
        params: dict[str, Any],
        on_stream_event: StreamCallback,
        timeout: float,
        cancel: CancelToken | None,
    ) -> CompletionResponse:
        """Handle streaming completion."""
        loop = asyncio.get_event_loop()

        text_acc: list[str] = []
        reasoning_acc: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        usage: Usage | None = None
        finish_reason: str | None = None
        current_block_type: str | None = None

        try:
            async with asyncio.timeout(timeout):
                response = await loop.run_in_executor(
                    None,
                    lambda: self._client.converse_stream(**params),
                )

                stream = response.get("stream", [])

                for event in stream:
                    if cancel and cancel.is_cancelled():
                        raise LLMCancelledError(message="Request cancelled", provider="bedrock")

                    if "contentBlockDelta" in event:
                        delta = event["contentBlockDelta"]["delta"]
                        if "text" in delta and current_block_type == "reasoning":
                            reasoning_acc.append(delta["text"])
                            on_stream_event(StreamEvent(delta_reasoning=delta["text"]))
                        elif "text" in delta:
                            text_acc.append(delta["text"])
                            on_stream_event(StreamEvent(delta_text=delta["text"]))
                        elif "reasoningContent" in delta:
                            rc = delta["reasoningContent"]
                            if isinstance(rc, str) and rc:
                                reasoning_acc.append(rc)
                                on_stream_event(StreamEvent(delta_reasoning=rc))
                            elif isinstance(rc, dict):
                                for key in ("text", "reasoningText", "thinkingText", "content"):
                                    val = rc.get(key)
                                    if isinstance(val, str) and val:
                                        reasoning_acc.append(val)
                                        on_stream_event(StreamEvent(delta_reasoning=val))
                                        break
                        elif "toolUse" in delta:
                            # Tool input streaming
                            pass  # Accumulate in content block stop

                    elif "contentBlockStart" in event:
                        start = event["contentBlockStart"]["start"]
                        if "toolUse" in start:
                            current_block_type = "toolUse"
                            tool_calls.append({
                                "id": start["toolUse"]["toolUseId"],
                                "name": start["toolUse"]["name"],
                                "input": "",
                            })
                        elif "reasoningContent" in start:
                            current_block_type = "reasoning"
                        elif "text" in start:
                            current_block_type = "text"

                    elif "contentBlockStop" in event:
                        current_block_type = None

                    elif "messageStop" in event:
                        finish_reason = event["messageStop"].get("stopReason")

                    elif "metadata" in event:
                        meta = event["metadata"]
                        if "usage" in meta:
                            u = meta["usage"]
                            usage = Usage(
                                input_tokens=u.get("inputTokens", 0),
                                output_tokens=u.get("outputTokens", 0),
                                total_tokens=u.get("totalTokens", 0),
                            )

        except TimeoutError as e:
            raise LLMTimeoutError(
                message=f"Stream timed out after {timeout}s",
                provider="bedrock",
                raw=e,
            ) from e

        # Build final message
        parts: list[Any] = []
        full_text = "".join(text_acc)
        if full_text:
            parts.append(TextPart(text=full_text))

        for tc in tool_calls:
            parts.append(
                ToolCallPart(
                    name=tc["name"],
                    arguments_json=tc.get("input", "{}"),
                    call_id=tc["id"],
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

    def _to_bedrock_messages(
        self, messages: tuple[Any, ...] | list[Any]
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

                    content.append({
                        "image": {
                            "format": part.media_type.split("/")[-1],
                            "source": {
                                "bytes": part.data,
                            },
                        },
                    })
                elif isinstance(part, ToolCallPart):
                    content.append({
                        "toolUse": {
                            "toolUseId": part.call_id or f"call_{len(content)}",
                            "name": part.name,
                            "input": json.loads(part.arguments_json) if part.arguments_json else {},
                        },
                    })
                elif isinstance(part, ToolResultPart):
                    content.append({
                        "toolResult": {
                            "toolUseId": part.call_id or "",
                            "content": [{"json": json.loads(part.result_json) if part.result_json else {}}],
                            "status": "error" if part.is_error else "success",
                        },
                    })

            if content:
                role = "user" if msg.role in ("user", "system", "tool") else "assistant"
                result.append({"role": role, "content": content})

        return system_text, result

    def _build_params(
        self,
        request: LLMRequest,
        system_content: str | None,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build Bedrock Converse API parameters from request."""
        params: dict[str, Any] = {
            "modelId": self._model,
            "messages": messages,
            "inferenceConfig": {
                "maxTokens": request.max_tokens or 4096,
                "temperature": request.temperature,
            },
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
            params = self._add_structured_output(params, request.structured_output)

        if request.extra:
            # Sanitize extra params
            extra = dict(request.extra)
            extra.pop("reasoning_effort", None)  # Not supported
            params.update(extra)

        return params

    def _to_bedrock_tools(self, tools: tuple[Any, ...] | list[Any] | None) -> list[dict[str, Any]]:
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
        self, params: dict[str, Any], structured_output: Any
    ) -> dict[str, Any]:
        """Add structured output via forced tool use."""
        tool_def = {
            "toolSpec": {
                "name": structured_output.name,
                "description": "Return structured data in the specified format.",
                "inputSchema": {"json": structured_output.json_schema},
            },
        }

        if "toolConfig" not in params:
            params["toolConfig"] = {"tools": []}
        params["toolConfig"]["tools"].append(tool_def)
        params["toolConfig"]["toolChoice"] = {"tool": {"name": structured_output.name}}

        return params

    def _from_bedrock_response(self, response: dict[str, Any]) -> CompletionResponse:
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
                    # Bedrock Converse uses different shapes depending on model/provider.
                    for key in ("text", "reasoningText", "thinkingText", "content"):
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

    def _map_error(self, exc: Exception) -> LLMError:
        """Map boto3 exceptions to LLMError."""
        error_str = str(exc).lower()

        try:
            from botocore.exceptions import ClientError

            if isinstance(exc, ClientError):
                error_code = exc.response.get("Error", {}).get("Code", "")
                status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 500)

                if error_code in ("AccessDeniedException", "UnauthorizedException"):
                    return LLMAuthError(
                        message=str(exc),
                        provider="bedrock",
                        status_code=status_code,
                        raw=exc,
                    )

                if error_code == "ThrottlingException":
                    return LLMRateLimitError(
                        message=str(exc),
                        provider="bedrock",
                        status_code=429,
                        raw=exc,
                    )

                if error_code == "ValidationException":
                    if is_context_length_error(exc):
                        return LLMContextLengthError(
                            message=str(exc),
                            provider="bedrock",
                            status_code=400,
                            raw=exc,
                        )
                    return LLMInvalidRequestError(
                        message=str(exc),
                        provider="bedrock",
                        status_code=400,
                        raw=exc,
                    )

                if status_code >= 500:
                    return LLMServerError(
                        message=str(exc),
                        provider="bedrock",
                        status_code=status_code,
                        raw=exc,
                    )

        except ImportError:
            pass

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
