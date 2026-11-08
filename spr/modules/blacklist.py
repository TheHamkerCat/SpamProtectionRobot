"""/blacklist and /whitelist, for sudo users only"""
from telethon import events
from telethon.errors import RPCError

from spr.core.commands import command
from spr.core.entities import mention
from spr.modules.info import get_info
from spr.reports import Report


def is_sudo(event) -> bool:
    return event.sender_id in event.client.settings.sudoers


def parse_entity_id(text: str):
    """Non-zero int from text, else None"""
    try:
        value = int(text)
    except ValueError:
        return None
    return value or None


def entity_kind(entity_id: int) -> str:
    """Negative IDs are chats, positive IDs are users"""
    if entity_id < 0:
        return "chat"
    return "user"


async def find_entity(event, entity_id: int):
    """Entity for an ID, or None after telling the user why not"""
    try:
        return await event.client.get_entity(entity_id)
    except (ValueError, RPCError) as error:
        await event.reply(f"I can't find {entity_id}: {error}")
        return None


@events.register(events.NewMessage(pattern=command("blacklist"), func=is_sudo))
async def blacklist_func(event):
    usage = "Enter a user/chat's id and give a reason."
    parts = event.raw_text.split(None, 2)
    if len(parts) < 3:
        return await event.reply(usage)
    entity_id = parse_entity_id(parts[1])
    reason = parts[2].strip()
    if not entity_id or not reason:
        return await event.reply(usage)

    kind = entity_kind(entity_id)
    if kind == "user" and entity_id in event.client.settings.sudoers:
        return await event.reply("This user is in SUDOERS and cannot be blacklisted.")
    entity = await find_entity(event, entity_id)
    if entity is None:
        return

    if not await event.client.db.blacklist(kind, entity_id, reason):
        return await event.reply(f"This {kind} is already blacklisted.")
    await event.reply(f"Blacklisted {kind} {mention(entity)}")
    info = await get_info(event.client, entity_id)
    event.client.reports.add(
        event.client.settings.spam_log_channel,
        Report(f"<b>BLACKLIST EVENT</b>\n{info}"),
    )


@events.register(events.NewMessage(pattern=command("whitelist"), func=is_sudo))
async def whitelist_func(event):
    usage = "Enter a user/chat's id."
    parts = event.raw_text.split()
    if len(parts) != 2:
        return await event.reply(usage)
    entity_id = parse_entity_id(parts[1])
    if not entity_id:
        return await event.reply(usage)

    kind = entity_kind(entity_id)
    entity = await find_entity(event, entity_id)
    if entity is None:
        return
    if not await event.client.db.whitelist(kind, entity_id):
        return await event.reply(f"This {kind} is already whitelisted.")
    await event.reply(f"Whitelisted {mention(entity)}")
