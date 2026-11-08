"""Vote buttons under the reports in the log channels.

Spam reports: anyone in the spam log channel can vote once per report.
NSFW notices: only sudo users review them, "Incorrect" stops the media
from being flagged again."""
import re

from telethon import events

from spr.core import ikb
from spr.core.entities import PRIVATE_CHAT_LINK, mention
from spr.utils.media import get_media_id

SPAM_VOTE_REGEX = re.compile(rb"^(up|down)vote_spam$")
# Notices carry the media ID: (up|down)vote_nsfw_<media id>
NSFW_VOTE_REGEX = re.compile(rb"^(up|down)vote_nsfw_(-?\d{1,20})$")
# Reports from older versions were copies of the media, without the ID
LEGACY_NSFW_VOTE_REGEX = re.compile(rb"^(up|down)vote_nsfw$")


async def report_chat_link(message) -> str:
    """The "Chat" button URL of a spam report"""
    rows = await message.get_buttons() or []
    for row in rows:
        for button in row:
            if button.url:
                return button.url
    return PRIVATE_CHAT_LINK


@events.register(events.CallbackQuery(pattern=SPAM_VOTE_REGEX))
async def spam_vote_callback(event):
    if event.chat_id != event.client.settings.spam_log_channel:
        return await event.answer()
    is_upvote = SPAM_VOTE_REGEX.match(event.data).group(1) == b"up"
    db = event.client.db
    report_id = event.message_id
    if not await db.add_vote(report_id, event.sender_id, is_upvote):
        return await event.answer("You've already casted your vote.")

    # Counts come from the database, so votes at the same time are not lost
    upvotes, downvotes = await db.get_vote_counts(report_id)
    message = await event.get_message()
    keyboard = ikb(
        {
            f"Correct ({upvotes})": "upvote_spam",
            f"Incorrect ({downvotes})": "downvote_spam",
            "Chat": await report_chat_link(message),
        }
    )
    # edit() also answers the callback query
    await event.edit(buttons=keyboard)


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
    if is_upvote:
        verdict = "correct"
    else:
        await event.client.db.ignore_media(int(match.group(2)))
        verdict = "incorrect, this file won't be flagged again"

    reviewer = await event.get_sender()
    notice = await event.get_message()
    # Editing without buttons removes them, so a notice is reviewed once
    await event.edit(
        f"{notice.text}\n<b>Reviewed:</b> marked {verdict} by {mention(reviewer)}"
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
