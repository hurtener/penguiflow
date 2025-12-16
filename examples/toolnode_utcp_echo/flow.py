import asyncio
import json

from penguiflow.registry import ModelRegistry
from penguiflow.tools import ExternalToolConfig, ToolNode, TransportType, UtcpMode


class DummyCtx:
    """Minimal ToolContext-compatible stub for examples."""

    def __init__(self) -> None:
        self.tool_context = {}
        self.llm_context = {}
        self.meta = {}

    async def pause(self, reason, payload=None):
        return None

    async def emit_chunk(self, *args, **kwargs):
        return None

    async def emit_artifact(self, *args, **kwargs):
        return None


async def main() -> None:
    config = ExternalToolConfig(
        name="echo",
        transport=TransportType.UTCP,
        connection="https://httpbin.org/anything/echo",
        utcp_mode=UtcpMode.BASE_URL,
    )

    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)
    await node.connect()

    tools = node.get_tools()
    print(f"Discovered tools: {[t.name for t in tools]}")

    if not tools:
        print("No tools discovered; check connectivity and utcp manual support.")
        return

    ctx = DummyCtx()
    tool_name = tools[0].name

    try:
        result = await node.call(tool_name, {"message": "hello from ToolNode via UTCP"}, ctx)
        print("Call result:\n", json.dumps(result, indent=2))
    finally:
        await node.close()


if __name__ == "__main__":
    asyncio.run(main())
