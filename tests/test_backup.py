import io
import zipfile
from datetime import datetime, timezone

from telethon.errors import UserIsBlockedError

from spr.backup import build_backup, seconds_until_midnight, send_backup
from tests.fakes import DatabaseTestCase, FakeClient, make_settings


class BackupTest(DatabaseTestCase):
    async def test_zip_holds_every_table(self):
        await self.db.add_spam_prediction(1, 5)
        backup = await build_backup(self.db)
        self.assertTrue(backup.name.startswith("spr-backup-"))
        with zipfile.ZipFile(backup) as archive:
            self.assertEqual(
                sorted(archive.namelist()),
                ["chats.csv", "ignored_media.csv", "users.csv", "votes.csv"],
            )

    async def test_only_the_database_is_sent(self):
        client = FakeClient(db=self.db, settings=make_settings(sudoers=frozenset({1, 2, 3})))
        client.send_errors[2] = UserIsBlockedError(request=None)
        await send_backup(client)
        recipients = sorted(chat_id for chat_id, _, _ in client.files)
        self.assertEqual(recipients, [1, 3])
        for _, file, kwargs in client.files:
            self.assertIsInstance(file, io.BytesIO)
            self.assertTrue(file.name.endswith(".zip"))
            self.assertTrue(kwargs["force_document"])

    def test_seconds_until_midnight(self):
        now = datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc)
        self.assertEqual(seconds_until_midnight(now), 3600)
