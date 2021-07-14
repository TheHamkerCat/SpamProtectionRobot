from pyrogram import filters
from pyrogram.types import Message

from spr import spr


@spr.on_message(
    (
        filters.document
        | filters.photo
        | filters.sticker
        | filters.animation
        | filters.video
        | filters.text
    )
    & ~filters.private
)
async def message_watcher(_, message: Message):
    pass
