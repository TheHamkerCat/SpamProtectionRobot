"""Bot settings, read from environment variables.

Locally they come from the .env file, in Docker from docker-compose.yml,
on Heroku from the app's Config Vars.
"""
import os
import re
from dataclasses import dataclass
from typing import Mapping, Optional

BOT_TOKEN_REGEX = re.compile(r"^\d+:[A-Za-z0-9_-]+$")
DATABASE_URL_PREFIXES = ("postgres://", "postgresql://")
URL_PREFIXES = ("https://", "http://")
# Shown for settings whose usual value is already in .env.example
COPY_FROM_EXAMPLE = "Copy it from .env.example."


class SettingsError(Exception):
    """One or more settings are missing or invalid"""


@dataclass(frozen=True)
class Settings:
    bot_token: str
    sudoers: frozenset
    nsfw_log_channel: int
    spam_log_channel: int
    arq_api_key: str
    database_url: str
    api_id: int
    api_hash: str
    arq_api_url: str


class _SettingsReader:
    """Reads settings and collects every problem, so all of them
    are reported at once instead of one per restart"""

    def __init__(self, env: Mapping[str, str]):
        self.env = env
        self.errors = []

    def text(self, name: str, hint: str) -> Optional[str]:
        value = self.env.get(name, "").strip()
        if value:
            return value
        self.errors.append(f"{name} is missing. {hint}")
        return None

    def number(self, name: str, hint: str) -> Optional[int]:
        value = self.text(name, hint)
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            self.errors.append(f"{name} must be a number, got {value!r}. {hint}")
            return None

    def channel_id(self, name: str, hint: str) -> Optional[int]:
        value = self.number(name, hint)
        if value is None:
            return None
        if not str(value).startswith("-100"):
            self.errors.append(f"{name} must start with -100, got {value}. {hint}")
            return None
        return value

    def user_ids(self, name: str, hint: str) -> frozenset:
        ids = set()
        for part in self.env.get(name, "").split():
            try:
                ids.add(int(part))
            except ValueError:
                self.errors.append(f"{name} has {part!r}, which is not a user ID. {hint}")
        return frozenset(ids)


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    """Read and check all settings. Raises SettingsError with one line per problem."""
    read = _SettingsReader(env)

    bot_token = read.text("BOT_TOKEN", "Get a bot token from @BotFather.")
    if bot_token and not BOT_TOKEN_REGEX.match(bot_token):
        # Never echo the token itself, it is a secret
        read.errors.append(
            "BOT_TOKEN does not look like a bot token "
            "(expected 123456:ABC-xyz). Copy it again from @BotFather."
        )

    database_url = read.text(
        "DATABASE_URL",
        "Set a PostgreSQL URL, e.g. postgresql://spr:password@localhost:5432/spr. "
        "Docker Compose and Heroku set it for you.",
    )
    if database_url and not database_url.startswith(DATABASE_URL_PREFIXES):
        read.errors.append("DATABASE_URL must start with postgresql:// or postgres://.")

    arq_api_url = read.text("ARQ_API_URL", COPY_FROM_EXAMPLE)
    if arq_api_url and not arq_api_url.startswith(URL_PREFIXES):
        read.errors.append(f"ARQ_API_URL must start with https://. {COPY_FROM_EXAMPLE}")

    channel_hint = (
        "Use the channel ID, e.g. -1001234567890, "
        "and add the bot to that channel as an admin."
    )
    values = dict(
        bot_token=bot_token,
        sudoers=read.user_ids(
            "SUDOERS", "Use space separated user IDs, e.g. 123456 654321."
        ),
        nsfw_log_channel=read.channel_id("NSFW_LOG_CHANNEL", channel_hint),
        spam_log_channel=read.channel_id("SPAM_LOG_CHANNEL", channel_hint),
        arq_api_key=read.text("ARQ_API_KEY", "Get an API key from @ARQRobot."),
        database_url=database_url,
        # .env.example has the official Android app's public credentials;
        # your own are at https://my.telegram.org
        api_id=read.number("API_ID", COPY_FROM_EXAMPLE),
        api_hash=read.text("API_HASH", COPY_FROM_EXAMPLE),
        arq_api_url=arq_api_url,
    )

    if read.errors:
        lines = "\n".join(f"  - {error}" for error in read.errors)
        raise SettingsError(
            "The bot can't start. Fix these settings in .env "
            f"(on Heroku: Settings > Config Vars):\n{lines}"
        )
    return Settings(**values)
