"""Sudo users, admins, anonymous admins and the group's linked channel
are never checked or punished.

Each chat's list costs two Telegram calls to fetch, so with tens of
thousands of chats the lists are:
- kept for hours (promotions and demotions refresh them right away),
- stored in Postgres, so a restart does not refetch every chat at once,
- refreshed in the background while the old list keeps working,
- fetched once per chat at a time, at most ADMIN_FETCHES_PER_SECOND.
"""
import asyncio
import logging
import random
from datetime import datetime, timezone
from time import monotonic

from telethon import events, functions, utils
from telethon.errors import RPCError
from telethon.tl import types

from spr.ratelimit import RateLimiter

logger = logging.getLogger(__name__)

# Seconds a fetched list is used before it is refreshed
ADMIN_CACHE_TTL = 6 * 3600
# Each list lasts 80-120 % of the TTL, so lists fetched together
# (e.g. after a restart) don't all expire together
ADMIN_CACHE_JITTER = 0.2
# Each fetch is 2 Telegram calls (admin list + linked channel)
ADMIN_FETCHES_PER_SECOND = 5
# After a failed fetch, the chat is not fetched again for this long
FETCH_RETRY_SECONDS = 600

# Admin types in supergroups (Channel*) and in basic groups (Chat*)
ADMIN_PARTICIPANT_TYPES = (
    types.ChannelParticipantAdmin,
    types.ChannelParticipantCreator,
    types.ChatParticipantAdmin,
    types.ChatParticipantCreator,
)


def is_admin_participant(participant) -> bool:
    return isinstance(participant, ADMIN_PARTICIPANT_TYPES)


class _Entry:
    __slots__ = ("ids", "expires_at")

    def __init__(self, ids: frozenset, expires_at: float):
        self.ids = ids
        self.expires_at = expires_at

    @property
    def expired(self) -> bool:
        return monotonic() >= self.expires_at


class AdminCache:
    def __init__(
        self,
        client,
        ttl: float = ADMIN_CACHE_TTL,
        fetches_per_second: float = ADMIN_FETCHES_PER_SECOND,
        rng: random.Random = None,
    ):
        self._client = client
        self._ttl = ttl
        self._limiter = RateLimiter(fetches_per_second)
        self._rng = rng or random.Random()
        self._entries = {}  # chat_id: _Entry
        self._fetches = {}  # chat_id: running fetch task
        self._failures = {}  # chat_id: (retry_at, error)

    async def is_privileged(self, chat_id: int, sender_id: int) -> bool:
        """Raises telethon RPCError if the chat's list was never fetched and
        can't be fetched now"""
        if sender_id in self._client.settings.sudoers:
            return True
        return sender_id in await self.privileged_ids(chat_id)

    async def privileged_ids(self, chat_id: int) -> frozenset:
        entry = self._entries.get(chat_id)
        if entry is None:
            entry = await self._load_stored(chat_id)
        if entry is None:
            # Nothing known about this chat: wait for the first fetch
            return await asyncio.shield(self._start_fetch(chat_id))
        if entry.expired:
            self._refresh_in_background(chat_id)
        # A stale list is still right almost always, and is used while
        # the new one is fetched
        return entry.ids

    def invalidate(self, chat_id: int):
        """Someone was promoted or demoted: fetch the list again now"""
        entry = self._entries.get(chat_id)
        if entry is not None:
            entry.expires_at = 0
        self._failures.pop(chat_id, None)
        self._refresh_in_background(chat_id)

    def attach(self):
        """Refresh a chat's list when someone there is promoted or demoted"""
        self._client.add_event_handler(
            self._on_participant_update,
            events.Raw(
                types=(types.UpdateChannelParticipant, types.UpdateChatParticipantAdmin)
            ),
        )

    async def _on_participant_update(self, update):
        # events.ChatAction ignores promotions and demotions, so raw updates
        # are used: UpdateChatParticipantAdmin for basic groups,
        # UpdateChannelParticipant for supergroups.
        if isinstance(update, types.UpdateChatParticipantAdmin):
            self.invalidate(utils.get_peer_id(types.PeerChat(update.chat_id)))
            return
        was_admin = is_admin_participant(update.prev_participant)
        is_admin = is_admin_participant(update.new_participant)
        if was_admin == is_admin:
            return
        self.invalidate(utils.get_peer_id(types.PeerChannel(update.channel_id)))

    def _lifetime(self) -> float:
        return self._ttl * self._rng.uniform(1 - ADMIN_CACHE_JITTER, 1 + ADMIN_CACHE_JITTER)

    async def _load_stored(self, chat_id: int):
        stored = await self._client.db.get_privileged_ids(chat_id)
        if stored is None:
            return None
        ids, fetched_at = stored
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        entry = _Entry(ids, monotonic() + self._lifetime() - age)
        self._entries[chat_id] = entry
        return entry

    def _start_fetch(self, chat_id: int) -> asyncio.Task:
        """The running fetch of this chat, or a new one"""
        task = self._fetches.get(chat_id)
        if task is None:
            task = asyncio.ensure_future(self._fetch_and_store(chat_id))
            self._fetches[chat_id] = task
            task.add_done_callback(lambda _: self._fetches.pop(chat_id, None))
        return task

    def _refresh_in_background(self, chat_id: int):
        failure = self._failures.get(chat_id)
        if failure and monotonic() < failure[0]:
            return
        task = self._start_fetch(chat_id)
        # Background errors are logged by _fetch_and_store
        task.add_done_callback(lambda done: done.cancelled() or done.exception())

    async def _fetch_and_store(self, chat_id: int) -> frozenset:
        failure = self._failures.get(chat_id)
        if failure and monotonic() < failure[0]:
            raise failure[1]
        try:
            ids = await self._fetch(chat_id)
        except RPCError as error:
            logger.warning("Can't fetch the admins of %s: %s", chat_id, error)
            self._failures[chat_id] = (monotonic() + FETCH_RETRY_SECONDS, error)
            raise
        self._failures.pop(chat_id, None)
        self._entries[chat_id] = _Entry(ids, monotonic() + self._lifetime())
        await self._client.db.store_privileged_ids(chat_id, ids)
        return ids

    async def _fetch(self, chat_id: int) -> frozenset:
        await self._limiter.wait()
        # Anonymous admins send messages as the chat itself
        ids = {chat_id}
        # For basic groups Telegram ignores the admin filter and returns
        # every member, so the participant type is checked too.
        async for user in self._client.iter_participants(
            chat_id, filter=types.ChannelParticipantsAdmins
        ):
            if is_admin_participant(user.participant):
                ids.add(user.id)
        linked_channel_id = await self._linked_channel_id(chat_id)
        if linked_channel_id:
            ids.add(linked_channel_id)
        return frozenset(ids)

    async def _linked_channel_id(self, chat_id: int):
        """Channel whose posts are copied into this group, if any.
        Only supergroups can have one."""
        _, peer_type = utils.resolve_id(chat_id)
        if peer_type is not types.PeerChannel:
            return None
        full = await self._client(functions.channels.GetFullChannelRequest(chat_id))
        linked_chat_id = full.full_chat.linked_chat_id
        if not linked_chat_id:
            return None
        return utils.get_peer_id(types.PeerChannel(linked_chat_id))
