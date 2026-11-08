"""PostgreSQL storage. All SQL of the bot lives here."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import asyncpg

# How many recent spam predictions are kept per user, for the trust score
SPAM_HISTORY_SIZE = 50
# Trust below these values marks a user as (potential) spammer
SPAMMER_TRUST = 50
POTENTIAL_SPAMMER_TRUST = 70
# Each NSFW media counts as this many 100 % spam predictions
NSFW_SPAM_WEIGHT = 2
NSFW_REPUTATION_PENALTY = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id          BIGINT PRIMARY KEY,
    spam_data        DOUBLE PRECISION[] NOT NULL DEFAULT '{}',
    nsfw_count       INTEGER NOT NULL DEFAULT 0,
    reputation       INTEGER NOT NULL DEFAULT 0,
    blacklist_reason TEXT,
    blacklisted_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS chats (
    chat_id          BIGINT PRIMARY KEY,
    spam_enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    nsfw_enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    blacklist_reason TEXT,
    blacklisted_at   TIMESTAMPTZ
);

-- Votes on spam reports in the spam log channel, one per user and report
CREATE TABLE IF NOT EXISTS votes (
    report_message_id BIGINT NOT NULL,
    user_id           BIGINT NOT NULL,
    is_upvote         BOOLEAN NOT NULL,
    voted_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (report_message_id, user_id)
);

-- Media marked as "not NSFW" by a sudo user, never flagged again
CREATE TABLE IF NOT EXISTS ignored_media (
    media_id   BIGINT PRIMARY KEY,
    ignored_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

TABLES = ("users", "chats", "votes", "ignored_media")

# Appends EXCLUDED.spam_data to users.spam_data and keeps the last
# SPAM_HISTORY_SIZE values. Postgres arrays start at index 1.
_APPEND_SPAM_DATA = f"""
    (users.spam_data || EXCLUDED.spam_data)[
        greatest(
            cardinality(users.spam_data) + cardinality(EXCLUDED.spam_data)
            - {SPAM_HISTORY_SIZE} + 1,
            1
        ):
    ]
