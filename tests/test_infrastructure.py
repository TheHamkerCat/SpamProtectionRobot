import asyncio
import unittest
from time import monotonic

from spr.cache import AsyncLruCache
from spr.ratelimit import RateLimiter
from spr.workqueue import WorkQueue


class AsyncLruCacheTest(unittest.IsolatedAsyncioTestCase):
    async def test_one_computation_for_concurrent_callers(self):
        cache = AsyncLruCache(10)
        calls = []

        async def compute():
            calls.append(1)
            await asyncio.sleep(0.01)
            return "result"

        results = await asyncio.gather(*(cache.get("key", compute) for _ in range(20)))
        self.assertEqual(results, ["result"] * 20)
        self.assertEqual(len(calls), 1)
        self.assertEqual(await cache.get("key", compute), "result")
        self.assertEqual(len(calls), 1)

    async def test_least_recently_used_is_dropped(self):
        cache = AsyncLruCache(2)

        def value(v):
            async def compute():
                return v
            return compute

        await cache.get("a", value(1))
        await cache.get("b", value(2))
        await cache.get("a", value(1))
        await cache.get("c", value(3))
        self.assertEqual(await cache.get("b", value("recomputed")), "recomputed")
        self.assertEqual(await cache.get("a", value("recomputed")), "recomputed")

    async def test_failures_are_not_cached(self):
        cache = AsyncLruCache(10)

        async def fail():
            raise ValueError("down")

        async def succeed():
            return "ok"

        with self.assertRaises(ValueError):
            await cache.get("key", fail)
        self.assertEqual(await cache.get("key", succeed), "ok")


class RateLimiterTest(unittest.IsolatedAsyncioTestCase):
    async def test_calls_are_spaced(self):
        limiter = RateLimiter(per_second=50)
        started = monotonic()
        for _ in range(6):
            await limiter.wait()
        # The first call is free, then 5 intervals of 20 ms
        self.assertGreaterEqual(monotonic() - started, 0.095)


class WorkQueueTest(unittest.IsolatedAsyncioTestCase):
    async def test_at_most_workers_items_at_once(self):
        running = 0
        most = 0

        async def handle(item):
            nonlocal running, most
            running += 1
            most = max(most, running)
            await asyncio.sleep(0.01)
            running -= 1

        queue = WorkQueue(handle, workers=3, max_size=100)
        queue.start()
        for item in range(20):
            self.assertTrue(queue.submit(item))
        await queue.join()
        await queue.stop()
        self.assertEqual(most, 3)

    async def test_full_queue_drops(self):
        async def handle(item):
            await asyncio.sleep(1)

        queue = WorkQueue(handle, workers=1, max_size=2)
        results = [queue.submit(item) for item in range(5)]
        self.assertEqual(results, [True, True, False, False, False])
        self.assertEqual(queue.dropped, 3)

    async def test_failing_item_does_not_stop_the_worker(self):
        handled = []

        async def handle(item):
            if item == 0:
                raise RuntimeError("boom")
            handled.append(item)

        queue = WorkQueue(handle, workers=1, max_size=10)
        queue.start()
        for item in range(3):
            queue.submit(item)
        with self.assertLogs("spr.workqueue", level="ERROR"):
            await queue.join()
        await queue.stop()
        self.assertEqual(handled, [1, 2])
