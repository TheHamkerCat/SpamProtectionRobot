from pyrogram import filters
from pyrogram.types import CallbackQuery

from spr import NSFW_LOG_CHANNEL, SPAM_LOG_CHANNEL, SUDOERS, spr
from spr.core import ikb
from spr.utils.db import downvote, ignore_nsfw, upvote, user_voted
from spr.utils.misc import clean, get_file_unique_id


@spr.on_callback_query(filters.regex(r"^upvote_"))
async def upvote_cb_func(_, cq: CallbackQuery):
    if cq.message.chat.id not in [SPAM_LOG_CHANNEL, NSFW_LOG_CHANNEL]:
        return await cq.answer()
    data = cq.data.split("_")[1]
    user_id = cq.from_user.id
    mid = cq.message.message_id
    if data == "spam":
        if user_voted(mid, user_id):
            return await cq.answer("You've already casted your vote.")
        upvote(mid, user_id)
        kb = cq.message.reply_markup.inline_keyboard
        upvotes = clean(kb[0][0])
        downvotes = clean(kb[0][1])
        link = kb[1][0].url

        keyb = ikb(
            {
                f"Correct ({upvotes + 1})": "upvote_spam",
                f"Incorrect ({downvotes})": "downvote_spam",
                "Chat": link,
            },
            2
        )
        await cq.edit_message_reply_markup(keyb)
    elif data == "nsfw":
        if user_id in SUDOERS:
            await cq.message.delete()
        await cq.answer()
    else:
        await cq.answer()


@spr.on_callback_query(filters.regex(r"^downvote_"))
async def downvote_cb_func(_, cq: CallbackQuery):
    if cq.message.chat.id not in [SPAM_LOG_CHANNEL, NSFW_LOG_CHANNEL]:
        return await cq.answer()
    data = cq.data.split("_")[1]
    user_id = cq.from_user.id
    mid = cq.message.message_id

    if data == "spam":
        if user_voted(mid, user_id):
            return await cq.answer("You've already casted your vote.")
        downvote(mid, user_id)
        kb = cq.message.reply_markup.inline_keyboard
        upvotes = clean(kb[0][0])
        downvotes = clean(kb[0][1])
        link = kb[1][0].url
        keyb = ikb(
            {
                f"Correct ({upvotes})": "upvote_spam",
                f"Incorrect ({downvotes + 1})": "downvote_spam",
                "Chat": link,
            },
            2
        )
        await cq.edit_message_reply_markup(keyb)
    elif data == "nsfw":
        if user_id in SUDOERS:
            file_id = get_file_unique_id(cq.message)
            ignore_nsfw(file_id)
            await cq.message.delete()
        await cq.answer()
    else:
        await cq.answer()
