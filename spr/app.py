import asyncio
import logging
import signal
from pathlib import Path

from telethon import events
from telethon.errors import FloodWaitError

from spr.arq import ArqClient
from spr.backup import backup_loop
from spr.client import SprClient
from spr.core.commands import set_bot_username
from spr.db import Database
from spr.modules import discover_modules
from spr.modules.watcher import check_message
from spr.scan_cache import cache_maintenance_loop
from spr.workqueue import WorkQueue

logger = logging.getLogger(__name__)

# Messages checked at the same time; each holds a database connection and
# at most one ARQ request (see the pool size in spr/db.py)
SCAN_WORKERS = 16
# Messages waiting to be checked; newer ones are dropped beyond this
SCAN_QUEUE_SIZE = 5000
# On shutdown, waiting reports get this long to be sent
SHUTDOWN_FLUSH_SECONDS = 15

# Telethon sessions (login key + cache of users/chats the bot has seen).
# Docker keeps this folder in a volume, so restarts keep the cache.
SESSION_DIR = Path("data")


def session_path(bot_token: str, name: str = "spr") -> Path:
    """One session file per bot. A session that is already logged in
    ignores a different BOT_TOKEN, so a new token needs a new file."""
    bot_id = bot_token.split(":", 1)[0]
    return SESSION_DIR / f"{name}-{bot_id}"


def register_modules(client) -> dict:
    """Add every handler of every module to the client.
    Returns the modules that have a help page, {lowercase name: module}."""
    help_modules = {}
    for module in discover_modules():
        for value in vars(module).values():
            # Skip handlers imported from other modules, they register there
            is_own = getattr(value, "__module__", None) == module.__name__
            if is_own and events.is_handler(value):
                client.add_event_handler(value)
        name = getattr(module, "__MODULE__", None)
        if name and getattr(module, "__HELP__", None):
            help_modules[name.lower()] = module
    return help_modules


async def log_in(client, bot_token: str):
    """Log in, waiting it out when Telegram limits logins.

    Telegram answers too many logins with the same token (new sessions)
    with a wait time. Crashing would make Docker restart the bot and try
    again, which can make the wait longer, so the bot waits instead."""
    while True:
        try:
            await client.start(bot_token=bot_token)
            return
        except FloodWaitError as error:
            logger.warning(
                "Telegram allows the next login in %s seconds (too many logins "
                "with this BOT_TOKEN). The bot waits and then starts by itself.",
                error.seconds,
            )
            await asyncio.sleep(error.seconds)


async def shut_down(client):
    """Send the reports that are still waiting, then disconnect"""
    logger.info("Stopping")
    try:
        await asyncio.wait_for(client.reports.flush(), SHUTDOWN_FLUSH_SECONDS)
    except Exception:
        logger.exception("Could not send the last reports")
    await client.disconnect()


def shut_down_on_signals(client):
    """Ctrl+C and `docker stop` stop cleanly instead of killing the bot"""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.ensure_future(shut_down(client)))


async def run(settings):
    SESSION_DIR.mkdir(exist_ok=True)
    db = await Database.connect(settings.database_url)
    await db.migrate()
    arq = ArqClient(settings.arq_api_url, settings.arq_api_key)
    client = SprClient(str(session_path(settings.bot_token)), settings, db, arq)
    client.message_queue = WorkQueue(
        check_message, workers=SCAN_WORKERS, max_size=SCAN_QUEUE_SIZE, name="message"
    )
    background_tasks = []
    try:
        await log_in(client, settings.bot_token)
        me = await client.get_me()
        set_bot_username(me.username)
        client.help_modules = register_modules(client)
        client.admins.attach()
        client.message_queue.start()
        background_tasks = [
            asyncio.create_task(client.reports.run()),
            asyncio.create_task(backup_loop(client)),
            asyncio.create_task(cache_maintenance_loop(client)),
        ]
        shut_down_on_signals(client)
        logger.info("Started as @%s", me.username)
        await client.run_until_disconnected()
    finally:
        for task in background_tasks:
            task.cancel()
        await client.message_queue.stop()
        await client.disconnect()
        await arq.close()
        await db.close()
        logger.info("Stopped")
