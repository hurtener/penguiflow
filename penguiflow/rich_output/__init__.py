"""Rich output components for PenguiFlow."""

from .prompting import generate_component_system_prompt
from .registry import ComponentDefinition, ComponentRegistry, RegistryError, get_registry, load_registry
from .runtime import (
    DEFAULT_ALLOWLIST,
    RichOutputConfig,
    RichOutputRuntime,
    attach_rich_output_nodes,
    configure_rich_output,
    get_runtime,
    reset_runtime,
)

__all__ = [
    "ComponentDefinition",
    "ComponentRegistry",
    "RegistryError",
    "get_registry",
    "load_registry",
    "DEFAULT_ALLOWLIST",
    "RichOutputConfig",
    "RichOutputRuntime",
    "attach_rich_output_nodes",
    "configure_rich_output",
    "get_runtime",
    "reset_runtime",
    "generate_component_system_prompt",
]
