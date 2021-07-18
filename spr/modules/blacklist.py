from pyrogram import filters
from pyrogram.types import Message

from spr import SPAM_LOG_CHANNEL, SUDOERS, spr
from spr.modules.info import get_info
from spr.utils.db import (add_chat, add_user, blacklist_chat,
                          blacklist_user, chat_exists,
                          is_chat_blacklisted, is_user_blacklisted,
                          user_exists, whitelist_chat, whitelist_user)


@spr.on_message(
    filters.command("blacklist") & filters.user(SUDOERS), group=3
)
async def blacklist_func(_, message: Message):
    err = "Enter a user/chat's id and give a reason."
    if len(message.command) < 3:
        return await message.reply_text(err)
    id = message.text.split(None, 2)[1]
    reason = message.text.split(None, 2)[2].strip()
    if not reason or not id:
        return await message.reply_text(err)
    try:
        id = int(id)
    except ValueError:
        return await message.reply_text(err)

    if id == 0:
        return await message.reply_text(err)

    if id < 0:
        try:
            chat = await spr.get_chat(id)
        except Exception as e:
            return await message.reply_text(str(e))

        if not chat_exists(id):
            add_chat(id)
        if is_chat_blacklisted(id):
            return await message.reply_text(
                "This chat is already blacklisted."
            )
        blacklist_chat(id, reason)
        await message.reply_text(f"Blacklisted chat {chat.title}")
        msg = f"**BLACKLIST EVENT**\n{await get_info(id)}"
        return await spr.send_message(SPAM_LOG_CHANNEL, text=msg)

    if id in SUDOERS:
        return await message.reply_text(
            "This user is in SUDOERS and cannot be blacklisted."
        )
    try:
        user = await spr.get_users(id)
    except Exception as e:
        return await message.reply_text(str(e))

    if not user_exists(id):
        add_user(id)
    if is_user_blacklisted(id):
        return await message.reply_text(
            "This user is already blacklisted."
        )
    blacklist_user(id, reason)
    await message.reply_text(f"Blacklisted user {user.mention}")
    msg = f"**BLACKLIST EVENT**\n{await get_info(id)}"
    await spr.send_message(SPAM_LOG_CHANNEL, text=msg)


@spr.on_message(
    filters.command("whitelist") & filters.user(SUDOERS), group=3
)
async def whitelist_func(_, message: Message):
    err = "Enter a user/chat's id."
    if len(message.command) != 2:
        return await message.reply_text(err)
    id = message.text.split(None, 1)[1]
    try:
        id = int(id)
    except ValueError:
        return await message.reply_text(err)
    if id == 0:
        return await message.reply_text(err)
    if id < 0:
        try:
            chat = await spr.get_chat(id)
        except Exception as e:
            return await message.reply_text(str(e))

        if not chat_exists(id):
            add_chat(id)
        if not is_chat_blacklisted(id):
            return await message.reply_text(
                "This chat is already whitelisted."
            )
        whitelist_chat(id)
        return await message.reply_text(f"Whitelisted {chat.title}")

    try:
        user = await spr.get_users(id)
    except Exception as e:
        return await message.reply_text(str(e))

    if not user_exists(id):
        add_user(id)
    if not is_user_blacklisted(id):
        return await message.reply_text(
            "This user is already whitelisted."
        )
    whitelist_user(id)
    return await message.reply_text(f"Whitelisted {user.mention}")
