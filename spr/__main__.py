"""Entry point: python -m spr"""
import logging
import sys

import uvloop
from dotenv import load_dotenv

from spr.app import run
from spr.logs import setup_logging
from spr.settings import SettingsError, load_settings

logger = logging.getLogger("spr")


def main():
    setup_logging()
    # Values already in the environment (Docker, Heroku) win over .env
    load_dotenv(".env")
    try:
        settings = load_settings()
    except SettingsError as error:
        logger.error("%s", error)
        sys.exit(1)
    # uvloop: a faster event loop, same asyncio API
    uvloop.run(run(settings))


if __name__ == "__main__":
    main()
