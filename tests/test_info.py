from spr.modules.info import NOT_FOUND_TEXT, get_info, info_func
from tests.fakes import (SUPERGROUP_ID, USER_ID, DatabaseTestCase, FakeClient,
                         FakeMessageEvent, make_channel, make_user)


class InfoTest(DatabaseTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.client = FakeClient(db=self.db)
        self.client.entities[USER_ID] = make_user(first_name="Bob <script>")
        self.client.entities["somegroup"] = make_channel(SUPERGROUP_ID, title="G", username="somegroup", megagroup=True)

    async def count(self, table):
        return await self.db._pool.fetchval(f"SELECT count(*) FROM {table}")

    async def test_user_info_is_read_only_and_escaped(self):
        info = await get_info(self.client, str(USER_ID))
        self.assertIn(f"<b>ID:</b> {USER_ID}", info)
        self.assertIn(f'<a href="tg://user?id={USER_ID}">Link</a>', info)
        self.assertIn("<b>Trust:</b> 100", info)
        self.assertEqual(await self.count("users"), 0)

    async def test_chat_info(self):
        await self.db.blacklist("chat", SUPERGROUP_ID, "<i>reason</i>")
        info = await get_info(self.client, "somegroup")
        self.assertIn("<b>Type:</b> supergroup", info)
        self.assertIn("<b>Members:</b> 42", info)
        self.assertIn("&lt;i&gt;reason&lt;/i&gt;", info)

    async def test_unknown(self):
        self.assertIsNone(await get_info(self.client, "nobody"))
        self.assertIsNone(await get_info(self.client, "  "))
        event = FakeMessageEvent(self.client, make_user(), "/info nobody")
        await info_func(event)
        self.assertEqual(event.reply_texts(), [NOT_FOUND_TEXT])

    async def test_reply_to_deleted_message(self):
        event = FakeMessageEvent(self.client, make_user(), "/info", has_reply=True)
        await info_func(event)
        self.assertEqual(event.reply_texts(), ["I can't find the message you replied to."])
