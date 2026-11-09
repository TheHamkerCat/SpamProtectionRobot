"""Benchmarks for the parts that limit how many chats one bot process
can serve. Run with:

    docker compose -f compose.test.yml run --rm --build benchmark

1. Telethon's SQLite session: the cost of storing entities per update,
   and of saving update states, which costs one row per channel.
2. The message pipeline: messages per second through the watcher with a
   fake Telegram, a fake ARQ that answers in ARQ_DELAY seconds and the
   real Postgres.
"""
import asyncio
import os
import random
import tempfile
import time
from datetime import datetime

from telethon.sessions import SQLiteSession
from telethon.tl import types

ARQ_DELAY = 0.05


def timed(function, *args):
    started = time.perf_counter()
    function(*args)
    return time.perf_counter() - started


def update_entities(user_id: int, channel_id: int):
    """The users and chats Telegram sends along with one group message"""
    user = types.User(id=user_id, access_hash=user_id, first_name="User", username=f"user{user_id}")
    channel = types.Channel(
        id=channel_id, title="Group", photo=types.ChatPhotoEmpty(), date=None,
        access_hash=channel_id, megagroup=True, username=None,
    )
    return types.contacts.ResolvedPeer(None, [user], [channel])


def benchmark_session():
    print("Telethon SQLite session")
    session = SQLiteSession(os.path.join(tempfile.mkdtemp(), "bench"))
    updates = [update_entities(1000 + i, 5000 + i % 10_000) for i in range(20_000)]

    elapsed = timed(lambda: [session.process_entities(update) for update in updates])
    print(f"  store entities: {elapsed / len(updates) * 1e6:.0f} us per update")
    print(f"  commit after {len(updates)} updates: {timed(session.save) * 1000:.0f} ms")

    now = datetime.now()
    for channels in (1_000, 10_000, 50_000):
        def save_states():
            for channel_id in range(channels):
                session.set_update_state(channel_id, types.updates.State(1, 0, now, 0, unread_count=0))
            session.save()

        print(f"  save update states of {channels} channels: {timed(save_states) * 1000:.0f} ms")


async def benchmark_pipeline(messages: int = 3000, chats: int = 100, users: int = 500):
    # Imported here: the session benchmark must not need a database
    from spr.app import SCAN_QUEUE_SIZE, SCAN_WORKERS
    from spr.db import Database
    from spr.modules.watcher import check_message
    from spr.workqueue import WorkQueue
    from tests.fakes import (DATABASE_URL, FakeArq, FakeClient,
                             FakeMessageEvent, make_user, setup_bot_username,
                             spam_result)

    class SlowArq(FakeArq):
        async def nlp(self, text):
            await asyncio.sleep(ARQ_DELAY)
            return await super().nlp(text)

    setup_bot_username()
    db = await Database.connect(DATABASE_URL)
    await db.migrate()
    chat_ids = [-1001000000000 - i for i in range(chats)]

    async def run(name: str, raid_share: float):
        client = FakeClient(db=db, arq=SlowArq(spam=spam_result(1.0)))
        for chat_id in chat_ids:
            client.participants[chat_id] = []
        pick = random.Random(1)
        events = []
        for i in range(messages):
            if pick.random() < raid_share:
                text = "join t.me/cheap_followers for 1000 followers"
            else:
                text = f"message number {i} with some normal text in it"
            sender = make_user(10_000 + pick.randrange(users))
            events.append(FakeMessageEvent(client, sender, text, chat_id=pick.choice(chat_ids), message_id=i))

        queue = WorkQueue(check_message, workers=SCAN_WORKERS, max_size=SCAN_QUEUE_SIZE)
        started = time.perf_counter()
        queue.start()
        accepted = sum(queue.submit(event) for event in events)
        await queue.join()
        elapsed = time.perf_counter() - started
        await queue.stop()
        print(
            f"  {name}: {accepted / elapsed:.0f} messages/s, "
            f"{len(client.arq.nlp_calls)} ARQ calls for {accepted} messages, "
            f"{queue.dropped} dropped, {client.fetch_count} admin list fetches"
        )

    print(
        f"Pipeline: {messages} messages, {chats} chats, {users} users, "
        f"{SCAN_WORKERS} workers, ARQ answers in {ARQ_DELAY * 1000:.0f} ms"
    )
    await run("all different texts", raid_share=0)
    await run("half the messages are one spam text", raid_share=0.5)
    await db.close()


if __name__ == "__main__":
    benchmark_session()
    asyncio.run(benchmark_pipeline())
