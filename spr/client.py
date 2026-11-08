from telethon import TelegramClient

from spr.admins import AdminCache


class SprClient(TelegramClient):
    """Telegram client that also carries the bot's services.
    Handlers reach them through event.client, e.g. event.client.db"""

    def __init__(self, session: str, settings, db, arq):
        super().__init__(session, settings.api_id, settings.api_hash)
        self.settings = settings
        self.db = db
        self.arq = arq
        self.admins = AdminCache(self)
        # {lowercase module name: module}, filled at startup
        self.help_modules = {}
        # All messages use HTML, user text is escaped with core.formatting.esc
        self.parse_mode = "html"
