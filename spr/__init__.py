from sqlite3 import connect

from aiohttp import ClientSession
from pyrogram import Client
from Python_ARQ import ARQ

from config import (API_HASH, API_ID, ARQ_API_KEY, ARQ_API_URL,
                    BOT_TOKEN, NSFW_LOG_CHANNEL, SPAM_LOG_CHANNEL,
                    SUDOERS, DB_NAME,
                    SESSION_NAME)

session = ClientSession()

arq = ARQ(ARQ_API_URL, ARQ_API_KEY, session)

conn = connect(DB_NAME)

spr = Client(
    SESSION_NAME,
    bot_token=BOT_TOKEN,
    api_id=API_ID,
    api_hash=API_HASH,
)
