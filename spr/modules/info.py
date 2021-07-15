from pyrogram import filters
from pyrogram.types import Message, InlineQuery, InlineQueryResultArticle, InputTextMessageContent

from spr import spr, SUDOERS
from spr.utils.db import (
        get_nsfw_count, get_reputation,
        get_user_trust, is_user_blacklisted,
        is_chat_blacklisted, user_exists, chat_exists,
        add_chat, add_user
        )

__MODULE__ = "Info"
__HELP__ = """
**Get Info About A Chat Or User**

/info [CHAT_ID/Username|USER_ID/Username]

or you can use inline mode >>
@SpamProtectionBot [CHAT_ID/Username|USER_ID/Username]
"""

async def get_user_info(user):
    try:
        user = await spr.get_users(user)
    except Exception:
        return
    if not user_exists(user.id):
        add_user(user.id)
    trust = get_user_trust(user.id)
    return f"""
**ID:** {user.id}
**DC:** {user.dc_id}
**Username:** {user.username}
**Mention: ** {user.mention("Link")}

**Is Sudo:** {user.id in SUDOERS}
**Trust:** {trust}
**Spammer:** {True if trust < 50 else False}
**Reputation:** {get_reputation(user.id)}
**NSFW Count:** {get_nsfw_count(user.id)}
**Blacklisted:** {is_user_blacklisted(user.id)}
**Potential Spammer:** {True if trust < 70 else False}
"""

async def get_chat_info(chat):
    try:
        chat = await spr.get_chat(chat)
    except Exception:
        return
    if not chat_exists(chat.id):
        add_chat(chat.id)
    return f"""
**ID:** {chat.id}
**Username:** {chat.username}
**Type:** {chat.type}
**Members:** {chat.members_count}
**Scam:** {chat.is_scam}
**Restricted:** {chat.is_restricted}
**Blacklisted:** {is_chat_blacklisted(chat.id)}
"""

async def get_info(entity):
    user = await get_user_info(entity)
    if user:
        return user
    chat = await get_chat_info(entity)
    return chat

@spr.on_message(filters.command("info"), group=3)
async def info_func(_, message: Message):
    if len(message.command) != 2:
        return await message.reply_text("Not enough arguments.")
    entity = message.text.split(None, 1)[1]
    entity = await get_info(entity)
    entity = entity or "I haven't seen this chat/user."
    await message.reply_text(entity)


@spr.on_inline_query()
async def inline_info_func(_, query: InlineQuery):
    query_ = query.query.strip()
    entity = await get_info(query_)
    if not entity:
        err = "I haven't seen this user/chat."
        results = [
                InlineQueryResultArticle(
                    err,
                    input_message_content=err
                    )
                ]
    else:
        results = [
                InlineQueryResultArticle(
                    "Found Entity",
                    input_message_content=entity
                    )
                ]
    await query.answer(results=results, cache_time=3)
