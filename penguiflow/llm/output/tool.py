"""Tool-based output strategy for structured output via function calling.

Forces tool calling to return structured output (portable across providers).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from ..types import (
    LLMMessage,
    LLMRequest,
    ToolSpec,
    extract_single_tool_call,
)

if TYPE_CHECKING:
    from ..profiles import ModelProfile
    from ..schema.plan import SchemaPlan
    from ..types import CompletionResponse


class ToolsOutputStrategy:
    """Tool-based structured output strategy.

    Forces the model to call a specific tool with the schema as its
    input schema. This is more portable than native structured output
    as most providers support function/tool calling.

    Trade-offs:
    - More portable across providers
    - May have slightly higher latency
    - Works even when native mode doesn't support the schema
    """

    TOOL_NAME = "structured_output"

    def build_request(
        self,
        model: str,
        messages: list[LLMMessage],
        response_model: type[BaseModel],
        profile: ModelProfile,
        plan: SchemaPlan,
    ) -> LLMRequest:
        """Build a request with forced tool calling.

        Args:
            model: Model identifier.
            messages: Conversation messages.
            response_model: Pydantic model for structured output.
            profile: Model profile with capabilities.
            plan: Schema plan with transformed schema.

        Returns:
            LLMRequest configured for tool-based structured output.
        """
        return LLMRequest(
            model=model,
            messages=tuple(messages),
            tools=(
                ToolSpec(
                    name=self.TOOL_NAME,
                    description=(
                        "Return structured data in the specified format. "
                        "You must call this tool with your response."
                    ),
                    json_schema=plan.transformed_schema,
                ),
            ),
            tool_choice=self.TOOL_NAME,
            temperature=0.0,
        )

    def parse_response(
        self,
        response: CompletionResponse,
        response_model: type[BaseModel],
    ) -> BaseModel:
        """Parse response from tool calling.

        Args:
            response: Completion response from provider.
            response_model: Pydantic model to parse into.

        Returns:
            Parsed Pydantic model instance.

        Raises:
            ValueError: If no tool call found or wrong tool called.
        """
        call = extract_single_tool_call(response.message, expected_name=self.TOOL_NAME)
        return response_model.model_validate_json(call.arguments_json)
