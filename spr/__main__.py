import asyncio
import re
from importlib import import_module as import_

from pyrogram import filters, idle
from pyrogram.types import (CallbackQuery, InlineKeyboardButton,
                            InlineKeyboardMarkup, Message)

from spr import BOT_USERNAME, conn, session, spr
from spr.core import ikb
from spr.modules import MODULES
from spr.utils.misc import once_a_day, paginate_modules

HELPABLE = {}


async def main():
    await spr.start()
    # Load all the modules.
    for module in MODULES:
        imported_module = import_(module)
        if (
            hasattr(imported_module, "__MODULE__")
            and imported_module.__MODULE__
        ):
            imported_module.__MODULE__ = imported_module.__MODULE__
            if (
                hasattr(imported_module, "__HELP__")
                and imported_module.__HELP__
            ):
                HELPABLE[
                    imported_module.__MODULE__.lower()
                ] = imported_module
    print("STARTED !")
    loop = asyncio.get_running_loop()
    loop.create_task(once_a_day())
    await idle()
    conn.commit()
    conn.close()
    await session.close()
    await spr.stop()


@spr.on_message(filters.command(["help", "start"]), group=2)
async def help_command(_, message: Message):
    if message.chat.type != "private":
        kb = ikb({"Help": f"https://t.me/{BOT_USERNAME}?start=help"})
        return await message.reply("Pm Me For Help", reply_markup=kb)
    kb = ikb(
        {
            "Help": "bot_commands",
            "Repo": "https://github.com/TheHamkerCat/SpamProtectionRobot",
            "Add Me To Your Group": f"https://t.me/{BOT_USERNAME}?startgroup=new",
            "Support Chat (for now)": "https://t.me/WBBSupport",
        }
    )
    mention = message.from_user.mention
    await message.reply_photo(
        "https://hamker.me/logo_3.png",
        caption=f"Hi {mention}, I'm SpamProtectionRobot,"
        + " Choose An Option From Below.",
        reply_markup=kb,
    )


@spr.on_callback_query(filters.regex("bot_commands"))
async def commands_callbacc(_, cq: CallbackQuery):
    text, keyboard = await help_parser(cq.from_user.mention)
    await asyncio.gather(
        cq.answer(),
        cq.message.delete(),
        spr.send_message(
            cq.message.chat.id,
            text=text,
            reply_markup=keyboard,
        ),
    )


async def help_parser(name, keyboard=None):
    if not keyboard:
        keyboard = InlineKeyboardMarkup(
            paginate_modules(0, HELPABLE, "help")
        )
    return (
        f"Hello {name}, I'm SpamProtectionRobot, I can protect "
        + "your group from Spam and NSFW media using "
        + "machine learning. Choose an option from below.",
        keyboard,
    )


@spr.on_callback_query(filters.regex(r"help_(.*?)"))
async def help_button(client, query: CallbackQuery):
    mod_match = re.match(r"help_module\((.+?)\)", query.data)
    prev_match = re.match(r"help_prev\((.+?)\)", query.data)
    next_match = re.match(r"help_next\((.+?)\)", query.data)
    back_match = re.match(r"help_back", query.data)
    create_match = re.match(r"help_create", query.data)
    u = query.from_user.mention
    top_text = (
        f"Hello {u}, I'm SpamProtectionRobot, I can protect "
        + "your group from Spam and NSFW media using "
        + "machine learning. Choose an option from below."
    )
    if mod_match:
        module = mod_match.group(1)
        text = (
            "{} **{}**:\n".format(
                "Here is the help for", HELPABLE[module].__MODULE__
            )
            + HELPABLE[module].__HELP__
        )

        await query.message.edit(
            text=text,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "back", callback_data="help_back"
                        )
                    ]
                ]
            ),
            disable_web_page_preview=True,
        )

    elif prev_match:
        curr_page = int(prev_match.group(1))
        await query.message.edit(
            text=top_text,
            reply_markup=InlineKeyboardMarkup(
                paginate_modules(curr_page - 1, HELPABLE, "help")
            ),
            disable_web_page_preview=True,
        )

    elif next_match:
        next_page = int(next_match.group(1))
        await query.message.edit(
            text=top_text,
            reply_markup=InlineKeyboardMarkup(
                paginate_modules(next_page + 1, HELPABLE, "help")
            ),
            disable_web_page_preview=True,
        )

    elif back_match:
        await query.message.edit(
            text=top_text,
            reply_markup=InlineKeyboardMarkup(
                paginate_modules(0, HELPABLE, "help")
            ),
            disable_web_page_preview=True,
        )

    elif create_match:
        text, keyboard = await help_parser(query)
        await query.message.edit(
            text=text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )

    return await client.answer_callback_query(query.id)


@spr.on_message(filters.command("runs"), group=3)
async def runs_func(_, message: Message):
    await message.reply("What am i? Rose?")


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
