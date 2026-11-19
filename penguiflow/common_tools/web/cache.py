"""A small in-process TTL cache."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Generic, TypeVar

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

    def __init__(
        self, *, ttl_s: float = 900.0, max_entries: int = 128
    ) -> None:
        """Configure the entry lifetime and the maximum number of entries."""
        self._ttl_s = float(ttl_s)
        self._max_entries = int(max_entries)
        self._data: OrderedDict[K, _CacheEntry[V]] = OrderedDict()

    def get(self, key: K) -> V | None:
        """Return the cached value, or ``None`` if missing or expired."""
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
        """Store ``value``, evicting the oldest entries beyond the limit."""
        now = time.time()
        self._data[key] = _CacheEntry(
            expires_at=now + self._ttl_s, value=value
        )
        self._data.move_to_end(key)
        while len(self._data) > self._max_entries:
            self._data.popitem(last=False)

    def clear(self) -> None:
        """Remove all entries."""
        self._data.clear()


def make_cache_key(*parts: object) -> str:
    """Join ``parts`` into a stable cache key (``None`` becomes empty)."""
    return "|".join("" if p is None else str(p) for p in parts)
