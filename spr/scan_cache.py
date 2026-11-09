"""Two layers of ARQ spam results, so repeated texts cost one request:

1. Memory (SPAM_MEMORY_CACHE_MB): every text, recent ones. Spam raids
   repeat long texts within minutes, this layer catches them.
2. Postgres (SPAM_DISK_CACHE_MB): short texts only, since those are the
   ones that keep coming back ("good morning everyone"). Survives
   restarts. Stores a hash of the text, never the text.

Both layers are per ARQ model version: after ARQ is retrained, every text
is scanned again by the new model.
"""
import asyncio
import hashlib
import logging
from datetime import timedelta

from spr.cache import AsyncLruCache

logger = logging.getLogger(__name__)

# Measured: an entry in memory takes about 330 bytes, a row in Postgres
# (with its indexes) about 190 bytes
MEMORY_BYTES_PER_ENTRY = 330
DISK_BYTES_PER_ROW = 190
# Texts up to this many characters (after normalizing) go to Postgres
DISK_CACHE_MAX_TEXT_LENGTH = 64
# Results unused this long are deleted
DISK_CACHE_MAX_AGE = timedelta(days=30)
STATS_INTERVAL_SECONDS = 600
PRUNE_EVERY_STATS_INTERVALS = 6  # every hour


def normalize(text: str) -> str:
    """The same text in other case or spacing is the same text"""
    return " ".join(text.casefold().split())


def text_key(text: str) -> bytes:
    return hashlib.blake2b(normalize(text).encode(), digest_size=16).digest()


def megabytes_to_entries(megabytes: int, bytes_per_entry: int) -> int:
    return megabytes * 1024 * 1024 // bytes_per_entry


def hit_rate(hits: int, misses: int) -> str:
    total = hits + misses
    if not total:
        return "no lookups"
    return f"{hits * 100 // total} % hits ({hits}/{total})"


class SpamResultCache:
    def __init__(self, memory_mb: int, disk_mb: int, db):
        self._memory = AsyncLruCache(megabytes_to_entries(memory_mb, MEMORY_BYTES_PER_ENTRY))
        self._db = db if disk_mb else None
        self.disk_max_rows = megabytes_to_entries(disk_mb, DISK_BYTES_PER_ROW)
        self.disk_hits = 0
        self.disk_misses = 0

    @property
    def disk_enabled(self) -> bool:
        return self._db is not None

    async def get(self, text: str, scan, model_version: str):
        """The spam result of text by ARQ's model_version; scan() asks ARQ
        when no layer has it"""
        key = text_key(text)
        memory_key = (model_version, key)
        if not self.disk_enabled or len(normalize(text)) > DISK_CACHE_MAX_TEXT_LENGTH:
            return await self._memory.get(memory_key, scan)

        async def from_disk_or_scan():
            result = await self._db.get_spam_result(key, model_version)
            if result is not None:
                self.disk_hits += 1
                return result
            self.disk_misses += 1
            result = await scan()
            await self._db.store_spam_result(key, result)
            return result

        # The memory layer runs one lookup per text at a time, disk included
        return await self._memory.get(memory_key, from_disk_or_scan)

    def stats_line(self) -> str:
        """Hit rates since the last call"""
        memory = hit_rate(self._memory.hits, self._memory.misses)
        self._memory.hits = self._memory.misses = 0
        if not self.disk_enabled:
            return f"memory {memory}, disk off"
        disk = hit_rate(self.disk_hits, self.disk_misses)
        self.disk_hits = self.disk_misses = 0
        return f"memory {memory}, disk {disk}"

    async def prune(self) -> int:
        if not self.disk_enabled:
            return 0
        return await self._db.prune_spam_results(self.disk_max_rows, DISK_CACHE_MAX_AGE)


async def cache_maintenance_loop(client):
    """Log hit rates every 10 minutes, prune the disk cache every hour"""
    rounds = 0
    while True:
        await asyncio.sleep(STATS_INTERVAL_SECONDS)
        rounds += 1
        nsfw = client.nsfw_cache
        logger.info(
            "Spam cache: %s. NSFW cache: %s",
            client.spam_cache.stats_line(),
            hit_rate(nsfw.hits, nsfw.misses),
        )
        nsfw.hits = nsfw.misses = 0
        if rounds % PRUNE_EVERY_STATS_INTERVALS:
            continue
        try:
            deleted = await client.spam_cache.prune()
        except Exception:
            logger.exception("Pruning the spam result cache failed")
            continue
        if deleted:
            logger.info("Deleted %s old spam results from the disk cache", deleted)
