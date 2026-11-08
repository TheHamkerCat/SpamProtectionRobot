import asyncio
import unittest

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from spr.db import (SPAM_HISTORY_SIZE, ChatSettings, UserStats, alembic_config,
                    async_database_url)
from spr.models import Base
from tests.fakes import DatabaseTestCase


class DatabaseTest(DatabaseTestCase):
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
        self.assertTrue(await self.db.add_vote(10, 1, 1, True))
        self.assertFalse(await self.db.add_vote(10, 1, 1, False))
        self.assertTrue(await self.db.add_vote(10, 1, 2, False))
        # The same user on another entry of the same message
        self.assertTrue(await self.db.add_vote(10, 2, 1, False))
        counts = await self.db.get_vote_counts(10)
        self.assertEqual((counts[1], counts[2], counts[3]), ((1, 1), (0, 1), (0, 0)))
        # +1 reputation per vote: user 1 voted on two entries
        self.assertEqual((await self.db.get_user_stats(1)).reputation, 2)

    async def test_concurrent_votes_are_all_counted(self):
        results = await asyncio.gather(*(self.db.add_vote(11, 1, user_id, True) for user_id in range(1, 31)))
        self.assertTrue(all(results))
        self.assertEqual((await self.db.get_vote_counts(11))[1], (30, 0))

    async def test_privileged_ids(self):
        self.assertIsNone(await self.db.get_privileged_ids(-1001))
        await self.db.store_privileged_ids(-1001, {5, -1001})
        ids, fetched_at = await self.db.get_privileged_ids(-1001)
        self.assertEqual(ids, frozenset({5, -1001}))
        self.assertIsNotNone(fetched_at.tzinfo)
        # Storing the list keeps the chat's settings
        await self.db.set_chat_feature(-1001, "spam", False)
        await self.db.store_privileged_ids(-1001, {6})
        self.assertFalse((await self.db.get_chat_settings(-1001)).spam_enabled)

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


class ConcurrencyTest(DatabaseTestCase):
    """ORM sessions read, then write. Row locks must keep messages that
    arrive at the same time from overwriting each other."""

    async def test_spam_predictions_for_a_new_user_at_once(self):
        await asyncio.gather(*(self.db.add_spam_prediction(77, value) for value in range(25)))
        stats = await self.db.get_user_stats(77)
        self.assertEqual(sorted(stats.spam_data), list(range(25)))

    async def test_mixed_writes_on_one_user(self):
        await asyncio.gather(
            *(self.db.record_nsfw(78) for _ in range(5)),
            *(self.db.add_vote(500 + i, 1, 78, True) for i in range(5)),
            *(self.db.add_spam_prediction(78, 1) for _ in range(5)),
        )
        stats = await self.db.get_user_stats(78)
        self.assertEqual(stats.nsfw_count, 5)
        self.assertEqual(stats.reputation, 5 * -10 + 5)
        self.assertEqual(len(stats.spam_data), 5 * 2 + 5)

    async def test_double_click_counts_once(self):
        results = await asyncio.gather(*(self.db.add_vote(12, 1, 5, True) for _ in range(5)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual((await self.db.get_vote_counts(12))[1], (1, 0))
        self.assertEqual((await self.db.get_user_stats(5)).reputation, 1)

    async def test_same_chat_toggled_at_once(self):
        results = await asyncio.gather(
            *(self.db.set_chat_feature(-1009, "nsfw", False) for _ in range(5))
        )
        self.assertEqual(results.count(True), 1)
        self.assertEqual(await self.count("chats"), 1)


class MigrationTest(DatabaseTestCase):
    async def test_migrations_match_the_models(self):
        def compare(connection):
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            return compare_metadata(context, Base.metadata)

        async with self.db._engine.connect() as connection:
            differences = await connection.run_sync(compare)
        self.assertEqual(differences, [], "spr/models.py changed without a migration")

    async def test_ids_have_no_sequences(self):
        # Telegram IDs come from Telegram, an auto-increment would be wrong
        self.assertEqual(await self.count("pg_sequences"), 0)

    async def test_downgrade_and_upgrade_again(self):
        def run(connection, action, target):
            config = alembic_config()
            config.attributes["connection"] = connection
            action(config, target)

        async with self.db._engine.begin() as connection:
            await connection.run_sync(run, command.downgrade, "base")
        self.assertIsNone(await self.sql("SELECT to_regclass('public.users')"))
        await self.db.migrate()
        await self.db.add_spam_prediction(1, 5)
        head = ScriptDirectory.from_config(alembic_config()).get_current_head()
        self.assertEqual(await self.sql("SELECT version_num FROM alembic_version"), head)


class DatabaseUrlTest(unittest.TestCase):
    def test_heroku_url_with_sslmode(self):
        url = async_database_url("postgres://u:p@db.example:5432/spr?sslmode=require")
        self.assertEqual(url.drivername, "postgresql+asyncpg")
        self.assertEqual(dict(url.query), {"ssl": "require"})
        self.assertEqual((url.host, url.database, url.password), ("db.example", "spr", "p"))

    def test_plain_url(self):
        url = async_database_url("postgresql://spr:pw@localhost/spr")
        self.assertEqual(url.drivername, "postgresql+asyncpg")
        self.assertEqual(dict(url.query), {})
