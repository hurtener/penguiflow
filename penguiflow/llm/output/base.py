"""Base interface shared by the structured output strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

if TYPE_CHECKING:
    from ..profiles import ModelProfile
    from ..schema.plan import SchemaPlan
    from ..types import CompletionResponse, LLMMessage, LLMRequest

ModelT = TypeVar("ModelT", bound=BaseModel)


class OutputStrategy(ABC):
    """Builds structured-output requests and parses their responses."""

    @abstractmethod
    def build_request(
        self,
        model: str,
        messages: list[LLMMessage],
        response_model: type[BaseModel],
        profile: ModelProfile,
        plan: SchemaPlan | None,
        temperature: float | None = None,
    ) -> LLMRequest:
        """Build a request that asks the model for ``response_model``.

        Args:
            model: Model identifier.
            messages: Conversation messages.
            response_model: Pydantic model for structured output.
            profile: Model profile with capabilities.
            plan: Schema plan with the transformed schema. Strategies that
                need one raise ``ValueError`` when it is ``None``.
            temperature: Optional sampling temperature (``None`` omits it).

        Returns:
            The request to send to the provider.
        """

    @abstractmethod
    def parse_response(
        self,
        response: CompletionResponse,
        response_model: type[ModelT],
    ) -> ModelT:
        """Parse a provider response into ``response_model``.

        Args:
            response: Completion response from the provider.
            response_model: Pydantic model to parse into.

        Returns:
            The validated model instance.
        """
