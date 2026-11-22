import asyncio
import time
from collections import OrderedDict
from typing import Awaitable, Callable, Hashable, Optional


class AsyncLruCache:
    """Results of async lookups, the least recently used dropped first.

    Callers asking for a key that is being computed wait for that one
    computation, so a spam raid of 100 identical messages costs one lookup.
    Failed lookups are not cached. With max_age (seconds), older results
    count as missing and are looked up again."""

    def __init__(self, max_size: int, max_age: Optional[float] = None):
        self._max_size = max_size
        self._max_age = max_age
        self._stored_at = {}
        self._results = OrderedDict()
        self._running = {}
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        return len(self._results)

    async def get(self, key: Hashable, compute: Callable[[], Awaitable]):
        if key in self._results and self._is_stale(key):
            del self._results[key]
            self._stored_at.pop(key, None)
        if key in self._results:
            self._results.move_to_end(key)
            self.hits += 1
            return self._results[key]
        task = self._running.get(key)
        if task is not None:
            self.hits += 1
            return await asyncio.shield(task)

        self.misses += 1
        task = asyncio.ensure_future(compute())
        self._running[key] = task
        try:
            result = await asyncio.shield(task)
        finally:
            self._running.pop(key, None)
        self._store(key, result)
        return result

    def _is_stale(self, key) -> bool:
        if self._max_age is None:
            return False
        stored_at = self._stored_at.get(key)
        if stored_at is None:
            return True
        return time.monotonic() - stored_at >= self._max_age

    def _store(self, key, result):
        self._results[key] = result
        self._results.move_to_end(key)
        if self._max_age is not None:
            self._stored_at[key] = time.monotonic()
        while len(self._results) > self._max_size:
            old_key, _ = self._results.popitem(last=False)
            self._stored_at.pop(old_key, None)
