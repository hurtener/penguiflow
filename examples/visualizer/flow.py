from __future__ import annotations

from pathlib import Path

from penguiflow import Node, NodePolicy, create, flow_to_dot, flow_to_mermaid


async def controller(message: str, ctx) -> str:
    """Simple controller that loops until it receives STOP."""

    if message == "STOP":
        return message
    return message


async def summarize(message: str, ctx) -> str:
    """Pretend to summarize the accumulated working memory."""

    return f"summary:{message}"


def build_flow_diagrams() -> None:
    controller_node = Node(
        controller,
        name="controller",
        allow_cycle=True,
        policy=NodePolicy(validate="none"),
    )
    summarize_node = Node(
        summarize,
        name="summarize",
        policy=NodePolicy(validate="none"),
    )

    flow = create(controller_node.to(controller_node, summarize_node))

    mermaid = flow_to_mermaid(flow, direction="LR")
    dot = flow_to_dot(flow, rankdir="LR")

    base = Path(__file__).parent
    (base / "diagram.md").write_text(f"```mermaid\n{mermaid}\n```\n", encoding="utf-8")
    (base / "diagram.dot").write_text(f"{dot}\n", encoding="utf-8")

    print("Mermaid diagram written to diagram.md")
    print("DOT diagram written to diagram.dot")


if __name__ == "__main__":  # pragma: no cover - manual example
    build_flow_diagrams()
