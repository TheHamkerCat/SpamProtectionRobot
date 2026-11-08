import asyncio

from telethon.tl import types

from spr.core.votes import (LEGACY_NSFW_VOTE_REGEX, LEGACY_SPAM_VOTE_REGEX,
                            NSFW_VOTE_REGEX, SPAM_VOTE_REGEX)
from spr.modules.vote import (legacy_nsfw_vote_callback,
                              legacy_spam_vote_callback, nsfw_vote_callback,
                              spam_vote_callback)
from tests.fakes import (NSFW_LOG, SPAM_LOG, SUDO_ID, USER_ID, DatabaseTestCase,
                         FakeButton, FakeCallbackEvent, FakeClient,
                         FakeLogMessage, make_message, make_photo)


def spam_rows(entries: int):
    """Buttons of a combined spam log message, as Telegram returns them"""
    rows = []
    for number in range(1, entries + 1):
        prefix = f"#{number} " if entries > 1 else ""
        rows.append([
            FakeButton(f"{prefix}Correct (0)", data=f"upvote_spam_{number}".encode()),
            FakeButton(f"{prefix}Incorrect (0)", data=f"downvote_spam_{number}".encode()),
            FakeButton(f"{prefix}Chat", url=f"https://t.me/group{number}"),
        ])
    return rows


def nsfw_rows(media_ids):
    return [
        [
            FakeButton(f"#{number} Correct", data=f"upvote_nsfw_{media_id}".encode()),
            FakeButton(f"#{number} Incorrect", data=f"downvote_nsfw_{media_id}".encode()),
        ]
        for number, media_id in enumerate(media_ids, start=1)
    ]


def texts(keyboard):
    return [[button.text for button in row] for row in keyboard]


class SpamVoteTest(DatabaseTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.client = FakeClient(db=self.db)

    def vote(self, user_id, data, entries=3, chat_id=SPAM_LOG):
        message = FakeLogMessage(buttons=spam_rows(entries))
        return FakeCallbackEvent(self.client, user_id, data, chat_id=chat_id, message=message)

    async def test_vote_on_one_entry_of_a_combined_report(self):
        await self.db.add_vote(900, 2, 1, False)
        event = self.vote(USER_ID, b"upvote_spam_2")
        await spam_vote_callback(event)
        keyboard = event.edits[0][1]["buttons"]
        self.assertEqual(texts(keyboard), [
            ["#1 Correct (0)", "#1 Incorrect (0)", "#1 Chat"],
            ["#2 Correct (1)", "#2 Incorrect (1)", "#2 Chat"],
            ["#3 Correct (0)", "#3 Incorrect (0)", "#3 Chat"],
        ])
        self.assertEqual(keyboard[2][2].type.url, "https://t.me/group3")

    async def test_single_report_has_no_numbers(self):
        event = self.vote(USER_ID, b"downvote_spam_1", entries=1)
        await spam_vote_callback(event)
        self.assertEqual(texts(event.edits[0][1]["buttons"]), [["Correct (0)", "Incorrect (1)", "Chat"]])

    async def test_one_vote_per_user_and_entry(self):
        await spam_vote_callback(self.vote(USER_ID, b"upvote_spam_1"))
        event = self.vote(USER_ID, b"downvote_spam_1")
        await spam_vote_callback(event)
        self.assertEqual(event.answers, ["You've already casted your vote."])
        # Another entry of the same message is a separate vote
        await spam_vote_callback(self.vote(USER_ID, b"upvote_spam_2"))
        counts = await self.db.get_vote_counts(900)
        self.assertEqual((counts[1], counts[2], counts[3]), ((1, 0), (1, 0), (0, 0)))

    async def test_votes_at_the_same_time_all_show(self):
        events = [self.vote(user_id, b"upvote_spam_1") for user_id in range(10, 20)]
        await asyncio.gather(*(spam_vote_callback(event) for event in events))
        # Edits are serialized and read the database, so the last one is complete
        self.assertEqual(texts(events[-1].edits[0][1]["buttons"])[0][0], "#1 Correct (10)")

    async def test_votes_outside_the_spam_log_are_ignored(self):
        for chat_id in (-1003, NSFW_LOG):
            await spam_vote_callback(self.vote(USER_ID, b"upvote_spam_1", chat_id=chat_id))
        self.assertEqual(dict(await self.db.get_vote_counts(900)), {})

    async def test_old_reports_take_no_votes(self):
        event = self.vote(USER_ID, b"upvote_spam")
        await legacy_spam_vote_callback(event)
        self.assertIn("older version", event.answers[0])


class NsfwVoteTest(DatabaseTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.client = FakeClient(db=self.db)
        self.notice_text = "<b>#1</b> NSFW in A\n\n<b>#2</b> NSFW in B"

    def vote(self, user_id, data, chat_id=NSFW_LOG):
        message = FakeLogMessage(buttons=nsfw_rows([31, 32]), text=self.notice_text)
        return FakeCallbackEvent(self.client, user_id, data, chat_id=chat_id, message=message)

    async def test_incorrect_ignores_the_media_and_removes_only_that_row(self):
        event = self.vote(SUDO_ID, b"downvote_nsfw_32")
        await nsfw_vote_callback(event)
        self.assertTrue(await self.db.is_media_ignored(32))
        self.assertFalse(await self.db.is_media_ignored(31))
        text, kwargs = event.edits[0]
        self.assertTrue(text.startswith(self.notice_text))
        self.assertIn("#2 <b>Reviewed:</b> marked incorrect, this file won't be flagged again by", text)
        self.assertEqual(texts(kwargs["buttons"]), [["#1 Correct", "#1 Incorrect"]])

    async def test_last_review_removes_all_buttons(self):
        message = FakeLogMessage(buttons=nsfw_rows([31])[:1], text="NSFW in A")
        event = FakeCallbackEvent(self.client, SUDO_ID, b"upvote_nsfw_31", chat_id=NSFW_LOG, message=message)
        await nsfw_vote_callback(event)
        self.assertIsNone(event.edits[0][1]["buttons"])
        self.assertFalse(await self.db.is_media_ignored(31))

    async def test_already_reviewed(self):
        event = self.vote(SUDO_ID, b"downvote_nsfw_99")
        await nsfw_vote_callback(event)
        self.assertEqual(event.answers, ["This report was already reviewed."])
        self.assertEqual(event.edits, [])

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
            b"upvote_", b"upvote_spam_x", b"xupvote_spam", b"downvote_other", b"upvote_spam_123",
            b"downvote_nsfw_", b"downvote_nsfw_12a", b"downvote_nsfw_" + b"9" * 21,
        )
        regexes = (SPAM_VOTE_REGEX, NSFW_VOTE_REGEX, LEGACY_SPAM_VOTE_REGEX, LEGACY_NSFW_VOTE_REGEX)
        for data in crafted:
            for regex in regexes:
                self.assertIsNone(regex.match(data), data)
