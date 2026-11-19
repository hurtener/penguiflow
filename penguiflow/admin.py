"""Developer CLI helpers for inspecting PenguiFlow trace history."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Sequence

    from .state import StoredEvent

from .state import StateStore

__all__ = ["load_state_store", "main", "render_events"]


class _Args(argparse.Namespace):
    handler: Callable[[_Args], Coroutine[None, None, None]]
    state_store: str
    trace_id: str
    tail: int | None
    delay: float


def _resolve_factory(spec: str) -> Callable[[], object]:
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        msg = "state store spec must be in the form 'package.module:callable'"
        raise ValueError(msg)
    module = importlib.import_module(module_name)
    try:
        factory: object = getattr(module, attr)
    except AttributeError as exc:  # pragma: no cover - defensive guard
        msg = f"{spec!r} does not resolve to a callable"
        raise ValueError(msg) from exc
    if not callable(factory):
        msg = f"{spec!r} resolved to {type(factory)!r}, not a callable"
        raise TypeError(msg)
    return factory


async def load_state_store(spec: str) -> StateStore:
    """Instantiate a :class:`StateStore` from ``module:callable`` spec."""
    factory = _resolve_factory(spec)
    instance = factory()
    if asyncio.iscoroutine(instance):
        instance = await instance
    if not isinstance(instance, StateStore):  # pragma: no cover
        msg = (
            "StateStore factories must implement "
            "save_event/load_history/save_remote_binding"
        )
        raise TypeError(msg)
    return instance


def _trim_events(
    events: Sequence[StoredEvent], tail: int | None
) -> list[StoredEvent]:
    items = list(events)
    if tail is None:
        return items
    if tail <= 0:
        return []
    return items[-tail:]


def render_events(
    events: Sequence[StoredEvent], *, tail: int | None = None
) -> list[str]:
    """Return JSON lines for ``events``, optionally keeping only the tail."""
    trimmed = _trim_events(events, tail)
    lines: list[str] = []
    for event in trimmed:
        payload = dict(event.payload)
        payload.setdefault("event", event.kind)
        payload.setdefault("trace_id", event.trace_id)
        payload.setdefault("node_name", event.node_name)
        payload.setdefault("node_id", event.node_id)
        payload.setdefault("ts", event.ts)
        lines.append(json.dumps(payload, sort_keys=True, default=str))
    return lines


async def _cmd_history(args: _Args) -> None:
    store = await load_state_store(args.state_store)
    events = await store.load_history(args.trace_id)
    for line in render_events(events, tail=args.tail):
        sys.stdout.write(f"{line}\n")


async def _cmd_replay(args: _Args) -> None:
    store = await load_state_store(args.state_store)
    events = _trim_events(await store.load_history(args.trace_id), args.tail)
    total = len(events)
    if not total:
        sys.stdout.write(f"# trace {args.trace_id} has no stored events\n")
        return
    sys.stdout.write(f"# replay trace={args.trace_id} events={total}\n")
    for event in events:
        payload = render_events([event])[0]
        sys.stdout.write(f"{payload}\n")
        if args.delay > 0:
            await asyncio.sleep(args.delay)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="penguiflow-admin",
        description=(
            "Inspect PenguiFlow trace history via configured "
            "StateStore adapters."
        ),
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--state-store",
        required=True,
        help=(
            "Import path to a factory returning a StateStore (module:callable)"
        ),
    )
    common.add_argument(
        "--tail",
        type=int,
        default=None,
        help="Only show the last N events from the trace history.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    history = subparsers.add_parser(
        "history",
        parents=[common],
        help="Print stored events for a trace as JSON lines.",
    )
    history.add_argument("trace_id", help="Trace identifier to inspect")
    history.set_defaults(handler=_cmd_history)

    replay = subparsers.add_parser(
        "replay",
        parents=[common],
        help="Replay events with optional delay to mimic runtime emission.",
    )
    replay.add_argument("trace_id", help="Trace identifier to replay")
    replay.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Sleep duration (seconds) between events when replaying.",
    )
    replay.set_defaults(handler=_cmd_replay)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``penguiflow-admin`` CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:  # pragma: no cover - argparse guard
        parser.print_help()
        return 1

    try:
        asyncio.run(handler(args))
    except Exception as exc:  # pragma: no cover - runtime guard
        sys.stderr.write(f"error: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - manual invocation
    raise SystemExit(main())
