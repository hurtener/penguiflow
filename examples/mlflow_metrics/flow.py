"""Demo flow that records node events to MLflow (or stdout fallback)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from penguiflow import (
    FinalAnswer,
    Headers,
    Message,
    Node,
    NodePolicy,
    PenguiFlow,
    create,
)
from penguiflow.metrics import FlowEvent


class MlflowMiddleware:
    """Forward runtime events to MLflow if available, otherwise print them."""

    def __init__(
        self,
        *,
        run_name: str = "penguiflow-demo",
        tracking_dir: str | None = None,
    ) -> None:
        self.run_name = run_name
        self.events: list[FlowEvent] = []
        self._warned = False
        self._tracking_dir = tracking_dir
        try:  # pragma: no cover - optional dependency
            import mlflow
        except ModuleNotFoundError:  # pragma: no cover - optional dependency
            self._mlflow = None
        else:  # pragma: no cover - optional dependency
            self._mlflow = mlflow
            if tracking_dir is not None:
                tracking_path = Path(tracking_dir).resolve()
                tracking_path.mkdir(parents=True, exist_ok=True)
                mlflow.set_tracking_uri(f"file:{tracking_path}")
            self._active_run = mlflow.start_run(run_name=run_name)

    async def __call__(self, event: FlowEvent) -> None:
        self.events.append(event)

        if self._mlflow is None:
            if not self._warned:
                print("MLflow not installed; capturing events locally only.")
                self._warned = True
            metrics = event.metric_samples()
            print(
                f"{event.event_type} | node={event.node_name} | metrics={metrics}"
            )
            return

        assert self._mlflow is not None  # for type-checkers
        self._mlflow.set_tags(event.tag_values())
        metrics = event.metric_samples()
        for key, value in metrics.items():
            self._mlflow.log_metric(key, value, step=int(event.ts * 1000))

    def close(self) -> None:
        if getattr(self, "_mlflow", None) is not None:
            self._mlflow.end_run()


def build_flow(middleware: MlflowMiddleware) -> tuple[PenguiFlow, Node]:
    """Assemble the demo flow and attach the provided middleware."""

    async def prepare(message: Message, _ctx) -> Message:
        meta = dict(message.meta)
        meta["prepared"] = True
        cleaned = str(message.payload).strip()
        return message.model_copy(update={"payload": cleaned, "meta": meta})

    async def score(message: Message, _ctx) -> Message:
        await asyncio.sleep(0.05)
        meta = dict(message.meta)
        meta["score"] = len(str(message.payload))
        enriched = str(message.payload).upper()
        return message.model_copy(update={"payload": enriched, "meta": meta})

    async def respond(message: Message, _ctx) -> Message:
        final = FinalAnswer(text=f"Tracked: {message.payload}", citations=["mlflow"])
        return message.model_copy(update={"payload": final})

    prepare_node = Node(prepare, name="prepare", policy=NodePolicy(validate="none"))
    score_node = Node(score, name="score", policy=NodePolicy(validate="none"))
    respond_node = Node(respond, name="respond", policy=NodePolicy(validate="none"))

    flow = create(prepare_node.to(score_node), score_node.to(respond_node))
    flow.add_middleware(middleware)
    return flow, respond_node


async def main() -> None:
    middleware = MlflowMiddleware(tracking_dir=Path(__file__).parent / "mlruns")
    flow, _ = build_flow(middleware)
    flow.run()

    headers = Headers(tenant="demo", topic="metrics")
    message = Message(payload="observe penguiflow", headers=headers)

    await flow.emit(message)
    result = await flow.fetch()
    await flow.stop()
    middleware.close()

    if isinstance(result, Message) and isinstance(result.payload, FinalAnswer):
        print(f"Final answer: {result.payload.text}")
    else:
        print(f"Result: {result}")

    print(f"Captured {len(middleware.events)} events.")


if __name__ == "__main__":
    asyncio.run(main())
