"""Per chat settings (spam detection, NSFW detection, alerts, Federation), and manual scans"""
from dataclasses import dataclass

from telethon import events
from telethon.errors import RPCError

from spr.arq import ArqError
from spr.core.commands import command, get_args
from spr.core.formatting import esc
from spr.core.text import plain_letters
from spr.utils.media import download_for_scan, get_media

__MODULE__ = "Manage"
__HELP__ = """
/anti_nsfw [ENABLE|DISABLE] - Enable or disable NSFW Detection.
/anti_spam [ENABLE|DISABLE] - Enable or disable Spam Detection.
/alerts [ENABLE|DISABLE] - Post a message in the chat when I delete spam or NSFW media or ban a spammer. On by default.
/ofd [ENABLE|DISABLE] - Check senders and messages with the Federation (OFD) server too, when this bot has one. On by default.
/ofd_privacy [ENABLE|DISABLE] - Send only the text of messages to Federation, without who sent them, and publish nothing. Senders are still looked up there. Off by default.
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
        "but without posting about it here.",
    ),
    "ofd": Feature(
        "ofd",
        "Federation (OFD)",
        "Enabled Federation (OFD) checks.",
        "Disabled Federation (OFD) checks. I don't send anything from this chat to the Federation server.",
    ),
    "ofd_privacy": Feature(
        "ofd_privacy",
        "Federation privacy mode",
        "Enabled Federation privacy mode.",
        "Disabled Federation privacy mode.",
    ),
}
# Only there when the host set OFD_HOST
OFD_FEATURES = ("ofd", "ofd_privacy")
OFD_NOT_SET_UP = "Federation (OFD) isn't set up on this bot."


def describe_federation(ofd, settings) -> str:
    """What this chat sends to the Federation server, after an OFD setting changed"""
    if not settings.ofd_enabled:
        return "Federation is off in this chat, nothing is sent (/ofd)."
    if settings.ofd_privacy:
        return (
            "Only the text of messages goes to the Federation server, without who sent them, "
            "and nothing is published. Senders are still looked up there, so those blacklisted "
            "on Federation are still banned."
        )
    if ofd.can_contribute:
        text = (
            "Messages I scan go to the Federation server with who sent them and message "
            "details, and I publish the senders' public profiles there."
        )
    else:
        text = "Messages I scan go to the Federation server with who sent them and message details."
        if ofd.collects_public_scan_content:
            text += " The server may keep what I send."
    return text + " /ofd_privacy ENABLE sends only the text."


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
    client = event.client
    if feature in OFD_FEATURES and client.ofd is None:
        return await event.reply(OFD_NOT_SET_UP)
    if not await can_manage(event):
        return

    status = args[1].lower()
    if status not in ("enable", "disable"):
        return await event.reply(
            f"Unknown Suffix, Use /{command_name} [ENABLE|DISABLE]"
        )
    enabled = status == "enable"
    changed = await client.db.set_chat_feature(event.chat_id, feature, enabled)
    if not changed:
        return await event.reply(f"Already {status}d.")
    reply = details.enabled_reply if enabled else details.disabled_reply
    if feature in OFD_FEATURES and (enabled or feature == "ofd_privacy"):
        settings = await client.db.get_chat_settings(event.chat_id)
        reply += " " + describe_federation(client.ofd, settings)
    await event.reply(reply)


@events.register(events.NewMessage(pattern=command("anti_nsfw"), func=in_group))
async def nsfw_toggle_func(event):
    await toggle_feature(event, "nsfw")


@events.register(events.NewMessage(pattern=command("anti_spam"), func=in_group))
async def spam_toggle_func(event):
    await toggle_feature(event, "spam")


@events.register(events.NewMessage(pattern=command("alerts"), func=in_group))
async def alerts_toggle_func(event):
    await toggle_feature(event, "alerts")


@events.register(events.NewMessage(pattern=command("ofd"), func=in_group))
async def ofd_toggle_func(event):
    await toggle_feature(event, "ofd")


@events.register(events.NewMessage(pattern=command("ofd_privacy"), func=in_group))
async def ofd_privacy_toggle_func(event):
    await toggle_feature(event, "ofd_privacy")


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
        "ofd": settings.ofd_enabled,
        "ofd_privacy": settings.ofd_privacy,
    }
    federation = event.client.ofd is not None
    lines = [
        f"{details.label}: <b>{on_off(values[feature])}</b> (/{details.command})"
        for feature, details in FEATURES.items()
        if federation or feature not in OFD_FEATURES
    ]
    await event.reply("<b>Settings of this chat</b>\n" + "\n".join(lines))


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
        result = await event.client.arq.nlp(plain_letters(reply.raw_text))
        model = await event.client.arq.spam_model(result.model_version)
    except ArqError as error:
        return await event.reply(str(error))
    deleted = "yes" if result.spam > model.spam_above else "no"
    await event.reply(
        f"<b>Spam:</b> <code>{result.spam} %</code> (deleted above {model.spam_above} %: {deleted})\n"
        f"<b>Category:</b> <code>{esc(result.category)}</code>\n"
        f"<b>Harm:</b> <code>{result.harm}</code> (0 none, 1 promotion, 2 scam)\n"
        f"<b>Abusive:</b> <code>{result.abusive} %</code>\n"
        f"<b>Language:</b> <code>{esc(result.language)}</code>\n"
        f"<b>Model:</b> <code>{esc(result.model_version)}</code>"
    )
