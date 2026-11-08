from datetime import timedelta

from spr.arq import SpamResult
from spr.scan_cache import (DISK_CACHE_MAX_TEXT_LENGTH, SpamResultCache,
                            megabytes_to_entries, text_key)
from tests.fakes import DatabaseTestCase

RESULT = SpamResult(True, 97, 97.25, 2.75, False)
SHORT_TEXT = "good morning everyone"
LONG_TEXT = "x" * (DISK_CACHE_MAX_TEXT_LENGTH + 1)


class SpamResultCacheTest(DatabaseTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.scans = []

    async def scan(self):
        self.scans.append(1)
        return RESULT

    def cache(self, memory_mb=1, disk_mb=1):
        return SpamResultCache(memory_mb, disk_mb, self.db)

    async def test_short_texts_survive_a_restart(self):
        self.assertEqual(await self.cache().get(SHORT_TEXT, self.scan), RESULT)
        # A new cache (empty memory) finds it in Postgres, in any case/spacing
        restarted = self.cache()
        self.assertEqual(await restarted.get("  Good Morning   everyone", self.scan), RESULT)
        self.assertEqual(len(self.scans), 1)
        self.assertEqual((restarted.disk_hits, restarted.disk_misses), (1, 0))

    async def test_only_a_hash_is_stored(self):
        await self.cache().get(SHORT_TEXT, self.scan)
        stored = await self.sql("SELECT text_hash FROM spam_results")
        self.assertEqual(bytes(stored), text_key(SHORT_TEXT))

    async def test_long_texts_stay_in_memory_only(self):
        cache = self.cache()
        await cache.get(LONG_TEXT, self.scan)
        await cache.get(LONG_TEXT, self.scan)
        self.assertEqual(len(self.scans), 1)
        self.assertEqual(await self.count("spam_results"), 0)
        await self.cache().get(LONG_TEXT, self.scan)
        self.assertEqual(len(self.scans), 2)

    async def test_disk_off(self):
        cache = self.cache(disk_mb=0)
        self.assertFalse(cache.disk_enabled)
        await cache.get(SHORT_TEXT, self.scan)
        self.assertEqual(await self.count("spam_results"), 0)
        self.assertEqual(await cache.prune(), 0)
        self.assertEqual(cache.stats_line(), "memory 0 % hits (0/1), disk off")

    async def test_memory_off_still_uses_disk(self):
        await self.cache(memory_mb=0).get(SHORT_TEXT, self.scan)
        await self.cache(memory_mb=0).get(SHORT_TEXT, self.scan)
        self.assertEqual(len(self.scans), 1)

    async def test_stats_line_resets(self):
        cache = self.cache()
        await cache.get(SHORT_TEXT, self.scan)
        await cache.get(SHORT_TEXT, self.scan)
        self.assertEqual(cache.stats_line(), "memory 50 % hits (1/2), disk 0 % hits (0/1)")
        self.assertEqual(cache.stats_line(), "memory no lookups, disk no lookups")

    def test_sizes(self):
        self.assertEqual(megabytes_to_entries(64, 330), 203_360)
        self.assertEqual(self.cache(disk_mb=4096).disk_max_rows, 22_605_091)


class SpamResultStorageTest(DatabaseTestCase):
    async def test_store_twice_keeps_one_row(self):
        await self.db.store_spam_result(b"k" * 16, RESULT)
        await self.db.store_spam_result(b"k" * 16, RESULT)
        self.assertEqual(await self.count("spam_results"), 1)
        self.assertEqual(await self.db.get_spam_result(b"k" * 16), RESULT)
        self.assertIsNone(await self.db.get_spam_result(b"x" * 16))

    async def test_last_use_is_written_at_most_daily(self):
        await self.db.store_spam_result(b"k" * 16, RESULT)
        await self.sql("UPDATE spam_results SET last_used_at = now() - interval '2 days'")
        await self.db.get_spam_result(b"k" * 16)
        age = await self.sql("SELECT now() - last_used_at < interval '1 minute' FROM spam_results")
        self.assertTrue(age)

    async def test_prune_by_age_and_size(self):
        for i in range(10):
            await self.db.store_spam_result(bytes([i]) * 16, RESULT)
        # Rows 0-9 used i hours ago; row 9 is too old
        await self.sql(
            "UPDATE spam_results SET last_used_at = now() - get_byte(text_hash, 0) * interval '1 hour'"
        )
        deleted = await self.db.prune_spam_results(max_rows=5, max_age=timedelta(hours=8, minutes=30))
        self.assertEqual(deleted, 5)
        remaining = await self.sql("SELECT array_agg(get_byte(text_hash, 0) ORDER BY 1) FROM spam_results")
        self.assertEqual(sorted(remaining), [0, 1, 2, 3, 4])
