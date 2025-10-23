"""Enterprise configuration management with environment variable support."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AgentConfig:
    """Production-grade agent configuration.

    All settings can be configured via environment variables with sensible
    defaults for development. In production, set all credentials explicitly.
    """

    # LLM Configuration
    llm_model: str
    llm_temperature: float
    llm_max_retries: int
    llm_timeout_s: float

    # Planner Configuration
    planner_max_iters: int
    planner_token_budget: int
    planner_deadline_s: float | None
    planner_hop_budget: int | None
    planner_absolute_max_parallel: int

    # Summarizer LLM (cheaper model for trajectory compression)
    summarizer_model: str | None

    # Observability
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    enable_telemetry: bool
    telemetry_backend: Literal["logging", "mlflow", "datadog"]
    mlflow_tracking_uri: str | None

    # Application Settings
    environment: Literal["development", "staging", "production"]
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
        """
        return cls(
            # LLM settings
            llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.0")),
            llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "3")),
            llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "60.0")),
            # Planner settings
            planner_max_iters=int(os.getenv("PLANNER_MAX_ITERS", "12")),
            planner_token_budget=int(os.getenv("PLANNER_TOKEN_BUDGET", "8000")),
            planner_deadline_s=_parse_optional_float(
                os.getenv("PLANNER_DEADLINE_S")
            ),
            planner_hop_budget=_parse_optional_int(os.getenv("PLANNER_HOP_BUDGET")),
            planner_absolute_max_parallel=int(
                os.getenv("PLANNER_ABSOLUTE_MAX_PARALLEL", "50")
            ),
            # Summarizer
            summarizer_model=os.getenv("SUMMARIZER_MODEL", "gpt-4o-mini"),
            # Observability
            log_level=os.getenv("LOG_LEVEL", "INFO"),  # type: ignore
            enable_telemetry=os.getenv("ENABLE_TELEMETRY", "true").lower() == "true",
            telemetry_backend=os.getenv("TELEMETRY_BACKEND", "logging"),  # type: ignore
            mlflow_tracking_uri=os.getenv("MLFLOW_TRACKING_URI"),
            # Application
            environment=os.getenv("AGENT_ENVIRONMENT", "development"),  # type: ignore
            agent_name=os.getenv("AGENT_NAME", "enterprise_agent"),
        )


def _parse_optional_float(value: str | None) -> float | None:
    """Parse optional float from environment variable."""
    return float(value) if value is not None else None


def _parse_optional_int(value: str | None) -> int | None:
    """Parse optional int from environment variable."""
    return int(value) if value is not None else None
