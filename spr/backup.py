"""Daily database backup are sent to every sudo user as a zip of CSV files."""
import asyncio
import io
import zipfile
from datetime import datetime, timedelta, timezone

from telethon.errors import RPCError

SECONDS_PER_DAY = 86400


async def build_backup(db) -> io.BytesIO:
    """All tables as CSV files in one in-memory zip"""
    tables = await db.export_csv()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for table, data in tables.items():
            archive.writestr(f"{table}.csv", data)
    buffer.name = f"spr-backup-{datetime.now(timezone.utc):%Y-%m-%d}.zip"
    buffer.seek(0)
    return buffer


async def send_backup(client):
    backup = await build_backup(client.db)
    for user_id in client.settings.sudoers:
        backup.seek(0)
        try:
            await client.send_file(user_id, backup, force_document=True)
        except (RPCError, ValueError) as error:
            # ValueError: the user never started the bot, so it can't message them
            print(f"Can't send the backup to {user_id}: {error}")


def seconds_until_midnight(now: datetime) -> float:
    next_midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (next_midnight - now).total_seconds()


async def backup_loop(client):
    """One backup at startup, then one every day at 00:00 UTC"""
    while True:
        print("BACKING UP DB...")
        try:
            await send_backup(client)
        except Exception as error:
            print(f"Backup failed: {error!r}")
        await asyncio.sleep(seconds_until_midnight(datetime.now(timezone.utc)))
