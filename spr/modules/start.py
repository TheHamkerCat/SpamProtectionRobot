"""/start, /help and the help menu"""
import re
from math import ceil
from pathlib import Path

from telethon import Button, events

from spr.core import ikb
from spr.core.commands import command, get_bot_username
from spr.core.entities import mention

START_IMAGE = Path(__file__).resolve().parents[2] / "assets" / "start.jpg"
REPO_URL = "https://github.com/TheHamkerCat/SpamProtectionRobot"
SUPPORT_CHAT_URL = "https://t.me/WBBSupport"

HELP_ROWS_PER_PAGE = 7
HELP_BUTTONS_PER_ROW = 3

HELP_MODULE_REGEX = re.compile(r"help_module\((\w+)\)")
HELP_PAGE_REGEX = re.compile(r"help_(prev|next)\((\d+)\)")


def help_intro(user) -> str:
    return (
        f"Hello {mention(user)}, I'm SpamProtectionRobot, I can protect "
        "your group from Spam and NSFW media using "
        "machine learning. Choose an option from below."
    )


def paginate_modules(page: int, help_modules: dict, prefix: str = "help") -> list:
    """Button rows for the help menu, HELP_ROWS_PER_PAGE rows per page"""
    modules = sorted(help_modules.values(), key=lambda module: module.__MODULE__)
    buttons = [
        Button.inline(
            module.__MODULE__, data=f"{prefix}_module({module.__MODULE__.lower()})"
        )
        for module in modules
    ]
    rows = [
        buttons[i : i + HELP_BUTTONS_PER_ROW]
        for i in range(0, len(buttons), HELP_BUTTONS_PER_ROW)
    ]
    if len(rows) <= HELP_ROWS_PER_PAGE:
        return rows

    page_count = ceil(len(rows) / HELP_ROWS_PER_PAGE)
    page %= page_count
    start = page * HELP_ROWS_PER_PAGE
    return rows[start : start + HELP_ROWS_PER_PAGE] + [
        [
            Button.inline("<", data=f"{prefix}_prev({page})"),
            Button.inline(">", data=f"{prefix}_next({page})"),
        ]
    ]


@events.register(events.NewMessage(pattern=command("help", "start")))
async def help_command(event):
    username = get_bot_username()
    if not event.is_private:
        keyboard = ikb({"Help": f"https://t.me/{username}?start=help"})
        return await event.reply("Pm Me For Help", buttons=keyboard)
    keyboard = ikb(
        {
            "Help": "bot_commands",
            "Repo": REPO_URL,
            "Add Me To Your Group": f"https://t.me/{username}?startgroup=new",
            "Support Chat (for now)": SUPPORT_CHAT_URL,
        }
    )
    user = await event.get_sender()
    await event.reply(
        f"Hi {mention(user)}, I'm SpamProtectionRobot, Choose An Option From Below.",
        file=str(START_IMAGE),
        buttons=keyboard,
    )


@events.register(events.CallbackQuery(data=b"bot_commands"))
async def commands_callback(event):
    user = await event.get_sender()
    await event.answer()
    await event.delete()
    await event.client.send_message(
        event.chat_id,
        help_intro(user),
        buttons=paginate_modules(0, event.client.help_modules),
    )


@events.register(events.CallbackQuery(pattern=rb"^help_"))
async def help_button(event):
    help_modules = event.client.help_modules
    data = event.data.decode(errors="replace")
    user = await event.get_sender()

    module_match = HELP_MODULE_REGEX.fullmatch(data)
    page_match = HELP_PAGE_REGEX.fullmatch(data)

    if module_match:
        module = help_modules.get(module_match.group(1))
        if module is None:
            return await event.answer("This help page doesn't exist anymore.")
        await event.edit(
            f"Here is the help for <b>{module.__MODULE__}</b>:\n{module.__HELP__}",
            buttons=[[Button.inline("back", data="help_back")]],
            link_preview=False,
        )
    elif page_match:
        step = -1 if page_match.group(1) == "prev" else 1
        page = int(page_match.group(2)) + step
        await event.edit(
            help_intro(user),
            buttons=paginate_modules(page, help_modules),
            link_preview=False,
        )
    elif data == "help_back":
        await event.edit(
            help_intro(user),
            buttons=paginate_modules(0, help_modules),
            link_preview=False,
        )
    await event.answer()


@events.register(events.NewMessage(pattern=command("runs")))
async def runs_func(event):
    await event.reply("What am i? Rose?")
