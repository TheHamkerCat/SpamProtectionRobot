from datetime import datetime

from telethon.errors import ChatAdminRequiredError, MessageDeleteForbiddenError
from telethon.tl import types

from spr.arq import ArqError, NsfwResult, SpamResult
from spr.modules.watcher import is_watched_message, message_watcher
from spr.utils.media import get_media_id
from tests.fakes import (ADMIN_ID, BASIC_GROUP_ID, NSFW_LOG, SPAM_LOG,
                         SUDO_ID, SUPERGROUP_ID, USER_ID, DatabaseTestCase,
                         FakeArq, FakeClient, FakeMessageEvent, make_channel,
                         make_photo, make_user)

NOW = datetime(2026, 1, 1)
SPAM = SpamResult(True, 97, 97.0, 3.0, False)
NSFW = NsfwResult(1.0, 95.0, 1.0, 2.0, 1.0, True)
PHOTO = types.MessageMediaPhoto(photo=make_photo(4242))


class WatcherTest(DatabaseTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.client = FakeClient(db=self.db, arq=FakeArq(spam=SPAM, nsfw=NSFW))
        self.client.participants[SUPERGROUP_ID] = [
            (ADMIN_ID, types.ChannelParticipantAdmin(ADMIN_ID, 1, NOW, types.ChatAdminRights()))
        ]
        self.client.participants[BASIC_GROUP_ID] = [
            (USER_ID, types.ChatParticipant(USER_ID, 1, NOW)),
        ]

    def event(self, sender, text="", **kwargs):
        return FakeMessageEvent(self.client, sender, text, **kwargs)

    async def test_spam_from_user_is_deleted_and_reported(self):
        event = self.event(make_user(first_name="<b>Spammer</b>"), "<a href='https://phish'>win</a> crypto")
        await message_watcher(event)

        self.assertEqual(self.client.deleted, [(SUPERGROUP_ID, [event.id])])
        report = self.client.texts_to(SPAM_LOG)[0]
        # User text is escaped, so it can't inject links or formatting
        self.assertIn("&lt;a href=&#x27;https://phish&#x27;&gt;win&lt;/a&gt;", report)
        self.assertIn("&lt;b&gt;Spammer&lt;/b&gt;", report)
        alert = self.client.texts_to(SUPERGROUP_ID)[0]
        self.assertIn("SPAM ALERT", alert)
        self.assertEqual((await self.db.get_user_stats(USER_ID)).spam_data, (97.0,))

    async def test_admin_spam_is_scored_but_not_deleted(self):
        await message_watcher(self.event(make_user(ADMIN_ID), "spammy"))
        self.assertEqual(self.client.deleted, [])
        self.assertEqual((await self.db.get_user_stats(ADMIN_ID)).spam_data, (97.0,))

    async def test_sudo_and_anonymous_admin_are_not_punished(self):
        await message_watcher(self.event(make_user(SUDO_ID), "spammy"))
        group_itself = make_channel(SUPERGROUP_ID, megagroup=True)
        await message_watcher(self.event(group_itself, "spammy"))
        self.assertEqual(self.client.deleted, [])

    async def test_sending_as_a_channel_does_not_bypass_checks(self):
        own_channel = make_channel(-1007777777777, title="Spam Channel")
        await message_watcher(self.event(own_channel, "spammy"))
        self.assertEqual(self.client.deleted, [(SUPERGROUP_ID, [100])])
        # Stats are stored under the marked channel ID, apart from user IDs
        self.assertEqual((await self.db.get_user_stats(-1007777777777)).spam_data, (97.0,))
        self.assertEqual(await self.db._pool.fetchval("SELECT count(*) FROM users"), 1)

    async def test_spam_detection_disabled(self):
        await self.db.set_chat_feature(SUPERGROUP_ID, "spam", False)
        await message_watcher(self.event(make_user(), "spammy"))
        self.assertEqual(self.client.deleted, [])
        self.assertEqual(len(self.client.arq.nlp_calls), 1)

    async def test_no_delete_rights(self):
        self.client.delete_error = MessageDeleteForbiddenError(request=None)
        event = self.event(make_user(), "spammy")
        await message_watcher(event)
        self.assertIn("I can't delete this message", event.reply_texts()[0])
        self.assertEqual(self.client.texts_to(SPAM_LOG), [])

    async def test_no_delete_rights_in_basic_group(self):
        # messages.deleteMessages "succeeds" there but deletes nothing
        self.client.delete_pts = 0
        event = self.event(make_user(), "spammy", chat_id=BASIC_GROUP_ID)
        await message_watcher(event)
        self.assertIn("I can't delete this message", event.reply_texts()[0])

    async def test_spam_log_failure_still_alerts_the_group(self):
        self.client.send_errors[SPAM_LOG] = ChatAdminRequiredError(request=None)
        await message_watcher(self.event(make_user(), "spammy"))
        alert = self.client.sent[-1]
        self.assertEqual(alert.chat_id, SUPERGROUP_ID)
        self.assertIsNone(alert.kwargs["buttons"])

    async def test_nsfw_log_gets_a_text_notice_never_the_media(self):
        event = self.event(make_user(), media=PHOTO)
        await message_watcher(event)
        self.assertEqual(self.client.deleted, [(SUPERGROUP_ID, [event.id])])
        self.assertEqual((await self.db.get_user_stats(USER_ID)).nsfw_count, 1)
        self.assertIn("NSFW ALERT", self.client.texts_to(SUPERGROUP_ID)[0])

        notice = [m for m in self.client.sent if m.chat_id == NSFW_LOG][0]
        self.assertIsInstance(notice.text, str)
        self.assertIn("NSFW DETECTION", notice.text)
        self.assertIn("<b>Media ID:</b> <code>4242</code>", notice.text)
        self.assertIn("<b>Message deleted:</b> True", notice.text)
        self.assertNotIn("file", notice.kwargs)
        buttons = notice.kwargs["buttons"][0]
        self.assertEqual([b.type.data for b in buttons], [b"upvote_nsfw_4242", b"downvote_nsfw_4242"])
        # Nothing anywhere carries the media
        self.assertTrue(all(isinstance(m.text, str) for m in self.client.sent))
        self.assertEqual(self.client.files, [])

    async def test_nsfw_is_deleted_even_if_the_notice_fails(self):
        self.client.send_errors[NSFW_LOG] = ChatAdminRequiredError(request=None)
        event = self.event(make_user(), media=PHOTO)
        await message_watcher(event)
        self.assertEqual(self.client.deleted, [(SUPERGROUP_ID, [event.id])])
        self.assertIn("NSFW ALERT", self.client.texts_to(SUPERGROUP_ID)[0])

    async def test_nsfw_without_delete_rights_is_still_logged(self):
        self.client.delete_error = MessageDeleteForbiddenError(request=None)
        event = self.event(make_user(), media=PHOTO)
        await message_watcher(event)
        self.assertIn("I can't delete this message", event.reply_texts()[0])
        self.assertIn("<b>Message deleted:</b> False", self.client.texts_to(NSFW_LOG)[0])
        self.assertEqual((await self.db.get_user_stats(USER_ID)).nsfw_count, 0)
        self.assertEqual(self.client.texts_to(SUPERGROUP_ID), [])

    async def test_nsfw_disabled_or_ignored_media_is_not_scanned(self):
        await self.db.ignore_media(get_media_id(self.event(make_user(), media=PHOTO).message))
        await message_watcher(self.event(make_user(), media=PHOTO))
        await self.db.set_chat_feature(SUPERGROUP_ID, "nsfw", False)
        other = types.MessageMediaPhoto(photo=make_photo(1))
        await message_watcher(self.event(make_user(), media=other))
        self.assertEqual(self.client.arq.nsfw_calls, [])
        self.assertEqual(self.client.deleted, [])

    async def test_blacklisted_user_is_banned_in_supergroup(self):
        await self.db.blacklist("user", USER_ID, "known spammer")
        await message_watcher(self.event(make_user(), "hi"))
        self.assertEqual(len(self.client.restricted), 1)
        _, user_id, rights = self.client.restricted[0]
        self.assertEqual((user_id, rights), (USER_ID, {"view_messages": False}))
        self.assertIn("SPAMMER ALERT", self.client.texts_to(SUPERGROUP_ID)[0])

    async def test_blacklisted_user_is_removed_from_basic_group(self):
        await self.db.blacklist("user", USER_ID, "known spammer")
        await message_watcher(self.event(make_user(), "hi", chat_id=BASIC_GROUP_ID))
        self.assertEqual(self.client.kicked, [(types.InputPeerChat(4242), USER_ID)])
        self.assertEqual(self.client.restricted, [])

    async def test_blacklisted_chat_is_left(self):
        await self.db.blacklist("chat", SUPERGROUP_ID, "spam group")
        await message_watcher(self.event(make_user(), "hi"))
        self.assertEqual(self.client.left, [SUPERGROUP_ID])
        self.assertEqual(self.client.arq.nlp_calls, [])

    async def test_arq_errors_do_not_crash(self):
        self.client.arq = FakeArq(error=ArqError("down"))
        await message_watcher(self.event(make_user(), "spammy", media=PHOTO))
        self.assertEqual(self.client.deleted, [])

    async def test_unknown_admin_list_means_no_action(self):
        self.client.participants_error = ChatAdminRequiredError(request=None)
        await message_watcher(self.event(make_user(), "spammy"))
        self.assertEqual(self.client.deleted, [])
        self.assertEqual(self.client.arq.nlp_calls, [])

    async def test_filter(self):
        self.assertTrue(is_watched_message(self.event(make_user(), "text")))
        self.assertTrue(is_watched_message(self.event(make_user(), media=PHOTO)))
        self.assertFalse(is_watched_message(self.event(make_user(), "")))
        self.assertFalse(is_watched_message(self.event(make_user(), "text", is_private=True)))
