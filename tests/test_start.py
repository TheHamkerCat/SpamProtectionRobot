import types as pytypes
import unittest

from spr.modules.start import (START_IMAGE, help_button, help_command,
                               paginate_modules)
from tests.fakes import (FakeCallbackEvent, FakeClient, FakeMessageEvent,
                         USER_ID, make_user, setup_bot_username)


def fake_module(name):
    return pytypes.SimpleNamespace(__MODULE__=name, __HELP__=f"help of {name}")


class StartTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        setup_bot_username()
        self.client = FakeClient()
        self.client.help_modules = {"info": fake_module("Info")}

    def test_start_image_is_a_jpeg(self):
        self.assertEqual(START_IMAGE.read_bytes()[:3], b"\xff\xd8\xff")

    async def test_start_in_private_sends_the_image(self):
        event = FakeMessageEvent(self.client, make_user(), "/start", is_private=True)
        await help_command(event)
        _, kwargs = event.replies[0]
        self.assertEqual(kwargs["file"], str(START_IMAGE))

    async def test_start_in_group_points_to_private_chat(self):
        event = FakeMessageEvent(self.client, make_user(), "/help")
        await help_command(event)
        self.assertEqual(event.reply_texts(), ["Pm Me For Help"])

    async def test_help_module_page(self):
        event = FakeCallbackEvent(self.client, USER_ID, b"help_module(info)")
        await help_button(event)
        self.assertIn("help of Info", event.edits[0][0])

    async def test_crafted_help_data(self):
        event = FakeCallbackEvent(self.client, USER_ID, b"help_module(nothere)")
        await help_button(event)
        self.assertEqual(event.edits, [])
        self.assertIn("doesn't exist", event.answers[0])
        for data in (b"help_prev(x)", b"help_\xff", b"help_"):
            event = FakeCallbackEvent(self.client, USER_ID, data)
            await help_button(event)
            self.assertEqual(event.edits, [])

    def test_pagination(self):
        modules = {f"m{i}": fake_module(f"M{i:02}") for i in range(30)}
        first_page = paginate_modules(0, modules)
        self.assertEqual(len(first_page), 8)  # 7 rows + navigation
        self.assertEqual(first_page[0][0].text, "M00")
        self.assertEqual(paginate_modules(1, modules)[0][0].text, "M21")
        # Pages wrap around
        self.assertEqual(paginate_modules(-1, modules)[0][0].text, "M21")
