"""Test doubles: a fake Telegram client and fake events.

The fakes record what the bot sends/deletes/bans, so tests check behaviour
without a Telegram connection. The database is real (Postgres from
compose.test.yml), the ARQ client is fake.
"""
import os
from pathlib import Path
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telethon import functions, utils
from telethon.tl import types
from telethon.tl.patched import Message

from spr.admins import AdminCache
from spr.arq import NsfwResult, SpamResult
from spr.cache import AsyncLruCache
from spr.reports import ReportQueue
from spr.scan_cache import SpamResultCache
from spr.core.commands import set_bot_username
from sqlalchemy import text

from spr.db import Database
from spr.models import Base
from spr.settings import Settings

SUDO_ID = 1000
ADMIN_ID = 2000
USER_ID = 3000
SPAM_LOG = -1001000000002
NSFW_LOG = -1001000000001
SUPERGROUP_ID = -1001234567890
BASIC_GROUP_ID = -4242
LINKED_CHANNEL_ID = -1009999999999
BOT_USERNAME = "TestSprBot"

DATABASE_URL = os.environ.get("DATABASE_URL")


def make_settings(**overrides) -> Settings:
    values = dict(
        bot_token="1:test",
        sudoers=frozenset({SUDO_ID}),
        nsfw_log_channel=NSFW_LOG,
        spam_log_channel=SPAM_LOG,
        arq_api_key="key",
        database_url=DATABASE_URL or "postgresql://unused",
        api_id=6,
        api_hash="hash",
        arq_api_url="https://arq.example",
        spam_memory_cache_mb=1,
        spam_disk_cache_mb=1,
    )
    values.update(overrides)
    return Settings(**values)


def make_user(user_id=USER_ID, first_name="Alice", username=None):
    return types.User(id=user_id, first_name=first_name, username=username, access_hash=1)


def make_channel(channel_id, title="Some Channel", username=None, megagroup=False):
    """channel_id is the marked "-100..." ID"""
    real_id, _ = utils.resolve_id(channel_id)
    return types.Channel(
        id=real_id,
        title=title,
        photo=types.ChatPhotoEmpty(),
        date=None,
        username=username,
        megagroup=megagroup,
        access_hash=1,
    )


def make_photo(photo_id=555):
    return types.Photo(
        id=photo_id, access_hash=1, file_reference=b"", date=None, sizes=[], dc_id=2
    )


def make_document(doc_id=777, mime_type="image/png", size=1000, attributes=(), thumbs=None):
    return types.Document(
        id=doc_id,
        access_hash=1,
        file_reference=b"",
        date=None,
        mime_type=mime_type,
        size=size,
        dc_id=2,
        attributes=list(attributes),
        thumbs=thumbs,
    )


def make_message(text="", media=None, chat_id=SUPERGROUP_ID, message_id=100):
    peer = utils.resolve_id(chat_id)[1](utils.resolve_id(chat_id)[0])
    return Message(id=message_id, peer_id=peer, date=None, message=text, media=media)


class FakeArq:
    """Returns preset results, or raises a preset error"""

    def __init__(self, spam=None, nsfw=None, error=None):
        self.spam = spam or SpamResult(False, 1, 1.0, 99.0, False)
        self.nsfw = nsfw or NsfwResult(99.0, 0.1, 0.1, 0.1, 0.1, False)
        self.error = error
        self.nlp_calls = []
        self.nsfw_calls = []

    async def nlp(self, text):
        self.nlp_calls.append(text)
        if self.error:
            raise self.error
        return self.spam

    async def nsfw_scan(self, image):
        self.nsfw_calls.append(image)
        if self.error:
            raise self.error
        return self.nsfw


class SentMessage:
    _next_id = 5000

    def __init__(self, client, chat_id, text, kwargs):
        SentMessage._next_id += 1
        self.id = SentMessage._next_id
        self.chat_id = chat_id
        self.text = text
        self.kwargs = kwargs
        self._client = client
        self.edits = []

    async def get_chat(self):
        return make_channel(self.chat_id, username="SpamLog")

    async def edit(self, text, **kwargs):
        self.edits.append(text)


