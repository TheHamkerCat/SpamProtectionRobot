from telethon.tl import types

from spr.modules.vote import (LEGACY_NSFW_VOTE_REGEX, NSFW_VOTE_REGEX,
                              SPAM_VOTE_REGEX, legacy_nsfw_vote_callback,
                              nsfw_vote_callback, spam_vote_callback)
from tests.fakes import (NSFW_LOG, SPAM_LOG, SUDO_ID, USER_ID, DatabaseTestCase,
                         FakeButton, FakeCallbackEvent, FakeClient,
                         FakeLogMessage, make_message, make_photo)

REPORT_BUTTONS = [
    [FakeButton("Correct (0)"), FakeButton("Incorrect (0)")],
    [FakeButton("Chat", url="https://t.me/somegroup")],
]
NOTICE_TEXT = "<b>NSFW DETECTION</b>\n<b>Media ID:</b> <code>31</code>"


def button_texts(keyboard):
    return [[button.text for button in row] for row in keyboard]


class SpamVoteTest(DatabaseTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.client = FakeClient(db=self.db)

    def vote(self, user_id, data=b"upvote_spam", chat_id=SPAM_LOG):
        message = FakeLogMessage(buttons=REPORT_BUTTONS)
        return FakeCallbackEvent(self.client, user_id, data, chat_id=chat_id, message=message)

    async def test_counts_come_from_the_database(self):
        # Another vote already stored, the buttons still say 0
        await self.db.add_vote(900, 1, False)
        event = self.vote(USER_ID)
        await spam_vote_callback(event)
        keyboard = event.edits[0][1]["buttons"]
        self.assertEqual(button_texts(keyboard), [["Correct (1)", "Incorrect (1)"], ["Chat"]])
        self.assertEqual(keyboard[1][0].type.url, "https://t.me/somegroup")

    async def test_second_vote_is_refused(self):
        await spam_vote_callback(self.vote(USER_ID))
        event = self.vote(USER_ID, b"downvote_spam")
        await spam_vote_callback(event)
        self.assertEqual(event.answers, ["You've already casted your vote."])
        self.assertEqual(await self.db.get_vote_counts(900), (1, 0))

    async def test_votes_outside_the_spam_log_are_ignored(self):
        for chat_id in (-1003, NSFW_LOG):
            await spam_vote_callback(self.vote(USER_ID, chat_id=chat_id))
        self.assertEqual(await self.db.get_vote_counts(900), (0, 0))


class NsfwVoteTest(DatabaseTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.client = FakeClient(db=self.db)

    def vote(self, user_id, data, chat_id=NSFW_LOG):
        message = FakeLogMessage(text=NOTICE_TEXT)
        return FakeCallbackEvent(self.client, user_id, data, chat_id=chat_id, message=message)

    async def test_incorrect_ignores_the_media_and_keeps_the_notice(self):
        event = self.vote(SUDO_ID, b"downvote_nsfw_31")
        await nsfw_vote_callback(event)
        self.assertTrue(await self.db.is_media_ignored(31))
        self.assertFalse(event.was_deleted)
        text, kwargs = event.edits[0]
        self.assertTrue(text.startswith(NOTICE_TEXT))
        self.assertIn("marked incorrect, this file won't be flagged again by", text)
        self.assertNotIn("buttons", kwargs)

    async def test_correct_marks_the_notice(self):
        event = self.vote(SUDO_ID, b"upvote_nsfw_-31")
        await nsfw_vote_callback(event)
        self.assertIn("<b>Reviewed:</b> marked correct by", event.edits[0][0])
        self.assertFalse(await self.db.is_media_ignored(-31))

    async def test_only_sudo_users_in_the_nsfw_log(self):
        event = self.vote(USER_ID, b"downvote_nsfw_31")
        await nsfw_vote_callback(event)
        self.assertEqual(event.answers, ["Only the bot owners can review NSFW reports."])
        await nsfw_vote_callback(self.vote(SUDO_ID, b"downvote_nsfw_31", chat_id=SPAM_LOG))
        self.assertFalse(await self.db.is_media_ignored(31))

    async def test_legacy_report_with_media_copy_is_deleted(self):
        message = make_message(media=types.MessageMediaPhoto(photo=make_photo(77)), chat_id=NSFW_LOG)
        event = FakeCallbackEvent(self.client, SUDO_ID, b"downvote_nsfw", chat_id=NSFW_LOG, message=message)
        await legacy_nsfw_vote_callback(event)
        self.assertTrue(event.was_deleted)
        self.assertTrue(await self.db.is_media_ignored(77))

    def test_crafted_data_does_not_match(self):
        crafted = (
            b"upvote_", b"upvote_spam_x", b"xupvote_spam", b"downvote_other",
            b"downvote_nsfw_", b"downvote_nsfw_12a", b"downvote_nsfw_" + b"9" * 21,
        )
        for data in crafted:
            for regex in (SPAM_VOTE_REGEX, NSFW_VOTE_REGEX, LEGACY_NSFW_VOTE_REGEX):
                self.assertIsNone(regex.match(data), data)