"""

# Table and column names come only from these fixed maps, never from
# user input, so building SQL with them is safe.
_ENTITY_TABLES = {"user": ("users", "user_id"), "chat": ("chats", "chat_id")}
_FEATURE_COLUMNS = {"spam": "spam_enabled", "nsfw": "nsfw_enabled"}


@dataclass(frozen=True)
class UserStats:
    """What the bot knows about a user. Defaults for unknown users."""

    spam_data: tuple = ()
    nsfw_count: int = 0
    reputation: int = 0
    blacklist_reason: Optional[str] = None
    blacklisted_at: Optional[datetime] = None

    @property
    def blacklisted(self) -> bool:
        return self.blacklisted_at is not None

    @property
    def trust(self) -> float:
        """100 minus the average spam prediction, 100 if there is no data"""
        if not self.spam_data:
            return 100
        return round(100 - sum(self.spam_data) / len(self.spam_data), 4)

    @property
    def is_spammer(self) -> bool:
        return self.trust < SPAMMER_TRUST

    @property
    def is_potential_spammer(self) -> bool:
        return self.trust < POTENTIAL_SPAMMER_TRUST


@dataclass(frozen=True)
class ChatSettings:
    """Settings of a chat. Defaults for unknown chats."""

    spam_enabled: bool = True
    nsfw_enabled: bool = True
    blacklist_reason: Optional[str] = None
    blacklisted_at: Optional[datetime] = None

    @property
    def blacklisted(self) -> bool:
        return self.blacklisted_at is not None


class Database:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> "Database":
        """Open a connection pool and create missing tables"""
        pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=10, command_timeout=30
        )
        database = cls(pool)
        await database.init_schema()
        return database

    async def init_schema(self):
        await self._pool.execute(SCHEMA)

    async def close(self):
        await self._pool.close()

    # Users

    async def get_user_stats(self, user_id: int) -> UserStats:
        """Read-only: unknown users get the defaults and are not stored"""
        row = await self._pool.fetchrow(
            """
            SELECT spam_data, nsfw_count, reputation,
                   blacklist_reason, blacklisted_at
            FROM users WHERE user_id = $1
            """,
            user_id,
        )
        if row is None:
            return UserStats()
        return UserStats(
            spam_data=tuple(row["spam_data"]),
            nsfw_count=row["nsfw_count"],
            reputation=row["reputation"],
            blacklist_reason=row["blacklist_reason"],
            blacklisted_at=row["blacklisted_at"],
        )

    async def add_spam_prediction(self, user_id: int, spam_value: float):
        """Store the spam prediction of a user's message"""
        await self._pool.execute(
            f"""
            INSERT INTO users (user_id, spam_data) VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET spam_data = {_APPEND_SPAM_DATA}
            """,
            user_id,
            [float(spam_value)],
        )

    async def record_nsfw(self, user_id: int):
        """Count an NSFW media: lowers reputation and trust of the user"""
        await self._pool.execute(
            f"""
            INSERT INTO users (user_id, nsfw_count, reputation, spam_data)
            VALUES ($1, 1, -$2::integer, $3)
            ON CONFLICT (user_id) DO UPDATE SET
                nsfw_count = users.nsfw_count + 1,
                reputation = users.reputation - $2::integer,
                spam_data = {_APPEND_SPAM_DATA}
            """,
            user_id,
            NSFW_REPUTATION_PENALTY,
            [100.0] * NSFW_SPAM_WEIGHT,
        )

    # Chats

    async def get_chat_settings(self, chat_id: int) -> ChatSettings:
        """Read-only: unknown chats get the defaults and are not stored"""
        row = await self._pool.fetchrow(
            """
            SELECT spam_enabled, nsfw_enabled, blacklist_reason, blacklisted_at
            FROM chats WHERE chat_id = $1
            """,
            chat_id,
        )
        if row is None:
            return ChatSettings()
        return ChatSettings(**dict(row))

    async def set_chat_feature(self, chat_id: int, feature: str, enabled: bool) -> bool:
        """Turn "spam" or "nsfw" detection on/off. False if it already was."""
        column = _FEATURE_COLUMNS[feature]
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO chats (chat_id) VALUES ($1) ON CONFLICT DO NOTHING",
                chat_id,
            )
            changed = await conn.fetchval(
                f"""
                UPDATE chats SET {column} = $2
                WHERE chat_id = $1 AND {column} IS DISTINCT FROM $2
                RETURNING true
                """,
                chat_id,
                enabled,
            )
        return bool(changed)

    # Blacklist, kind is "user" or "chat"

    async def blacklist(self, kind: str, entity_id: int, reason: str) -> bool:
        """False if it was already blacklisted"""
        table, key = _ENTITY_TABLES[kind]
        changed = await self._pool.fetchval(
            f"""
            INSERT INTO {table} ({key}, blacklist_reason, blacklisted_at)
            VALUES ($1, $2, now())
            ON CONFLICT ({key}) DO UPDATE SET
                blacklist_reason = EXCLUDED.blacklist_reason,
                blacklisted_at = EXCLUDED.blacklisted_at
            WHERE {table}.blacklisted_at IS NULL
            RETURNING true
            """,
            entity_id,
            reason,
        )
        return bool(changed)

    async def whitelist(self, kind: str, entity_id: int) -> bool:
        """False if it was not blacklisted"""
        table, key = _ENTITY_TABLES[kind]
        changed = await self._pool.fetchval(
            f"""
            UPDATE {table} SET blacklist_reason = NULL, blacklisted_at = NULL
            WHERE {key} = $1 AND blacklisted_at IS NOT NULL
            RETURNING true
            """,
            entity_id,
        )
        return bool(changed)

    # Votes on spam reports

    async def add_vote(self, report_message_id: int, user_id: int, is_upvote: bool) -> bool:
        """Store a vote and reward the voter with +1 reputation.
        False if the user already voted on this report."""
        async with self._pool.acquire() as conn, conn.transaction():
            inserted = await conn.fetchval(
                """
                INSERT INTO votes (report_message_id, user_id, is_upvote)
                VALUES ($1, $2, $3)
                ON CONFLICT DO NOTHING
                RETURNING true
                """,
                report_message_id,
                user_id,
                is_upvote,
            )
            if not inserted:
                return False
            await conn.execute(
                """
                INSERT INTO users (user_id, reputation) VALUES ($1, 1)
                ON CONFLICT (user_id) DO UPDATE SET reputation = users.reputation + 1
                """,
                user_id,
            )
        return True

    async def get_vote_counts(self, report_message_id: int) -> tuple:
        """(upvotes, downvotes) of a spam report"""
        row = await self._pool.fetchrow(
            """
            SELECT count(*) FILTER (WHERE is_upvote) AS upvotes,
                   count(*) FILTER (WHERE NOT is_upvote) AS downvotes
            FROM votes WHERE report_message_id = $1
            """,
            report_message_id,
        )
        return row["upvotes"], row["downvotes"]

    # NSFW false positives

    async def ignore_media(self, media_id: int):
        await self._pool.execute(
            "INSERT INTO ignored_media (media_id) VALUES ($1) ON CONFLICT DO NOTHING",
            media_id,
        )

    async def is_media_ignored(self, media_id: int) -> bool:
        return bool(
            await self._pool.fetchval(
                "SELECT true FROM ignored_media WHERE media_id = $1", media_id
            )
        )

    # Backup

    async def export_csv(self) -> dict:
        """Every table as CSV bytes, {table name: data}"""
        exported = {}
        async with self._pool.acquire() as conn:
            for table in TABLES:
                chunks = []

                async def collect(data: bytes, chunks=chunks):
                    chunks.append(data)

                await conn.copy_from_table(
                    table, output=collect, format="csv", header=True
                )
                exported[table] = b"".join(chunks)
        return exported
