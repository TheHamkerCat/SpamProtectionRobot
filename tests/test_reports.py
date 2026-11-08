import unittest
from unittest import mock

from telethon.errors import (ChatAdminRequiredError, ChatWriteForbiddenError,
                             FloodWaitError)

from spr import reports
from spr.reports import Report
from tests.fakes import NSFW_LOG, SPAM_LOG, SUPERGROUP_ID, FakeClient


def numbered_buttons(number, prefix):
    from telethon import Button
    return [Button.inline(f"{prefix}Vote", data=f"vote_{number}")]


class ReportQueueTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = FakeClient()
        self.queue = self.client.reports

    def add(self, chat_id, count, **kwargs):
        for i in range(count):
            self.queue.add(chat_id, Report(f"report {i}", **kwargs))

    async def test_reports_are_combined(self):
        self.add(SUPERGROUP_ID, 3)
        await self.queue.flush()
        self.assertEqual(self.client.texts_to(SUPERGROUP_ID), ["report 0\n\nreport 1\n\nreport 2"])
        self.assertEqual(self.queue.pending(SUPERGROUP_ID), [])

    async def test_entries_with_buttons_are_numbered(self):
        self.add(SPAM_LOG, 2, buttons=numbered_buttons)
        await self.queue.flush()
        message = self.client.sent[0]
        self.assertEqual(message.text, "<b>#1</b> report 0\n\n<b>#2</b> report 1")
        rows = message.kwargs["buttons"]
        self.assertEqual([row[0].text for row in rows], ["#1 Vote", "#2 Vote"])
        self.assertEqual(rows[1][0].type.data, b"vote_2")

    async def test_single_entry_has_no_number(self):
        self.add(SPAM_LOG, 1, buttons=numbered_buttons)
        await self.queue.flush()
        message = self.client.sent[0]
        self.assertEqual(message.text, "report 0")
        self.assertEqual(message.kwargs["buttons"][0][0].text, "Vote")

    async def test_per_chat_limit_per_flush(self):
        self.add(SUPERGROUP_ID, 40)
        await self.queue.flush()
        per_message = reports.MAX_REPORTS_PER_MESSAGE
        per_flush = reports.MAX_MESSAGES_PER_CHAT_PER_FLUSH
        self.assertEqual(len(self.client.sent), per_flush)
        self.assertEqual(len(self.queue.pending(SUPERGROUP_ID)), 40 - per_flush * per_message)
        await self.queue.flush()
        self.assertEqual(len(self.client.sent), 2 * per_flush)

    async def test_long_reports_split_by_length(self):
        for i in range(3):
            self.queue.add(SUPERGROUP_ID, Report("x" * 1500))
        await self.queue.flush()
        self.assertEqual([len(text) for text in self.client.texts_to(SUPERGROUP_ID)], [3002, 1500])

    async def test_too_many_pending_drops_the_oldest(self):
        with mock.patch.object(reports, "MAX_PENDING_PER_CHAT", 5):
            self.add(SUPERGROUP_ID, 8)
        self.assertEqual(self.queue.pending(SUPERGROUP_ID)[0].text, "report 3")
        await self.queue.flush()
        self.assertIn("3 older reports were not sent", self.client.texts_to(SUPERGROUP_ID)[0])

    async def test_log_channels_go_first(self):
        self.add(SUPERGROUP_ID, 1)
        self.add(NSFW_LOG, 1)
        self.add(SPAM_LOG, 1)
        await self.queue.flush()
        self.assertEqual([m.chat_id for m in self.client.sent][-1], SUPERGROUP_ID)

    async def test_alert_links_to_its_log_report(self):
        log_report = Report("spam report", buttons=numbered_buttons)
        self.queue.add(SPAM_LOG, log_report)
        self.queue.add(SUPERGROUP_ID, Report("spam alert", refers_to=log_report))
        await self.queue.flush()
        alert = self.client.sent[-1]
        self.assertEqual(alert.kwargs["buttons"][0][0].text, "View in spam log")
        self.assertEqual(alert.kwargs["buttons"][0][0].type.url, log_report.link)
        self.assertTrue(log_report.link.startswith("https://t.me/SpamLog/"))

    async def test_flood_wait_keeps_the_reports(self):
        self.client.send_errors[SUPERGROUP_ID] = FloodWaitError(request=None, capture=30)
        self.add(SUPERGROUP_ID, 3)
        await self.queue.flush()
        self.assertEqual(len(self.queue.pending(SUPERGROUP_ID)), 3)
        # Paused: the next flush doesn't try again
        del self.client.send_errors[SUPERGROUP_ID]
        await self.queue.flush()
        self.assertEqual(self.client.sent, [])

    async def test_group_where_the_bot_cant_write_is_left(self):
        self.client.send_errors[SUPERGROUP_ID] = ChatWriteForbiddenError(request=None)
        self.add(SUPERGROUP_ID, 2)
        await self.queue.flush()
        self.assertEqual(self.client.left, [SUPERGROUP_ID])
        self.assertEqual(self.queue.pending(SUPERGROUP_ID), [])

    async def test_log_channel_is_never_left(self):
        self.client.send_errors[SPAM_LOG] = ChatWriteForbiddenError(request=None)
        self.add(SPAM_LOG, 1)
        await self.queue.flush()
        self.assertEqual(self.client.left, [])

    async def test_other_errors_drop_the_batch_only(self):
        self.client.send_errors[SPAM_LOG] = ChatAdminRequiredError(request=None)
        self.add(SPAM_LOG, 1)
        self.add(SUPERGROUP_ID, 1)
        await self.queue.flush()
        self.assertEqual(self.client.texts_to(SUPERGROUP_ID), ["report 0"])
