"""Per chat settings (spam detection, NSFW detection, alerts), and manual scans"""
from dataclasses import dataclass

from telethon import events
from telethon.errors import RPCError

from spr.arq import ArqError
from spr.core.commands import command, get_args
from spr.utils.media import download_for_scan, get_media

__MODULE__ = "Manage"
__HELP__ = """
/anti_nsfw [ENABLE|DISABLE] - Enable or disable NSFW Detection.
/anti_spam [ENABLE|DISABLE] - Enable or disable Spam Detection.
/alerts [ENABLE|DISABLE] - Post a message in the chat when I delete spam or NSFW media or ban a spammer. On by default.
/settings - Show this chat's settings.

/nsfw_scan - Classify a media.
/spam_scan - Get Spam predictions of replied message.
"""



@dataclass(frozen=True)
class Feature:
    command: str
    label: str
    enabled_reply: str
    disabled_reply: str


FEATURES = {
    "spam": Feature(
        "anti_spam", "Spam detection", "Enabled Spam Detection.", "Disabled Spam Detection."
    ),
    "nsfw": Feature(
        "anti_nsfw", "NSFW detection", "Enabled NSFW Detection.", "Disabled NSFW Detection."
    ),
    "alerts": Feature(
        "alerts",
        "Alerts",
        "Alerts are on. When I delete spam or NSFW media or ban a spammer, "
        "I post a short message about it here.",
        "Alerts are off. I still delete spam and NSFW media and ban spammers, "
        "but without posting about it here. If I lack admin rights, I still say so.",
    ),
}


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
    """Shared body of /anti_nsfw, /anti_spam and /alerts"""
    details = FEATURES[feature]
    command_name = details.command
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
    if enabled:
        await event.reply(details.enabled_reply)
    else:
        await event.reply(details.disabled_reply)


@events.register(events.NewMessage(pattern=command("anti_nsfw"), func=in_group))
async def nsfw_toggle_func(event):
    await toggle_feature(event, "nsfw")


@events.register(events.NewMessage(pattern=command("anti_spam"), func=in_group))
async def spam_toggle_func(event):
    await toggle_feature(event, "spam")


@events.register(events.NewMessage(pattern=command("alerts"), func=in_group))
async def alerts_toggle_func(event):
    await toggle_feature(event, "alerts")


def on_off(enabled: bool) -> str:
    if enabled:
        return "on"
    return "off"


@events.register(events.NewMessage(pattern=command("settings"), func=in_group))
async def settings_command(event):
    """Anyone can see the settings, only admins can change them"""
    settings = await event.client.db.get_chat_settings(event.chat_id)
    values = {
        "spam": settings.spam_enabled,
        "nsfw": settings.nsfw_enabled,
        "alerts": settings.alerts_enabled,
    }
    lines = [
        f"{details.label}: <b>{on_off(values[feature])}</b> (/{details.command})"
        for feature, details in FEATURES.items()
    ]
    await event.reply(
        "<b>Settings of this chat</b>\n" + "\n".join(lines)
        + "\n\nAdmins can change them with the commands in brackets."
    )


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
