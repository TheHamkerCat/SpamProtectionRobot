import unittest
from datetime import datetime

from telethon.errors import ChatAdminRequiredError
from telethon.tl import types

from tests.fakes import (ADMIN_ID, BASIC_GROUP_ID, LINKED_CHANNEL_ID,
                         SUDO_ID, SUPERGROUP_ID, USER_ID, FakeClient)

NOW = datetime(2026, 1, 1)
RIGHTS = types.ChatAdminRights(delete_messages=True)


class AdminCacheTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = FakeClient()

    async def test_basic_group_only_admins_are_privileged(self):
        # For basic groups Telegram returns every member, admin filter or not
        self.client.participants[BASIC_GROUP_ID] = [
            (USER_ID, types.ChatParticipant(USER_ID, 1, NOW)),
            (ADMIN_ID, types.ChatParticipantAdmin(ADMIN_ID, 1, NOW)),
            (4000, types.ChatParticipantCreator(4000)),
        ]
        admins = self.client.admins
        self.assertTrue(await admins.is_privileged(BASIC_GROUP_ID, ADMIN_ID))
        self.assertTrue(await admins.is_privileged(BASIC_GROUP_ID, 4000))
        self.assertFalse(await admins.is_privileged(BASIC_GROUP_ID, USER_ID))

    async def test_supergroup_special_senders(self):
        self.client.participants[SUPERGROUP_ID] = [
            (ADMIN_ID, types.ChannelParticipantAdmin(ADMIN_ID, 1, NOW, RIGHTS)),
        ]
        self.client.linked_channels[SUPERGROUP_ID] = 9999999999
        admins = self.client.admins
        self.assertTrue(await admins.is_privileged(SUPERGROUP_ID, SUDO_ID))
        self.assertTrue(await admins.is_privileged(SUPERGROUP_ID, ADMIN_ID))
        # Anonymous admins post as the group itself
        self.assertTrue(await admins.is_privileged(SUPERGROUP_ID, SUPERGROUP_ID))
        self.assertTrue(await admins.is_privileged(SUPERGROUP_ID, LINKED_CHANNEL_ID))
        # Any other channel (a user posting "as channel") is not privileged
        self.assertFalse(await admins.is_privileged(SUPERGROUP_ID, -1005555555555))
        self.assertFalse(await admins.is_privileged(SUPERGROUP_ID, USER_ID))

    async def test_cache_and_invalidation(self):
        self.client.participants[SUPERGROUP_ID] = []
        admins = self.client.admins
        self.assertFalse(await admins.is_privileged(SUPERGROUP_ID, ADMIN_ID))
        promoted = types.ChannelParticipantAdmin(ADMIN_ID, 1, NOW, RIGHTS)
        self.client.participants[SUPERGROUP_ID] = [(ADMIN_ID, promoted)]
        # Still cached
        self.assertFalse(await admins.is_privileged(SUPERGROUP_ID, ADMIN_ID))
        await admins._on_participant_update(
            types.UpdateChannelParticipant(
                channel_id=1234567890, date=NOW, actor_id=1, user_id=ADMIN_ID, qts=0,
                prev_participant=types.ChannelParticipant(ADMIN_ID, NOW),
                new_participant=promoted,
            )
        )
        self.assertTrue(await admins.is_privileged(SUPERGROUP_ID, ADMIN_ID))

    async def test_fetch_errors_are_raised(self):
        self.client.participants_error = ChatAdminRequiredError(request=None)
        with self.assertRaises(ChatAdminRequiredError):
            await self.client.admins.is_privileged(SUPERGROUP_ID, USER_ID)

    def test_attach_registers_raw_handler(self):
        self.client.admins.attach()
        self.assertEqual(len(self.client.handlers), 1)
