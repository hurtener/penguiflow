"""ToolNode example: call an echo tool over UTCP."""

import asyncio
import json
import sys

from penguiflow.artifacts import InMemoryArtifactStore
from penguiflow.registry import ModelRegistry
from penguiflow.sessions.tool_jobs import ToolJobContext
from penguiflow.tools import (
    ExternalToolConfig,
    ToolNode,
    TransportType,
    UtcpMode,
)


async def main() -> None:
    """Run the example."""
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
    sys.stdout.write(f"Discovered tools: {[t.name for t in tools]}\n")

    if not tools:
        sys.stdout.write(
            "No tools discovered; check connectivity and utcp manual "
            "support.\n"
        )
        return

    # A standalone ToolContext: tools run here without a planner.
    ctx = ToolJobContext(
        llm_context={}, tool_context={}, artifacts=InMemoryArtifactStore()
    )
    tool_name = tools[0].name

    try:
        result = await node.call(
            tool_name, {"message": "hello from ToolNode via UTCP"}, ctx
        )
        sys.stdout.write(f"Call result:\n {json.dumps(result, indent=2)}\n")
    finally:
        await node.close()


if __name__ == "__main__":
    asyncio.run(main())
