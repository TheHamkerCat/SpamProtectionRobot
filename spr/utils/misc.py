from asyncio import gather, sleep
from datetime import datetime
from math import ceil
from time import time

from pyrogram.types import InlineKeyboardButton, ChatMemberUpdated


from spr import DB_NAME, SESSION_NAME, SUDOERS, spr


async def backup():
    for user in SUDOERS:
        try:
            await gather(
                spr.send_document(user, DB_NAME),
                spr.send_document(user, SESSION_NAME + ".session"),
            )
        except Exception:
            pass

admins_in_chat = {}


async def admins(chat_id: int):
    global admins_in_chat
    if chat_id in admins_in_chat:
        interval = time() - admins_in_chat[chat_id]["last_updated_at"]
        if interval < 3600:
            return admins_in_chat[chat_id]["data"]

    admins_in_chat[chat_id] = {
        "last_updated_at": time(),
        "data": [
            member.user.id
            async for member in spr.iter_chat_members(
                chat_id, filter="administrators"
            )
        ],
    }
    return admins_in_chat[chat_id]["data"]


# Admin cache reload


@spr.on_chat_member_updated()
async def admin_cache_func(_, cmu: ChatMemberUpdated):
    if cmu.old_chat_member and cmu.old_chat_member.promoted_by:
        admins_in_chat[cmu.chat.id] = {
            "last_updated_at": time(),
            "data": [
                member.user.id
                async for member in spr.iter_chat_members(
                    cmu.chat.id, filter="administrators"
                )
            ],
        }
        print(f"Updated admin cache for {cmu.chat.id} [{cmu.chat.title}]")


async def once_a_day():
    print("BACKING UP DB...")
    await backup()
    dt = datetime.now()
    seconds_till_twelve = (
        ((24 - dt.hour - 1) * 60 * 60)
        + ((60 - dt.minute - 1) * 60)
        + (60 - dt.second)
    )
    print(
        "BACKED UP, NEXT BACKUP WILL HAPPEN AFTER "
        + f"{round(seconds_till_twelve/60/60, 4)} HOUR(S)"
    )
    await sleep(int(seconds_till_twelve))  # Sleep till 12 AM
    while True:
        print("DB BACKED UP!, NEXT BACKUP WILL HAPPEN AFTER 24 HOURS")
        await backup()
        await sleep(86400)  # sleep for a day


def get_file_id(message):
    if message.document:
        if int(message.document.file_size) > 3145728:
            return
        mime_type = message.document.mime_type
        if mime_type != "image/png" and mime_type != "image/jpeg":
            return
        return message.document.file_id

    if message.sticker:
        if message.sticker.is_animated:
            if not message.sticker.thumbs:
                return
            return message.sticker.thumbs[0].file_id
        return message.sticker.file_id

    if message.photo:
        return message.photo.file_id

    if message.animation:
        if not message.animation.thumbs:
            return
        return message.animation.thumbs[0].file_id

    if message.video:
        if not message.video.thumbs:
            return
        return message.video.thumbs[0].file_id


def get_file_unique_id(message):
    m = message
    m = m.sticker or m.video or m.document or m.animation or m.photo
    if not m:
        return
    return m.file_unique_id


class EqInlineKeyboardButton(InlineKeyboardButton):
    def __eq__(self, other):
        return self.text == other.text

    def __lt__(self, other):
        return self.text < other.text

    def __gt__(self, other):
        return self.text > other.text


def paginate_modules(page_n, module_dict, prefix, chat=None):
    if not chat:
        modules = sorted(
            [
                EqInlineKeyboardButton(
                    x.__MODULE__,
                    callback_data="{}_module({})".format(
                        prefix, x.__MODULE__.lower()
                    ),
                )
                for x in module_dict.values()
            ]
        )
    else:
        modules = sorted(
            [
                EqInlineKeyboardButton(
                    x.__MODULE__,
                    callback_data="{}_module({},{})".format(
                        prefix, chat, x.__MODULE__.lower()
                    ),
                )
                for x in module_dict.values()
            ]
        )

    pairs = list(zip(modules[::3], modules[1::3], modules[2::3]))
    i = 0
    for m in pairs:
        for _ in m:
            i += 1
    if len(modules) - i == 1:
        pairs.append((modules[-1],))
    elif len(modules) - i == 2:
        pairs.append(
            (
                modules[-2],
                modules[-1],
            )
        )

    max_num_pages = ceil(len(pairs) / 7)
    modulo_page = page_n % max_num_pages

    # can only have a certain amount of buttons side by side
    if len(pairs) > 7:
        pairs = pairs[modulo_page * 7 : 7 * (modulo_page + 1)] + [
            (
                EqInlineKeyboardButton(
                    "<",
                    callback_data="{}_prev({})".format(
                        prefix, modulo_page
                    ),
                ),
                EqInlineKeyboardButton(
                    ">",
                    callback_data="{}_next({})".format(
                        prefix, modulo_page
                    ),
                ),
            )
        ]

    return pairs


""" TO GET UPVOTES/DOWNVOTES FROM MESSAGE """
clean = lambda x: int(
    x.text.split()[1].replace("(", "").replace(")", "")
)
