"""PostgreSQL storage through SQLAlchemy ORM sessions.

Every write loads its row with SELECT ... FOR UPDATE, so two messages
from the same user at the same time wait for each other instead of
overwriting each other's changes.
"""
from dataclasses import dataclass
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)

from spr.arq import SpamResult
from spr.models import (CachedSpamResult, Chat, IgnoredMedia, SqliteImport, User,
                        Vote, utc_now)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# How many recent spam predictions are kept per user, for the trust score
SPAM_HISTORY_SIZE = 50
# Trust below these values marks a user as (potential) spammer
SPAMMER_TRUST = 50
POTENTIAL_SPAMMER_TRUST = 70
# Each NSFW media counts as this many 100 % spam predictions
NSFW_SPAM_WEIGHT = 2
NSFW_REPUTATION_PENALTY = 10
# A cached spam result's last use is written at most this often, so cache
# hits don't each cost a write
SPAM_RESULT_TOUCH_INTERVAL = timedelta(days=1)

_ENTITY_MODELS = {"user": User, "chat": Chat}
_FEATURE_ATTRIBUTES = {
    "spam": "spam_enabled",
    "nsfw": "nsfw_enabled",
    "alerts": "alerts_enabled",
}


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
    alerts_enabled: bool = True
    blacklist_reason: Optional[str] = None
    blacklisted_at: Optional[datetime] = None

    @property
    def blacklisted(self) -> bool:
        return self.blacklisted_at is not None


@dataclass(frozen=True)
class ImportState:
    """Progress of an old SQLite database import (spr/sqlite_import.py)"""

    chats_done: bool
    users_done_through: int
    finished_at: Optional[datetime]


def async_database_url(url: str) -> URL:
    """DATABASE_URL as an SQLAlchemy asyncpg URL. Accepts postgres://
    (Heroku) and postgresql://, and asyncpg's name for sslmode."""
    parsed = make_url(url)
    query = dict(parsed.query)
    sslmode = query.pop("sslmode", None)
    if sslmode:
        query["ssl"] = sslmode
    return parsed.set(drivername="postgresql+asyncpg", query=query)


def alembic_config() -> Config:
    """Alembic settings without alembic.ini, so the working directory does not matter"""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def _upgrade_to_head(connection):
    config = alembic_config()
    # env.py uses this connection instead of opening its own
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


async def _lock_or_create(session: AsyncSession, model, primary_key: int):
    """The row, locked until the session commits. Created if missing.

    ON CONFLICT DO NOTHING: when another message creates the same row at
    the same moment, nothing fails (and Postgres logs no error); the
    lock below then waits for the other transaction and takes its row."""
    row = await session.get(model, primary_key, with_for_update=True)
    if row is not None:
        return row
    key_name = model.__mapper__.primary_key[0].key
    await session.execute(
        pg_insert(model).values({key_name: primary_key}).on_conflict_do_nothing()
    )
    return await session.get(model, primary_key, with_for_update=True, populate_existing=True)


