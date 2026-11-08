"""Actions on detected spam / NSFW / blacklisted senders, and the
messages the bot posts about them."""
from telethon.errors import (ChatAdminRequiredError, ChatWriteForbiddenError,
                             MessageDeleteForbiddenError, RPCError,
                             UserAdminInvalidError)
from telethon import utils
from telethon.tl import types

from spr.core import ikb
from spr.core.entities import chat_link, get_username, mention, message_link
from spr.core.formatting import esc
from spr.utils.media import get_media_id

# Errors raised when the bot lacks the rights to delete or ban
NO_RIGHTS_ERRORS = (
    ChatAdminRequiredError,
    UserAdminInvalidError,
    MessageDeleteForbiddenError,
)
REPORT_CONTENT_LENGTH = 400


def format_sender_report(sender, stats) -> str:
    """Sender details shown in alerts and log reports"""
    username = get_username(sender)
    if username:
        name = esc("@" + username)
    else:
        name = mention(sender)
    report = (
        "\n<b>User:</b>\n"
        f"    <b>Username:</b> {name} [<code>{utils.get_peer_id(sender)}</code>]\n"
        f"    <b>Trust:</b> {stats.trust}\n"
        f"    <b>Spammer:</b> {stats.is_spammer}\n"
        f"    <b>Reputation:</b> {stats.reputation}\n"
        f"    <b>NSFW Count:</b> {stats.nsfw_count}\n"
        f"    <b>Potential Spammer:</b> {stats.is_potential_spammer}\n"
        f"    <b>Blacklisted:</b> {stats.blacklisted}\n"
    )
    if stats.blacklisted:
        report += (
            f"    <b>Blacklist Reason:</b> {esc(stats.blacklist_reason)}"
            f" | {stats.blacklisted_at:%Y-%m-%d %H:%M} UTC\n"
        )
    return report


async def reply_or_leave(event, text: str):
    """Reply in the chat, leave it if the bot can't write there"""
    try:
        await event.reply(text)
    except ChatWriteForbiddenError:
        await event.client.delete_dialog(event.chat_id)


async def send_safely(client, chat_id: int, text: str, **kwargs):
    """Send a message, log and return None if Telegram refuses it"""
    try:
        return await client.send_message(chat_id, text, **kwargs)
    except RPCError as error:
        print(f"Can't send a message to {chat_id}: {error}")
        return None


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


async def delete_and_report(event, sender, action: str) -> bool:
    """Delete the message, or tell the chat the bot can't. True if deleted."""
    if await delete_message(event):
        return True
    await reply_or_leave(
        event,
        "I can't delete this message, which was flagged as "
        f"{action}. Make me an admin with the Delete messages right.",
    )
    return False


def format_nsfw_prediction(result) -> str:
    return (
        "<b>Prediction:</b>\n"
        f"    <b>Safe:</b> <code>{result.neutral} %</code>\n"
        f"    <b>Porn:</b> <code>{result.porn} %</code>\n"
        f"    <b>Adult:</b> <code>{result.sexy} %</code>\n"
        f"    <b>Hentai:</b> <code>{result.hentai} %</code>\n"
        f"    <b>Drawings:</b> <code>{result.drawings} %</code>\n"
    )


async def handle_nsfw(event, sender, result):
    client = event.client
    deleted = await delete_and_report(event, sender, "NSFW")
    if deleted:
        await client.db.record_nsfw(event.sender_id)
    stats = await client.db.get_user_stats(event.sender_id)
    sender_report = format_sender_report(sender, stats)
    prediction = format_nsfw_prediction(result)

    # The log gets a text notice only. The media itself is never copied
    # or forwarded anywhere: keeping NSFW media may be illegal. The media
    # ID in the buttons lets a sudo user mark a false positive.
    media_id = get_media_id(event.message)
    chat = await event.get_chat()
    await send_safely(
        client,
        client.settings.nsfw_log_channel,
        "<b>NSFW DETECTION</b>\n"
        f"{sender_report}"
        f"<b>Chat:</b> {mention(chat)} [<code>{event.chat_id}</code>]\n"
        f"<b>Media ID:</b> <code>{media_id}</code>\n"
        f"<b>Message deleted:</b> {deleted}\n"
        f"{prediction}",
        buttons=ikb(
            {
                "Correct": f"upvote_nsfw_{media_id}",
                "Incorrect": f"downvote_nsfw_{media_id}",
            }
        ),
    )

    if deleted:
        await send_safely(
            client, event.chat_id, f"🚨 <b>NSFW ALERT</b>  🚔\n{sender_report}{prediction}"
        )


async def handle_spam(event, sender, spam_probability: float):
    client = event.client
    if not await delete_and_report(event, sender, "spam"):
        return
    stats = await client.db.get_user_stats(event.sender_id)
    sender_report = format_sender_report(sender, stats)

    content = event.raw_text
    if len(content) > REPORT_CONTENT_LENGTH:
        content = content[:REPORT_CONTENT_LENGTH] + "..."
    chat = await event.get_chat()
    log_report = await send_safely(
        client,
        client.settings.spam_log_channel,
        f"<b>SPAM DETECTION</b>\n{sender_report}<b>Content:</b>\n{esc(content)}",
        buttons=ikb(
            {
                "Correct (0)": "upvote_spam",
                "Incorrect (0)": "downvote_spam",
                "Chat": chat_link(chat),
            }
        ),
        link_preview=False,
    )

    buttons = None
    if log_report:
        log_channel = await log_report.get_chat()
        buttons = ikb({"View Message": message_link(log_channel, log_report.id)})
    text = (
        "🚨 <b>SPAM ALERT</b>  🚔\n"
        f"{sender_report}"
        f"<b>Spam Probability:</b> {spam_probability} %\n\n"
        "<i>Message has been deleted</i>"
    )
    await send_safely(client, event.chat_id, text, buttons=buttons)


async def handle_blacklisted_sender(event, sender, stats):
    """Ban a blacklisted sender and tell the chat"""
    if not await ban_sender(event, sender):
        await reply_or_leave(
            event,
            "I can't ban this user, who is blacklisted as a spammer. "
            "Make me an admin with the Ban users right.",
        )
        return
    text = (
        "🚨 <b>SPAMMER ALERT</b>  🚔\n"
        f"{format_sender_report(sender, stats)}\n"
        "<i>User has been banned</i>"
    )
    await send_safely(event.client, event.chat_id, text)
