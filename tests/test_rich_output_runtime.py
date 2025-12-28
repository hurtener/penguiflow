from __future__ import annotations

from penguiflow.registry import ModelRegistry
from penguiflow.rich_output.runtime import RichOutputConfig, attach_rich_output_nodes, configure_rich_output, reset_runtime


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


def test_runtime_prompt_section() -> None:
    reset_runtime()
    runtime = configure_rich_output(RichOutputConfig(enabled=True, allowlist=["markdown"]))
    prompt = runtime.prompt_section()
    assert "`markdown`" in prompt
