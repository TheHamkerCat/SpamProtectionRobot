"""/migrate_sqlite: a sudo user replies to the old bot's db.sqlite3 file
to import its data (spr/sqlite_import.py)"""
import asyncio
import logging
import tempfile
from pathlib import Path
from time import monotonic

from telethon import events

from spr.core.commands import command
from spr.sqlite_import import MAX_FILE_SIZE, OldDatabaseError, import_old_database

logger = logging.getLogger(__name__)

# Edit the status message at most this often while importing
PROGRESS_EDIT_SECONDS = 10

# One import at a time
_import_lock = asyncio.Lock()


def is_sudo(event) -> bool:
    return event.sender_id in event.client.settings.sudoers


def summary_text(summary) -> str:
    if summary.finished_before is not None:
        return (
            f"This file was already imported on {summary.finished_before:%Y-%m-%d %H:%M} UTC. "
            "Nothing was changed."
        )
    lines = []
    if summary.resumed:
        lines.append("Continued an import that had stopped earlier.")
    lines += [
        "<b>Import finished.</b>",
        f"Users: {summary.users:,} (spam history, NSFW count and reputation "
        "added to what the bot already had)",
        f"Chats with their own settings or a blacklist: {summary.chats:,}",
        f"Blacklisted users and chats: {summary.blacklisted:,}",
        "",
        "Not imported: votes on old reports (already counted in reputation) "
        "and ignored media (old file IDs don't match the new ones).",
    ]
    return "\n".join(lines)


@events.register(events.NewMessage(pattern=command("migrate_sqlite"), func=is_sudo))
async def migrate_sqlite_command(event):
    reply = await event.get_reply_message()
    document = reply.document if reply is not None else None
    if document is None:
        return await event.reply(
            "Reply to the old bot's db.sqlite3 file with /migrate_sqlite to import its data."
        )
    if document.size > MAX_FILE_SIZE:
        return await event.reply(
            "This file is bigger than 1 GB, which is too big for the old database. "
            "Check that you replied to the right file."
        )
    if _import_lock.locked():
        return await event.reply("An import is already running. Wait until it finishes.")

    async with _import_lock:
        status = await event.reply("Downloading the file...")
        last_edit = monotonic()

        async def show_progress(done: int, total: int):
            nonlocal last_edit
            if monotonic() - last_edit < PROGRESS_EDIT_SECONDS:
                return
            last_edit = monotonic()
            await status.edit(f"Importing: {done:,} of {total:,} old users checked...")

        try:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "old.sqlite3"
                await event.client.download_media(reply, file=str(path))
                await status.edit("Importing...")
                summary = await import_old_database(event.client.db, path, on_progress=show_progress)
        except OldDatabaseError as error:
            return await status.edit(str(error))
        except Exception:
            logger.exception("Importing an old SQLite database failed")
            return await status.edit(
                "The import stopped because of an error (details are in the bot's log). "
                "Send /migrate_sqlite again on the same file: it continues where it stopped."
            )
        await status.edit(summary_text(summary))
