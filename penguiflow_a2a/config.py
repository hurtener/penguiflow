"""A2A service configuration."""

from __future__ import annotations

from dataclasses import dataclass

SUPPORTED_CONTENT_TYPES = ("application/a2a+json", "application/json")


@dataclass(slots=True)
class A2AConfig:
    """Protocol versions, extensions and routing options of an A2A service."""

    supported_versions: tuple[str, ...] = ("0.3",)
    default_version: str | None = None
    allow_v1_aliases: bool = True
    allow_tenant_prefix: bool = True
    default_tenant: str = "default"
    agent_url: str | None = None

    def __post_init__(self) -> None:
        """Validate the supported protocol versions."""
        if not self.supported_versions:
            msg = "supported_versions must be non-empty"
            raise ValueError(msg)
        if self.default_version is None:
            self.default_version = self.supported_versions[0]

    def is_version_supported(self, version: str) -> bool:
        """Whether ``version`` is a supported protocol version."""
        return version in self.supported_versions
