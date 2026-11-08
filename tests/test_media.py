import unittest

from telethon.tl import types

from spr.utils.media import (LARGEST_THUMB, MAX_SCAN_FILE_SIZE, ScanSource,
                             get_media_id, get_scan_source)
from tests.fakes import make_document, make_message, make_photo

THUMB = types.PhotoSize(type="m", w=320, h=320, size=100)
STICKER = types.DocumentAttributeSticker(alt="x", stickerset=types.InputStickerSetEmpty())
VIDEO = types.DocumentAttributeVideo(duration=1, w=10, h=10)
GIF = types.DocumentAttributeAnimated()


def document_message(**kwargs):
    return make_message(media=types.MessageMediaDocument(document=make_document(**kwargs)))


class ScanSourceTest(unittest.TestCase):
    def test_photo(self):
        message = make_message(media=types.MessageMediaPhoto(photo=make_photo(11)))
        self.assertEqual(get_scan_source(message), ScanSource())
        self.assertEqual(get_media_id(message), 11)

    def test_expired_self_destructing_photo(self):
        message = make_message(media=types.MessageMediaPhoto(photo=None, ttl_seconds=10))
        self.assertIsNone(get_scan_source(message))
        self.assertIsNone(get_media_id(message))

    def test_text_and_link_preview_are_not_scanned(self):
        self.assertIsNone(get_scan_source(make_message("hello")))
        preview = make_message(media=types.MessageMediaWebPage(webpage=types.WebPageEmpty(id=1)))
        self.assertIsNone(get_scan_source(preview))
        self.assertIsNone(get_media_id(preview))

    def test_static_sticker(self):
        message = document_message(mime_type="image/webp", attributes=[STICKER])
        self.assertEqual(get_scan_source(message), ScanSource())

    def test_animated_sticker_uses_thumb(self):
        message = document_message(mime_type="application/x-tgsticker", attributes=[STICKER], thumbs=[THUMB])
        self.assertEqual(get_scan_source(message), ScanSource(thumb=LARGEST_THUMB))
        no_thumb = document_message(mime_type="application/x-tgsticker", attributes=[STICKER])
        self.assertIsNone(get_scan_source(no_thumb))

    def test_video_and_gif_use_thumb(self):
        for attributes in ([VIDEO], [VIDEO, GIF]):
            message = document_message(mime_type="video/mp4", attributes=attributes, thumbs=[THUMB])
            self.assertEqual(get_scan_source(message), ScanSource(thumb=LARGEST_THUMB))

    def test_image_documents(self):
        self.assertEqual(get_scan_source(document_message(mime_type="image/jpeg")), ScanSource())
        self.assertIsNone(get_scan_source(document_message(mime_type="application/pdf")))
        too_big = document_message(mime_type="image/png", size=MAX_SCAN_FILE_SIZE + 1)
        self.assertIsNone(get_scan_source(too_big))
        self.assertEqual(get_media_id(document_message(doc_id=99)), 99)
