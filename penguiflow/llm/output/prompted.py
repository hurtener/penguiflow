"""Prompted output strategy for schema-in-prompt structured output.

Injects schema into prompt and parses/retries the response (fallback mode).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel

from ..types import (
    LLMMessage,
    LLMRequest,
    TextPart,
    extract_text,
    strip_markdown_fences,
)

if TYPE_CHECKING:
    from ..profiles import ModelProfile
    from ..schema.plan import SchemaPlan
    from ..types import CompletionResponse


class PromptedOutputStrategy:
    """Prompt-based structured output strategy (last resort).

    Injects the JSON schema into the prompt and relies on the model
    to output valid JSON. Parse validation happens after the response.

    Trade-offs:
    - Works with any model that can output text
    - Less reliable than native or tool-based modes
    - May require more retries
    - Useful when schema is too complex for other modes
    """

    SYSTEM_TEMPLATE = """You must respond with a valid JSON object matching this schema:

```json
{schema}
```

Requirements:
- Output ONLY the JSON object, nothing else
- Do not include any text before or after the JSON
- Do not wrap the JSON in markdown code fences
- Ensure all required fields are present
- Follow the exact types specified in the schema"""

    USER_TEMPLATE = """Remember: Respond with ONLY a valid JSON object matching the schema. No other text."""

    def build_request(
        self,
        model: str,
        messages: list[LLMMessage],
        response_model: type[BaseModel],
        profile: ModelProfile,
        plan: SchemaPlan,
    ) -> LLMRequest:
        """Build a request with schema injection in prompt.

        Args:
            model: Model identifier.
            messages: Conversation messages.
            response_model: Pydantic model for structured output.
            profile: Model profile with capabilities.
            plan: Schema plan with transformed schema.

        Returns:
            LLMRequest with schema injected into messages.
        """
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
                    combined_text = f"{original_text}\n\n{self.SYSTEM_TEMPLATE.format(schema=schema_str)}"
                    modified_messages.append(
                        LLMMessage(role="system", parts=[TextPart(text=combined_text)])
                    )
                else:
                    modified_messages.append(msg)
        else:
            # Add system message at the beginning
            modified_messages.append(
                LLMMessage(
                    role="system",
                    parts=[TextPart(text=self.SYSTEM_TEMPLATE.format(schema=schema_str))],
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
            temperature=0.0,
        )

    def parse_response(
        self,
        response: CompletionResponse,
        response_model: type[BaseModel],
    ) -> BaseModel:
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
        cleaned = self._extract_json(cleaned)

        return response_model.model_validate_json(cleaned)

    def _extract_json(self, text: str) -> str:
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
