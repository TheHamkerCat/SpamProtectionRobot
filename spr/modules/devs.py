"""/eval and /sh for sudo users"""
import asyncio
import io
import shlex
import sys
import traceback
from io import StringIO
from time import monotonic

from telethon import Button, events

from spr.core.commands import command, get_args
from spr.core.formatting import esc, pre

__MODULE__ = "Devs"
__HELP__ = """
<b>THIS MODULE IS ONLY FOR DEVS</b>

/eval - Execute python code.
/sh - Execute shell code.

/blacklist [CHAT_ID|USER_ID] - Blacklist a chat/user.
/whitelist [CHAT_ID|USER_ID] - Whitelist a chat/user.
"""

# Telegram's message length limit, longer output is sent as a file
MAX_MESSAGE_LENGTH = 4096
SHELL_TIMEOUT_SECONDS = 60
RUNTIME_DATA_PREFIX = "runtime "


def is_trusted_sudo(event) -> bool:
    """Sudo user, typed by hand (not forwarded, not sent via a bot)"""
    if event.sender_id not in event.client.settings.sudoers:
        return False
    return not event.fwd_from and not event.via_bot_id


def text_file(text: str, name: str = "output.txt") -> io.BytesIO:
    """In-memory file, so concurrent commands never share a file on disk"""
    file = io.BytesIO(text.encode("utf-8"))
    file.name = name
    return file


def runtime_keyboard(seconds: float) -> list:
    return [[Button.inline("⏳", data=f"{RUNTIME_DATA_PREFIX}{seconds:.3f} Seconds")]]


async def aexec(code: str, client, message):
    """Run code as the body of an async function. `client` and `message`
    are available to it."""
    exec(
        "async def __aexec(client, message): "
        + "".join(f"\n {line}" for line in code.split("\n"))
    )
    return await locals()["__aexec"](client, message)


async def edit_or_reply(event, text: str, buttons=None):
    """Edit own messages, reply to others"""
    if event.out:
        return await event.edit(text, buttons=buttons)
    return await event.reply(text, buttons=buttons)


@events.register(events.NewMessage(pattern=command("eval"), func=is_trusted_sudo))
async def eval_command(event):
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await event.delete()
    code = parts[1]
    started = monotonic()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    redirected_output = sys.stdout = StringIO()
    redirected_error = sys.stderr = StringIO()
    exc = None
    try:
        await aexec(code, event.client, event)
    except Exception:
        exc = traceback.format_exc()
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
    output = exc or redirected_error.getvalue() or redirected_output.getvalue() or "Success"
    keyboard = runtime_keyboard(monotonic() - started)

    text = f"<b>OUTPUT</b>:\n{pre(output.strip())}"
    if len(text) <= MAX_MESSAGE_LENGTH:
        return await edit_or_reply(event, text, buttons=keyboard)
    await event.respond(
        f"<b>INPUT:</b>\n<code>{esc(code[:980])}</code>\n\n"
        "<b>OUTPUT:</b>\n<code>Attached Document</code>",
        file=text_file(output.strip()),
        buttons=keyboard,
    )
    await event.delete()


@events.register(events.CallbackQuery(pattern=RUNTIME_DATA_PREFIX.encode()))
async def runtime_callback(event):
    runtime = event.data.decode(errors="replace")[len(RUNTIME_DATA_PREFIX):]
    await event.answer(runtime[:200], alert=True)


async def run_shell_line(line: str) -> str:
    """Run one command line (no shell), return its output and errors"""
    try:
        argv = shlex.split(line)
    except ValueError as error:
        return f"Can't parse this line: {error}"
    if not argv:
        return ""
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as error:
        return f"Can't run {argv[0]}: {error}"
    try:
        output, _ = await asyncio.wait_for(
            process.communicate(), SHELL_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return f"Stopped after {SHELL_TIMEOUT_SECONDS} seconds."
    return output.decode("utf-8", errors="replace").rstrip("\n")


@events.register(events.NewMessage(pattern=command("sh"), func=is_trusted_sudo))
async def shell_command(event):
    if len(get_args(event)) < 2:
        return await edit_or_reply(event, "<b>Usage:</b>\n/sh git pull")
    lines = [line for line in event.raw_text.split(None, 1)[1].split("\n") if line.strip()]
    if len(lines) == 1:
        output = await run_shell_line(lines[0])
    else:
        results = [f"$ {line}\n{await run_shell_line(line)}" for line in lines]
        output = "\n".join(results)

    if not output.strip():
        return await edit_or_reply(event, "<b>OUTPUT: </b>\n<code>No output</code>")
    text = f"<b>OUTPUT:</b>\n{pre(output)}"
    if len(text) <= MAX_MESSAGE_LENGTH:
        return await edit_or_reply(event, text)
    await event.respond("<code>Output</code>", file=text_file(output))
