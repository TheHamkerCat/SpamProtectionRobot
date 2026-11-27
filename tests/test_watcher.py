import asyncio
from datetime import datetime
from unittest import mock

from telethon.errors import ChatAdminRequiredError, MessageDeleteForbiddenError
from telethon.tl import types

from spr.arq import ArqError, NsfwResult, SpamModel
from spr.db import SPAM_HISTORY_SIZE
from spr.modules import watcher
from spr.ofd import OfdError
from spr.modules.watcher import (check_message, is_watched_message,
                                 message_watcher, should_scan_text)
from spr.scan_cache import text_key
from spr.db import UserStats
from spr.utils.media import get_media_id
from spr.workqueue import WorkQueue
from tests.fakes import (ADMIN_ID, BASIC_GROUP_ID, NSFW_LOG, SPAM_LOG,
                         SPAM_MODEL, SUDO_ID, SUPERGROUP_ID, USER_ID, DatabaseTestCase,
                         FakeArq, FakeClient, FakeMessageEvent, FakeOfd,
                         federation_query, federation_scan, make_channel,
                         make_photo, make_user, spam_result)

NOW = datetime(2026, 1, 1)
SPAM = spam_result(97.0, "crypto_trading", 2)
NSFW = NsfwResult(1.0, 95.0, 1.0, 2.0, 1.0, True)
PHOTO = types.MessageMediaPhoto(photo=make_photo(4242))
SPAM_TEXT = "Earn 5000 daily, DM me for crypto signals"


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

    async def check(self, event):
        """What a queue worker does, then send the queued reports"""
        await check_message(event)
        await self.client.flush_reports()

    async def test_spam_from_user_is_deleted_and_reported(self):
        event = self.event(make_user(first_name="<b>Spammer</b>"), "<a href='https://phish'>win</a> crypto")
        await self.check(event)

        self.assertEqual(self.client.deleted, [(SUPERGROUP_ID, [event.id])])
        log = [m for m in self.client.sent if m.chat_id == SPAM_LOG][0]
        # User text is escaped, so it can't inject links or formatting
        self.assertIn("&lt;a href=&#x27;https://phish&#x27;&gt;win&lt;/a&gt;", log.text)
        self.assertIn("&lt;b&gt;Spammer&lt;/b&gt;", log.text)
        self.assertEqual(
            [b.type.data for b in log.kwargs["buttons"][0][:2]], [b"upvote_spam_1", b"downvote_spam_1"]
        )
        alert = [m for m in self.client.sent if m.chat_id == SUPERGROUP_ID][0]
        self.assertIn("SPAM ALERT", alert.text)
        # The group alert links to the log report, sent first in the same flush
        self.assertEqual(alert.kwargs["buttons"][0][0].text, "View in spam log")
        self.assertEqual((await self.db.get_user_stats(USER_ID)).spam_data, (97.0,))
        # The log says what kind of spam it was
        self.assertIn("97 % · crypto_trading · scam", log.text)

    async def test_below_threshold_is_scored_not_deleted(self):
        self.client.arq = FakeArq(spam=spam_result(SPAM_MODEL.spam_above - 0.5, "promotion", 1))
        await self.check(self.event(make_user(), SPAM_TEXT))
        self.assertEqual(self.client.deleted, [])
        self.assertEqual((await self.db.get_user_stats(USER_ID)).spam_data, (SPAM_MODEL.spam_above - 0.5,))

    async def test_retrained_model_uses_its_own_threshold(self):
        # ARQ answers with model 2 while the bot still knows model 1;
        # model 2's threshold (99 %) keeps a 97 % message
        retrained = SpamModel("test-model-2", 99.0)
        self.client.arq = FakeArq(spam=spam_result(97.0, version=retrained.version), models=(SPAM_MODEL, retrained))
        await self.check(self.event(make_user(), SPAM_TEXT))
        self.assertEqual(self.client.arq.model_calls, [None, "test-model-2"])
        self.assertEqual(self.client.deleted, [])

    async def test_admin_spam_is_scored_but_not_deleted(self):
        await self.check(self.event(make_user(ADMIN_ID), SPAM_TEXT))
        self.assertEqual(self.client.deleted, [])
        self.assertEqual((await self.db.get_user_stats(ADMIN_ID)).spam_data, (97.0,))

    async def test_sudo_and_anonymous_admin_are_not_punished(self):
        await self.check(self.event(make_user(SUDO_ID), SPAM_TEXT))
        self.assertEqual(self.client.arq.nlp_calls, [])
        group_itself = make_channel(SUPERGROUP_ID, megagroup=True)
        await self.check(self.event(group_itself, SPAM_TEXT))
        self.assertEqual(self.client.deleted, [])

    async def test_bot_messages_are_not_checked(self):
        alert = "🚨 SPAM ALERT 🚔 Deleted a message from @someone [123] · trust 8 · spam"
        await self.check(self.event(make_user(bot=True), alert, media=PHOTO))
        self.assertEqual((self.client.arq.nlp_calls, self.client.arq.nsfw_calls), ([], []))
        self.assertEqual(self.client.deleted, [])
        self.assertEqual(await self.count("users"), 0)

    async def test_sending_as_a_channel_does_not_bypass_checks(self):
        own_channel = make_channel(-1007777777777, title="Spam Channel")
        await self.check(self.event(own_channel, SPAM_TEXT))
        self.assertEqual(self.client.deleted, [(SUPERGROUP_ID, [100])])
        # Stats are stored under the marked channel ID, apart from user IDs
        self.assertEqual((await self.db.get_user_stats(-1007777777777)).spam_data, (97.0,))
        self.assertEqual(await self.count("users"), 1)

    async def test_spam_detection_disabled(self):
        await self.db.set_chat_feature(SUPERGROUP_ID, "spam", False)
        await self.check(self.event(make_user(), SPAM_TEXT))
        self.assertEqual(self.client.deleted, [])
        self.assertEqual(len(self.client.arq.nlp_calls), 1)

    async def test_no_delete_rights(self):
        self.client.delete_error = MessageDeleteForbiddenError(request=None)
        await self.check(self.event(make_user(), SPAM_TEXT))
        self.assertIn("I can't delete a message from", self.client.texts_to(SUPERGROUP_ID)[0])
        self.assertEqual(self.client.texts_to(SPAM_LOG), [])

    async def test_no_delete_rights_in_basic_group(self):
        # messages.deleteMessages "succeeds" there but deletes nothing
        self.client.delete_pts = 0
        await self.check(self.event(make_user(), SPAM_TEXT, chat_id=BASIC_GROUP_ID))
        self.assertIn("I can't delete a message from", self.client.texts_to(BASIC_GROUP_ID)[0])

    async def test_spam_log_failure_still_alerts_the_group(self):
        self.client.send_errors[SPAM_LOG] = ChatAdminRequiredError(request=None)
        await self.check(self.event(make_user(), SPAM_TEXT))
        alert = self.client.sent[-1]
        self.assertEqual(alert.chat_id, SUPERGROUP_ID)
        self.assertIsNone(alert.kwargs["buttons"])

    async def test_nsfw_log_gets_a_text_notice_never_the_media(self):
        event = self.event(make_user(), media=PHOTO)
        await self.check(event)
        self.assertEqual(self.client.deleted, [(SUPERGROUP_ID, [event.id])])
        self.assertEqual((await self.db.get_user_stats(USER_ID)).nsfw_count, 1)
        self.assertIn("NSFW ALERT", self.client.texts_to(SUPERGROUP_ID)[0])

        notice = [m for m in self.client.sent if m.chat_id == NSFW_LOG][0]
        self.assertIsInstance(notice.text, str)
        self.assertIn("<b>NSFW</b> in", notice.text)
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
        await self.check(event)
        self.assertEqual(self.client.deleted, [(SUPERGROUP_ID, [event.id])])
        self.assertIn("NSFW ALERT", self.client.texts_to(SUPERGROUP_ID)[0])

    async def test_nsfw_without_delete_rights_is_still_logged(self):
        self.client.delete_error = MessageDeleteForbiddenError(request=None)
        await self.check(self.event(make_user(), media=PHOTO))
        self.assertIn("I can't delete a message from", self.client.texts_to(SUPERGROUP_ID)[0])
        self.assertIn("<b>Message deleted:</b> False", self.client.texts_to(NSFW_LOG)[0])
        self.assertEqual((await self.db.get_user_stats(USER_ID)).nsfw_count, 0)

    async def test_nsfw_disabled_or_ignored_media_is_not_scanned(self):
        await self.db.ignore_media(get_media_id(self.event(make_user(), media=PHOTO).message))
        await self.check(self.event(make_user(), media=PHOTO))
        await self.db.set_chat_feature(SUPERGROUP_ID, "nsfw", False)
        other = types.MessageMediaPhoto(photo=make_photo(1))
        await self.check(self.event(make_user(), media=other))
        self.assertEqual(self.client.arq.nsfw_calls, [])
        self.assertEqual(self.client.deleted, [])

    async def test_forwarded_file_is_scanned_once(self):
        for message_id in (1, 2, 3):
            await self.check(self.event(make_user(), media=PHOTO, message_id=message_id))
        self.assertEqual(len(self.client.arq.nsfw_calls), 1)
        self.assertEqual(len(self.client.deleted), 3)

    async def test_blacklisted_user_is_banned_in_supergroup(self):
        await self.db.blacklist("user", USER_ID, "known spammer")
        await self.check(self.event(make_user(), "hi"))
        self.assertEqual(len(self.client.restricted), 1)
        _, user_id, rights = self.client.restricted[0]
        self.assertEqual((user_id, rights), (USER_ID, {"view_messages": False}))
        self.assertIn("SPAMMER ALERT", self.client.texts_to(SUPERGROUP_ID)[0])

    async def test_blacklisted_user_is_removed_from_basic_group(self):
        await self.db.blacklist("user", USER_ID, "known spammer")
        await self.check(self.event(make_user(), "hi", chat_id=BASIC_GROUP_ID))
        self.assertEqual(self.client.kicked, [(types.InputPeerChat(4242), USER_ID)])
        self.assertEqual(self.client.restricted, [])

    async def test_blacklisted_chat_is_left(self):
        await self.db.blacklist("chat", SUPERGROUP_ID, "spam group")
        await self.check(self.event(make_user(), SPAM_TEXT))
        self.assertEqual(self.client.left, [SUPERGROUP_ID])
        self.assertEqual(self.client.arq.nlp_calls, [])

    async def test_arq_errors_do_not_crash(self):
        self.client.arq = FakeArq(error=ArqError("down"))
        await self.check(self.event(make_user(), SPAM_TEXT, media=PHOTO))
        self.assertEqual(self.client.deleted, [])

    async def test_unknown_admin_list_means_no_action(self):
        self.client.participants_error = ChatAdminRequiredError(request=None)
        await self.check(self.event(make_user(), SPAM_TEXT))
        self.assertEqual(self.client.deleted, [])
        self.assertEqual(self.client.arq.nlp_calls, [])

    async def test_raid_of_one_text_costs_one_scan(self):
        senders = [make_user(USER_ID + i) for i in range(20)]
        events = [self.event(sender, SPAM_TEXT, message_id=i) for i, sender in enumerate(senders)]
        # Different case and spacing is the same text
        events.append(self.event(make_user(5), "  earn 5000 DAILY,  dm me for crypto signals "))
        await asyncio.gather(*(check_message(event) for event in events))
        self.assertEqual(len(self.client.arq.nlp_calls), 1)
        self.assertEqual(len(self.client.deleted), 21)

    async def test_trusted_senders_are_sampled(self):
        for _ in range(SPAM_HISTORY_SIZE):
            await self.db.add_spam_prediction(USER_ID, 1)
        self.client.arq = FakeArq(spam=spam_result(1.0))
        with mock.patch.object(watcher._random, "random", return_value=0.5):
            await self.check(self.event(make_user(), "see you at the meeting tomorrow"))
            # Texts with links are always scanned
            await self.check(self.event(make_user(), "look at https://example.com now"))
        self.assertEqual(self.client.arq.nlp_calls, ["look at https://example.com now"])

    async def test_look_alike_letters_are_scanned_as_plain_text(self):
        fancy = "𝙀𝙖𝙧𝙣 5000 𝙙𝙖𝙞𝙡𝙮, DM me for crypto signals"
        await self.check(self.event(make_user(), fancy, message_id=1))
        await self.check(self.event(make_user(USER_ID + 1), SPAM_TEXT, message_id=2))
        # One scan for both: the plain text is the same
        self.assertEqual(self.client.arq.nlp_calls, [SPAM_TEXT])
        self.assertEqual(len(self.client.deleted), 2)

    async def test_link_in_look_alike_letters_is_a_link(self):
        for _ in range(SPAM_HISTORY_SIZE):
            await self.db.add_spam_prediction(USER_ID, 1)
        self.client.arq = FakeArq(spam=spam_result(1.0))
        with mock.patch.object(watcher._random, "random", return_value=0.5):
            await self.check(self.event(make_user(), "𝗷𝗼𝗶𝗻 𝘁.𝗺𝗲/𝗳𝗿𝗲𝗲𝗰𝗼𝗶𝗻𝘀"))
        self.assertEqual(self.client.arq.nlp_calls, ["join t.me/freecoins"])

    async def test_only_links_are_not_judged_by_arq(self):
        await self.check(self.event(make_user(), "https://www.youtube.com/watch?v=PX7bQRgCxk0"))
        await self.check(self.event(make_user(), "t.me/somechannel/123 @someone"))
        self.assertEqual(self.client.arq.nlp_calls, [])
        self.assertEqual(self.client.deleted, [])
        # Not counted for the sender's trust either
        self.assertEqual((await self.db.get_user_stats(USER_ID)).spam_data, ())

    async def test_handler_only_queues(self):
        handled = []

        async def handle(event):
            handled.append(event)

        self.client.message_queue = WorkQueue(handle, workers=2, max_size=10)
        self.client.message_queue.start()
        await message_watcher(self.event(make_user(), SPAM_TEXT))
        await self.client.message_queue.join()
        await self.client.message_queue.stop()
        self.assertEqual(len(handled), 1)
        self.assertEqual(self.client.arq.nlp_calls, [])

    async def test_alerts_off_deletes_silently(self):
        await self.db.set_chat_feature(SUPERGROUP_ID, "alerts", False)
        spam = self.event(make_user(), SPAM_TEXT, message_id=1)
        nsfw = self.event(make_user(USER_ID + 1), media=PHOTO, message_id=2)
        await self.check(spam)
        await self.check(nsfw)
        self.assertEqual(self.client.deleted, [(SUPERGROUP_ID, [1]), (SUPERGROUP_ID, [2])])
        self.assertEqual(self.client.texts_to(SUPERGROUP_ID), [])
        # The log channels are unchanged
        self.assertEqual(len(self.client.texts_to(SPAM_LOG)), 1)
        self.assertEqual(len(self.client.texts_to(NSFW_LOG)), 1)

    async def test_alerts_off_bans_silently(self):
        await self.db.set_chat_feature(SUPERGROUP_ID, "alerts", False)
        await self.db.blacklist("user", USER_ID, "known spammer")
        await self.check(self.event(make_user(), "hi"))
        self.assertEqual(len(self.client.restricted), 1)
        self.assertEqual(self.client.texts_to(SUPERGROUP_ID), [])

    async def test_alerts_off_still_reports_missing_rights(self):
        await self.db.set_chat_feature(SUPERGROUP_ID, "alerts", False)
        self.client.delete_error = MessageDeleteForbiddenError(request=None)
        await self.check(self.event(make_user(), SPAM_TEXT))
        self.assertIn("I can't delete a message from", self.client.texts_to(SUPERGROUP_ID)[0])

    async def test_filter(self):
        self.assertTrue(is_watched_message(self.event(make_user(), "text")))
        self.assertTrue(is_watched_message(self.event(make_user(), media=PHOTO)))
        self.assertFalse(is_watched_message(self.event(make_user(), "")))
        self.assertFalse(is_watched_message(self.event(make_user(), "text", is_private=True)))


