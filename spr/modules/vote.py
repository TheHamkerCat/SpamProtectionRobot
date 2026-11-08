"""Vote buttons under the reports in the log channels. One log message
holds several reports, each with its own row of buttons.

Spam reports: anyone in the spam log channel can vote once per report.
NSFW notices: only sudo users review them, "Incorrect" stops the media
from being flagged again. The review is written into the message."""
from telethon import events

from spr.core.entities import PRIVATE_CHAT_LINK, mention
from spr.core.votes import (ENTRY_PREFIX_REGEX, LEGACY_NSFW_VOTE_REGEX,
                            LEGACY_SPAM_VOTE_REGEX, NSFW_VOTE_REGEX,
                            SPAM_VOTE_REGEX, copy_button, message_lock,
                            spam_vote_row)
from spr.reports import entry_prefix
from spr.utils.media import get_media_id


def spam_report_keyboard(rows: list, counts: dict) -> list:
    """The buttons of a spam log message with fresh vote counts"""
    keyboard = []
    for row in rows:
        match = SPAM_VOTE_REGEX.match(row[0].data or b"")
        if match is None:
            keyboard.append([copy_button(button) for button in row])
            continue
        number = int(match.group(2))
        url = next((button.url for button in row if button.url), PRIVATE_CHAT_LINK)
        upvotes, downvotes = counts[number]
        keyboard.append(
            spam_vote_row(number, entry_prefix(number, len(rows)), url, upvotes, downvotes)
        )
    return keyboard


@events.register(events.CallbackQuery(pattern=SPAM_VOTE_REGEX))
async def spam_vote_callback(event):
    if event.chat_id != event.client.settings.spam_log_channel:
        return await event.answer()
    match = SPAM_VOTE_REGEX.match(event.data)
    is_upvote = match.group(1) == b"up"
    report_index = int(match.group(2))
    db = event.client.db
    if not await db.add_vote(event.message_id, report_index, event.sender_id, is_upvote):
        return await event.answer("You've already casted your vote.")

    # Counts come from the database and edits of one message wait for each
    # other, so votes at the same time are never lost
    async with message_lock(event.chat_id, event.message_id):
        counts = await db.get_vote_counts(event.message_id)
        message = await event.get_message()
        rows = await message.get_buttons() or []
        # edit() also answers the callback query
        await event.edit(buttons=spam_report_keyboard(rows, counts))


@events.register(events.CallbackQuery(pattern=LEGACY_SPAM_VOTE_REGEX))
async def legacy_spam_vote_callback(event):
    await event.answer("This report is from an older version and takes no votes.")


async def can_review_nsfw(event) -> bool:
    """Sudo users, in the NSFW log channel. Answers everyone else."""
    if event.chat_id != event.client.settings.nsfw_log_channel:
        await event.answer()
        return False
    if event.sender_id not in event.client.settings.sudoers:
        await event.answer("Only the bot owners can review NSFW reports.")
        return False
    return True


@events.register(events.CallbackQuery(pattern=NSFW_VOTE_REGEX))
async def nsfw_vote_callback(event):
    if not await can_review_nsfw(event):
        return
    match = NSFW_VOTE_REGEX.match(event.data)
    is_upvote = match.group(1) == b"up"

    async with message_lock(event.chat_id, event.message_id):
        notice = await event.get_message()
        rows = await notice.get_buttons() or []
        reviewed_row = next(
            (row for row in rows if any(button.data == event.data for button in row)), None
        )
        if reviewed_row is None:
            return await event.answer("This report was already reviewed.")

        if is_upvote:
            verdict = "correct"
        else:
            await event.client.db.ignore_media(int(match.group(2)))
            verdict = "incorrect, this file won't be flagged again"
        prefix_match = ENTRY_PREFIX_REGEX.match(reviewed_row[0].text)
        prefix = prefix_match.group(1) if prefix_match else ""
        reviewer = await event.get_sender()
        remaining = [
            [copy_button(button) for button in row] for row in rows if row is not reviewed_row
        ]
        # Only the reviewed entry's buttons go away
        await event.edit(
            f"{notice.text}\n{prefix}<b>Reviewed:</b> marked {verdict} by {mention(reviewer)}",
            buttons=remaining or None,
        )


@events.register(events.CallbackQuery(pattern=LEGACY_NSFW_VOTE_REGEX))
async def legacy_nsfw_vote_callback(event):
    """Old reports hold a copy of the media: either answer deletes it"""
    if not await can_review_nsfw(event):
        return
    if LEGACY_NSFW_VOTE_REGEX.match(event.data).group(1) == b"down":
        media_id = get_media_id(await event.get_message())
        if media_id is not None:
            await event.client.db.ignore_media(media_id)
    await event.delete()
    await event.answer()
