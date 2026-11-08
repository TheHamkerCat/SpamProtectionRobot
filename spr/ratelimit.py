import asyncio
from time import monotonic


class RateLimiter:
    """Spaces calls out evenly: at most `per_second` wait() returns per second.
    Callers wait in the order they arrive."""

    def __init__(self, per_second: float):
        self._interval = 1 / per_second
        self._next_at = 0.0
        self._lock = asyncio.Lock()

    async def wait(self):
        async with self._lock:
            now = monotonic()
            if self._next_at > now:
                await asyncio.sleep(self._next_at - now)
            self._next_at = max(now, self._next_at) + self._interval
