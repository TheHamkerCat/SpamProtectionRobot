import unittest

from telethon.tl import types

from spr.core import ikb
from spr.core.commands import command
from spr.core.entities import PRIVATE_CHAT_LINK, chat_link, get_username, mention
from spr.core.formatting import esc
from spr.core.text import plain_letters
from tests.fakes import BOT_USERNAME, make_channel, make_user, setup_bot_username


class CommandTest(unittest.TestCase):
    def setUp(self):
        setup_bot_username()

    def test_matches(self):
        match = command("info")
        for text in ("/info", "/INFO", "/info 123", f"/info@{BOT_USERNAME} x", f"/info@{BOT_USERNAME.lower()}", "/info\nline"):
            self.assertTrue(match(text), text)

    def test_does_not_match(self):
        match = command("info")
        for text in ("/information", "/info@OtherBot", "info", " /info", "x /info"):
            self.assertFalse(match(text), text)

    def test_several_names(self):
        match = command("help", "start")
        self.assertTrue(match("/start help"))
        self.assertTrue(match("/help"))


class KeyboardTest(unittest.TestCase):
    def test_rows_and_button_types(self):
        rows = ikb({"A": "upvote_spam", "B": "downvote_spam", "Chat": "https://t.me/x"})
        self.assertEqual([len(row) for row in rows], [2, 1])
        self.assertIsInstance(rows[0][0].type, types.InlineButtonTypeCallback)
        self.assertEqual(rows[0][0].type.data, b"upvote_spam")
        self.assertEqual(rows[1][0].type.url, "https://t.me/x")

    def test_row_width(self):
        rows = ikb({"A": "a", "B": "b", "C": "c"}, row_width=3)
        self.assertEqual([len(row) for row in rows], [3])


class HtmlTest(unittest.TestCase):
    def test_escape(self):
        self.assertEqual(esc("<b>&"), "&lt;b&gt;&amp;")

    def test_user_mention_escapes_name(self):
        user = make_user(5, first_name='<a href="https://evil">x</a>')
        text = mention(user)
        self.assertTrue(text.startswith('<a href="tg://user?id=5">'))
        self.assertNotIn('href="https://evil"', text)

    def test_channel_mention(self):
        public = make_channel(-1005, title="News & <Stuff>", username="news")
        self.assertEqual(mention(public), '<a href="https://t.me/news">News &amp; &lt;Stuff&gt;</a>')
        private = make_channel(-1006, title="Private")
        self.assertEqual(mention(private), "Private")

    def test_deleted_account_uses_id(self):
        self.assertEqual(mention(types.User(id=9, deleted=True)), '<a href="tg://user?id=9">9</a>')

    def test_collectible_username(self):
        channel = make_channel(-1009, title="Durov")
        channel.usernames = [
            types.Username(username="old", active=False),
            types.Username(username="durov", active=True),
        ]
        self.assertEqual(get_username(channel), "durov")
        self.assertEqual(chat_link(channel), "https://t.me/durov")

    def test_chat_link(self):
        self.assertEqual(chat_link(make_channel(-1007, username="grp")), "https://t.me/grp")
        self.assertEqual(chat_link(make_channel(-1008)), PRIVATE_CHAT_LINK)


class PlainLettersTest(unittest.TestCase):
    def test_look_alike_letters(self):
        self.assertEqual(plain_letters("𝙁𝙧𝙚𝙚 𝙘𝙧𝙮𝙥𝙩𝙤"), "Free crypto")
        self.assertEqual(plain_letters("ｔ．ｍｅ/ｊｏｉｎ"), "t.me/join")

    def test_other_scripts_stay_the_same(self):
        for text in ("नमस्ते दोस्तों", "Привет всем", "你好", "hello 👋"):
            self.assertEqual(plain_letters(text), text)
