"""sudo users, admins, anonymous admins and the group's linked channel are never checked or punished"""
from time import monotonic

from telethon import events, functions, utils
from telethon.tl import types

# Seconds to keep the privileged list of a chat before fetching it again
ADMIN_CACHE_TTL = 300

# Admin types in supergroups (Channel*) and in basic groups (Chat*)
ADMIN_PARTICIPANT_TYPES = (
    types.ChannelParticipantAdmin,
    types.ChannelParticipantCreator,
    types.ChatParticipantAdmin,
    types.ChatParticipantCreator,
)


def is_admin_participant(participant) -> bool:
    return isinstance(participant, ADMIN_PARTICIPANT_TYPES)


class AdminCache:
    def __init__(self, client, ttl: float = ADMIN_CACHE_TTL):
        self._client = client
        self._ttl = ttl
        # {chat_id: (fetched_at, frozenset of privileged sender IDs)}
        self._entries = {}

    async def is_privileged(self, chat_id: int, sender_id: int) -> bool:
        """Raises telethon RPCError if the admin list can't be fetched"""
        if sender_id in self._client.settings.sudoers:
            return True
        return sender_id in await self.privileged_ids(chat_id)

    async def privileged_ids(self, chat_id: int) -> frozenset:
        cached = self._entries.get(chat_id)
        if cached and monotonic() - cached[0] < self._ttl:
            return cached[1]
        ids = await self._fetch(chat_id)
        self._entries[chat_id] = (monotonic(), ids)
        return ids

    def invalidate(self, chat_id: int):
        self._entries.pop(chat_id, None)

    def attach(self):
        """Clear the cache of a chat when someone there is promoted or demoted"""
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

    async def _fetch(self, chat_id: int) -> frozenset:
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
