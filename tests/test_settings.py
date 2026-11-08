import unittest

from spr.settings import SettingsError, load_settings

VALID_ENV = {
    "BOT_TOKEN": "123456:ABC-def_ghi",
    "SUDOERS": "1 2",
    "NSFW_LOG_CHANNEL": "-1001111111111",
    "SPAM_LOG_CHANNEL": "-1002222222222",
    "ARQ_API_KEY": "key",
    "DATABASE_URL": "postgresql://spr:pw@localhost:5432/spr",
    "API_ID": "6",
    "API_HASH": "eb06d4abfb49dc3eeb1aeb98ae0f581e",
    "ARQ_API_URL": "https://arq.hamker.dev",
}


class LoadSettingsTest(unittest.TestCase):
    def test_valid_settings(self):
        settings = load_settings(VALID_ENV)
        self.assertEqual(settings.sudoers, frozenset({1, 2}))
        self.assertEqual(settings.api_id, 6)
        self.assertEqual(settings.arq_api_url, "https://arq.hamker.dev")
        self.assertEqual(settings.nsfw_log_channel, -1001111111111)

    def test_all_problems_are_reported_at_once(self):
        with self.assertRaises(SettingsError) as raised:
            load_settings({})
        message = str(raised.exception)
        names = (
            "BOT_TOKEN", "NSFW_LOG_CHANNEL", "SPAM_LOG_CHANNEL", "ARQ_API_KEY",
            "DATABASE_URL", "API_ID", "API_HASH", "ARQ_API_URL",
        )
        for name in names:
            self.assertIn(f"{name} is missing", message)
        # There are no hidden defaults in the code
        self.assertIn("API_ID is missing. Copy it from .env.example.", message)

    def test_invalid_values(self):
        env = dict(
            VALID_ENV,
            BOT_TOKEN="secret-token-without-colon",
            SUDOERS="1 abc",
            NSFW_LOG_CHANNEL="12345",
            API_ID="six",
            DATABASE_URL="mysql://x",
            ARQ_API_URL="arq.hamker.dev",
        )
        with self.assertRaises(SettingsError) as raised:
            load_settings(env)
        message = str(raised.exception)
        self.assertIn("SUDOERS has 'abc'", message)
        self.assertIn("NSFW_LOG_CHANNEL must start with -100", message)
        self.assertIn("API_ID must be a number", message)
        self.assertIn("DATABASE_URL must start with", message)
        self.assertIn("ARQ_API_URL must start with https://", message)
        # The token is a secret and must never be printed
        self.assertNotIn("secret-token-without-colon", message)

    def test_empty_sudoers_is_allowed(self):
        settings = load_settings(dict(VALID_ENV, SUDOERS=""))
        self.assertEqual(settings.sudoers, frozenset())
