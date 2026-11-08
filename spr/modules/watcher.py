"""Checks every group message: blacklisted senders, NSFW media, spam text.

The handler only queues the message (client.message_queue). Workers run
check_message(), so only a fixed number of checks run at a time.

Sudo users, admins, anonymous admins and the group's linked channel are
never punished. Senders posting as a channel are checked like users."""
import logging
import random
import re

from telethon import events
from telethon.errors import RPCError

from spr.arq import ArqError
from spr.db import SPAM_HISTORY_SIZE
from spr.utils.alerts import handle_blacklisted_sender, handle_nsfw, handle_spam
from spr.utils.media import download_for_scan, get_media, get_media_id

logger = logging.getLogger(__name__)

# Link-free texts shorter than this are not scanned ("ok", "thanks")
MIN_SCAN_TEXT_LENGTH = 10
LINK_REGEX = re.compile(
    r"https?://|www\.|t\.me/|telegram\.(?:me|dog)/|@[a-z]\w{3,}", re.IGNORECASE
)
# Senders with a full history and at least this trust are "trusted":
# only TRUSTED_SCAN_RATE of their link-free texts are scanned
TRUSTED_MIN_TRUST = 90
TRUSTED_SCAN_RATE = 0.1

_random = random.Random()


def is_watched_message(event) -> bool:
    """Group messages with text, or with a photo/file"""
    if not event.is_group:
        return False
    return bool(event.raw_text) or get_media(event.message) is not None


@events.register(events.NewMessage(func=is_watched_message))
async def message_watcher(event):
    event.client.message_queue.submit(event)


def has_link(text: str) -> bool:
    return LINK_REGEX.search(text) is not None


def is_trusted(stats) -> bool:
    return len(stats.spam_data) >= SPAM_HISTORY_SIZE and stats.trust >= TRUSTED_MIN_TRUST


def should_scan_text(text: str, stats) -> bool:
    """Texts with links are always scanned, spam nearly always has one"""
    if has_link(text):
        return True
    if len(text.strip()) < MIN_SCAN_TEXT_LENGTH:
        return False
    if is_trusted(stats):
        return _random.random() < TRUSTED_SCAN_RATE
    return True


async def scan_nsfw(event, sender, alerts: bool) -> bool:
    """Scan the media of a message. True if it was NSFW and handled."""
    client = event.client
    media_id = get_media_id(event.message)
    if media_id is None:
        return False
    if await client.db.is_media_ignored(media_id):
        return False

    async def download_and_scan():
        image = await download_for_scan(client, event.message)
        if not image:
            return None
        return await client.arq.nsfw_scan(image)

    # The same file forwarded to many chats is scanned once
    try:
        result = await client.nsfw_cache.get(media_id, download_and_scan)
    except ArqError as error:
        logger.warning("NSFW scan failed in %s: %s", event.chat_id, error)
        return False
    if result is None or not result.is_nsfw:
        return False
    await handle_nsfw(event, sender, result, alerts=alerts)
    return True


async def scan_spam(event, sender, stats, punish: bool, alerts: bool):
    """Score the text of a message. Every sender's score is stored, for
    the trust value; only senders that may be punished get deleted."""
    client = event.client
    text = event.raw_text
    if not text or not should_scan_text(text, stats):
        return
    # Repeated texts (spam raids, common short messages) are scanned once
    try:
        result = await client.spam_cache.get(text, lambda: client.arq.nlp(text))
    except ArqError as error:
        logger.warning("Spam scan failed in %s: %s", event.chat_id, error)
        return
    await client.db.add_spam_prediction(event.sender_id, result.spam)
    if punish and result.is_spam:
        await handle_spam(event, sender, result.spam_probability, alerts=alerts)


async def check_message(event):
    """Run by the message queue workers"""
    client = event.client
    chat_id = event.chat_id
    chat_settings = await client.db.get_chat_settings(chat_id)
    if chat_settings.blacklisted:
        return await client.delete_dialog(chat_id)

    sender = await event.get_sender()
    if sender is None:
        return
    # Marked ID: channels are "-100...", so they never clash with user IDs
    sender_id = event.sender_id
    try:
        privileged = await client.admins.is_privileged(chat_id, sender_id)
    except RPCError as error:
        # Without the admin list, punishing anyone could hit an admin
        logger.warning("Skipping a message in %s, admin list unknown: %s", chat_id, error)
        return

    stats = await client.db.get_user_stats(sender_id)
    # Off: punish without posting about it in the chat
    alerts = chat_settings.alerts_enabled
    if not privileged:
        if stats.blacklisted:
            return await handle_blacklisted_sender(event, sender, stats, alerts=alerts)
        if chat_settings.nsfw_enabled and await scan_nsfw(event, sender, alerts):
            return

    await scan_spam(
        event,
        sender,
        stats,
        punish=chat_settings.spam_enabled and not privileged,
        alerts=alerts,
    )