class ShouldScanTextTest(DatabaseTestCase):
    def test_rules(self):
        new_user = UserStats()
        self.assertFalse(should_scan_text("ok thanks", new_user))
        self.assertTrue(should_scan_text("t.me/joinme", new_user))
        self.assertTrue(should_scan_text("dm @crypto_king", new_user))
        self.assertTrue(should_scan_text("this is a normal sentence", new_user))

    def test_text_key_ignores_case_and_spacing(self):
        self.assertEqual(text_key("Hello   World"), text_key(" hello world "))
        self.assertNotEqual(text_key("hello world"), text_key("hello world!"))


CLEAN = spam_result(1.0)
PERMANENT_BLOCK = federation_query("PERMANENTLY_BLOCK_ENTITY", incidents=("SPAM", "SCAM"))
LIFT_AT = 1_900_000_000


class FederationWatcherTest(DatabaseTestCase):
    """With OFD_HOST: sender lookups, scans and publishing (spr/ofd.py)"""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.client = FakeClient(db=self.db, arq=FakeArq(spam=CLEAN), ofd=FakeOfd())
        self.client.participants[SUPERGROUP_ID] = [
            (ADMIN_ID, types.ChannelParticipantAdmin(ADMIN_ID, 1, NOW, types.ChatAdminRights()))
        ]

    def event(self, sender, text="", **kwargs):
        kwargs.setdefault(
            "chat", make_channel(SUPERGROUP_ID, title="Group", username="publicgroup", megagroup=True)
        )
        return FakeMessageEvent(self.client, sender, text, **kwargs)

    async def check(self, event):
        await check_message(event)
        await self.client.flush_reports()

    async def test_blocked_sender_is_banned_and_logged(self):
        self.client.ofd.query = PERMANENT_BLOCK
        event = self.event(make_user(), "hello there everyone")
        await self.check(event)
        self.assertEqual(len(self.client.restricted), 1)
        self.assertEqual(self.client.deleted, [(SUPERGROUP_ID, [event.id])])
        self.assertNotIn("until_date", self.client.restricted[0][2])
        log = self.client.texts_to(SPAM_LOG)[0]
        self.assertIn("FEDERATION BAN", log)
        self.assertIn("SCAM, SPAM · permanently", log)
        self.assertIn("blacklisted on Federation (SCAM, SPAM)", self.client.texts_to(SUPERGROUP_ID)[0])
        # Banned before anything else is checked
        self.assertEqual(self.client.arq.nlp_calls, [])

    async def test_temporary_block_bans_until_the_lift_time(self):
        self.client.ofd.query = federation_query("TEMPORARILY_BLOCK_ENTITY", lift=LIFT_AT)
        await self.check(self.event(make_user(), "hello there everyone"))
        until = self.client.restricted[0][2]["until_date"]
        self.assertEqual(int(until.timestamp()), LIFT_AT)

    async def test_temporary_block_with_expired_lift_time_is_not_acted_on(self):
        self.client.ofd.query = federation_query("TEMPORARILY_BLOCK_ENTITY", lift=1_000_000_000)
        await self.check(self.event(make_user(), "hello there everyone"))
        self.assertEqual(self.client.restricted, [])
        self.assertEqual(self.client.deleted, [])

    async def test_temporary_block_without_lift_time_is_not_acted_on(self):
        self.client.ofd.query = federation_query("TEMPORARILY_BLOCK_ENTITY")
        await self.check(self.event(make_user(), "hello there everyone"))
        self.assertEqual(self.client.restricted, [])
        self.assertEqual(self.client.kicked, [])

    async def test_caution_is_ignored(self):
        self.client.ofd.query = federation_query("CAUTION")
        self.client.ofd.scan_result = federation_scan("CAUTION", flag="SUSPICIOUS")
        await self.check(self.event(make_user(), SPAM_TEXT))
        self.assertEqual(self.client.restricted, [])
        self.assertEqual(self.client.deleted, [])

    async def test_admins_are_never_looked_up_scanned_or_banned(self):
        self.client.ofd.query = PERMANENT_BLOCK
        self.client.ofd.scan_result = federation_scan("BLOCK_CONTENT")
        self.client.ofd.can_contribute = True
        await self.check(self.event(make_user(ADMIN_ID), SPAM_TEXT))
        self.assertEqual(self.client.ofd.queries, [])
        self.assertEqual(self.client.ofd.scans, [])
        self.assertEqual(self.client.ofd.published, [])
        self.assertEqual(self.client.restricted, [])
        self.assertEqual(self.client.deleted, [])

    async def test_block_content_deletes_and_reports(self):
        self.client.ofd.scan_result = federation_scan("BLOCK_CONTENT", concerns=("PHISHING",))
        event = self.event(make_user(), SPAM_TEXT)
        await self.check(event)
        self.assertEqual(self.client.deleted, [(SUPERGROUP_ID, [event.id])])
        log = [m for m in self.client.sent if m.chat_id == SPAM_LOG][0]
        self.assertIn("Federation · malicious · phishing", log.text)
        self.assertEqual(
            [b.type.data for b in log.kwargs["buttons"][0][:2]], [b"upvote_spam_1", b"downvote_spam_1"]
        )
        self.assertIn("flagged by Federation", self.client.texts_to(SUPERGROUP_ID)[0])
        # ARQ still scores the sender as before
        self.assertEqual((await self.db.get_user_stats(USER_ID)).spam_data, (1.0,))

    async def test_arq_spam_is_handled_by_arq_once(self):
        self.client.arq = FakeArq(spam=SPAM)
        self.client.ofd.scan_result = federation_scan("BLOCK_CONTENT")
        await self.check(self.event(make_user(), SPAM_TEXT))
        self.assertEqual(len(self.client.deleted), 1)
        self.assertIn("crypto_trading", self.client.texts_to(SPAM_LOG)[0])

    async def test_scan_carries_author_and_details(self):
        await self.db.set_chat_feature(SUPERGROUP_ID, "ofd_privacy", False)
        await self.check(self.event(make_user(username="alice"), SPAM_TEXT))
        content, author = self.client.ofd.scans[0]
        self.assertEqual(author, f"{USER_ID}@telegram.org")
        self.assertEqual(content.text_content, SPAM_TEXT)
        self.assertEqual(content.tag, "telegram_message")
        self.assertEqual(content.metadata["author_username"], "alice")
        self.assertEqual(content.metadata["chat_id"], SUPERGROUP_ID)

    async def test_privacy_mode_sends_text_only_but_still_looks_up(self):
        await self.db.set_chat_feature(SUPERGROUP_ID, "ofd_privacy", True)
        self.client.ofd.can_contribute = True
        await self.check(self.event(make_user(username="alice"), SPAM_TEXT))
        content, author = self.client.ofd.scans[0]
        self.assertIsNone(author)
        self.assertIsNone(content.metadata)
        self.assertIsNone(content.note)
        self.assertEqual(self.client.ofd.published, [])
        self.assertEqual(self.client.ofd.queries, [USER_ID])

    async def test_privacy_mode_still_bans_blocked_senders(self):
        await self.db.set_chat_feature(SUPERGROUP_ID, "ofd_privacy", True)
        self.client.ofd.query = PERMANENT_BLOCK
        await self.check(self.event(make_user(), "hello there everyone"))
        self.assertEqual(len(self.client.restricted), 1)

    async def test_senders_are_published_only_with_a_contributing_token(self):
        await self.db.set_chat_feature(SUPERGROUP_ID, "ofd_privacy", False)
        await self.check(self.event(make_user(), SPAM_TEXT))
        self.assertEqual(self.client.ofd.published, [])
        self.client.ofd.can_contribute = True
        await self.check(self.event(make_user(), SPAM_TEXT))
        self.assertEqual(self.client.ofd.published, [USER_ID])

    async def test_ofd_disabled_sends_nothing(self):
        await self.db.set_chat_feature(SUPERGROUP_ID, "ofd", False)
        self.client.ofd.can_contribute = True
        self.client.ofd.query = PERMANENT_BLOCK
        await self.check(self.event(make_user(), SPAM_TEXT))
        self.assertEqual(self.client.ofd.queries, [])
        self.assertEqual(self.client.ofd.scans, [])
        self.assertEqual(self.client.ofd.published, [])
        self.assertEqual(self.client.restricted, [])

    async def test_spam_detection_disabled_sends_no_scan(self):
        await self.db.set_chat_feature(SUPERGROUP_ID, "spam", False)
        await self.check(self.event(make_user(), SPAM_TEXT))
        self.assertEqual(self.client.ofd.scans, [])

    async def test_server_without_anonymous_scans(self):
        self.client.ofd.can_scan = False
        self.client.ofd.can_query = False
        await self.check(self.event(make_user(), SPAM_TEXT))
        self.assertEqual(self.client.ofd.scans, [])
        self.assertEqual(self.client.ofd.queries, [])
        self.assertEqual(len(self.client.arq.nlp_calls), 1)

    async def test_federation_failure_leaves_arq_working(self):
        self.client.ofd.error = OfdError("down")
        self.client.arq = FakeArq(spam=SPAM)
        await self.check(self.event(make_user(), SPAM_TEXT))
        self.assertEqual(len(self.client.deleted), 1)

    async def test_arq_failure_leaves_federation_working(self):
        self.client.arq = FakeArq(error=ArqError("down"))
        self.client.ofd.scan_result = federation_scan("BLOCK_CONTENT")
        await self.check(self.event(make_user(), SPAM_TEXT))
        self.assertEqual(len(self.client.deleted), 1)

    async def test_only_links_are_still_scanned_by_federation(self):
        self.client.arq = FakeArq(spam=SPAM)
        self.client.ofd.scan_result = federation_scan("BLOCK_CONTENT")
        await self.check(self.event(make_user(), "https://phish.example/login"))
        self.assertEqual(self.client.arq.nlp_calls, [])
        self.assertEqual(len(self.client.deleted), 1)
        self.assertIn("flagged by Federation", self.client.texts_to(SUPERGROUP_ID)[0])

    async def test_ofd_does_not_run_in_private_chat(self):
        self.client.ofd.query = PERMANENT_BLOCK
        self.client.ofd.scan_result = federation_scan("BLOCK_CONTENT")
        self.client.ofd.can_contribute = True
        dm_user = make_user(username="alice")
        event = FakeMessageEvent(
            self.client, dm_user, SPAM_TEXT, chat_id=USER_ID, is_private=True, chat=dm_user
        )
        await self.check(event)
        self.assertEqual(self.client.ofd.queries, [])
        self.assertEqual(self.client.ofd.scans, [])
        self.assertEqual(self.client.ofd.published, [])
        self.assertEqual(self.client.restricted, [])
        self.assertEqual(self.client.deleted, [])

    async def test_ofd_does_not_run_in_private_group(self):
        await self.db.set_chat_feature(SUPERGROUP_ID, "ofd_privacy", False)
        self.client.ofd.query = PERMANENT_BLOCK
        self.client.ofd.scan_result = federation_scan("BLOCK_CONTENT")
        self.client.ofd.can_contribute = True
        private_group = make_channel(SUPERGROUP_ID, title="Private Group", megagroup=True)
        await self.check(self.event(make_user(), SPAM_TEXT, chat=private_group))
        self.assertEqual(self.client.ofd.queries, [])
        self.assertEqual(self.client.ofd.scans, [])
        self.assertEqual(self.client.ofd.published, [])
        self.assertEqual(self.client.restricted, [])
        self.assertEqual(self.client.deleted, [])
