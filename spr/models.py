"""Database tables. Changing a model needs an Alembic migration:

    alembic revision --autogenerate -m "what changed"

Primary keys are Telegram IDs, set by the bot, so they have no
auto-increment sequence (autoincrement=False).

Each column has a Python default (known right after an insert, without
reloading the row) and the same server default (for the migration).
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (BigInteger, Boolean, DateTime, Double, Integer,
                        LargeBinary, SmallInteger, Text, text)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    # Spam predictions (0-100) of the user's last messages, oldest first
    spam_data: Mapped[list] = mapped_column(
        ARRAY(Double), default=list, server_default=text("'{}'")
    )
    nsfw_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    reputation: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    blacklist_reason: Mapped[Optional[str]] = mapped_column(Text)
    blacklisted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Chat(Base):
    __tablename__ = "chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    spam_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    nsfw_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    # Off: spam/NSFW is deleted and spammers banned without a message in the chat
    alerts_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    blacklist_reason: Mapped[Optional[str]] = mapped_column(Text)
    blacklisted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Sender IDs never punished here (admins, the chat itself for anonymous
    # admins, the linked channel), kept so restarts don't refetch them all
    privileged_ids: Mapped[Optional[list]] = mapped_column(ARRAY(BigInteger))
    privileged_ids_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Vote(Base):
    """A vote on a spam report in the spam log channel, one per user and report.
    One log message holds several reports, report_index (1, 2, ...) tells
    which one."""

    __tablename__ = "votes"

    report_message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_index: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    is_upvote: Mapped[bool] = mapped_column(Boolean)
    voted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=text("now()")
    )


class IgnoredMedia(Base):
    """Media marked as "not NSFW" by a sudo user, never flagged again"""

    __tablename__ = "ignored_media"

    media_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    ignored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=text("now()")
    )


class CachedSpamResult(Base):
    """ARQ spam results of short texts, so common messages ("good morning
    everyone") are scanned once. Only a hash of the text is stored, never
    the text itself. A result from another ARQ model version counts as missing."""

    __tablename__ = "spam_results"

    text_hash: Mapped[bytes] = mapped_column(LargeBinary(16), primary_key=True)
    model_version: Mapped[str] = mapped_column(Text)
    spam: Mapped[float] = mapped_column(Double)
    category: Mapped[str] = mapped_column(Text)
    harm: Mapped[int] = mapped_column(SmallInteger)
    abusive: Mapped[float] = mapped_column(Double)
    language: Mapped[str] = mapped_column(Text)
    # Updated at most once a day; the oldest rows are deleted first
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=text("now()"), index=True
    )


class SqliteImport(Base):
    """Imports of an old SQLite database (/migrate_sqlite), one row per file.
    Users are imported in chunks; users_done_through is the last imported
    SQLite rowid, so an interrupted import continues where it stopped."""

    __tablename__ = "sqlite_imports"

    file_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=text("now()")
    )
    chats_done: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    users_done_through: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

