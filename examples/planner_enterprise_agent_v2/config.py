"""Enterprise configuration management with environment variable support."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Literal, TypeVar

StateStoreBackend = Literal["memory", "redis", "sqlite"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
TelemetryBackend = Literal["logging", "mlflow", "datadog"]
Environment = Literal["development", "staging", "production"]

_STATE_STORE_BACKENDS: tuple[StateStoreBackend, ...] = (
    "memory",
    "redis",
    "sqlite",
)
_LOG_LEVELS: tuple[LogLevel, ...] = ("DEBUG", "INFO", "WARNING", "ERROR")
_TELEMETRY_BACKENDS: tuple[TelemetryBackend, ...] = (
    "logging",
    "mlflow",
    "datadog",
)
_ENVIRONMENTS: tuple[Environment, ...] = (
    "development",
    "staging",
    "production",
)

_Choice = TypeVar("_Choice", bound=str)


def _env_choice(
    name: str, default: _Choice, choices: tuple[_Choice, ...]
) -> _Choice:
    """Read env var ``name``, which must hold one of ``choices``.

    Raises:
        ValueError: If the variable holds a value outside ``choices``.
    """
    value = os.getenv(name, default)
    for choice in choices:
        if value == choice:
            return choice
    msg = f"{name} must be one of {choices}, got {value!r}"
    raise ValueError(msg)


@dataclass(frozen=True)
class AgentConfig:
    """Production-grade agent configuration (v2 - Enhanced).

    This v2 configuration demonstrates ALL ReactPlanner capabilities including
    reflection loops, tool policies, planning hints, and advanced features.

    All settings can be configured via environment variables with sensible
    defaults for development. In production, set all credentials explicitly.
    """

    # LLM Configuration
    llm_model: str
    llm_temperature: float
    llm_max_retries: int
    llm_timeout_s: float
    llm_max_tokens: int

    # Planner Configuration
    planner_max_iters: int
    planner_token_budget: int
    planner_deadline_s: float | None
    planner_hop_budget: int | None
    planner_absolute_max_parallel: int
    planner_repair_attempts: int

    # Summarizer LLM (cheaper model for trajectory compression)
    summarizer_model: str | None

    # Reflection Configuration (v2 FLAGSHIP FEATURE)
    reflection_enabled: bool
    reflection_llm: str | None
    reflection_quality_threshold: float
    reflection_max_revisions: int
    reflection_use_separate_llm: bool

    # Tool Policy Configuration (v2 NEW)
    tool_policy_enabled: bool
    tool_policy_allowed_tools: set[str] | None
    tool_policy_denied_tools: set[str]
    tool_policy_require_tags: set[str]

    # Planning Hints Configuration (v2 NEW)
    planning_hints_enabled: bool
    planning_hints: dict[str, object] | None

    # State Store Configuration (v2 NEW)
    state_store_enabled: bool
    state_store_backend: StateStoreBackend | None

    # Observability
    log_level: LogLevel
    enable_telemetry: bool
    telemetry_backend: TelemetryBackend
    mlflow_tracking_uri: str | None

    # LLM Client Configuration Explicitly use DSPy for structured outputs
    # (better for non-OpenAI models)
    use_dspy_client: bool

    # Application Settings
    environment: Environment
    agent_name: str

    @classmethod
    def from_env(cls) -> AgentConfig:
        """Load configuration from environment variables.

        Required environment variables:
        - OPENAI_API_KEY or other LLM provider credentials

        Optional (with defaults):
        - LLM_MODEL (default: gpt-4o-mini)
        - AGENT_ENVIRONMENT (default: development)
        - ENABLE_TELEMETRY (default: true)
        - LOG_LEVEL (default: INFO)

        V2 New Options:
        - REFLECTION_ENABLED (default: true) - Enable answer quality loop
        - REFLECTION_LLM (default: gpt-4o-mini) - Separate LLM for critique
        - TOOL_POLICY_ENABLED (default: false) - Enable tool filtering
        - PLANNING_HINTS_ENABLED (default: false) - Enable planning constraints
        - STATE_STORE_ENABLED (default: false) - Enable durable pause/resume
        """
        return cls(
            # LLM settings
            llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.0")),
            llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "3")),
            llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "360.0")),
            llm_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            # Planner settings
            planner_max_iters=int(os.getenv("PLANNER_MAX_ITERS", "15")),
            planner_token_budget=int(
                os.getenv("PLANNER_TOKEN_BUDGET", "8000")
            ),
            planner_deadline_s=_parse_optional_float(
                os.getenv("PLANNER_DEADLINE_S")
            ),
            planner_hop_budget=_parse_optional_int(
                os.getenv("PLANNER_HOP_BUDGET")
            ),
            planner_absolute_max_parallel=int(
                os.getenv("PLANNER_ABSOLUTE_MAX_PARALLEL", "50")
            ),
            planner_repair_attempts=int(
                os.getenv("PLANNER_REPAIR_ATTEMPTS", "3")
            ),
            # Summarizer
            summarizer_model=os.getenv("SUMMARIZER_MODEL", "gpt-4o-mini"),
            # Reflection (v2 FLAGSHIP)
            reflection_enabled=os.getenv("REFLECTION_ENABLED", "true").lower()
            == "true",
            reflection_llm=os.getenv("REFLECTION_LLM", "gpt-4o-mini"),
            reflection_quality_threshold=float(
                os.getenv("REFLECTION_QUALITY_THRESHOLD", "0.80")
            ),
            reflection_max_revisions=int(
                os.getenv("REFLECTION_MAX_REVISIONS", "2")
            ),
            reflection_use_separate_llm=os.getenv(
                "REFLECTION_USE_SEPARATE_LLM", "false"
            ).lower()
            == "true",
            # Tool Policy (v2)
            tool_policy_enabled=os.getenv(
                "TOOL_POLICY_ENABLED", "false"
            ).lower()
            == "true",
            tool_policy_allowed_tools=_parse_tool_list(
                os.getenv("TOOL_POLICY_ALLOWED_TOOLS")
            ),
            tool_policy_denied_tools=_parse_tool_set(
                os.getenv("TOOL_POLICY_DENIED_TOOLS", "")
            ),
            tool_policy_require_tags=_parse_tool_set(
                os.getenv("TOOL_POLICY_REQUIRE_TAGS", "")
            ),
            # Planning Hints (v2)
            planning_hints_enabled=os.getenv(
                "PLANNING_HINTS_ENABLED", "false"
            ).lower()
            == "true",
            planning_hints=_parse_planning_hints(os.getenv("PLANNING_HINTS")),
            # State Store (v2)
            state_store_enabled=os.getenv(
                "STATE_STORE_ENABLED", "false"
            ).lower()
            == "true",
            state_store_backend=_env_choice(
                "STATE_STORE_BACKEND", "memory", _STATE_STORE_BACKENDS
            ),
            # Observability
            log_level=_env_choice("LOG_LEVEL", "INFO", _LOG_LEVELS),
            enable_telemetry=os.getenv("ENABLE_TELEMETRY", "true").lower()
            == "true",
            telemetry_backend=_env_choice(
                "TELEMETRY_BACKEND", "logging", _TELEMETRY_BACKENDS
            ),
            mlflow_tracking_uri=os.getenv("MLFLOW_TRACKING_URI"),
            # LLM Client
            use_dspy_client=os.getenv("DSPY_CLIENT", "false").lower()
            == "true",
            # Application
            environment=_env_choice(
                "AGENT_ENVIRONMENT", "development", _ENVIRONMENTS
            ),
            agent_name=os.getenv("AGENT_NAME", "enterprise_agent_v2"),
        )


def _parse_optional_float(value: str | None) -> float | None:
    """Parse optional float from environment variable."""
    return float(value) if value is not None else None


def _parse_optional_int(value: str | None) -> int | None:
    """Parse optional int from environment variable."""
    return int(value) if value is not None else None


def _parse_tool_list(value: str | None) -> set[str] | None:
    """Parse comma-separated tool names into a set (whitelist)."""
    if value is None or value.strip() == "":
        return None
    return {tool.strip() for tool in value.split(",") if tool.strip()}


def _parse_tool_set(value: str) -> set[str]:
    """Parse comma-separated tool names into a set (blacklist or tags)."""
    if not value or value.strip() == "":
        return set()
    return {tool.strip() for tool in value.split(",") if tool.strip()}


def _parse_planning_hints(value: str | None) -> dict[str, object] | None:
    """Parse JSON planning hints from environment variable.

    Example:
        PLANNING_HINTS='{"ordering_hints": ["triage", "retrieve"],
        "max_parallel": 3}'
    """
    if value is None or value.strip() == "":
        return None
    try:
        hints = json.loads(value)
    except json.JSONDecodeError:
        return None
    return hints if isinstance(hints, dict) else None