class Database:
    def __init__(self, engine):
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    async def connect(cls, database_url: str) -> "Database":
        engine = create_async_engine(
            async_database_url(database_url),
            # Room for every scan worker (spr/scanner.py) plus commands
            pool_size=20,
            max_overflow=5,
            pool_pre_ping=True,
            connect_args={"command_timeout": 30},
        )
        return cls(engine)

    async def migrate(self):
        """Bring the schema up to date (alembic upgrade head)"""
        async with self._engine.begin() as connection:
            await connection.run_sync(_upgrade_to_head)

    async def close(self):
        await self._engine.dispose()

    # Users

    async def get_user_stats(self, user_id: int) -> UserStats:
        """Read-only: unknown users get the defaults and are not stored"""
        async with self._sessions() as session:
            user = await session.get(User, user_id)
        if user is None:
            return UserStats()
        return UserStats(
            spam_data=tuple(user.spam_data),
            nsfw_count=user.nsfw_count,
            reputation=user.reputation,
            blacklist_reason=user.blacklist_reason,
            blacklisted_at=user.blacklisted_at,
        )

    async def add_spam_prediction(self, user_id: int, spam_value: float):
        """Store the spam prediction of a user's message"""
        await self._append_spam_data(user_id, [float(spam_value)])

    async def record_nsfw(self, user_id: int):
        """Count an NSFW media: lowers reputation and trust of the user"""
        await self._append_spam_data(
            user_id, [100.0] * NSFW_SPAM_WEIGHT, nsfw_media=True
        )

    async def _append_spam_data(self, user_id: int, values: list, nsfw_media=False):
        async with self._sessions.begin() as session:
            user = await _lock_or_create(session, User, user_id)
            # A new list (not an in-place change), so SQLAlchemy sees the update
            user.spam_data = (list(user.spam_data) + values)[-SPAM_HISTORY_SIZE:]
            if nsfw_media:
                user.nsfw_count += 1
                user.reputation -= NSFW_REPUTATION_PENALTY

    # Chats

    async def get_chat_settings(self, chat_id: int) -> ChatSettings:
        """Read-only: unknown chats get the defaults and are not stored"""
        async with self._sessions() as session:
            chat = await session.get(Chat, chat_id)
        if chat is None:
            return ChatSettings()
        return ChatSettings(
            spam_enabled=chat.spam_enabled,
            nsfw_enabled=chat.nsfw_enabled,
            alerts_enabled=chat.alerts_enabled,
            blacklist_reason=chat.blacklist_reason,
            blacklisted_at=chat.blacklisted_at,
        )

    async def get_privileged_ids(self, chat_id: int):
        """(frozenset of IDs, when they were fetched), None if never stored"""
        async with self._sessions() as session:
            chat = await session.get(Chat, chat_id)
        if chat is None or chat.privileged_ids is None:
            return None
        return frozenset(chat.privileged_ids), chat.privileged_ids_at

    async def store_privileged_ids(self, chat_id: int, ids):
        async with self._sessions.begin() as session:
            chat = await _lock_or_create(session, Chat, chat_id)
            chat.privileged_ids = sorted(ids)
            chat.privileged_ids_at = utc_now()

    async def set_chat_feature(self, chat_id: int, feature: str, enabled: bool) -> bool:
        """Turn "spam"/"nsfw" detection or "alerts" on/off. False if it already was."""
        attribute = _FEATURE_ATTRIBUTES[feature]
        async with self._sessions.begin() as session:
            chat = await _lock_or_create(session, Chat, chat_id)
            if getattr(chat, attribute) == enabled:
                return False
            setattr(chat, attribute, enabled)
        return True

    # Blacklist, kind is "user" or "chat"

    async def blacklist(self, kind: str, entity_id: int, reason: str) -> bool:
        """False if it was already blacklisted"""
        async with self._sessions.begin() as session:
            row = await _lock_or_create(session, _ENTITY_MODELS[kind], entity_id)
            if row.blacklisted_at is not None:
                return False
            row.blacklist_reason = reason
            row.blacklisted_at = utc_now()
        return True

    async def whitelist(self, kind: str, entity_id: int) -> bool:
        """False if it was not blacklisted"""
        async with self._sessions.begin() as session:
            row = await session.get(_ENTITY_MODELS[kind], entity_id, with_for_update=True)
            if row is None or row.blacklisted_at is None:
                return False
            row.blacklist_reason = None
            row.blacklisted_at = None
        return True

    # Votes on spam reports

    async def add_vote(
        self, report_message_id: int, report_index: int, user_id: int, is_upvote: bool
    ) -> bool:
        """Store a vote and reward the voter with +1 reputation.
        False if the user already voted on this report."""
        async with self._sessions.begin() as session:
            # Also covers the same user pressing twice at the same moment
            inserted = await session.scalar(
                pg_insert(Vote)
                .values(
                    report_message_id=report_message_id,
                    report_index=report_index,
                    user_id=user_id,
                    is_upvote=is_upvote,
                )
                .on_conflict_do_nothing()
                .returning(Vote.user_id)
            )
            if inserted is None:
                return False
            voter = await _lock_or_create(session, User, user_id)
            voter.reputation += 1
        return True

    async def get_vote_counts(self, report_message_id: int) -> dict:
        """{report_index: (upvotes, downvotes)} of the reports in a log message.
        Reports without votes are missing, read them as (0, 0)."""
        query = (
            select(
                Vote.report_index,
                func.count().filter(Vote.is_upvote),
                func.count().filter(Vote.is_upvote.is_(False)),
            )
            .where(Vote.report_message_id == report_message_id)
            .group_by(Vote.report_index)
        )
        async with self._sessions() as session:
            rows = (await session.execute(query)).all()
        counts = defaultdict(lambda: (0, 0))
        counts.update({index: (up, down) for index, up, down in rows})
        return counts

    # Spam results of short texts (see spr/scan_cache.py)

    async def get_spam_result(self, text_hash: bytes) -> Optional[SpamResult]:
        async with self._sessions.begin() as session:
            row = await session.get(CachedSpamResult, text_hash)
            if row is None:
                return None
            if row.last_used_at < utc_now() - SPAM_RESULT_TOUCH_INTERVAL:
                row.last_used_at = utc_now()
            return SpamResult(
                is_spam=row.is_spam,
                spam_probability=row.spam_probability,
                spam=row.spam,
                ham=row.ham,
                profanity=row.profanity,
            )

    async def store_spam_result(self, text_hash: bytes, result: SpamResult):
        # Another worker may store the same text at the same moment
        async with self._sessions.begin() as session:
            await session.execute(
                pg_insert(CachedSpamResult)
                .values(
                    text_hash=text_hash,
                    is_spam=result.is_spam,
                    spam_probability=result.spam_probability,
                    spam=result.spam,
                    ham=result.ham,
                    profanity=result.profanity,
                )
                .on_conflict_do_nothing()
            )

    async def prune_spam_results(self, max_rows: int, max_age: timedelta) -> int:
        """Delete results unused for max_age, then the least recently used
        beyond max_rows. Returns how many were deleted."""
        async with self._sessions.begin() as session:
            expired = await session.execute(
                delete(CachedSpamResult)
                .where(CachedSpamResult.last_used_at < utc_now() - max_age)
                .execution_options(synchronize_session=False)
            )
            deleted = expired.rowcount
            count = await session.scalar(select(func.count()).select_from(CachedSpamResult))
            excess = count - max_rows
            if excess > 0:
                oldest = (
                    select(CachedSpamResult.text_hash)
                    .order_by(CachedSpamResult.last_used_at)
                    .limit(excess)
                )
                trimmed = await session.execute(
                    delete(CachedSpamResult)
                    .where(CachedSpamResult.text_hash.in_(oldest.scalar_subquery()))
                    .execution_options(synchronize_session=False)
                )
                deleted += trimmed.rowcount
        return deleted

    # NSFW false positives

    async def ignore_media(self, media_id: int):
        async with self._sessions.begin() as session:
            await _lock_or_create(session, IgnoredMedia, media_id)

    async def is_media_ignored(self, media_id: int) -> bool:
        async with self._sessions() as session:
            return await session.get(IgnoredMedia, media_id) is not None

    # Import of the old SQLite database (spr/sqlite_import.py). Each chunk
    # and its progress are stored in one transaction, so an interrupted
    # import continues where it stopped and nothing is added twice.

    async def start_sqlite_import(self, file_hash: bytes) -> ImportState:
        async with self._sessions.begin() as session:
            await session.execute(
                pg_insert(SqliteImport).values(file_hash=file_hash).on_conflict_do_nothing()
            )
            record = await session.get(SqliteImport, file_hash, populate_existing=True)
            return ImportState(record.chats_done, record.users_done_through, record.finished_at)

    async def merge_old_chats(self, file_hash: bytes, chats: list):
        async with self._sessions.begin() as session:
            record = await session.get(SqliteImport, file_hash, with_for_update=True)
            if record.chats_done:
                return
            ids = sorted({chat.chat_id for chat in chats})
            if ids:
                await session.execute(
                    pg_insert(Chat).on_conflict_do_nothing(), [{"chat_id": chat_id} for chat_id in ids]
                )
                rows = await session.scalars(
                    select(Chat).where(Chat.chat_id.in_(ids)).order_by(Chat.chat_id).with_for_update()
                )
                current = {chat.chat_id: chat for chat in rows}
                for old in chats:
                    chat = current[old.chat_id]
                    # Turned off in either database stays off
                    chat.spam_enabled = chat.spam_enabled and old.spam_enabled
                    chat.nsfw_enabled = chat.nsfw_enabled and old.nsfw_enabled
                    if old.blacklisted_at and chat.blacklisted_at is None:
                        chat.blacklist_reason = old.blacklist_reason
                        chat.blacklisted_at = old.blacklisted_at
            record.chats_done = True

    async def merge_old_users(self, file_hash: bytes, users: list, last_rowid: int):
        """Merge one chunk; last_rowid is the old table's rowid it ends at"""
        async with self._sessions.begin() as session:
            record = await session.get(SqliteImport, file_hash, with_for_update=True)
            if last_rowid <= record.users_done_through:
                # Already imported by an earlier run
                return
            ids = sorted({user.user_id for user in users})
            if ids:
                await session.execute(
                    pg_insert(User).on_conflict_do_nothing(), [{"user_id": user_id} for user_id in ids]
                )
                rows = await session.scalars(
                    select(User).where(User.user_id.in_(ids)).order_by(User.user_id).with_for_update()
                )
                current = {user.user_id: user for user in rows}
                for old in users:
                    user = current[old.user_id]
                    # Old predictions are older than the new ones
                    user.spam_data = (list(old.spam_data) + list(user.spam_data))[-SPAM_HISTORY_SIZE:]
                    user.nsfw_count += old.nsfw_count
                    user.reputation += old.reputation
                    if old.blacklisted_at and user.blacklisted_at is None:
                        user.blacklist_reason = old.blacklist_reason
                        user.blacklisted_at = old.blacklisted_at
            record.users_done_through = last_rowid

    async def finish_sqlite_import(self, file_hash: bytes):
        async with self._sessions.begin() as session:
            record = await session.get(SqliteImport, file_hash, with_for_update=True)
            if record.finished_at is None:
                record.finished_at = utc_now()

