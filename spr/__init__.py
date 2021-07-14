from sqlite3 import connect

from aiohttp import ClientSession
from pyrogram import Client
from Python_ARQ import ARQ

from config import (API_HASH, API_ID, ARQ_API_KEY, ARQ_API_URL,
                    BOT_TOKEN, NSFW_LOG_CHANNEL, SPAM_LOG_CHANNEL,
                    SUDOERS)

session = ClientSession()

arq = ARQ(ARQ_API_URL, ARQ_API_KEY, session)

conn = connect("db.sqlite3")

spr = Client(
    "spr", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH
)
