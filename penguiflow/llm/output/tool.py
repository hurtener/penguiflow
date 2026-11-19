"""Tool-based output strategy for structured output via function calling.

Forces tool calling to return structured output (portable across providers).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from ..types import (
    LLMMessage,
    LLMRequest,
    ToolSpec,
    extract_single_tool_call,
)
from .base import ModelT, OutputStrategy

if TYPE_CHECKING:
    from pydantic import BaseModel

    from ..profiles import ModelProfile
    from ..schema.plan import SchemaPlan
    from ..types import CompletionResponse


class ToolsOutputStrategy(OutputStrategy):
    """Tool-based structured output strategy.

    Forces the model to call a specific tool with the schema as its input
    schema. This is more portable than native structured output as most
    providers support function/tool calling.

    Trade-offs:
    - More portable across providers
    - May have slightly higher latency
    - Works even when native mode doesn't support the schema
    """

    TOOL_NAME = "structured_output"

    @override
    def build_request(
        self,
        model: str,
        messages: list[LLMMessage],
        response_model: type[BaseModel],
        profile: ModelProfile,
        plan: SchemaPlan | None,
        temperature: float | None = None,
    ) -> LLMRequest:
        """Build a request with forced tool calling.

        Args:
            model: Model identifier.
            messages: Conversation messages.
            response_model: Pydantic model for structured output.
            profile: Model profile with capabilities.
            plan: Schema plan with transformed schema.
            temperature: Optional sampling temperature (opt-in; None omits it).

        Returns:
            LLMRequest configured for tool-based structured output.
        """
        if plan is None:
            msg = "The tool-based output strategy requires a schema plan"
            raise ValueError(msg)
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
            temperature=temperature,
        )

    @override
    def parse_response(
        self,
        response: CompletionResponse,
        response_model: type[ModelT],
    ) -> ModelT:
        """Parse response from tool calling.

        Args:
            response: Completion response from provider.
            response_model: Pydantic model to parse into.

        Returns:
            Parsed Pydantic model instance.

        Raises:
            ValueError: If no tool call found or wrong tool called.
        """
        call = extract_single_tool_call(
            response.message, expected_name=self.TOOL_NAME
        )
        return response_model.model_validate_json(call.arguments_json)
