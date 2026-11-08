from telethon import utils
from telethon.tl import types

from spr.core.formatting import esc

# Shown for spam in chats without a public username
PRIVATE_CHAT_LINK = "https://t.me/SpamProtectionLog/93"


def get_username(entity):
    """Public username, also for accounts whose usernames are collectible
    (Fragment) ones, which Telegram lists in `usernames` instead"""
    username = getattr(entity, "username", None)
    if username:
        return username
    for item in getattr(entity, "usernames", None) or []:
        if item.active:
            return item.username
    return None


def display_name(entity) -> str:
    """Name of a user or chat, its ID if it has none (deleted accounts)"""
    return utils.get_display_name(entity) or str(entity.id)


def mention(entity, name: str = None) -> str:
    """HTML link to a user, or to a public channel/group"""
    label = esc(name or display_name(entity))
    if isinstance(entity, types.User):
        return f'<a href="tg://user?id={entity.id}">{label}</a>'
    username = get_username(entity)
    if username:
        return f'<a href="https://t.me/{esc(username)}">{label}</a>'
    return label


def chat_link(chat) -> str:
    """Public link to a chat, PRIVATE_CHAT_LINK if it has no username"""
    username = get_username(chat)
    if username:
        return f"https://t.me/{username}"
    return PRIVATE_CHAT_LINK


def message_link(chat, message_id: int) -> str:
    """Link to a message in a group or channel"""
    username = get_username(chat)
    if username:
        return f"https://t.me/{username}/{message_id}"
    return f"https://t.me/c/{chat.id}/{message_id}"
