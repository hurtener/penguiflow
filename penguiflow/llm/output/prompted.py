"""Prompted output strategy for schema-in-prompt structured output.

Injects schema into prompt and parses/retries the response (fallback mode).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typing_extensions import override

from ..types import (
    LLMMessage,
    LLMRequest,
    TextPart,
    extract_text,
    strip_markdown_fences,
)
from .base import ModelT, OutputStrategy

if TYPE_CHECKING:
    from pydantic import BaseModel

    from ..profiles import ModelProfile
    from ..schema.plan import SchemaPlan
    from ..types import CompletionResponse


def _extract_json(text: str) -> str:
    """Extract JSON from potentially mixed text content.

    Args:
        text: Raw response text.

    Returns:
        Extracted JSON string.
    """
    text = text.strip()

    # If it looks like JSON already, return as-is
    if text.startswith("{") and text.endswith("}"):
        return text

    if text.startswith("[") and text.endswith("]"):
        return text

    # Try to find JSON object in the text
    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]

    # Try to find JSON array
    start = text.find("[")
    end = text.rfind("]")

    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]

    # Return as-is and let validation fail with clear error
    return text


class PromptedOutputStrategy(OutputStrategy):
    """Prompt-based structured output strategy (last resort).

    Injects the JSON schema into the prompt and relies on the model to output
    valid JSON. Parse validation happens after the response.

    Trade-offs:
    - Works with any model that can output text
    - Less reliable than native or tool-based modes
    - May require more retries
    - Useful when schema is too complex for other modes
    """

    SYSTEM_TEMPLATE = (
        "You must respond with a valid JSON object matching this schema:\n"
        "\n"
        "```json\n"
        "{schema}\n"
        "```\n"
        "\n"
        "Requirements:\n"
        "- Output ONLY the JSON object, nothing else\n"
        "- Do not include any text before or after the JSON\n"
        "- Do not wrap the JSON in markdown code fences\n"
        "- Ensure all required fields are present\n"
        "- Follow the exact types specified in the schema"
    )

    USER_TEMPLATE = (
        "Remember: Respond with ONLY a valid JSON object matching the "
        "schema. No other text."
    )

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
        """Build a request with schema injection in prompt.

        Args:
            model: Model identifier.
            messages: Conversation messages.
            response_model: Pydantic model for structured output.
            profile: Model profile with capabilities.
            plan: Schema plan with transformed schema.
            temperature: Optional sampling temperature (opt-in; None omits it).

        Returns:
            LLMRequest with schema injected into messages.
        """
        if plan is None:
            error = "The prompted output strategy requires a schema plan"
            raise ValueError(error)
        schema_str = json.dumps(plan.transformed_schema, indent=2)

        # Build modified messages with schema injection
        modified_messages: list[LLMMessage] = []

        # Find or create system message
        has_system = any(m.role == "system" for m in messages)

        if has_system:
            # Append to existing system message
            for msg in messages:
                if msg.role == "system":
                    original_text = extract_text(msg)
                    instructions = self.SYSTEM_TEMPLATE.format(
                        schema=schema_str
                    )
                    combined_text = f"{original_text}\n\n{instructions}"
                    modified_messages.append(
                        LLMMessage(
                            role="system", parts=[TextPart(text=combined_text)]
                        )
                    )
                else:
                    modified_messages.append(msg)
        else:
            # Add system message at the beginning
            modified_messages.append(
                LLMMessage(
                    role="system",
                    parts=[
                        TextPart(
                            text=self.SYSTEM_TEMPLATE.format(schema=schema_str)
                        )
                    ],
                )
            )
            modified_messages.extend(messages)

        # Add reminder at the end if there are user messages
        if modified_messages and modified_messages[-1].role == "user":
            last_msg = modified_messages[-1]
            original_text = extract_text(last_msg)
            combined_text = f"{original_text}\n\n{self.USER_TEMPLATE}"
            modified_messages[-1] = LLMMessage(
                role="user", parts=[TextPart(text=combined_text)]
            )

        return LLMRequest(
            model=model,
            messages=tuple(modified_messages),
            temperature=temperature,
        )

    @override
    def parse_response(
        self,
        response: CompletionResponse,
        response_model: type[ModelT],
    ) -> ModelT:
        """Parse response from prompted output.

        Args:
            response: Completion response from provider.
            response_model: Pydantic model to parse into.

        Returns:
            Parsed Pydantic model instance.

        Raises:
            ValueError: If response cannot be parsed as valid JSON.
            ValidationError: If JSON doesn't match schema.
        """
        text = extract_text(response.message)

        # Strip markdown fences if present
        cleaned = strip_markdown_fences(text)

        # Try to extract JSON from the response
        cleaned = _extract_json(cleaned)

        return response_model.model_validate_json(cleaned)
