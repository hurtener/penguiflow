"""Rich output components for PenguiFlow."""

from .prompting import generate_component_system_prompt
from .registry import ComponentDefinition, ComponentRegistry, RegistryError, get_registry, load_registry
from .runtime import (
    DEFAULT_ALLOWLIST,
    RichOutputConfig,
    RichOutputExtension,
    RichOutputRuntime,
    attach_rich_output_nodes,
    clear_rich_output_extensions,
    configure_rich_output,
    get_runtime,
    list_rich_output_extensions,
    register_rich_output_extension,
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
    "RichOutputExtension",
    "RichOutputRuntime",
    "attach_rich_output_nodes",
    "clear_rich_output_extensions",
    "configure_rich_output",
    "get_runtime",
    "list_rich_output_extensions",
    "register_rich_output_extension",
    "reset_runtime",
    "generate_component_system_prompt",
]
