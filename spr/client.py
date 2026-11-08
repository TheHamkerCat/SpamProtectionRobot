from telethon import TelegramClient

from spr.admins import AdminCache
from spr.cache import AsyncLruCache
from spr.reports import ReportQueue
from spr.scan_cache import SpamResultCache

# Telethon keeps users and chats it has seen in memory. Above this many it
# writes them all to the session file and keeps only the chats; with more
# chats than the limit it would do that on every update. Measured with
# tests/benchmark.py: about 10 ms per update at 10,000 chats.
ENTITY_CACHE_LIMIT = 300_000
# NSFW results of recent files, so a file forwarded to many chats is
# scanned once (spam results: spr/scan_cache.py)
NSFW_CACHE_SIZE = 20_000


class SprClient(TelegramClient):
    """Telegram client that also carries the bot's services.
    Handlers reach them through event.client, e.g. event.client.db"""

    def __init__(self, session: str, settings, db, arq):
        super().__init__(
            session, settings.api_id, settings.api_hash, entity_cache_limit=ENTITY_CACHE_LIMIT
        )
        self.settings = settings
        self.db = db
        self.arq = arq
        self.admins = AdminCache(self)
        self.reports = ReportQueue(
            self, priority_chats=(settings.spam_log_channel, settings.nsfw_log_channel)
        )
        self.spam_cache = SpamResultCache(
            settings.spam_memory_cache_mb, settings.spam_disk_cache_mb, db
        )
        self.nsfw_cache = AsyncLruCache(NSFW_CACHE_SIZE)
        # Messages waiting to be checked, set up in spr/app.py
        self.message_queue = None
        # {lowercase module name: module}, filled at startup
        self.help_modules = {}
        # All messages use HTML, user text is escaped with core.formatting.esc
        self.parse_mode = "html"
