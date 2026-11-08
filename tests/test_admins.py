import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from telethon.errors import ChatAdminRequiredError
from telethon.tl import types

from spr.admins import ADMIN_CACHE_TTL, AdminCache
from tests.fakes import (ADMIN_ID, BASIC_GROUP_ID, LINKED_CHANNEL_ID,
                         SUDO_ID, SUPERGROUP_ID, USER_ID, FakeClient)

NOW = datetime(2026, 1, 1)
RIGHTS = types.ChatAdminRights(delete_messages=True)


def channel_admin(user_id):
    return (user_id, types.ChannelParticipantAdmin(user_id, 1, NOW, RIGHTS))


async def settle():
    """Let background refreshes finish"""
    for _ in range(5):
        await asyncio.sleep(0)


class AdminCacheTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = FakeClient()

    def restarted_cache(self):
        """A new AdminCache with the same storage, like after a restart"""
        return AdminCache(self.client, fetches_per_second=10_000)

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
        self.client.participants[SUPERGROUP_ID] = [channel_admin(ADMIN_ID)]
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

    async def test_one_fetch_for_many_messages_at_once(self):
        self.client.participants[SUPERGROUP_ID] = [channel_admin(ADMIN_ID)]
        results = await asyncio.gather(
            *(self.client.admins.is_privileged(SUPERGROUP_ID, ADMIN_ID) for _ in range(20))
        )
        self.assertTrue(all(results))
        self.assertEqual(self.client.fetch_count, 1)

    async def test_lists_survive_a_restart(self):
        self.client.participants[SUPERGROUP_ID] = [channel_admin(ADMIN_ID)]
        await self.client.admins.is_privileged(SUPERGROUP_ID, ADMIN_ID)
        self.assertIn(SUPERGROUP_ID, self.client.db.stored)

        self.assertTrue(await self.restarted_cache().is_privileged(SUPERGROUP_ID, ADMIN_ID))
        self.assertEqual(self.client.fetch_count, 1)

    async def test_stale_list_is_used_while_refreshing(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=ADMIN_CACHE_TTL * 2)
        self.client.db.stored[SUPERGROUP_ID] = (frozenset({SUPERGROUP_ID, ADMIN_ID}), old)
        self.client.participants[SUPERGROUP_ID] = [channel_admin(4000)]
        admins = self.restarted_cache()

        # The old list answers right away, the new one is fetched meanwhile
        self.assertTrue(await admins.is_privileged(SUPERGROUP_ID, ADMIN_ID))
        await settle()
        self.assertEqual(self.client.fetch_count, 1)
        self.assertFalse(await admins.is_privileged(SUPERGROUP_ID, ADMIN_ID))
        self.assertTrue(await admins.is_privileged(SUPERGROUP_ID, 4000))

    async def test_promotion_refreshes_the_list(self):
        self.client.participants[SUPERGROUP_ID] = []
        admins = self.client.admins
        self.assertFalse(await admins.is_privileged(SUPERGROUP_ID, ADMIN_ID))
        promoted = channel_admin(ADMIN_ID)
        self.client.participants[SUPERGROUP_ID] = [promoted]
        await admins._on_participant_update(
            types.UpdateChannelParticipant(
                channel_id=1234567890, date=NOW, actor_id=1, user_id=ADMIN_ID, qts=0,
                prev_participant=types.ChannelParticipant(ADMIN_ID, NOW),
                new_participant=promoted[1],
            )
        )
        await settle()
        self.assertTrue(await admins.is_privileged(SUPERGROUP_ID, ADMIN_ID))

    async def test_failed_fetch_is_not_retried_at_once(self):
        self.client.participants_error = ChatAdminRequiredError(request=None)
        for _ in range(3):
            with self.assertRaises(ChatAdminRequiredError):
                await self.client.admins.is_privileged(SUPERGROUP_ID, USER_ID)
        self.assertEqual(self.client.fetch_count, 1)

    async def test_expiry_is_spread_out(self):
        admins = self.client.admins
        lifetimes = {round(admins._lifetime()) for _ in range(50)}
        self.assertGreater(len(lifetimes), 10)
        self.assertTrue(all(0.8 * ADMIN_CACHE_TTL <= value <= 1.2 * ADMIN_CACHE_TTL for value in lifetimes))

    def test_attach_registers_raw_handler(self):
        self.client.admins.attach()
        self.assertEqual(len(self.client.handlers), 1)
