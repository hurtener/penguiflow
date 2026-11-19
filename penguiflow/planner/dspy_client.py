"""DSPy-based LLM client for ReactPlanner with robust structured outputs.

This module provides a DSPy-powered alternative to direct LiteLLM calls,
offering better structured output handling across different LLM providers.
DSPy's signature system with Pydantic models works reliably even with
providers that don't support native JSON schema mode (like Databricks).
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from typing_extensions import override

from .models import ReasoningJSONLLMClient

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

logger = logging.getLogger(__name__)


# Marks text that is neither JSON nor a Python literal.
_UNPARSEABLE = object()


def _strip_code_fences(text: str) -> str:
    """Return the first non-empty fenced block (minus a ``json`` tag)."""
    for part in text.split("```"):
        stripped = part.strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
        if stripped:
            return stripped
    return ""


def _parse_lenient(candidate: str) -> object:
    """Parse JSON, falling back to a Python literal (single quotes, etc.)."""
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(candidate)
    except Exception:
        return _UNPARSEABLE


class DSPyLLMClient(ReasoningJSONLLMClient):
    """LLM client using DSPy for structured outputs.

    Deprecated:
        DSPyLLMClient is deprecated and unmaintained as of 3.11.0a6. It is no
        longer part of PenguiFlow's supported LLM surface and does not
        participate in built-in rate-limit fallback (``llm_fallback``). Use the
        native LLM layer (default) or ``transport="pydantic-ai"`` instead; this
        class will be removed in a future release.

    This client implements the JSONLLMClient protocol and uses DSPy's signature
    system to generate structured outputs. DSPy handles the prompt engineering
    and parsing internally, providing more reliable structured outputs across
    different LLM providers.

    Benefits over direct LiteLLM:
    - Better structured output reliability across providers
    - Automatic prompt optimization for structure extraction
    - Works with models that don't support native JSON schema mode
    - Graceful degradation with retry logic

    Args:
        llm: Model identifier (e.g., "gpt-4o-mini",
            "databricks/databricks-gpt-oss-120b")
        temperature: Sampling temperature (0.0 = deterministic)
        max_retries: Number of retry attempts for transient failures
        timeout_s: Timeout per LLM call in seconds

    Example:
        >>> client = DSPyLLMClient(
        ...     llm="databricks/databricks-gpt-oss-120b",
        ...     temperature=0.0,
        ... )
        response = await client.complete(
            messages=[{"role": "user", "content": "..."}],
            response_format={"type": "json_schema", "json_schema": {...}},
        )
    """

    expects_json_schema = True

    def __init__(
        self,
        llm: str | dict[str, Any],
        *,
        output_schema: type[BaseModel] | None = None,
        temperature: float = 0.0,
        max_retries: int = 3,
        timeout_s: float = 360.0,
        max_tokens: int = 4096,
    ) -> None:
        """Create a DSPy-backed client for ``llm`` (deprecated)."""
        import warnings

        warnings.warn(
            "DSPyLLMClient is deprecated and unmaintained; it is no "
            "longer part of "
            "PenguiFlow's supported LLM surface and does not participate in "
            "built-in "
            "rate-limit fallback (llm_fallback). Use the native LLM layer "
            "(default) or "
            "transport='pydantic-ai' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._llm = llm
        self._output_schema = (
            output_schema  # Will default to PlannerAction if None
        )
        self._temperature = temperature
        self._max_retries = max_retries
        self._timeout_s = timeout_s
        self._max_tokens = max_tokens
        self._dspy_module: Any = None
        self._lm: Any = None

    @classmethod
    def from_base_client(
        cls,
        base_client: DSPyLLMClient,
        output_schema: type[BaseModel],
    ) -> DSPyLLMClient:
        """Create a new DSPy client with a different output schema.

        This factory method allows creating separate clients for different
        tasks (e.g., reflection, summarization) while reusing the same LLM
        configuration.

        Args:
            base_client: Existing DSPy client to clone configuration from
            output_schema: New output schema for the cloned client

        Returns:
            New DSPyLLMClient instance with same config but different output
            schema

        Example:
            >>> planner_client = DSPyLLMClient(llm="gpt-4o")
            >>> reflection_client = DSPyLLMClient.from_base_client(
            ...     planner_client, ReflectionCritique
            ... )
        """
        return cls(
            llm=base_client._llm,
            output_schema=output_schema,
            temperature=base_client._temperature,
            max_retries=base_client._max_retries,
            timeout_s=base_client._timeout_s,
            max_tokens=base_client._max_tokens,
        )

    def _ensure_dspy_initialized(self) -> None:
        """Lazy-initialize DSPy to avoid import overhead."""
        if self._lm is not None:
            return

        try:
            import dspy
        except ModuleNotFoundError as exc:  # pragma: no cover
            msg = (
                "DSPy is not installed. Install penguiflow-lite[planner] "
                "or provide a custom llm_client."
            )
            raise RuntimeError(msg) from exc

        # Configure DSPy LM
        model_name = (
            self._llm if isinstance(self._llm, str) else self._llm["model"]
        )

        # DSPy uses LiteLLM under the hood, so all LiteLLM model names work
        self._lm = dspy.LM(
            model=model_name,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )

        logger.info(
            "dspy_lm_initialized",
            extra={"model": model_name, "temperature": self._temperature},
        )

    def _create_signature(
        self, response_format: Mapping[str, Any] | None
    ) -> type[Any]:
        """Create a DSPy signature with dynamic output schema.

        Args:
            response_format: OpenAI-style response_format (used to detect
                schema mode)

        Returns:
            DSPy Signature class with output type from self._output_schema
        """
        import dspy

        # Import at runtime to avoid circular dependency
        from penguiflow.planner.react import PlannerAction

        # Determine output schema: use provided schema or default to
        # PlannerAction
        output_schema = (
            self._output_schema
            if self._output_schema is not None
            else PlannerAction
        )

        if not response_format or "json_schema" not in response_format:
            # Fallback: simple string output for non-schema requests
            attrs = {
                "__doc__": "Generate a response.",
                "__annotations__": {"messages": str, "response": str},
                "messages": dspy.InputField(),
                "response": dspy.OutputField(),
            }
            return type("TextOutputSignature", (dspy.Signature,), attrs)

        # Use output_schema directly - no schema conversion needed! DSPy will
        # handle the Pydantic model → JSON → Pydantic validation
        schema_name = output_schema.__name__
        attrs = {
            "__doc__": "Generate a structured "
            f"{schema_name} output with proper "
            "type safety.",
            "__annotations__": {"messages": str, "response": output_schema},
            "messages": dspy.InputField(
                desc=(
                    "Conversation history and user query requiring "
                    "structured output"
                )
            ),
            "response": dspy.OutputField(
                desc=f"Structured {schema_name} output"
            ),
        }
        return type(f"{schema_name}Signature", (dspy.Signature,), attrs)

    def _messages_to_text(self, messages: Sequence[Mapping[str, Any]]) -> str:
        """Convert OpenAI-style messages to a single text prompt.

        Args:
            messages: List of message dicts with role and content

        Returns:
            Concatenated text suitable for DSPy input
        """
        parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                parts.append(f"System: {content}")
            elif role == "user":
                parts.append(f"User: {content}")
            elif role == "assistant":
                parts.append(f"Assistant: {content}")
            else:
                parts.append(content)
        return "\n\n".join(parts)

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
        on_reasoning_chunk: Callable[[str, bool], None] | None = None,
    ) -> tuple[str, float]:
        """Generate completion with structured output via DSPy.

        DSPy does not stream: ``stream`` and the chunk callbacks are
        accepted for protocol compatibility and ignored.

        Args:
            messages: OpenAI-style message list
            response_format: Optional JSON schema for structured output
            stream: Ignored.
            on_stream_chunk: Ignored.
            on_reasoning_chunk: Ignored.

        Returns:
            Tuple of JSON string response and cost in USD (DSPy cost is 0.0)

        Raises:
            RuntimeError: If all retry attempts fail
        """
        import dspy

        self._ensure_dspy_initialized()

        # Create signature based on response format
        signature_class = self._create_signature(response_format)

        # Create DSPy predictor
        predictor = dspy.Predict(signature_class)

        # Convert messages to text
        input_text = self._messages_to_text(messages)

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                async with asyncio.timeout(self._timeout_s):
                    result = await self._predict(predictor, input_text)
                    return self._response_json(result), 0.0
            except TimeoutError as exc:
                last_error = exc
                logger.warning(
                    "dspy_timeout",
                    extra={
                        "attempt": attempt + 1,
                        "timeout_s": self._timeout_s,
                    },
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "dspy_error",
                    extra={
                        "attempt": attempt + 1,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )

            # Exponential backoff
            if attempt < self._max_retries - 1:
                await asyncio.sleep(2**attempt)

        # All retries exhausted
        error_msg = f"DSPy LLM call failed after {self._max_retries} attempts"
        if last_error:
            error_msg += f": {last_error}"
        raise RuntimeError(error_msg)

    async def _predict(
        self, predictor: Callable[..., object], input_text: str
    ) -> object:
        """Run the DSPy predictor in an executor (DSPy is synchronous)."""
        import dspy

        loop = asyncio.get_running_loop()

        def _run_dspy() -> object:
            with dspy.context(lm=self._lm):
                return predictor(messages=input_text)

        return await loop.run_in_executor(None, _run_dspy)

    def _response_json(self, result: object) -> str:
        """Serialize the prediction's ``response`` field as a JSON string.

        Raises:
            RuntimeError: If there is no response or it is not JSON.
        """
        if not hasattr(result, "response"):
            msg = "DSPy returned no response field"
            raise RuntimeError(msg)
        response_obj = result.response
        logger.debug(
            "dspy_response_extracted",
            extra={
                "response_type": type(response_obj).__name__,
                "response_preview": str(response_obj)[:200],
            },
        )
        if isinstance(response_obj, BaseModel):
            # PlannerAction or other Pydantic model - already validated!
            json_output = response_obj.model_dump_json()
            logger.debug(
                "dspy_pydantic_success",
                extra={
                    "model": type(response_obj).__name__,
                    "json_length": len(json_output),
                },
            )
            return json_output
        if isinstance(response_obj, dict):
            return json.dumps(response_obj)
        # DSPy sometimes returns string - normalise to JSON
        response_str = str(response_obj)
        logger.debug(
            "dspy_string_response",
            extra={"response_preview": response_str[:500]},
        )
        normalised = self._normalise_json(response_str)
        if normalised is not None:
            logger.debug("dspy_json_normalised_success")
            return normalised
        logger.warning(
            "dspy_invalid_json",
            extra={"response": response_str[:500]},
        )
        msg = "DSPy returned output that could not be coerced to JSON"
        raise RuntimeError(msg)

    def _normalise_json(self, text: str) -> str | None:
        """Attempt to coerce arbitrary text into canonical JSON string."""
        candidate = text.strip()
        if not candidate:
            return None
        if candidate.startswith("```"):
            candidate = _strip_code_fences(candidate) or text.strip("` \n")
        # Extract substring bounded by braces if extra commentary exists
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = candidate[start : end + 1]
        payload = _parse_lenient(candidate)
        if payload is _UNPARSEABLE:
            return None
        try:
            return json.dumps(payload)
        except TypeError:
            return None
