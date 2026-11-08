"""Actions on detected spam / NSFW / blacklisted senders, and the
reports about them. Reports never go out directly: they wait in
client.reports (spr/reports.py) and are sent combined."""
from telethon import utils
from telethon.errors import (ChatAdminRequiredError, MessageDeleteForbiddenError,
                             UserAdminInvalidError)
from telethon.tl import types

from spr.core.entities import chat_link, get_username, mention
from spr.core.formatting import esc
from spr.core.votes import nsfw_review_row, spam_vote_row
from spr.reports import Report
from spr.utils.media import get_media_id

# Errors raised when the bot lacks the rights to delete or ban
NO_RIGHTS_ERRORS = (
    ChatAdminRequiredError,
    UserAdminInvalidError,
    MessageDeleteForbiddenError,
)
REPORT_CONTENT_LENGTH = 400


def format_sender_line(sender, stats) -> str:
    """One line about a sender, for alerts and log reports"""
    username = get_username(sender)
    if username:
        name = esc("@" + username)
    else:
        name = mention(sender)
    line = (
        f"{name} [<code>{utils.get_peer_id(sender)}</code>]"
        f" · trust {stats.trust} · reputation {stats.reputation}"
        f" · NSFW {stats.nsfw_count}"
    )
    if stats.blacklisted:
        line += f" · blacklisted: {esc(stats.blacklist_reason)}"
    return line


def format_nsfw_scores(result) -> str:
    return (
        f"Safe {result.neutral} % · Porn {result.porn} % · Adult {result.sexy} %"
        f" · Hentai {result.hentai} % · Drawings {result.drawings} %"
    )


def notify_chat(event, text: str):
    """Queue a message for the event's chat"""
    event.client.reports.add(event.chat_id, Report(text))


async def delete_message(event) -> bool:
    """Delete the event's message. False if the bot lacks the rights."""
    try:
        results = await event.client.delete_messages(event.chat_id, [event.id])
    except NO_RIGHTS_ERRORS:
        return False
    return sum(result.pts_count for result in results) > 0


async def ban_sender(event, sender) -> bool:
    """Ban the sender from the chat. False if the bot lacks the rights."""
    client = event.client
    input_chat = await event.get_input_chat()
    try:
        if isinstance(input_chat, types.InputPeerChat):
            # Basic groups have no ban list, removing is all there is
            await client.kick_participant(input_chat, sender)
        else:
            await client.edit_permissions(input_chat, sender, view_messages=False)
    except NO_RIGHTS_ERRORS:
        return False
    return True


async def delete_or_complain(event, sender, stats, action: str) -> bool:
    """Delete the message, or tell the chat the bot can't. True if deleted."""
    if await delete_message(event):
        return True
    notify_chat(
        event,
        f"I can't delete a message from {format_sender_line(sender, stats)}, which was "
        f"flagged as {action}. Make me an admin with the Delete messages right.",
    )
    return False


async def handle_nsfw(event, sender, result):
    client = event.client
    stats = await client.db.get_user_stats(event.sender_id)
    deleted = await delete_or_complain(event, sender, stats, "NSFW")
    if deleted:
        await client.db.record_nsfw(event.sender_id)
        stats = await client.db.get_user_stats(event.sender_id)
    sender_line = format_sender_line(sender, stats)
    scores = format_nsfw_scores(result)

    # The log gets a text notice only. The media itself is never copied
    # or forwarded anywhere: keeping NSFW media may be illegal. The media
    # ID in the buttons lets a sudo user mark a false positive.
    media_id = get_media_id(event.message)
    chat = await event.get_chat()
    client.reports.add(
        client.settings.nsfw_log_channel,
        Report(
            f"<b>NSFW</b> in {mention(chat)} [<code>{event.chat_id}</code>]\n"
            f"<b>User:</b> {sender_line}\n"
            f"<b>Media ID:</b> <code>{media_id}</code> · <b>Message deleted:</b> {deleted}\n"
            f"{scores}",
            buttons=lambda number, prefix: nsfw_review_row(prefix, media_id),
        ),
    )
    if deleted:
        notify_chat(event, f"🚨 <b>NSFW ALERT</b> 🚔 Deleted a message from {sender_line}\n{scores}")


async def handle_spam(event, sender, spam_probability: float):
    client = event.client
    stats = await client.db.get_user_stats(event.sender_id)
    if not await delete_or_complain(event, sender, stats, "spam"):
        return
    sender_line = format_sender_line(sender, stats)

    content = event.raw_text
    if len(content) > REPORT_CONTENT_LENGTH:
        content = content[:REPORT_CONTENT_LENGTH] + "..."
    chat = await event.get_chat()
    url = chat_link(chat)
    log_report = Report(
        f"<b>SPAM</b> in {mention(chat)} [<code>{event.chat_id}</code>] · {spam_probability} %\n"
        f"<b>User:</b> {sender_line}\n"
        f"{esc(content)}",
        buttons=lambda number, prefix: spam_vote_row(number, prefix, url),
    )
    client.reports.add(client.settings.spam_log_channel, log_report)
    client.reports.add(
        event.chat_id,
        Report(
            f"🚨 <b>SPAM ALERT</b> 🚔 Deleted a message from {sender_line}"
            f" · spam probability {spam_probability} %",
            refers_to=log_report,
        ),
    )


async def handle_blacklisted_sender(event, sender, stats):
    """Ban a blacklisted sender and tell the chat"""
    sender_line = format_sender_line(sender, stats)
    if not await ban_sender(event, sender):
        notify_chat(
            event,
            f"I can't ban {sender_line}, who is blacklisted as a spammer. "
            "Make me an admin with the Ban users right.",
        )
        return
    notify_chat(event, f"🚨 <b>SPAMMER ALERT</b> 🚔 Banned {sender_line}")
