from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


@dataclass(slots=True)
class _CacheEntry(Generic[V]):
    expires_at: float
    value: V


class TTLCache(Generic[K, V]):
    """Small in-memory TTL + LRU cache.

    Intended for tool-pack instances (not global singletons).
    """

    def __init__(self, *, ttl_s: float = 900.0, max_entries: int = 128) -> None:
        self._ttl_s = float(ttl_s)
        self._max_entries = int(max_entries)
        self._data: OrderedDict[K, _CacheEntry[V]] = OrderedDict()

    def get(self, key: K) -> V | None:
        now = time.time()
        entry = self._data.get(key)
        if entry is None:
            return None
        if entry.expires_at <= now:
            self._data.pop(key, None)
            return None
        # LRU update
        self._data.move_to_end(key)
        return entry.value

    def set(self, key: K, value: V) -> None:
        now = time.time()
        self._data[key] = _CacheEntry(expires_at=now + self._ttl_s, value=value)
        self._data.move_to_end(key)
        while len(self._data) > self._max_entries:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()


def make_cache_key(*parts: Any) -> str:
    # Keep it stable and JSON-ish without importing json here.
    return "|".join("" if p is None else str(p) for p in parts)

