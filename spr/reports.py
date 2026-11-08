"""Combined reports. Everything the bot posts on its own (log channel
reports, alerts in groups) goes through ReportQueue: reports wait per
chat and go out together every REPORT_INTERVAL_SECONDS, so the bot stays
within Telegram's limits of about 20 messages a minute per chat and
about 30 a second overall."""
import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from time import monotonic
from typing import Callable, Optional

from telethon import Button
from telethon.errors import ChatWriteForbiddenError, FloodWaitError, RPCError

from spr.core.entities import message_link
from spr.ratelimit import RateLimiter

logger = logging.getLogger(__name__)

REPORT_INTERVAL_SECONDS = 10
# 2 messages every 10 s is 12 a minute, under the 20 per chat limit
MAX_MESSAGES_PER_CHAT_PER_FLUSH = 2
MAX_REPORTS_PER_MESSAGE = 8
# Telegram allows 4096 characters; HTML tags don't count, so this is safe
MAX_MESSAGE_LENGTH = 3800
# Reports waiting per chat; older ones are dropped beyond this
MAX_PENDING_PER_CHAT = 200
SENDS_PER_SECOND = 20
REPORT_SEPARATOR = "\n\n"


@dataclass(eq=False)
class Report:
    """One entry of a combined message.

    buttons(number, prefix) returns this entry's row of buttons; number is
    its position in the message (1, 2, ...) and prefix is "#2 " when the
    message has several entries, "" when it has one.
    link is set once the report was sent. A group alert can point at its
    log report through refers_to and gets a "View in spam log" button."""

    text: str
    buttons: Optional[Callable[[int, str], list]] = None
    refers_to: Optional["Report"] = None
    link: Optional[str] = None


def entry_prefix(number: int, total: int) -> str:
    if total == 1:
        return ""
    return f"#{number} "


class _ChatQueue:
    __slots__ = ("reports", "dropped", "paused_until")

    def __init__(self):
        self.reports = deque()
        self.dropped = 0
        self.paused_until = 0.0


class ReportQueue:
    def __init__(
        self,
        client,
        priority_chats=(),
        interval: float = REPORT_INTERVAL_SECONDS,
        sends_per_second: float = SENDS_PER_SECOND,
    ):
        self._client = client
        # Log channels go first in every flush
        self._priority_chats = set(priority_chats)
        self._interval = interval
        self._limiter = RateLimiter(sends_per_second)
        self._chats = {}  # chat_id: _ChatQueue
        self._flush_lock = asyncio.Lock()

    def add(self, chat_id: int, report: Report):
        queue = self._chats.setdefault(chat_id, _ChatQueue())
        queue.reports.append(report)
        if len(queue.reports) > MAX_PENDING_PER_CHAT:
            queue.reports.popleft()
            queue.dropped += 1

    def pending(self, chat_id: int) -> list:
        queue = self._chats.get(chat_id)
        if queue is None:
            return []
        return list(queue.reports)

    async def run(self):
        """Flush every interval, until cancelled"""
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.flush()
            except Exception:
                logger.exception("Sending reports failed")

    async def flush(self):
        """Send what is waiting, within the per chat limits"""
        async with self._flush_lock:
            chat_ids = sorted(self._chats, key=lambda chat_id: chat_id not in self._priority_chats)
            for chat_id in chat_ids:
                await self._flush_chat(chat_id)

    async def _flush_chat(self, chat_id: int):
        queue = self._chats[chat_id]
        if monotonic() < queue.paused_until:
            return
        for _ in range(MAX_MESSAGES_PER_CHAT_PER_FLUSH):
            batch = self._take_batch(queue)
            if not batch:
                break
            if not await self._send(chat_id, queue, batch):
                break
        if not queue.reports and not queue.dropped:
            del self._chats[chat_id]

    @staticmethod
    def _take_batch(queue: _ChatQueue) -> list:
        batch = []
        length = 0
        while queue.reports and len(batch) < MAX_REPORTS_PER_MESSAGE:
            next_length = len(queue.reports[0].text) + len(REPORT_SEPARATOR)
            if batch and length + next_length > MAX_MESSAGE_LENGTH:
                break
            batch.append(queue.reports.popleft())
            length += next_length
        return batch

    def _render(self, batch: list, dropped: int):
        total = len(batch)
        numbered = total > 1 and any(report.buttons for report in batch)
        entries = []
        rows = []
        for number, report in enumerate(batch, start=1):
            prefix = entry_prefix(number, total)
            if numbered:
                entries.append(f"<b>#{number}</b> {report.text}")
            else:
                entries.append(report.text)
            if report.buttons:
                rows.append(report.buttons(number, prefix))
        if dropped:
            entries.append(
                f"<i>{dropped} older reports were not sent: too many came in at once.</i>"
            )

        links = [report.refers_to.link for report in batch if report.refers_to and report.refers_to.link]
        if links:
            rows.append([Button.url("View in spam log", links[0])])
        return REPORT_SEPARATOR.join(entries), rows or None

    async def _send(self, chat_id: int, queue: _ChatQueue, batch: list) -> bool:
        text, buttons = self._render(batch, queue.dropped)
        await self._limiter.wait()
        try:
            message = await self._client.send_message(
                chat_id, text, buttons=buttons, link_preview=False
            )
        except FloodWaitError as error:
            # Put the batch back and leave this chat alone for a while
            queue.reports.extendleft(reversed(batch))
            queue.paused_until = monotonic() + error.seconds
            logger.warning("Telegram asks to wait %s s before writing to %s", error.seconds, chat_id)
            return False
        except ChatWriteForbiddenError:
            queue.reports.clear()
            queue.dropped = 0
            if chat_id in self._priority_chats:
                logger.error("The bot can't write to the log channel %s", chat_id)
                return False
            logger.info("Leaving %s: the bot can't write there", chat_id)
            await self._client.delete_dialog(chat_id)
            return False
        except RPCError as error:
            logger.warning("Can't send %s reports to %s: %s", len(batch), chat_id, error)
            return False

        if queue.dropped:
            logger.warning("Dropped %s reports for %s: too many at once", queue.dropped, chat_id)
            queue.dropped = 0
        chat = await message.get_chat()
        link = message_link(chat, message.id)
        for report in batch:
            report.link = link
        return True
