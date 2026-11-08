import unittest
from collections import Counter

from spr.app import register_modules
from tests.fakes import FakeClient


class RegisterModulesTest(unittest.TestCase):
    def test_every_handler_is_registered_once(self):
        client = FakeClient()
        help_modules = register_modules(client)
        names = [f"{callback.__module__}.{callback.__name__}" for callback, _ in client.handlers]
        self.assertEqual(len(names), 20)
        self.assertEqual(max(Counter(names).values()), 1)
        self.assertEqual(sorted(help_modules), ["devs", "info", "manage"])


class LogInTest(unittest.IsolatedAsyncioTestCase):
    async def test_waits_out_a_login_flood_limit(self):
        from unittest import mock

        from telethon.errors import FloodWaitError

        from spr import app

        class FlakyClient:
            attempts = 0

            async def start(self, bot_token):
                self.attempts += 1
                if self.attempts == 1:
                    raise FloodWaitError(request=None, capture=1341)

        client = FlakyClient()
        with mock.patch.object(app.asyncio, "sleep", new=mock.AsyncMock()) as sleep:
            await app.log_in(client, "1:token")
        sleep.assert_awaited_once_with(1341)
        self.assertEqual(client.attempts, 2)


class SessionPathTest(unittest.TestCase):
    def test_one_session_per_bot(self):
        from spr.app import session_path

        self.assertEqual(session_path("111:aaa").name, "spr-111")
        self.assertNotEqual(session_path("111:aaa"), session_path("222:bbb"))
        self.assertEqual(session_path("111:aaa", name="live-check").name, "live-check-111")
