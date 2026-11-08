import asyncio
from collections import OrderedDict
from typing import Awaitable, Callable, Hashable


class AsyncLruCache:
    """Results of async lookups, the least recently used dropped first.

    Callers asking for a key that is being computed wait for that one
    computation, so a spam raid of 100 identical messages costs one lookup.
    Failed lookups are not cached."""

    def __init__(self, max_size: int):
        self._max_size = max_size
        self._results = OrderedDict()
        self._running = {}
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        return len(self._results)

    async def get(self, key: Hashable, compute: Callable[[], Awaitable]):
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

    def _store(self, key, result):
        self._results[key] = result
        self._results.move_to_end(key)
        while len(self._results) > self._max_size:
            self._results.popitem(last=False)
