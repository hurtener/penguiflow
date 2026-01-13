"""Native output strategy for provider-native structured output.

Uses the provider's native schema-guided decoding mechanism.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from ..types import (
    LLMMessage,
    LLMRequest,
    StructuredOutputSpec,
    extract_text,
)

if TYPE_CHECKING:
    from ..profiles import ModelProfile
    from ..schema.plan import SchemaPlan
    from ..types import CompletionResponse


class NativeOutputStrategy:
    """Provider-native structured output strategy.

    Uses the provider's native mechanism for schema-guided output:
    - OpenAI: response_format with json_schema
    - Anthropic: tool_use with forced tool
    - Google: response_schema
    - Bedrock: tool use via Converse
    - Databricks: constrained decoding

    This is the highest-fidelity mode when supported.
    """

    def build_request(
        self,
        model: str,
        messages: list[LLMMessage],
        response_model: type[BaseModel],
        profile: ModelProfile,
        plan: SchemaPlan,
    ) -> LLMRequest:
        """Build a request with native structured output.

        Args:
            model: Model identifier.
            messages: Conversation messages.
            response_model: Pydantic model for structured output.
            profile: Model profile with capabilities.
            plan: Schema plan with transformed schema.

        Returns:
            LLMRequest configured for native structured output.
        """
        # Anthropic uses tool_use for structured output, not response_format
        if profile.native_structured_kind == "anthropic_tool_use":
            return self._build_anthropic_request(model, messages, response_model, plan)

        # Bedrock also uses tool use
        if profile.native_structured_kind == "bedrock_tool_use":
            return self._build_bedrock_request(model, messages, response_model, plan)

        # Standard response_format approach (OpenAI, Databricks, Google, OpenRouter)
        return LLMRequest(
            model=model,
            messages=tuple(messages),
            structured_output=StructuredOutputSpec(
                name=response_model.__name__,
                json_schema=plan.transformed_schema,
                strict=plan.strict_applied,
            ),
            temperature=0.0,
        )

    def _build_anthropic_request(
        self,
        model: str,
        messages: list[LLMMessage],
        response_model: type[BaseModel],
        plan: SchemaPlan,
    ) -> LLMRequest:
        """Build request for Anthropic's tool_use structured output."""
        from ..types import ToolSpec

        return LLMRequest(
            model=model,
            messages=tuple(messages),
            tools=(
                ToolSpec(
                    name=response_model.__name__,
                    description="Return structured data in the specified format.",
                    json_schema=plan.transformed_schema,
                ),
            ),
            tool_choice=response_model.__name__,
            temperature=0.0,
        )

    def _build_bedrock_request(
        self,
        model: str,
        messages: list[LLMMessage],
        response_model: type[BaseModel],
        plan: SchemaPlan,
    ) -> LLMRequest:
        """Build request for Bedrock's tool use structured output."""
        from ..types import ToolSpec

        return LLMRequest(
            model=model,
            messages=tuple(messages),
            tools=(
                ToolSpec(
                    name=response_model.__name__,
                    description="Return structured data in the specified format.",
                    json_schema=plan.transformed_schema,
                ),
            ),
            tool_choice=response_model.__name__,
            temperature=0.0,
        )

    def parse_response(
        self,
        response: CompletionResponse,
        response_model: type[BaseModel],
    ) -> BaseModel:
        """Parse response from native structured output.

        Args:
            response: Completion response from provider.
            response_model: Pydantic model to parse into.

        Returns:
            Parsed Pydantic model instance.
        """
        message = response.message

        # Check for tool calls (Anthropic/Bedrock style)
        tool_calls = message.tool_calls
        if tool_calls:
            # Use the first (and should be only) tool call
            call = tool_calls[0]
            return response_model.model_validate_json(call.arguments_json)

        # Standard text response (OpenAI/Databricks/Google style)
        text = extract_text(message)
        return response_model.model_validate_json(text)
