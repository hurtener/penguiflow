"""Tests for AWS Bedrock provider initialization and configuration.

Tests use Claude Sonnet 4.5 (anthropic.claude-sonnet-4-5-20250929-v1:0) as the
default model reference. All tests mock boto3 to avoid AWS dependencies.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from penguiflow.llm.errors import (
    LLMCancelledError,
)
from penguiflow.llm.types import (
    ImagePart,
    LLMMessage,
    LLMRequest,
    StructuredOutputSpec,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
)


@pytest.fixture
def mock_boto3() -> Any:
    """Mock boto3 SDK."""
    mock_sdk = MagicMock()
    mock_session = MagicMock()
    mock_client = MagicMock()
    mock_session.client.return_value = mock_client
    mock_sdk.Session.return_value = mock_session

    # Mock Config
    mock_config = MagicMock()
    return mock_sdk, mock_config


class TestBedrockProviderInit:
    """Test Bedrock provider initialization."""

    def test_init_with_credentials(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test initialization with explicit credentials."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.providers.bedrock import BedrockProvider

                provider = BedrockProvider(
                    "anthropic.claude-sonnet-4-5-20250929-v1:0",
                    region_name="us-east-1",
                    aws_access_key_id="AKID",
                    aws_secret_access_key="SECRET",
                )

                assert provider.model == "anthropic.claude-sonnet-4-5-20250929-v1:0"
                assert provider.provider_name == "bedrock"
                mock_sdk.Session.assert_called_once()
                call_kwargs = mock_sdk.Session.call_args[1]
                assert call_kwargs["aws_access_key_id"] == "AKID"
                assert call_kwargs["aws_secret_access_key"] == "SECRET"
                assert call_kwargs["region_name"] == "us-east-1"

    def test_init_with_profile_name(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test initialization with AWS profile name."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.providers.bedrock import BedrockProvider

                BedrockProvider(
                    "anthropic.claude-sonnet-4-5-20250929-v1:0",
                    profile_name="my-profile",
                )

                call_kwargs = mock_sdk.Session.call_args[1]
                assert call_kwargs["profile_name"] == "my-profile"

    def test_init_with_session_token(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test initialization with session token."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.providers.bedrock import BedrockProvider

                BedrockProvider(
                    "anthropic.claude-sonnet-4-5-20250929-v1:0",
                    aws_session_token="TOKEN",
                )

                call_kwargs = mock_sdk.Session.call_args[1]
                assert call_kwargs["aws_session_token"] == "TOKEN"

    def test_init_with_timeout(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test initialization with custom timeout."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.providers.bedrock import BedrockProvider

                provider = BedrockProvider(
                    "anthropic.claude-sonnet-4-5-20250929-v1:0",
                    timeout=600.0,
                )

                assert provider._timeout == 600.0

    def test_init_uses_env_var(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test initialization uses AWS_DEFAULT_REGION env var."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.providers.bedrock import BedrockProvider

                with patch.dict(os.environ, {"AWS_DEFAULT_REGION": "eu-west-1"}):
                    BedrockProvider("anthropic.claude-sonnet-4-5-20250929-v1:0")

                    call_kwargs = mock_sdk.Session.call_args[1]
                    assert call_kwargs["region_name"] == "eu-west-1"

    def test_init_with_custom_profile(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test initialization with custom model profile."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.profiles import ModelProfile
                from penguiflow.llm.providers.bedrock import BedrockProvider

                custom_profile = ModelProfile(
                    supports_tools=True,
                    supports_schema_guided_output=True,
                    max_output_tokens=4096,
                )
                provider = BedrockProvider(
                    "anthropic.claude-sonnet-4-5-20250929-v1:0",
                    profile=custom_profile,
                )

                assert provider.profile is custom_profile

    def test_provider_properties(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test provider property accessors."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.providers.bedrock import BedrockProvider

                provider = BedrockProvider("anthropic.claude-sonnet-4-5-20250929-v1:0")

                assert provider.provider_name == "bedrock"
                assert provider.model == "anthropic.claude-sonnet-4-5-20250929-v1:0"
                assert provider.profile is not None


class TestBedrockProviderMessageConversion:
    """Test Bedrock provider message conversion."""

    def test_convert_messages_basic(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test basic message conversion."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.providers.bedrock import BedrockProvider

                provider = BedrockProvider("anthropic.claude-sonnet-4-5-20250929-v1:0")

                messages = [
                    LLMMessage(role="system", parts=[TextPart(text="You are helpful.")]),
                    LLMMessage(role="user", parts=[TextPart(text="Hello")]),
                ]

                system_text, result = provider._to_bedrock_messages(messages)

                assert system_text == "You are helpful."
                assert len(result) == 1
                assert result[0]["role"] == "user"

    def test_convert_messages_with_tool_call(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test message conversion with tool calls."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.providers.bedrock import BedrockProvider

                provider = BedrockProvider("anthropic.claude-sonnet-4-5-20250929-v1:0")

                messages = [
                    LLMMessage(
                        role="assistant",
                        parts=[
                            ToolCallPart(
                                name="get_weather",
                                arguments_json='{"city": "NYC"}',
                                call_id="call_123",
                            )
                        ],
                    ),
                ]

                _, result = provider._to_bedrock_messages(messages)

                assert len(result) == 1
                assert "toolUse" in result[0]["content"][0]

    def test_convert_messages_with_tool_result(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test message conversion with tool results."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.providers.bedrock import BedrockProvider

                provider = BedrockProvider("anthropic.claude-sonnet-4-5-20250929-v1:0")

                messages = [
                    LLMMessage(
                        role="tool",
                        parts=[
                            ToolResultPart(
                                name="get_weather",
                                result_json='{"temp": 72}',
                                call_id="call_123",
                                is_error=False,
                            )
                        ],
                    ),
                ]

                _, result = provider._to_bedrock_messages(messages)

                assert len(result) == 1
                assert "toolResult" in result[0]["content"][0]

    def test_convert_messages_with_image(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test message conversion with images."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.providers.bedrock import BedrockProvider

                provider = BedrockProvider("anthropic.claude-sonnet-4-5-20250929-v1:0")

                messages = [
                    LLMMessage(
                        role="user",
                        parts=[
                            ImagePart(data=b"fake_image_data", media_type="image/png"),
                            TextPart(text="What is this?"),
                        ],
                    ),
                ]

                _, result = provider._to_bedrock_messages(messages)

                assert len(result) == 1
                assert len(result[0]["content"]) == 2
                assert "image" in result[0]["content"][0]


class TestBedrockProviderBuildParams:
    """Test Bedrock provider parameter building."""

    def test_build_params_basic(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test basic parameter building."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.providers.bedrock import BedrockProvider

                provider = BedrockProvider("anthropic.claude-sonnet-4-5-20250929-v1:0")

                request = LLMRequest(
                    model="anthropic.claude-sonnet-4-5-20250929-v1:0",
                    messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                    temperature=0.7,
                )

                _, messages = provider._to_bedrock_messages(request.messages)
                params = provider._build_params(request, None, messages)

                assert params["modelId"] == "anthropic.claude-sonnet-4-5-20250929-v1:0"
                assert params["inferenceConfig"]["temperature"] == 0.7

    def test_build_params_with_system(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test parameter building with system message."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.providers.bedrock import BedrockProvider

                provider = BedrockProvider("anthropic.claude-sonnet-4-5-20250929-v1:0")

                messages = [
                    LLMMessage(role="system", parts=[TextPart(text="Be helpful.")]),
                    LLMMessage(role="user", parts=[TextPart(text="Hello")]),
                ]

                request = LLMRequest(
                    model="anthropic.claude-sonnet-4-5-20250929-v1:0",
                    messages=tuple(messages),
                )

                system_text, converted = provider._to_bedrock_messages(request.messages)
                params = provider._build_params(request, system_text, converted)

                assert params["system"][0]["text"] == "Be helpful."

    def test_build_params_with_tools(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test parameter building with tools."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.providers.bedrock import BedrockProvider

                provider = BedrockProvider("anthropic.claude-sonnet-4-5-20250929-v1:0")

                request = LLMRequest(
                    model="anthropic.claude-sonnet-4-5-20250929-v1:0",
                    messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                    tools=(
                        ToolSpec(
                            name="get_weather",
                            description="Get weather",
                            json_schema={"type": "object"},
                        ),
                    ),
                    tool_choice="get_weather",
                )

                _, messages = provider._to_bedrock_messages(request.messages)
                params = provider._build_params(request, None, messages)

                assert "toolConfig" in params
                assert params["toolConfig"]["toolChoice"]["tool"]["name"] == "get_weather"

    def test_build_params_with_structured_output(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test parameter building with structured output."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.providers.bedrock import BedrockProvider

                provider = BedrockProvider("anthropic.claude-sonnet-4-5-20250929-v1:0")

                request = LLMRequest(
                    model="anthropic.claude-sonnet-4-5-20250929-v1:0",
                    messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                    structured_output=StructuredOutputSpec(
                        name="MySchema",
                        json_schema={"type": "object"},
                        strict=True,
                    ),
                )

                _, messages = provider._to_bedrock_messages(request.messages)
                params = provider._build_params(request, None, messages)

                # Bedrock uses tool_choice for structured output
                assert params["toolConfig"]["toolChoice"]["tool"]["name"] == "MySchema"


class TestBedrockProviderComplete:
    """Test Bedrock provider complete method."""

    def _create_mock_response(
        self,
        text: str = "Hello!",
        tool_calls: list[dict[str, Any]] | None = None,
        stop_reason: str = "end_turn",
    ) -> dict[str, Any]:
        """Create a mock Bedrock response."""
        content = []

        if text:
            content.append({"text": text})

        if tool_calls:
            for tc in tool_calls:
                content.append({
                    "toolUse": {
                        "toolUseId": tc["id"],
                        "name": tc["name"],
                        "input": tc["input"],
                    }
                })

        return {
            "output": {
                "message": {
                    "content": content,
                }
            },
            "usage": {
                "inputTokens": 10,
                "outputTokens": 5,
                "totalTokens": 15,
            },
            "stopReason": stop_reason,
        }

    @pytest.mark.asyncio
    async def test_complete_simple(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test simple completion."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.providers.bedrock import BedrockProvider

                mock_response = self._create_mock_response("Hello from Bedrock!")

                mock_client = MagicMock()
                mock_client.converse.return_value = mock_response
                mock_sdk.Session.return_value.client.return_value = mock_client

                provider = BedrockProvider("anthropic.claude-sonnet-4-5-20250929-v1:0")

                request = LLMRequest(
                    model="anthropic.claude-sonnet-4-5-20250929-v1:0",
                    messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                )

                # Run in executor, so we mock the loop
                with patch("asyncio.get_event_loop") as mock_loop:
                    mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_response)

                    response = await provider.complete(request)

                    assert response.message.text == "Hello from Bedrock!"
                    assert response.usage.input_tokens == 10
                    assert response.usage.output_tokens == 5

    @pytest.mark.asyncio
    async def test_complete_with_tool_calls(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test completion with tool calls."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.providers.bedrock import BedrockProvider

                mock_response = self._create_mock_response(
                    text="",
                    tool_calls=[
                        {"id": "call_123", "name": "get_weather", "input": {"city": "NYC"}}
                    ],
                )

                mock_client = MagicMock()
                mock_client.converse.return_value = mock_response
                mock_sdk.Session.return_value.client.return_value = mock_client

                provider = BedrockProvider("anthropic.claude-sonnet-4-5-20250929-v1:0")

                request = LLMRequest(
                    model="anthropic.claude-sonnet-4-5-20250929-v1:0",
                    messages=(LLMMessage(role="user", parts=[TextPart(text="Weather?")]),),
                )

                with patch("asyncio.get_event_loop") as mock_loop:
                    mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_response)

                    response = await provider.complete(request)

                    assert len(response.message.parts) == 1
                    assert isinstance(response.message.parts[0], ToolCallPart)
                    assert response.message.parts[0].name == "get_weather"

    @pytest.mark.asyncio
    async def test_complete_with_cancel_token(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test early cancellation via cancel token."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.providers.bedrock import BedrockProvider

                provider = BedrockProvider("anthropic.claude-sonnet-4-5-20250929-v1:0")

                cancel_token = MagicMock()
                cancel_token.is_cancelled.return_value = True

                request = LLMRequest(
                    model="anthropic.claude-sonnet-4-5-20250929-v1:0",
                    messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                )

                with pytest.raises(LLMCancelledError):
                    await provider.complete(request, cancel=cancel_token)


class TestBedrockProviderErrorMapping:
    """Test Bedrock provider error mapping."""

    def test_map_auth_error(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test mapping authentication error."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.errors import LLMAuthError
                from penguiflow.llm.providers.bedrock import BedrockProvider

                provider = BedrockProvider("anthropic.claude-sonnet-4-5-20250929-v1:0")

                exc = ValueError("Access denied to Bedrock")
                result = provider._map_error(exc)

                assert isinstance(result, LLMAuthError)

    def test_map_rate_limit_error(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test mapping rate limit error."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.errors import LLMRateLimitError
                from penguiflow.llm.providers.bedrock import BedrockProvider

                provider = BedrockProvider("anthropic.claude-sonnet-4-5-20250929-v1:0")

                exc = ValueError("Throttling: Request rate exceeded")
                result = provider._map_error(exc)

                assert isinstance(result, LLMRateLimitError)

    def test_map_unknown_error(self, mock_boto3: tuple[MagicMock, MagicMock]) -> None:
        """Test mapping unknown error."""
        mock_sdk, mock_config = mock_boto3

        with patch.dict("sys.modules", {"boto3": mock_sdk, "botocore": MagicMock(), "botocore.config": MagicMock()}):
            with patch("botocore.config.Config", return_value=mock_config):
                from penguiflow.llm.errors import LLMError
                from penguiflow.llm.providers.bedrock import BedrockProvider

                provider = BedrockProvider("anthropic.claude-sonnet-4-5-20250929-v1:0")

                exc = ValueError("Something unexpected")
                result = provider._map_error(exc)

                assert isinstance(result, LLMError)
                assert "Something unexpected" in result.message
