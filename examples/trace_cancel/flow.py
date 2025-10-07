from __future__ import annotations

import asyncio
from typing import Any

from penguiflow import FlowEvent, Headers, Message, Node, NodePolicy, create

_started = asyncio.Event()


def build_cancel_playbook() -> tuple[Any, Any]:
    async def sub_worker(message: Message, _ctx) -> Message:
        print(f"subflow: started for trace {message.trace_id}")
        _started.set()
        try:
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            print(f"subflow: received cancellation for trace {message.trace_id}")
            raise
        return message

    node = Node(sub_worker, name="sub", policy=NodePolicy(validate="none"))
    return create(node.to()), None


async def controller(message: Message, ctx) -> Message:
    await ctx.call_playbook(build_cancel_playbook, message)
    return message


async def sink(message: Message, _ctx) -> str:
    return str(message.payload)


async def _metrics_printer(event: FlowEvent) -> None:
    if event.event_type.startswith("trace_cancel"):
        payload = event.to_payload()
        pending = payload.get("trace_pending")
        inflight = payload.get("trace_inflight")
        q_in = payload.get("q_depth_in")
        q_out = payload.get("q_depth_out")
        print(
            f"{event.event_type} pending={pending} inflight={inflight} "
            f"q_in={q_in} q_out={q_out}"
        )


async def main() -> None:
    controller_node = Node(
        controller,
        name="controller",
        policy=NodePolicy(validate="none"),
    )
    sink_node = Node(
        sink,
        name="sink",
        policy=NodePolicy(validate="none"),
    )

    flow = create(controller_node.to(sink_node))
    flow.add_middleware(_metrics_printer)
    flow.run()

    cancel_msg = Message(payload="cancel-me", headers=Headers(tenant="demo"))
    safe_msg = Message(payload="safe", headers=Headers(tenant="demo"))

    await flow.emit(cancel_msg)
    await _started.wait()
    await flow.cancel(cancel_msg.trace_id)

    await flow.emit(safe_msg)
    safe_result = await flow.fetch()
    print(f"safe result: {safe_result}")

    await flow.stop()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
