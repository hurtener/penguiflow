from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PayloadMode(str, Enum):
    AUTO = "auto"
    ENVELOPE = "envelope"


SUPPORTED_CONTENT_TYPES = ("application/a2a+json", "application/json")


@dataclass(slots=True)
class A2AConfig:
    supported_versions: tuple[str, ...] = ("0.3",)
    default_version: str | None = None
    allow_v1_aliases: bool = True
    allow_tenant_prefix: bool = True
    default_tenant: str = "default"
    payload_mode: PayloadMode = PayloadMode.AUTO
    agent_url: str | None = None

    def __post_init__(self) -> None:
        if not self.supported_versions:
            raise ValueError("supported_versions must be non-empty")
        if self.default_version is None:
            self.default_version = self.supported_versions[0]

    def is_version_supported(self, version: str) -> bool:
        return version in self.supported_versions
