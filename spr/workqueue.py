"""A bounded queue worked by a fixed number of tasks. Telegram can
deliver messages faster than they can be checked; then new messages are
dropped instead of piling up in memory, and at most `workers` checks
(database connections, ARQ requests, images in memory) run at a time."""
import asyncio
import logging

logger = logging.getLogger(__name__)

# Log a dropped-messages warning at the first drop and then every this many
DROP_LOG_EVERY = 1000


class WorkQueue:
    def __init__(self, handle, workers: int, max_size: int, name: str = "work"):
        self._handle = handle
        self._worker_count = workers
        self._queue = asyncio.Queue(max_size)
        self._workers = []
        self._name = name
        self.dropped = 0

    def __len__(self) -> int:
        return self._queue.qsize()

    def submit(self, item) -> bool:
        """Queue an item. False (and the item is dropped) if the queue is full."""
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped % DROP_LOG_EVERY == 1:
                logger.warning(
                    "The %s queue is full, %s items dropped so far", self._name, self.dropped
                )
            return False
        return True

    def start(self):
        self._workers = [
            asyncio.create_task(self._work()) for _ in range(self._worker_count)
        ]

    async def join(self):
        """Wait until every queued item was handled"""
        await self._queue.join()

    async def stop(self):
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        if self._queue.qsize():
            logger.info("Stopped with %s unhandled items in the %s queue", self._queue.qsize(), self._name)

    async def _work(self):
        while True:
            item = await self._queue.get()
            try:
                await self._handle(item)
            except Exception:
                logger.exception("Handling a %s queue item failed", self._name)
            finally:
                self._queue.task_done()
