import asyncio
import signal
from pathlib import Path

from telethon import events

from spr.arq import ArqClient
from spr.backup import backup_loop
from spr.client import SprClient
from spr.core.commands import set_bot_username
from spr.db import Database
from spr.modules import discover_modules

# Telethon session (login key + cache of users/chats the bot has seen).
# Docker keeps this folder in a volume, so restarts keep the cache.
SESSION_PATH = Path("data") / "spr"


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


def disconnect_on_signals(client):
    """Ctrl+C and `docker stop` disconnect cleanly instead of killing"""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.ensure_future(client.disconnect()))


async def run(settings):
    SESSION_PATH.parent.mkdir(exist_ok=True)
    db = await Database.connect(settings.database_url)
    arq = ArqClient(settings.arq_api_url, settings.arq_api_key)
    client = SprClient(str(SESSION_PATH), settings, db, arq)
    backup_task = None
    try:
        await client.start(bot_token=settings.bot_token)
        me = await client.get_me()
        set_bot_username(me.username)
        client.help_modules = register_modules(client)
        client.admins.attach()
        disconnect_on_signals(client)
        backup_task = asyncio.create_task(backup_loop(client))
        print(f"STARTED ! @{me.username}")
        await client.run_until_disconnected()
    finally:
        if backup_task:
            backup_task.cancel()
        await client.disconnect()
        await arq.close()
        await db.close()
        print("STOPPED")
