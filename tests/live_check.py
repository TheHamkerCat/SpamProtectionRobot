"""Live check against real Telegram and ARQ servers.

Run with: docker compose -f compose.test.yml --env-file .env run --rm --build live

Logs in with BOT_TOKEN, runs read-only calls through the bot's own code
and prints one line per check. Exit code 1 if any check fails. Sends no
messages: a bot can't start chats, so commands still need a manual test
in Telegram.
"""
import asyncio
import sys
import traceback

from dotenv import load_dotenv

from spr.app import SESSION_DIR, log_in, register_modules, session_path
from spr.arq import ArqClient, ArqError
from spr.client import SprClient
from spr.core.commands import command, set_bot_username
from spr.db import Database
from spr.modules.info import get_info
from spr.modules.start import START_IMAGE
from spr.settings import load_settings

SPAM_TEXT = "Earn $5000 daily from home!!! DM me now for crypto investment, limited slots"
HAM_TEXT = "Hey, are we still meeting at 6 for the project review?"

results = []


async def check(name, coroutine):
    try:
        detail = await coroutine
        results.append((True, name, detail))
    except Exception as error:
        traceback.print_exc()
        results.append((False, name, repr(error)))


def expect(condition, detail):
    if not condition:
        raise AssertionError(detail)
    return detail


async def main():
    load_dotenv(".env")
    settings = load_settings()
    db = await Database.connect(settings.database_url)
    await db.migrate()
    arq = ArqClient(settings.arq_api_url, settings.arq_api_key)
    # Kept in a volume (compose.test.yml): every new session is a new
    # login, and Telegram limits how many logins a token may do
    SESSION_DIR.mkdir(exist_ok=True)
    session = session_path(settings.bot_token, name="live-check")
    client = SprClient(str(session), settings, db, arq)
    try:
        await log_in(client, settings.bot_token)
        me = await client.get_me()
        set_bot_username(me.username)
        results.append((me.bot, "log in with BOT_TOKEN", f"@{me.username} (id {me.id}, bot={me.bot})"))
        await run_checks(client, me)
    finally:
        await client.disconnect()
        await arq.close()
        await db.close()


async def run_checks(client, me):
    async def wiring():
        help_modules = register_modules(client)
        client.admins.attach()
        handler_count = len(client.list_event_handlers())
        return expect(
            handler_count == 24 and sorted(help_modules) == ["devs", "info", "manage"],
            f"{handler_count} handlers, help pages: {sorted(help_modules)}",
        )

    async def command_matching():
        match = command("info")
        return expect(
            match(f"/info@{me.username} 1") and not match("/info@SomeOtherBot"),
            f"/info@{me.username} matches, /info@SomeOtherBot does not",
        )

    async def info_public_channel():
        info = await get_info(client, "telegram")
        return expect(info and "<b>Type:</b> channel" in info, info.strip().replace("\n", " | "))

    async def info_user():
        info = await get_info(client, "BotFather")
        return expect(
            info and "<b>ID:</b> 93372553" in info and "<b>Username:</b> BotFather" in info,
            info.strip().replace("\n", " | "),
        )

    async def info_collectible_username():
        # @durov is a channel whose username is a collectible one
        info = await get_info(client, "durov")
        return expect(info and "<b>Username:</b> durov" in info, info.strip().replace("\n", " | "))

    async def info_unknown():
        info = await get_info(client, "this_username_does_not_exist_spr_check")
        return expect(info is None, "unknown username -> not found")

    async def arq_spam():
        spam = await client.arq.nlp(SPAM_TEXT)
        ham = await client.arq.nlp(HAM_TEXT)
        return expect(
            spam.is_spam and not ham.is_spam,
            f"spam text: {spam.spam_probability} %, normal text: {ham.spam_probability} %",
        )

    async def arq_nsfw():
        result = await client.arq.nsfw_scan(START_IMAGE.read_bytes())
        return expect(not result.is_nsfw, f"start image: neutral {result.neutral} %, nsfw={result.is_nsfw}")

    async def arq_bad_key():
        bad = ArqClient(client.settings.arq_api_url, "invalid-key")
        try:
            await bad.nlp("x")
        except ArqError as error:
            return str(error)
        finally:
            await bad.close()
        raise AssertionError("an invalid key was accepted")

    async def upload_start_image():
        uploaded = await client.upload_file(str(START_IMAGE))
        return expect(uploaded.parts >= 1, f"{START_IMAGE.name} uploaded in {uploaded.parts} part(s)")

    async def database_round_trip():
        await client.db.add_spam_prediction(me.id, 12.5)
        stats = await client.db.get_user_stats(me.id)
        return expect(stats.trust == 87.5, f"trust {stats.trust}")

    await check("register handlers", wiring())
    await check("command matching with the real username", command_matching())
    await check("/info of a public channel (@telegram)", info_public_channel())
    await check("/info of a user (@BotFather)", info_user())
    await check("/info of a collectible username (@durov)", info_collectible_username())
    await check("/info of an unknown username", info_unknown())
    await check("ARQ spam scan", arq_spam())
    await check("ARQ NSFW scan", arq_nsfw())
    await check("ARQ invalid key error", arq_bad_key())
    await check("upload the /start image", upload_start_image())
    await check("Postgres", database_round_trip())


if __name__ == "__main__":
    asyncio.run(main())
    for ok, name, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    sys.exit(0 if results and all(ok for ok, _, _ in results) else 1)