class FakeAdminStore:
    """The two Database methods AdminCache uses, in memory"""

    def __init__(self):
        self.stored = {}  # chat_id: (ids, fetched_at)

    async def get_privileged_ids(self, chat_id):
        return self.stored.get(chat_id)

    async def store_privileged_ids(self, chat_id, ids):
        self.stored[chat_id] = (frozenset(ids), utc_now())


class FakeClient:
    """Stands in for SprClient. Without a database, admin lists are kept
    in a FakeAdminStore."""

    def __init__(self, db=None, settings=None, arq=None):
        self.settings = settings or make_settings()
        self.db = db if db is not None else FakeAdminStore()
        self.arq = arq or FakeArq()
        self.admins = AdminCache(self, fetches_per_second=10_000)
        self.reports = ReportQueue(self, priority_chats=(SPAM_LOG, NSFW_LOG), sends_per_second=10_000)
        # The disk layer needs the real database
        disk_db = db if isinstance(db, Database) else None
        self.spam_cache = SpamResultCache(
            self.settings.spam_memory_cache_mb,
            self.settings.spam_disk_cache_mb if disk_db else 0,
            disk_db,
        )
        self.nsfw_cache = AsyncLruCache(100)
        self.message_queue = None
        self.help_modules = {}
        self.fetch_count = 0  # admin list fetches
        self.sent = []  # SentMessage
        self.deleted = []  # (chat_id, [message ids])
        self.left = []  # chat IDs
        self.kicked = []  # (chat, user id)
        self.restricted = []  # (chat, user id, kwargs)
        self.files = []  # (chat_id, file, kwargs)
        self.handlers = []
        # Behaviour switches
        self.participants = {}  # chat_id: [(user_id, participant)]
        self.linked_channels = {}  # chat_id: linked channel's real ID
        self.entities = {}  # id or username: entity
        self.send_errors = {}  # chat_id: exception to raise
        self.delete_error = None
        self.ban_error = None
        self.participants_error = None
        self.delete_pts = 1
        self.media_bytes = b"image-bytes"

    async def send_message(self, chat_id, text="", **kwargs):
        if chat_id in self.send_errors:
            raise self.send_errors[chat_id]
        message = SentMessage(self, chat_id, text, kwargs)
        self.sent.append(message)
        return message

    async def send_file(self, chat_id, file, **kwargs):
        if chat_id in self.send_errors:
            raise self.send_errors[chat_id]
        self.files.append((chat_id, file, kwargs))

    async def delete_messages(self, chat_id, message_ids):
        if self.delete_error:
            raise self.delete_error
        self.deleted.append((chat_id, list(message_ids)))
        return [SimpleNamespace(pts_count=self.delete_pts)]

    async def delete_dialog(self, chat_id):
        self.left.append(chat_id)

    async def kick_participant(self, chat, user):
        if self.ban_error:
            raise self.ban_error
        self.kicked.append((chat, user.id))

    async def edit_permissions(self, chat, user, **kwargs):
        if self.ban_error:
            raise self.ban_error
        self.restricted.append((chat, user.id, kwargs))

    async def iter_participants(self, chat_id, filter=None):
        self.fetch_count += 1
        if self.participants_error:
            raise self.participants_error
        for user_id, participant in self.participants.get(chat_id, []):
            user = make_user(user_id)
            user.participant = participant
            yield user

    async def __call__(self, request):
        if isinstance(request, functions.channels.GetFullChannelRequest):
            chat_id = request.channel
            if not isinstance(chat_id, int):
                chat_id = utils.get_peer_id(chat_id)
            return SimpleNamespace(
                full_chat=SimpleNamespace(
                    linked_chat_id=self.linked_channels.get(chat_id),
                    participants_count=42,
                )
            )
        raise NotImplementedError(type(request).__name__)

    async def get_entity(self, query):
        if query in self.entities:
            return self.entities[query]
        raise ValueError(f"Could not find the input entity for {query!r}")

    async def download_media(self, message, file=None, thumb=None):
        if isinstance(file, str):
            Path(file).write_bytes(self.media_bytes)
            return file
        return self.media_bytes

    def add_event_handler(self, callback, event=None):
        self.handlers.append((callback, event))

    async def flush_reports(self):
        """Send the queued reports now"""
        await self.reports.flush()

    def texts_to(self, chat_id) -> list:
        return [message.text for message in self.sent if message.chat_id == chat_id]


