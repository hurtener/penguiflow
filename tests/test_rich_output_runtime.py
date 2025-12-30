from __future__ import annotations

from pydantic import BaseModel

from penguiflow.node import Node
from penguiflow.registry import ModelRegistry
from penguiflow.rich_output.runtime import (
    RichOutputConfig,
    RichOutputExtension,
    attach_rich_output_nodes,
    clear_rich_output_extensions,
    configure_rich_output,
    register_rich_output_extension,
    reset_runtime,
)


def test_attach_rich_output_nodes_disabled() -> None:
    registry = ModelRegistry()
    nodes = attach_rich_output_nodes(registry, config=RichOutputConfig(enabled=False))
    assert nodes == []


def test_attach_rich_output_nodes_enabled() -> None:
    registry = ModelRegistry()
    nodes = attach_rich_output_nodes(
        registry,
        config=RichOutputConfig(enabled=True, allowlist=["markdown"], max_payload_bytes=1000, max_total_bytes=2000),
    )
    assert nodes
    assert registry.has("render_component")
    assert registry.has("list_artifacts")


def test_runtime_prompt_section() -> None:
    reset_runtime()
    runtime = configure_rich_output(RichOutputConfig(enabled=True, allowlist=["markdown"]))
    prompt = runtime.prompt_section()
    assert "`markdown`" in prompt


def test_rich_output_extensions_patch_registry_and_nodes() -> None:
    reset_runtime()
    clear_rich_output_extensions()

    try:
        class CustomArgs(BaseModel):
            message: str

        class CustomResult(BaseModel):
            ok: bool = True

        async def custom_tool(_args: CustomArgs, _ctx) -> CustomResult:
            return CustomResult()

        def register_nodes(registry: ModelRegistry):
            registry.register("custom_tool", CustomArgs, CustomResult)
            return [Node(custom_tool, name="custom_tool")]

        extension = RichOutputExtension(
            name="custom-ui",
            registry_patch={
                "sparkline": {
                    "name": "sparkline",
                    "description": "Render a tiny trend line.",
                    "category": "visualization",
                    "interactive": False,
                    "tags": ["chart", "sparkline"],
                    "propsSchema": {
                        "type": "object",
                        "required": ["data"],
                        "properties": {
                            "data": {"type": "array", "items": {"type": "number"}},
                        },
                    },
                }
            },
            prompt_extra="Custom UI tools available: custom_tool",
            register_nodes=register_nodes,
        )
        register_rich_output_extension(extension)

        registry = ModelRegistry()
        nodes = attach_rich_output_nodes(
            registry,
            config=RichOutputConfig(enabled=True, allowlist=["markdown", "sparkline"]),
        )
        runtime = configure_rich_output(RichOutputConfig(enabled=True, allowlist=["markdown", "sparkline"]))
        prompt = runtime.prompt_section()

        assert runtime.registry.get("sparkline") is not None
        assert "Custom UI tools available" in prompt
        assert registry.has("custom_tool")
        assert any(node.name == "custom_tool" for node in nodes)
    finally:
        clear_rich_output_extensions()
