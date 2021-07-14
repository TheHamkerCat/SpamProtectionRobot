import asyncio
from importlib import import_module as import_

from pyrogram import filters, idle

from spr import conn, session, spr
from spr.modules import MODULES
from spr.utils import once_a_day


async def main():
    await spr.start()
    # Load all the modules.
    [import_(module) for module in MODULES]
    print("ALIVE!")
    loop = asyncio.get_running_loop()
    loop.create_task(once_a_day())
    await idle()
    await session.close()
    conn.commit()
    conn.close()
    await spr.stop()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
