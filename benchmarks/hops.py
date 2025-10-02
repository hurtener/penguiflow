"""Microbenchmark for hop latency and streaming throughput."""

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

from penguiflow import Headers, Message, Node, NodePolicy, StreamChunk, create


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


async def _identity(msg: Message, _ctx) -> Message:
    return msg


async def _streamer(msg: Message, ctx) -> None:
    tokens = list(msg.payload)
    for idx, token in enumerate(tokens):
        await ctx.emit_chunk(
            parent=msg,
            text=str(token),
            stream_id=msg.trace_id,
            done=idx == len(tokens) - 1,
        )


async def _stream_sink(message: Message, _ctx) -> StreamChunk:
    chunk = message.payload
    assert isinstance(chunk, StreamChunk)
    return chunk


async def run_hop_benchmark(hops: int, messages: int) -> dict[str, Any]:
    hop_nodes = [
        Node(
            _identity,
            name=f"hop-{idx}",
            policy=NodePolicy(validate="none"),
        )
        for idx in range(hops)
    ]
    sink_node = Node(
        _identity,
        name="sink",
        policy=NodePolicy(validate="none"),
    )

    adjacencies = []
    chain = hop_nodes + [sink_node]
    for current, nxt in zip(chain, chain[1:], strict=True):
        adjacencies.append(current.to(nxt))
    adjacencies.append(sink_node.to())

    flow = create(*adjacencies)
    flow.run()

    tracemalloc.start()
    latencies_us: list[float] = []
    headers = Headers(tenant="bench")
    start_wall = time.perf_counter()

    try:
        for _ in range(messages):
            message = Message(payload="ping", headers=headers)
            start = time.perf_counter_ns()
            await flow.emit(message)
            await asyncio.wait_for(flow.fetch(), timeout=5.0)
            elapsed_us = (time.perf_counter_ns() - start) / 1000
            latencies_us.append(elapsed_us)
    finally:
        elapsed_wall = time.perf_counter() - start_wall
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        await flow.stop()

    throughput = messages / elapsed_wall if elapsed_wall > 0 else float("inf")

    return {
        "benchmark": "hops",
        "config": {"hops": hops, "messages": messages},
        "latency_us": {
            "p50": percentile(latencies_us, 0.5),
            "p95": percentile(latencies_us, 0.95),
            "mean": statistics.fmean(latencies_us) if latencies_us else 0.0,
            "min": min(latencies_us) if latencies_us else 0.0,
            "max": max(latencies_us) if latencies_us else 0.0,
        },
        "wall_time_s": elapsed_wall,
        "messages_per_sec": throughput,
        "memory_bytes_peak": peak,
    }


async def run_streaming_benchmark(
    tokens_per_message: int, messages: int
) -> dict[str, Any]:
    stream_node = Node(
        _streamer,
        name="streamer",
        policy=NodePolicy(validate="none"),
    )
    sink_node = Node(
        _stream_sink,
        name="stream-sink",
        policy=NodePolicy(validate="none"),
    )
    flow = create(stream_node.to(sink_node), sink_node.to())
    flow.run()

    headers = Headers(tenant="bench")
    tokens_emitted = 0
    latencies_ms: list[float] = []
    start_wall = time.perf_counter()

    tracemalloc.start()
    try:
        for _ in range(messages):
            payload = [f"tok-{idx}" for idx in range(tokens_per_message)]
            parent = Message(payload=payload, headers=headers)
            start = time.perf_counter()
            await flow.emit(parent)
            for _ in range(tokens_per_message):
                streamed = await asyncio.wait_for(flow.fetch(), timeout=5.0)
                chunk = (
                    streamed
                    if isinstance(streamed, StreamChunk)
                    else getattr(streamed, "payload", streamed)
                )
                assert isinstance(chunk, StreamChunk)
                tokens_emitted += 1
                if chunk.done:
                    latencies_ms.append((time.perf_counter() - start) * 1000)
    finally:
        elapsed_wall = time.perf_counter() - start_wall
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        await flow.stop()

    tokens_per_sec = tokens_emitted / elapsed_wall if elapsed_wall > 0 else float("inf")

    return {
        "tokens_per_message": tokens_per_message,
        "messages": messages,
        "tokens_emitted": tokens_emitted,
        "latency_ms": {
            "p50": percentile(latencies_ms, 0.5),
            "p95": percentile(latencies_ms, 0.95),
            "mean": statistics.fmean(latencies_ms) if latencies_ms else 0.0,
            "min": min(latencies_ms) if latencies_ms else 0.0,
            "max": max(latencies_ms) if latencies_ms else 0.0,
        },
        "wall_time_s": elapsed_wall,
        "tokens_per_sec": tokens_per_sec,
        "memory_bytes_peak": peak,
    }


async def main(args: argparse.Namespace) -> dict[str, Any]:
    hop_results = await run_hop_benchmark(args.hops, args.messages)
    streaming_results = await run_streaming_benchmark(
        args.stream_tokens, args.stream_messages
    )
    hop_results["streaming"] = streaming_results
    return hop_results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hops",
        type=int,
        default=4,
        help="Number of hop nodes to chain",
    )
    parser.add_argument(
        "--messages",
        type=int,
        default=1000,
        help="Number of messages to emit for hop latency",
    )
    parser.add_argument(
        "--stream-tokens",
        type=int,
        default=32,
        help="Tokens per message in the streaming benchmark",
    )
    parser.add_argument(
        "--stream-messages",
        type=int,
        default=200,
        help="Number of messages for the streaming benchmark",
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
    results = asyncio.run(main(arguments))
    payload = json.dumps(results, indent=2)
    print(payload)
    if arguments.output:
        arguments.output.write_text(payload)