class FakeMessageEvent:
    """Stands in for events.NewMessage.Event"""

    def __init__(
        self,
        client,
        sender,
        text="",
        media=None,
        chat_id=SUPERGROUP_ID,
        message_id=100,
        reply_message=None,
        has_reply=False,
        is_private=False,
        chat=None,
    ):
        self.client = client
        self.sender = sender
        self.sender_id = None if sender is None else utils.get_peer_id(sender)
        self.raw_text = text
        self.chat_id = chat_id
        self.id = message_id
        self.message = make_message(text, media, chat_id, message_id)
        self.is_private = is_private
        self.is_group = not is_private
        self.is_reply = has_reply or reply_message is not None
        self._reply_message = reply_message
        self.out = False
        self.fwd_from = None
        self.via_bot_id = None
        self.chat = chat or make_channel(chat_id, title="Group", megagroup=True)
        self.replies = []  # (text, kwargs)
        self.sent_replies = []  # SentMessage, to check later edits
        self.responses = []
        self.was_deleted = False

    async def get_sender(self):
        return self.sender

    async def get_chat(self):
        return self.chat

    async def get_input_chat(self):
        real_id, peer_type = utils.resolve_id(self.chat_id)
        if peer_type is types.PeerChat:
            return types.InputPeerChat(real_id)
        return types.InputPeerChannel(real_id, 1)

    async def get_reply_message(self):
        return self._reply_message

    async def reply(self, text="", **kwargs):
        if self.chat_id in self.client.send_errors:
            raise self.client.send_errors[self.chat_id]
        self.replies.append((text, kwargs))
        message = SentMessage(self.client, self.chat_id, text, kwargs)
        self.sent_replies.append(message)
        return message

    async def respond(self, text="", **kwargs):
        self.responses.append((text, kwargs))

    async def edit(self, text, **kwargs):
        self.replies.append((text, kwargs))

    async def delete(self):
        self.was_deleted = True

    def reply_texts(self) -> list:
        return [text for text, _ in self.replies]


class FakeButton:
    """A button read back from a message (telethon MessageButton)"""

    def __init__(self, text, url=None, data=None):
        self.text = text
        self.url = url
        self.data = data


class FakeLogMessage:
    def __init__(self, media=None, buttons=None, text=""):
        self.media = media
        self.text = text
        self._buttons = buttons

    async def get_buttons(self):
        return self._buttons


class FakeCallbackEvent:
    """Stands in for events.CallbackQuery.Event"""

    def __init__(self, client, sender_id, data: bytes, chat_id=SPAM_LOG, message_id=900, message=None):
        self.client = client
        self.sender_id = sender_id
        self.data = data
        self.chat_id = chat_id
        self.message_id = message_id
        self._message = message or FakeLogMessage()
        self.answers = []
        self.edits = []
        self.was_deleted = False

    async def get_sender(self):
        return make_user(self.sender_id)

    async def get_message(self):
        return self._message

    async def answer(self, message=None, **kwargs):
        self.answers.append(message)

    async def edit(self, text=None, **kwargs):
        self.edits.append((text, kwargs))

    async def delete(self):
        self.was_deleted = True


def setup_bot_username():
    set_bot_username(BOT_USERNAME)


def utc_now():
    return datetime.now(timezone.utc)


@unittest.skipUnless(DATABASE_URL, "needs DATABASE_URL (run through compose.test.yml)")
class DatabaseTestCase(unittest.IsolatedAsyncioTestCase):
    """Schema from the real migrations, empty tables for every test"""

    async def asyncSetUp(self):
        setup_bot_username()
        self.db = await Database.connect(DATABASE_URL)
        await self.db.migrate()
        tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
        await self.sql(f"TRUNCATE {tables}")

    async def asyncTearDown(self):
        await self.db.close()

    async def sql(self, query: str):
        """Run SQL, return the first value of the result if there is one"""
        async with self.db._engine.begin() as connection:
            result = await connection.execute(text(query))
            if result.returns_rows:
                return result.scalar()
            return None

    async def count(self, table: str) -> int:
        return await self.sql(f"SELECT count(*) FROM {table}")
