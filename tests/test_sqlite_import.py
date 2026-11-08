import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from telethon.tl import types

from spr import sqlite_import
from spr.modules.migrate import is_sudo, migrate_sqlite_command
from spr.sqlite_import import (OldDatabaseError, import_old_database,
                               parse_spam_data)
from tests.fakes import (SUDO_ID, USER_ID, DatabaseTestCase, FakeClient,
                         FakeMessageEvent, make_document, make_message,
                         make_user)

# The tables exactly as the old bot created them: no types, no keys
OLD_SCHEMA = """
CREATE TABLE users (user_id, spam_data, nsfw_count, reputation, blacklisted);
CREATE TABLE chats (chat_id, spam_enabled, nsfw_enabled, blacklisted);
CREATE TABLE reports (message_id, upvote, downvote, user_id);
CREATE TABLE ignored_media (file_id, time);
CREATE TABLE reasons (id, reason, time);
"""


class OldDatabaseTestCase(DatabaseTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.folder = tempfile.TemporaryDirectory()

    async def asyncTearDown(self):
        self.folder.cleanup()
        await super().asyncTearDown()

    def old_db(self, users=(), chats=(), reasons=(), schema=OLD_SCHEMA, name="db.sqlite3") -> Path:
        path = Path(self.folder.name) / name
        connection = sqlite3.connect(path)
        connection.executescript(schema)
        connection.executemany("INSERT INTO users VALUES (?, ?, ?, ?, ?)", users)
        connection.executemany("INSERT INTO chats VALUES (?, ?, ?, ?)", chats)
        connection.executemany("INSERT INTO reasons VALUES (?, ?, ?)", reasons)
        connection.commit()
        connection.close()
        return path


class ImportTest(OldDatabaseTestCase):
    async def test_merge_with_what_the_new_bot_stored(self):
        await self.db.add_spam_prediction(1, 10)
        await self.db.set_chat_feature(-1001, "nsfw", False)
        path = self.old_db(
            users=[
                (1, "[1, 2]", 2, 5, 0),
                (2, "[]", 0, 0, 0),  # nothing known: not imported
                (3, "[50]", 0, 0, 1),
            ],
            chats=[
                (-1001, 0, 1, 0),  # spam detection off
                (-1002, 1, 1, 1),  # blacklisted
                (-1003, 1, 1, 0),  # defaults: not imported
            ],
            reasons=[(3, "known spammer", 1600000000.0), (-1002, "spam group", 1600000000.0)],
        )
        summary = await import_old_database(self.db, path)
        self.assertEqual((summary.users, summary.chats, summary.blacklisted), (2, 2, 2))

        user = await self.db.get_user_stats(1)
        # Old predictions first, then the new ones
        self.assertEqual(user.spam_data, (1.0, 2.0, 10.0))
        self.assertEqual((user.nsfw_count, user.reputation), (2, 5))
        spammer = await self.db.get_user_stats(3)
        self.assertEqual(spammer.blacklist_reason, "known spammer")
        self.assertEqual(spammer.blacklisted_at, datetime(2020, 9, 13, 12, 26, 40, tzinfo=timezone.utc))
        self.assertEqual(await self.count("users"), 2)

        chat = await self.db.get_chat_settings(-1001)
        # Off in either database stays off
        self.assertEqual((chat.spam_enabled, chat.nsfw_enabled), (False, False))
        self.assertEqual((await self.db.get_chat_settings(-1002)).blacklist_reason, "spam group")
        self.assertEqual(await self.count("chats"), 2)

    async def test_spam_history_keeps_the_newest_values(self):
        await self.db.add_spam_prediction(1, 99)
        path = self.old_db(users=[(1, str(list(range(60))), 0, 0, 0)])
        await import_old_database(self.db, path)
        history = (await self.db.get_user_stats(1)).spam_data
        self.assertEqual(len(history), 50)
        self.assertEqual(history[-2:], (59.0, 99.0))

    async def test_same_file_twice_changes_nothing(self):
        path = self.old_db(users=[(1, "[5]", 1, 3, 0)])
        await import_old_database(self.db, path)
        summary = await import_old_database(self.db, path)
        self.assertIsNotNone(summary.finished_before)
        user = await self.db.get_user_stats(1)
        self.assertEqual((user.spam_data, user.nsfw_count, user.reputation), ((5.0,), 1, 3))

    async def test_interrupted_import_continues_without_doubling(self):
        path = self.old_db(users=[(user_id, "[1]", 0, 1, 0) for user_id in range(1, 8)])
        original = self.db.merge_old_users
        calls = 0

        async def fail_on_second_chunk(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("connection lost")
            return await original(*args)

        with mock.patch.object(sqlite_import, "USERS_PER_CHUNK", 2):
            with mock.patch.object(self.db, "merge_old_users", fail_on_second_chunk):
                with self.assertRaises(RuntimeError):
                    await import_old_database(self.db, path)
            summary = await import_old_database(self.db, path)
        self.assertTrue(summary.resumed)
        self.assertEqual(summary.users, 5)
        for user_id in range(1, 8):
            user = await self.db.get_user_stats(user_id)
            self.assertEqual((user.spam_data, user.reputation), ((1.0,), 1), user_id)

    async def test_progress_is_reported(self):
        path = self.old_db(users=[(user_id, "[1]", 0, 0, 0) for user_id in range(1, 6)])
        progress = []

        async def record(done, total):
            progress.append((done, total))

        with mock.patch.object(sqlite_import, "USERS_PER_CHUNK", 2):
            await import_old_database(self.db, path, on_progress=record)
        self.assertEqual(progress, [(2, 5), (4, 5), (5, 5)])


class RejectedFileTest(OldDatabaseTestCase):
    async def check_rejected(self, path, message):
        with self.assertRaises(OldDatabaseError) as raised:
            await import_old_database(self.db, path)
        self.assertIn(message, str(raised.exception))
        self.assertEqual(await self.count("sqlite_imports"), 0)

    async def test_not_sqlite(self):
        path = Path(self.folder.name) / "notes.txt"
        path.write_text("hello")
        await self.check_rejected(path, "not an SQLite database")

    async def test_other_sqlite_database(self):
        path = Path(self.folder.name) / "other.sqlite3"
        connection = sqlite3.connect(path)
        connection.executescript(
            "CREATE TABLE users (id, name); CREATE TABLE chats (chat_id, spam_enabled, nsfw_enabled, blacklisted);"
        )
        connection.close()
        await self.check_rejected(path, "missing columns")

    async def test_view_is_not_read(self):
        schema = OLD_SCHEMA.replace(
            "CREATE TABLE users (user_id, spam_data, nsfw_count, reputation, blacklisted);",
            "CREATE TABLE real_users (user_id, spam_data, nsfw_count, reputation, blacklisted);"
            "CREATE VIEW users AS SELECT * FROM real_users;",
        )
        path = Path(self.folder.name) / "view.sqlite3"
        connection = sqlite3.connect(path)
        connection.executescript(schema)
        connection.close()
        await self.check_rejected(path, "no users table")

    def test_parse_spam_data(self):
        self.assertEqual(parse_spam_data("[1, 2.5]"), (1.0, 2.5))
        self.assertEqual(parse_spam_data('[1, "x", true, null]'), (1.0,))
        self.assertEqual(parse_spam_data("not json"), ())
        self.assertEqual(parse_spam_data('{"a": 1}'), ())
        self.assertEqual(parse_spam_data(None), ())


class MigrateCommandTest(OldDatabaseTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.client = FakeClient(db=self.db)

    def command_event(self, file_bytes=None, size=None, sender_id=SUDO_ID):
        reply = None
        if file_bytes is not None:
            document = make_document(mime_type="application/octet-stream", size=size or len(file_bytes))
            reply = make_message(media=types.MessageMediaDocument(document=document))
            self.client.media_bytes = file_bytes
        return FakeMessageEvent(
            self.client, make_user(sender_id), "/migrate_sqlite", reply_message=reply, is_private=True
        )

    def test_only_sudo_users(self):
        self.assertTrue(is_sudo(self.command_event()))
        self.assertFalse(is_sudo(self.command_event(sender_id=USER_ID)))

    async def test_needs_a_file(self):
        event = self.command_event()
        await migrate_sqlite_command(event)
        self.assertIn("Reply to the old bot's db.sqlite3 file", event.reply_texts()[0])

    async def test_file_too_big(self):
        event = self.command_event(b"x", size=sqlite_import.MAX_FILE_SIZE + 1)
        await migrate_sqlite_command(event)
        self.assertIn("bigger than 1 GB", event.reply_texts()[0])

    async def test_import_from_a_reply(self):
        path = self.old_db(users=[(1, "[5]", 0, 2, 0)], chats=[(-1001, 0, 1, 0)])
        event = self.command_event(path.read_bytes())
        await migrate_sqlite_command(event)
        status = event.sent_replies[0]
        self.assertEqual(status.text, "Downloading the file...")
        self.assertIn("<b>Import finished.</b>", status.edits[-1])
        self.assertIn("Users: 1 ", status.edits[-1])
        self.assertEqual((await self.db.get_user_stats(1)).reputation, 2)

        # The same file again
        event = self.command_event(path.read_bytes())
        await migrate_sqlite_command(event)
        self.assertIn("already imported", event.sent_replies[0].edits[-1])
        self.assertEqual((await self.db.get_user_stats(1)).reputation, 2)

    async def test_wrong_file(self):
        event = self.command_event(b"this is not a database")
        await migrate_sqlite_command(event)
        self.assertIn("not an SQLite database", event.sent_replies[0].edits[-1])
        self.assertEqual(await self.count("users"), 0)
