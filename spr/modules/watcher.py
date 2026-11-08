"""Checks every group message: blacklisted senders, NSFW media, spam text.

Sudo users, admins, anonymous admins and the group's linked channel are
never punished. Senders posting as a channel are checked like users."""
from telethon import events
from telethon.errors import RPCError

from spr.arq import ArqError
from spr.utils.alerts import handle_blacklisted_sender, handle_nsfw, handle_spam
from spr.utils.media import download_for_scan, get_media, get_media_id


def is_watched_message(event) -> bool:
    """Group messages with text, or with a photo/file"""
    if not event.is_group:
        return False
    return bool(event.raw_text) or get_media(event.message) is not None


async def scan_nsfw(event, sender) -> bool:
    """Scan the media of a message. True if it was NSFW and handled."""
    client = event.client
    media_id = get_media_id(event.message)
    if media_id is None:
        return False
    if await client.db.is_media_ignored(media_id):
        return False
    image = await download_for_scan(client, event.message)
    if not image:
        return False
    try:
        result = await client.arq.nsfw_scan(image)
    except ArqError as error:
        print(f"NSFW scan failed in {event.chat_id}: {error}")
        return False
    if not result.is_nsfw:
        return False
    await handle_nsfw(event, sender, result)
    return True


async def scan_spam(event, sender, punish: bool):
    """Score the text of a message. Every sender's score is stored, for
    the trust value; only senders that may be punished get deleted."""
    client = event.client
    text = event.raw_text
    if not text:
        return
    try:
        result = await client.arq.nlp(text)
    except ArqError as error:
        print(f"Spam scan failed in {event.chat_id}: {error}")
        return
    await client.db.add_spam_prediction(event.sender_id, result.spam)
    if punish and result.is_spam:
        await handle_spam(event, sender, result.spam_probability)


@events.register(events.NewMessage(func=is_watched_message))
async def message_watcher(event):
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
        print(f"Can't read the admins of {chat_id}: {error}")
        return

    if not privileged:
        stats = await client.db.get_user_stats(sender_id)
        if stats.blacklisted:
            return await handle_blacklisted_sender(event, sender, stats)
        if chat_settings.nsfw_enabled and await scan_nsfw(event, sender):
            return

    await scan_spam(event, sender, punish=chat_settings.spam_enabled and not privileged)
