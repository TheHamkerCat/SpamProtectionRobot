"""/info and inline lookups of users and chats."""
from telethon import events, functions, utils
from telethon.errors import RPCError
from telethon.tl import types

from spr.core.commands import command, get_args
from spr.core.entities import get_username, mention
from spr.core.formatting import esc

__MODULE__ = "Info"
__HELP__ = """
<b>Get Info About A Chat Or User</b>

/info [CHAT_ID/Username|USER_ID/Username]

or you can use inline mode &gt;&gt;
@SpamProtectionRobot [CHAT_ID/Username|USER_ID/Username]
"""

NOT_FOUND_TEXT = "I haven't seen this chat/user."


async def resolve_entity(client, query):
    """User/chat from an ID or a username. None if not found."""
    if isinstance(query, str):
        query = query.strip()
        if query.lstrip("-").isdigit():
            query = int(query)
    if not query:
        return None
    try:
        return await client.get_entity(query)
    except (ValueError, TypeError, RPCError):
        return None


def get_chat_type(chat) -> str:
    if isinstance(chat, types.Chat):
        return "group"
    if getattr(chat, "megagroup", False):
        return "supergroup"
    return "channel"


async def get_members_count(client, chat):
    """Member count of a group/channel, None if unknown"""
    if not isinstance(chat, types.Channel):
        return getattr(chat, "participants_count", None)
    try:
        full = await client(functions.channels.GetFullChannelRequest(chat))
    except RPCError:
        return None
    return full.full_chat.participants_count


def blacklist_line(settings_or_stats) -> str:
    if not settings_or_stats.blacklisted:
        return ""
    return (
        f"<b>Blacklist Reason:</b> {esc(settings_or_stats.blacklist_reason)}"
        f" | {settings_or_stats.blacklisted_at:%Y-%m-%d %H:%M} UTC\n"
    )


async def get_user_info(client, user) -> str:
    stats = await client.db.get_user_stats(user.id)
    # telegram only tells the DC of users if they have a profile photo
    dc_id = getattr(user.photo, "dc_id", None)
    return (
        f"\n<b>ID:</b> {user.id}\n"
        f"<b>DC:</b> {dc_id}\n"
        f"<b>Username:</b> {esc(get_username(user))}\n"
        f"<b>Mention: </b> {mention(user, 'Link')}\n\n"
        f"<b>Is Sudo:</b> {user.id in client.settings.sudoers}\n"
        f"<b>Trust:</b> {stats.trust}\n"
        f"<b>Spammer:</b> {stats.is_spammer}\n"
        f"<b>Reputation:</b> {stats.reputation}\n"
        f"<b>NSFW Count:</b> {stats.nsfw_count}\n"
        f"<b>Potential Spammer:</b> {stats.is_potential_spammer}\n"
        f"<b>Blacklisted:</b> {stats.blacklisted}\n"
        f"{blacklist_line(stats)}"
    )


async def get_chat_info(client, chat) -> str:
    # The same "-100..." style ID that the database and the Bot API use
    chat_id = utils.get_peer_id(chat)
    settings = await client.db.get_chat_settings(chat_id)
    return (
        f"\n<b>ID:</b> {chat_id}\n"
        f"<b>Username:</b> {esc(get_username(chat))}\n"
        f"<b>Type:</b> {get_chat_type(chat)}\n"
        f"<b>Members:</b> {await get_members_count(client, chat)}\n"
        f"<b>Scam:</b> {bool(getattr(chat, 'scam', False))}\n"
        f"<b>Restricted:</b> {bool(getattr(chat, 'restricted', False))}\n"
        f"<b>Blacklisted:</b> {settings.blacklisted}\n"
        f"{blacklist_line(settings)}"
    )


async def get_info(client, query):
    """Info text for a user/chat ID or username. None if not found."""
    entity = await resolve_entity(client, query)
    if entity is None:
        return None
    if isinstance(entity, types.User):
        return await get_user_info(client, entity)
    return await get_chat_info(client, entity)


@events.register(events.NewMessage(pattern=command("info")))
async def info_func(event):
    args = get_args(event)
    if event.is_reply:
        reply = await event.get_reply_message()
        if reply is None:
            return await event.reply("I can't find the message you replied to.")
        target = reply.sender_id or event.chat_id
    elif len(args) == 1:
        target = event.sender_id or event.chat_id
    elif len(args) == 2:
        target = args[1]
    else:
        return await event.reply("Read the help menu")
    info = await get_info(event.client, target)
    await event.reply(info or NOT_FOUND_TEXT)


@events.register(events.InlineQuery())
async def inline_info_func(event):
    info = await get_info(event.client, event.text)
    builder = event.builder
    if info:
        results = [builder.article("Found Entity", text=info)]
    else:
        results = [builder.article(NOT_FOUND_TEXT, text=NOT_FOUND_TEXT)]
    await event.answer(results, cache_time=3)
