"""Buttons under log channel reports, and the callback data they send.
spr.utils.alerts builds them, spr.modules.vote reads them."""
import asyncio
import re
import weakref

from telethon import Button

# Spam reports: one row per entry, number = entry position in the message
SPAM_VOTE_REGEX = re.compile(rb"^(up|down)vote_spam_(\d{1,2})$")
# NSFW notices carry the media ID instead of a position
NSFW_VOTE_REGEX = re.compile(rb"^(up|down)vote_nsfw_(-?\d{1,20})$")
# Reports from versions before combined reports
LEGACY_SPAM_VOTE_REGEX = re.compile(rb"^(up|down)vote_spam$")
LEGACY_NSFW_VOTE_REGEX = re.compile(rb"^(up|down)vote_nsfw$")
# "#3 Correct" -> "#3 "
ENTRY_PREFIX_REGEX = re.compile(r"^(#\d+ )")


def spam_vote_row(number: int, prefix: str, chat_url: str, upvotes: int = 0, downvotes: int = 0) -> list:
    return [
        Button.inline(f"{prefix}Correct ({upvotes})", data=f"upvote_spam_{number}"),
        Button.inline(f"{prefix}Incorrect ({downvotes})", data=f"downvote_spam_{number}"),
        Button.url(f"{prefix}Chat", chat_url),
    ]


def nsfw_review_row(prefix: str, media_id: int) -> list:
    return [
        Button.inline(f"{prefix}Correct", data=f"upvote_nsfw_{media_id}"),
        Button.inline(f"{prefix}Incorrect", data=f"downvote_nsfw_{media_id}"),
    ]


def copy_button(button):
    """A button read from a message (MessageButton), as one to send again"""
    if button.url:
        return Button.url(button.text, button.url)
    return Button.inline(button.text, data=button.data)


# One lock per log message, so two votes at once can't overwrite each
# other's edit. Locks disappear when nobody holds them.
_message_locks = weakref.WeakValueDictionary()


def message_lock(chat_id: int, message_id: int) -> asyncio.Lock:
    key = (chat_id, message_id)
    lock = _message_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _message_locks[key] = lock
    return lock
