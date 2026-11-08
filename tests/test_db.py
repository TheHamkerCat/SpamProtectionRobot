import asyncio

from spr.db import SPAM_HISTORY_SIZE, ChatSettings, UserStats
from tests.fakes import DatabaseTestCase


class DatabaseTest(DatabaseTestCase):
    async def count(self, table):
        return await self.db._pool.fetchval(f"SELECT count(*) FROM {table}")

    async def test_lookups_of_unknown_ids_store_nothing(self):
        self.assertEqual(await self.db.get_user_stats(1), UserStats())
        self.assertEqual(await self.db.get_chat_settings(-1001), ChatSettings())
        self.assertEqual(await self.count("users"), 0)
        self.assertEqual(await self.count("chats"), 0)

    async def test_spam_history_keeps_last_values(self):
        for value in range(SPAM_HISTORY_SIZE + 10):
            await self.db.add_spam_prediction(1, value)
        stats = await self.db.get_user_stats(1)
        self.assertEqual(len(stats.spam_data), SPAM_HISTORY_SIZE)
        self.assertEqual(stats.spam_data[0], 10)
        self.assertEqual(stats.spam_data[-1], SPAM_HISTORY_SIZE + 9)

    async def test_trust(self):
        self.assertEqual(UserStats().trust, 100)
        await self.db.add_spam_prediction(1, 80)
        await self.db.add_spam_prediction(1, 60)
        stats = await self.db.get_user_stats(1)
        self.assertEqual(stats.trust, 30)
        self.assertTrue(stats.is_spammer)
        self.assertTrue(stats.is_potential_spammer)

    async def test_record_nsfw(self):
        await self.db.record_nsfw(1)
        await self.db.record_nsfw(1)
        stats = await self.db.get_user_stats(1)
        self.assertEqual(stats.nsfw_count, 2)
        self.assertEqual(stats.reputation, -20)
        self.assertEqual(stats.spam_data, (100.0,) * 4)

    async def test_blacklist_and_whitelist(self):
        for kind, entity_id in (("user", 5), ("chat", -1005)):
            self.assertTrue(await self.db.blacklist(kind, entity_id, "spam <bot>"))
            self.assertFalse(await self.db.blacklist(kind, entity_id, "again"))
            self.assertTrue(await self.db.whitelist(kind, entity_id))
            self.assertFalse(await self.db.whitelist(kind, entity_id))
        await self.db.blacklist("user", 5, "spam <bot>")
        stats = await self.db.get_user_stats(5)
        self.assertTrue(stats.blacklisted)
        self.assertEqual(stats.blacklist_reason, "spam <bot>")
        self.assertIsNotNone(stats.blacklisted_at.tzinfo)

    async def test_chat_features(self):
        self.assertFalse(await self.db.set_chat_feature(-1001, "spam", True))
        self.assertTrue(await self.db.set_chat_feature(-1001, "spam", False))
        self.assertFalse(await self.db.set_chat_feature(-1001, "spam", False))
        settings = await self.db.get_chat_settings(-1001)
        self.assertFalse(settings.spam_enabled)
        self.assertTrue(settings.nsfw_enabled)

    async def test_votes(self):
        self.assertTrue(await self.db.add_vote(10, 1, True))
        self.assertFalse(await self.db.add_vote(10, 1, False))
        self.assertTrue(await self.db.add_vote(10, 2, False))
        self.assertEqual(await self.db.get_vote_counts(10), (1, 1))
        self.assertEqual((await self.db.get_user_stats(1)).reputation, 1)

    async def test_concurrent_votes_are_all_counted(self):
        results = await asyncio.gather(*(self.db.add_vote(11, user_id, True) for user_id in range(1, 31)))
        self.assertTrue(all(results))
        self.assertEqual(await self.db.get_vote_counts(11), (30, 0))

    async def test_ignored_media(self):
        self.assertFalse(await self.db.is_media_ignored(123))
        await self.db.ignore_media(123)
        await self.db.ignore_media(123)
        self.assertTrue(await self.db.is_media_ignored(123))

    async def test_export_csv(self):
        await self.db.add_spam_prediction(1, 5)
        exported = await self.db.export_csv()
        self.assertEqual(set(exported), {"users", "chats", "votes", "ignored_media"})
        self.assertTrue(exported["users"].startswith(b"user_id,spam_data"))
        self.assertIn(b"1,{5}", exported["users"])
