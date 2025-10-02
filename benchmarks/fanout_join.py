"""Macro benchmark: fan-out workers + join_k with streaming summary."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
import tracemalloc
from pathlib import Path
from typing import Any

from penguiflow import Headers, Message, Node, NodePolicy, StreamChunk, create, join_k


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * pct
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


async def fan(msg: Message, _ctx) -> Message:
    return msg


def make_worker(suffix: str, delay: float = 0.0):
    async def worker(msg: Message, _ctx) -> Message:
        if delay:
            await asyncio.sleep(delay)
        return msg.model_copy(update={"payload": f"{msg.payload}::{suffix}"})

    return worker


async def summarize(msg: Message, ctx) -> None:
    parts = [str(part) for part in msg.payload]
    for idx, part in enumerate(parts):
        await ctx.emit_chunk(
            parent=msg,
            text=part,
            stream_id=msg.trace_id,
            done=idx == len(parts) - 1,
        )


async def run_benchmark(
    total_messages: int, worker_latency_ms: float, branches: int
) -> dict[str, Any]:
    fan_node = Node(fan, name="fan", policy=NodePolicy(validate="none"))

    worker_nodes = []
    for idx in range(branches):
        worker = make_worker(chr(ord("A") + idx), worker_latency_ms / 1000.0)
        worker_node = Node(worker, name=f"worker-{idx}", policy=NodePolicy(validate="none"))
        worker_nodes.append(worker_node)

    join_node = join_k("join", branches)
    summarize_node = Node(summarize, name="summarize", policy=NodePolicy(validate="none"))

    adjacencies = [fan_node.to(*worker_nodes)]
    for node in worker_nodes:
        adjacencies.append(node.to(join_node))
    adjacencies.append(join_node.to(summarize_node))
    adjacencies.append(summarize_node.to())

    flow = create(*adjacencies, queue_maxsize=32)
    flow.run()

    headers = Headers(tenant="bench")
    latencies_ms: list[float] = []
    tokens = 0

    tracemalloc.start()
    start_wall = time.perf_counter()

    try:
        for i in range(total_messages):
            message = Message(payload=f"msg-{i}", headers=headers)
            start = time.perf_counter()
            await flow.emit(message)
            for _ in range(branches):
                streamed = await asyncio.wait_for(flow.fetch(), timeout=5.0)
                chunk = streamed.payload
                assert isinstance(chunk, StreamChunk)
                tokens += 1
                if chunk.done:
                    latencies_ms.append((time.perf_counter() - start) * 1000)
    finally:
        elapsed_wall = time.perf_counter() - start_wall
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        await flow.stop()

    tokens_per_sec = tokens / elapsed_wall if elapsed_wall > 0 else float("inf")

    return {
        "benchmark": "fanout_join",
        "config": {
            "messages": total_messages,
            "worker_latency_ms": worker_latency_ms,
            "branches": branches,
        },
        "latency_ms": {
            "p50": percentile(latencies_ms, 0.5),
            "p95": percentile(latencies_ms, 0.95),
            "mean": statistics.fmean(latencies_ms) if latencies_ms else 0.0,
            "min": min(latencies_ms) if latencies_ms else 0.0,
            "max": max(latencies_ms) if latencies_ms else 0.0,
        },
        "wall_time_s": elapsed_wall,
        "tokens_per_sec": tokens_per_sec,
        "tokens_emitted": tokens,
        "memory_bytes_peak": peak,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--messages", type=int, default=800, help="Number of ingress messages to emit"
    )
    parser.add_argument(
        "--worker-latency-ms",
        type=float,
        default=0.5,
        help="Synthetic latency added to each worker (milliseconds)",
    )
    parser.add_argument(
        "--branches", type=int, default=2, help="Number of fan-out branches to join"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write the JSON results",
    )
    return parser.parse_args()


if __name__ == "__main__":  # pragma: no cover
    arguments = parse_args()
    results = asyncio.run(run_benchmark(arguments.messages, arguments.worker_latency_ms, arguments.branches))
    payload = json.dumps(results, indent=2)
    print(payload)
    if arguments.output:
        arguments.output.write_text(payload)
