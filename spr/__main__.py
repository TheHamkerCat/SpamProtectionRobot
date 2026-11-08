import asyncio
import sys

from dotenv import load_dotenv

from spr.app import run
from spr.settings import SettingsError, load_settings


def main():
    load_dotenv(".env")
    try:
        settings = load_settings()
    except SettingsError as error:
        print(error, file=sys.stderr)
        sys.exit(1)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
