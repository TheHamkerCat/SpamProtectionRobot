from datetime import datetime

from telethon.errors import ChatAdminRequiredError
from telethon.tl import types

from spr.arq import ArqError
from spr.modules.manage import (OFD_NOT_SET_UP, OFD_PUBLIC_ONLY,
                                alerts_toggle_func, nsfw_scan_command,
                                ofd_privacy_toggle_func, ofd_toggle_func,
                                settings_command, spam_scan_command,
                                spam_toggle_func)
from tests.fakes import (ADMIN_ID, SUDO_ID, SUPERGROUP_ID, DatabaseTestCase,
                         FakeArq, FakeClient, FakeMessageEvent, FakeOfd,
                         make_channel, make_message, make_user, spam_result)

NOW = datetime(2026, 1, 1)


class ToggleTest(DatabaseTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.client = FakeClient(db=self.db)
        self.client.participants[SUPERGROUP_ID] = [
            (ADMIN_ID, types.ChannelParticipantAdmin(ADMIN_ID, 1, NOW, types.ChatAdminRights()))
        ]

    async def toggle(self, sender, text="/anti_spam disable"):
        event = FakeMessageEvent(self.client, sender, text)
        await spam_toggle_func(event)
        return event.reply_texts()

    async def spam_enabled(self):
        return (await self.db.get_chat_settings(SUPERGROUP_ID)).spam_enabled

    async def test_regular_user_is_denied(self):
        self.assertEqual(await self.toggle(make_user()), ["You don't have enough permissions"])
        self.assertTrue(await self.spam_enabled())

    async def test_user_posting_as_own_channel_is_denied(self):
        replies = await self.toggle(make_channel(-1007777777777))
        self.assertEqual(replies, ["You don't have enough permissions"])
        self.assertTrue(await self.spam_enabled())

    async def test_admin_anonymous_admin_and_sudo_are_allowed(self):
        self.assertEqual(await self.toggle(make_user(ADMIN_ID)), ["Disabled Spam Detection."])
        self.assertEqual(await self.toggle(make_user(SUDO_ID)), ["Already disabled."])
        group_itself = make_channel(SUPERGROUP_ID, megagroup=True)
        self.assertEqual(await self.toggle(group_itself, "/anti_spam ENABLE"), ["Enabled Spam Detection."])
        self.assertTrue(await self.spam_enabled())

    async def test_usage_and_unknown_suffix(self):
        self.assertIn("Usage", (await self.toggle(make_user(ADMIN_ID), "/anti_spam"))[0])
        self.assertIn("Unknown Suffix", (await self.toggle(make_user(ADMIN_ID), "/anti_spam maybe"))[0])

    async def test_unreadable_admin_list_denies(self):
        self.client.participants_error = ChatAdminRequiredError(request=None)
        replies = await self.toggle(make_user())
        self.assertIn("can't read the admin list", replies[0])
        self.assertTrue(await self.spam_enabled())


class AlertsSettingTest(DatabaseTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.client = FakeClient(db=self.db)
        self.client.participants[SUPERGROUP_ID] = [
            (ADMIN_ID, types.ChannelParticipantAdmin(ADMIN_ID, 1, NOW, types.ChatAdminRights()))
        ]

    async def run_command(self, handler, sender, text):
        event = FakeMessageEvent(self.client, sender, text)
        await handler(event)
        return event.reply_texts()

    async def test_admin_turns_alerts_off_and_on(self):
        replies = await self.run_command(alerts_toggle_func, make_user(ADMIN_ID), "/alerts disable")
        self.assertTrue(replies[0].startswith("Alerts are off."))
        self.assertFalse((await self.db.get_chat_settings(SUPERGROUP_ID)).alerts_enabled)
        replies = await self.run_command(alerts_toggle_func, make_user(ADMIN_ID), "/alerts disable")
        self.assertEqual(replies, ["Already disabled."])
        replies = await self.run_command(alerts_toggle_func, make_user(ADMIN_ID), "/alerts enable")
        self.assertTrue(replies[0].startswith("Alerts are on."))

    async def test_regular_user_can_not_change_alerts(self):
        replies = await self.run_command(alerts_toggle_func, make_user(), "/alerts disable")
        self.assertEqual(replies, ["You don't have enough permissions"])
        self.assertTrue((await self.db.get_chat_settings(SUPERGROUP_ID)).alerts_enabled)

    async def test_usage(self):
        replies = await self.run_command(alerts_toggle_func, make_user(ADMIN_ID), "/alerts")
        self.assertEqual(replies, ["Usage: /alerts [ENABLE|DISABLE]"])

    async def test_settings_for_anyone(self):
        await self.db.set_chat_feature(SUPERGROUP_ID, "alerts", False)
        replies = await self.run_command(settings_command, make_user(), "/settings")
        self.assertEqual(
            replies[0],
            "<b>Settings of this chat</b>\n"
            "Spam detection: <b>on</b> (/anti_spam)\n"
            "NSFW detection: <b>on</b> (/anti_nsfw)\n"
            "Alerts: <b>off</b> (/alerts)",
        )


class FederationSettingsTest(DatabaseTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.client = FakeClient(db=self.db, ofd=FakeOfd())
        self.client.participants[SUPERGROUP_ID] = [
            (ADMIN_ID, types.ChannelParticipantAdmin(ADMIN_ID, 1, NOW, types.ChatAdminRights()))
        ]

    async def run_command(self, handler, sender, text, chat=None):
        if chat is None:
            chat = make_channel(SUPERGROUP_ID, title="Group", username="publicgroup", megagroup=True)
        event = FakeMessageEvent(self.client, sender, text, chat=chat)
        await handler(event)
        return event.reply_texts()

    async def settings(self):
        return await self.db.get_chat_settings(SUPERGROUP_ID)

    async def test_defaults(self):
        settings = await self.settings()
        self.assertTrue(settings.ofd_enabled)
        self.assertTrue(settings.ofd_privacy)

    async def test_admin_turns_federation_off_and_on(self):
        admin = make_user(ADMIN_ID)
        replies = await self.run_command(ofd_toggle_func, admin, "/ofd disable")
        self.assertIn("I don't send anything", replies[0])
        self.assertFalse((await self.settings()).ofd_enabled)
        replies = await self.run_command(ofd_toggle_func, admin, "/ofd DISABLE")
        self.assertEqual(replies, ["Already disabled."])
        replies = await self.run_command(ofd_toggle_func, admin, "/ofd enable")
        self.assertTrue(replies[0].startswith("Enabled Federation (OFD) checks."))
        self.assertIn("Only the text of messages", replies[0])
        self.assertTrue((await self.settings()).ofd_enabled)

    async def test_enable_reply_tells_what_is_shared(self):
        admin = make_user(ADMIN_ID)
        await self.db.set_chat_feature(SUPERGROUP_ID, "ofd", False)
        await self.db.set_chat_feature(SUPERGROUP_ID, "ofd_privacy", False)
        self.client.ofd.can_contribute = True
        replies = await self.run_command(ofd_toggle_func, admin, "/ofd enable")
        self.assertIn("publish the senders' public profiles", replies[0])

        await self.db.set_chat_feature(SUPERGROUP_ID, "ofd", False)
        self.client.ofd = FakeOfd(collects_public_scan_content=True)
        replies = await self.run_command(ofd_toggle_func, admin, "/ofd enable")
        self.assertIn("The server may keep what I send.", replies[0])

    async def test_privacy_mode(self):
        admin = make_user(ADMIN_ID)
        replies = await self.run_command(ofd_privacy_toggle_func, admin, "/ofd_privacy disable")
        self.assertTrue(replies[0].startswith("Disabled Federation privacy mode."))
        self.assertFalse((await self.settings()).ofd_privacy)
        replies = await self.run_command(ofd_privacy_toggle_func, admin, "/ofd_privacy enable")
        self.assertTrue(replies[0].startswith("Enabled Federation privacy mode."))
        self.assertIn("Only the text of messages", replies[0])
        self.assertTrue((await self.settings()).ofd_privacy)

    async def test_regular_user_can_not_change_federation(self):
        replies = await self.run_command(ofd_toggle_func, make_user(), "/ofd disable")
        self.assertEqual(replies, ["You don't have enough permissions"])
        replies = await self.run_command(ofd_privacy_toggle_func, make_user(), "/ofd_privacy enable")
        self.assertEqual(replies, ["You don't have enough permissions"])
        settings = await self.settings()
        self.assertTrue(settings.ofd_enabled)
        self.assertTrue(settings.ofd_privacy)

    async def test_bot_without_federation(self):
        self.client.ofd = None
        for handler, text in ((ofd_toggle_func, "/ofd disable"), (ofd_privacy_toggle_func, "/ofd_privacy enable")):
            self.assertEqual(await self.run_command(handler, make_user(ADMIN_ID), text), [OFD_NOT_SET_UP])
        self.assertEqual(await self.count("chats"), 0)

    async def test_settings_show_federation(self):
        await self.db.set_chat_feature(SUPERGROUP_ID, "ofd_privacy", True)
        replies = await self.run_command(settings_command, make_user(), "/settings")
        self.assertIn("Federation (OFD): <b>on</b> (/ofd)", replies[0])
        self.assertIn("Federation privacy mode: <b>on</b> (/ofd_privacy)", replies[0])

    async def test_private_group_cannot_use_federation(self):
        private_group = make_channel(SUPERGROUP_ID, title="Private Group", megagroup=True)
        for handler, text in ((ofd_toggle_func, "/ofd disable"), (ofd_privacy_toggle_func, "/ofd_privacy disable")):
            replies = await self.run_command(handler, make_user(ADMIN_ID), text, chat=private_group)
            self.assertEqual(replies, [OFD_PUBLIC_ONLY])
        self.assertEqual(await self.count("chats"), 0)
        replies = await self.run_command(settings_command, make_user(), "/settings", chat=private_group)
        self.assertNotIn("Federation", replies[0])


class ScanCommandTest(DatabaseTestCase):
    async def test_spam_scan_shows_arq_errors(self):
        client = FakeClient(db=self.db, arq=FakeArq(error=ArqError("ARQ is down")))
        event = FakeMessageEvent(client, make_user(), "/spam_scan", reply_message=make_message("buy"))
        await spam_scan_command(event)
        self.assertEqual(event.reply_texts(), ["ARQ is down"])

    async def test_spam_scan_sends_plain_letters(self):
        client = FakeClient(db=self.db, arq=FakeArq())
        event = FakeMessageEvent(client, make_user(), "/spam_scan", reply_message=make_message("𝙛𝙧𝙚𝙚 𝙘𝙧𝙮𝙥𝙩𝙤"))
        await spam_scan_command(event)
        self.assertEqual(client.arq.nlp_calls, ["free crypto"])

    async def test_spam_scan_shows_the_answer(self):
        client = FakeClient(db=self.db, arq=FakeArq(spam=spam_result(97.25, "crypto_trading", 2)))
        event = FakeMessageEvent(client, make_user(), "/spam_scan", reply_message=make_message("buy signals"))
        await spam_scan_command(event)
        reply = event.reply_texts()[0]
        for part in ("97.25 %", "deleted above 72.0 %: yes", "crypto_trading", "<code>2</code>", "english", "test-model-1"):
            self.assertIn(part, reply)

    async def test_spam_scan_without_reply(self):
        event = FakeMessageEvent(FakeClient(db=self.db), make_user(), "/spam_scan")
        await spam_scan_command(event)
        self.assertEqual(event.reply_texts(), ["Reply to a message to scan it."])

    async def test_nsfw_scan_of_text_message(self):
        event = FakeMessageEvent(FakeClient(db=self.db), make_user(), "/nsfw_scan", reply_message=make_message("hi"))
        await nsfw_scan_command(event)
        self.assertIn("Reply to an image", event.reply_texts()[0])
