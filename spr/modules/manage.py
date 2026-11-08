"""Turn detection on/off per chat, and manual scans"""
from telethon import events
from telethon.errors import RPCError

from spr.arq import ArqError
from spr.core.commands import command, get_args
from spr.utils.media import download_for_scan, get_media

__MODULE__ = "Manage"
__HELP__ = """
/anti_nsfw [ENABLE|DISABLE] - Enable or disable NSFW Detection.
/anti_spam [ENABLE|DISABLE] - Enable or disable Spam Detection.

/nsfw_scan - Classify a media.
/spam_scan - Get Spam predictions of replied message.
"""

FEATURE_LABELS = {"nsfw": "NSFW Detection", "spam": "Spam Detection"}


def in_group(event) -> bool:
    return not event.is_private


async def can_manage(event) -> bool:
    """Sudo users, admins and anonymous admins."""
    try:
        if await event.client.admins.is_privileged(event.chat_id, event.sender_id):
            return True
    except RPCError:
        await event.reply(
            "I can't read the admin list of this chat. Make me an admin and try again."
        )
        return False
    await event.reply("You don't have enough permissions")
    return False


async def toggle_feature(event, feature: str):
    """Shared body of /anti_nsfw and /anti_spam"""
    command_name = f"anti_{feature}"
    label = FEATURE_LABELS[feature]
    args = get_args(event)
    if len(args) != 2:
        return await event.reply(f"Usage: /{command_name} [ENABLE|DISABLE]")
    if not await can_manage(event):
        return

    status = args[1].lower()
    if status not in ("enable", "disable"):
        return await event.reply(
            f"Unknown Suffix, Use /{command_name} [ENABLE|DISABLE]"
        )
    enabled = status == "enable"
    changed = await event.client.db.set_chat_feature(event.chat_id, feature, enabled)
    if not changed:
        return await event.reply(f"Already {status}d.")
    await event.reply(f"{status.capitalize()}d {label}.")


@events.register(events.NewMessage(pattern=command("anti_nsfw"), func=in_group))
async def nsfw_toggle_func(event):
    await toggle_feature(event, "nsfw")


@events.register(events.NewMessage(pattern=command("anti_spam"), func=in_group))
async def spam_toggle_func(event):
    await toggle_feature(event, "spam")


@events.register(events.NewMessage(pattern=command("nsfw_scan")))
async def nsfw_scan_command(event):
    usage = "Reply to an image/document/sticker/animation to scan it."
    reply = await event.get_reply_message()
    if reply is None or get_media(reply) is None:
        return await event.reply(usage)
    status = await event.reply("Scanning")
    image = await download_for_scan(event.client, reply)
    if not image:
        return await status.edit("I can't scan this kind of file.")
    try:
        result = await event.client.arq.nsfw_scan(image)
    except ArqError as error:
        return await status.edit(str(error))
    await status.edit(
        f"<b>Neutral:</b> <code>{result.neutral} %</code>\n"
        f"<b>Porn:</b> <code>{result.porn} %</code>\n"
        f"<b>Hentai:</b> <code>{result.hentai} %</code>\n"
        f"<b>Sexy:</b> <code>{result.sexy} %</code>\n"
        f"<b>Drawings:</b> <code>{result.drawings} %</code>\n"
        f"<b>NSFW:</b> <code>{result.is_nsfw}</code>"
    )


@events.register(events.NewMessage(pattern=command("spam_scan")))
async def spam_scan_command(event):
    reply = await event.get_reply_message()
    if reply is None:
        return await event.reply("Reply to a message to scan it.")
    if not reply.raw_text:
        return await event.reply("Can't scan that")
    try:
        result = await event.client.arq.nlp(reply.raw_text)
    except ArqError as error:
        return await event.reply(str(error))
    await event.reply(
        f"<b>Is Spam:</b> {result.is_spam}\n"
        f"<b>Spam Probability:</b> {result.spam_probability} %\n"
        f"<b>Spam:</b> {result.spam}\n"
        f"<b>Ham:</b> {result.ham}\n"
        f"<b>Profanity:</b> {result.profanity}"
    )
