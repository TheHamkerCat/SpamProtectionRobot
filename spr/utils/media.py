"""Which media the bot scans for NSFW, and how it gets the image."""
from dataclasses import dataclass
from typing import Optional

from telethon.tl import types

# Image documents bigger than this are not scanned
MAX_SCAN_FILE_SIZE = 3 * 1024 * 1024
SCANNABLE_DOCUMENT_MIME_TYPES = {"image/png", "image/jpeg"}
STATIC_STICKER_MIME_TYPE = "image/webp"
LARGEST_THUMB = -1


@dataclass(frozen=True)
class ScanSource:
    """thumb is the download_media argument: None downloads the file
    itself, LARGEST_THUMB its largest thumbnail"""

    thumb: Optional[int] = None


def get_media(message):
    """The Photo or Document sent in the message. None for link previews,
    polls, locations, and expired self-destructing media."""
    media = message.media
    if isinstance(media, types.MessageMediaPhoto) and isinstance(media.photo, types.Photo):
        return media.photo
    if isinstance(media, types.MessageMediaDocument) and isinstance(
        media.document, types.Document
    ):
        return media.document
    return None


def get_media_id(message) -> Optional[int]:
    """ID of the photo/file in a message. It stays the same when the
    message is forwarded or copied to another chat."""
    media = get_media(message)
    if media is None:
        return None
    return media.id


def get_scan_source(message) -> Optional[ScanSource]:
    """Which image of a message to scan, None if nothing can be scanned"""
    media = get_media(message)
    if media is None:
        return None
    if isinstance(media, types.Photo):
        return ScanSource()

    if message.sticker:
        # Static stickers are images, animated/video ones only have thumbs
        if media.mime_type == STATIC_STICKER_MIME_TYPE:
            return ScanSource()
        return _thumb_source(media)

    if message.gif or message.video:
        return _thumb_source(media)

    if media.size > MAX_SCAN_FILE_SIZE:
        return None
    if media.mime_type not in SCANNABLE_DOCUMENT_MIME_TYPES:
        return None
    return ScanSource()


def _thumb_source(document) -> Optional[ScanSource]:
    if not document.thumbs:
        return None
    return ScanSource(thumb=LARGEST_THUMB)


async def download_for_scan(client, message) -> Optional[bytes]:
    """The image to scan, in memory. None if nothing can be scanned."""
    source = get_scan_source(message)
    if source is None:
        return None
    return await client.download_media(message, file=bytes, thumb=source.thumb)
