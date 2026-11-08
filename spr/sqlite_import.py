"""Import of the old bot's SQLite database (db.sqlite3) into Postgres,
merged with what the new bot already stored:

- users: spam history (old values first, the last SPAM_HISTORY_SIZE
  kept), NSFW count and reputation (added), blacklist with its reason
- chats: spam/NSFW detection (off in either database stays off), blacklist

Not imported: votes on old reports (already counted in reputation) and
ignored media (old file IDs don't match Telethon's media IDs).

The file comes from a sudo user, but it is still opened read-only, its
schema is not trusted, and only plain tables are read.
"""
import asyncio
import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from spr.db import SPAM_HISTORY_SIZE

SQLITE_HEADER = b"SQLite format 3\x00"
MAX_FILE_SIZE = 1024**3
USERS_PER_CHUNK = 5000
REQUIRED_COLUMNS = {
    "users": {"user_id", "spam_data", "nsfw_count", "reputation", "blacklisted"},
    "chats": {"chat_id", "spam_enabled", "nsfw_enabled", "blacklisted"},
}
DEFAULT_REASON = "Blacklisted in the old database"


class OldDatabaseError(Exception):
    """The file can't be imported. The message is safe to show in chats."""


@dataclass(frozen=True)
class OldUser:
    user_id: int
    spam_data: tuple
    nsfw_count: int
    reputation: int
    blacklist_reason: Optional[str]
    blacklisted_at: Optional[datetime]


@dataclass(frozen=True)
class OldChat:
    chat_id: int
    spam_enabled: bool
    nsfw_enabled: bool
    blacklist_reason: Optional[str]
    blacklisted_at: Optional[datetime]


@dataclass
class ImportSummary:
    users: int = 0
    chats: int = 0
    blacklisted: int = 0
    resumed: bool = False
    finished_before: Optional[datetime] = None


def file_sha256(path: Path) -> bytes:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def open_old_database(path: Path) -> sqlite3.Connection:
    """Open read-only and check it is the old bot's database"""
    with open(path, "rb") as file:
        if file.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
            raise OldDatabaseError("This file is not an SQLite database. Send the old db.sqlite3 file.")
    uri = Path(path).resolve().as_uri() + "?mode=ro&immutable=1"
    # The connection is used from worker threads, one call at a time
    connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
    try:
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA query_only = ON")
        tables = {
            name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        for table, columns in REQUIRED_COLUMNS.items():
            if table not in tables:
                raise OldDatabaseError(
                    f"This file has no {table} table, so it is not the old bot's database."
                )
            # Table names come from REQUIRED_COLUMNS only, never from the file
            found = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            if columns - found:
                raise OldDatabaseError(
                    f"The {table} table in this file is missing columns "
                    f"({', '.join(sorted(columns - found))}), so it is not the old bot's database."
                )
    except sqlite3.DatabaseError as error:
        connection.close()
        raise OldDatabaseError(f"This SQLite file can't be read: {error}") from error
    except OldDatabaseError:
        connection.close()
        raise
    return connection


def to_datetime(timestamp) -> datetime:
    try:
        return datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return datetime.now(timezone.utc)


def read_reasons(connection: sqlite3.Connection) -> dict:
    """{user or chat ID: (reason, blacklisted at)}"""
    has_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'reasons'"
    ).fetchone()
    if not has_table:
        return {}
    return {
        entity_id: (str(reason or DEFAULT_REASON), to_datetime(timestamp))
        for entity_id, reason, timestamp in connection.execute("SELECT id, reason, time FROM reasons")
    }


def parse_spam_data(text) -> tuple:
    """The old JSON list of spam predictions, only finite numbers kept"""
    try:
        values = json.loads(text or "[]")
    except (TypeError, ValueError):
        return ()
    if not isinstance(values, list):
        return ()
    numbers = [
        float(value)
        for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    ]
    return tuple(numbers[-SPAM_HISTORY_SIZE:])


def blacklist_of(entity_id: int, blacklisted, reasons: dict):
    if not blacklisted:
        return None, None
    return reasons.get(entity_id, (DEFAULT_REASON, datetime.now(timezone.utc)))


def read_chats(connection: sqlite3.Connection, reasons: dict) -> list:
    """Chats with their own settings or a blacklist; the rest are defaults"""
    chats = []
    query = "SELECT chat_id, spam_enabled, nsfw_enabled, blacklisted FROM chats"
    for chat_id, spam_enabled, nsfw_enabled, blacklisted in connection.execute(query):
        if spam_enabled and nsfw_enabled and not blacklisted:
            continue
        reason, blacklisted_at = blacklist_of(chat_id, blacklisted, reasons)
        chats.append(OldChat(int(chat_id), bool(spam_enabled), bool(nsfw_enabled), reason, blacklisted_at))
    return chats


def count_users(connection: sqlite3.Connection, after_rowid: int = 0) -> int:
    return connection.execute("SELECT count(*) FROM users WHERE rowid > ?", (after_rowid,)).fetchone()[0]


def read_user_chunk(connection: sqlite3.Connection, after_rowid: int, reasons: dict):
    """(users with data, last rowid read, rows read) of the next chunk.
    Last rowid is None when there are no more rows."""
    rows = connection.execute(
        "SELECT rowid, user_id, spam_data, nsfw_count, reputation, blacklisted "
        "FROM users WHERE rowid > ? ORDER BY rowid LIMIT ?",
        (after_rowid, USERS_PER_CHUNK),
    ).fetchall()
    if not rows:
        return [], None, 0
    users = []
    for _, user_id, spam_data, nsfw_count, reputation, blacklisted in rows:
        values = parse_spam_data(spam_data)
        nsfw_count = int(nsfw_count or 0)
        reputation = int(reputation or 0)
        # Users the old bot knew nothing about would only add empty rows
        if not values and not nsfw_count and not reputation and not blacklisted:
            continue
        reason, blacklisted_at = blacklist_of(user_id, blacklisted, reasons)
        users.append(OldUser(int(user_id), values, nsfw_count, reputation, reason, blacklisted_at))
    return users, rows[-1][0], len(rows)


async def import_old_database(db, path: Path, on_progress=None) -> ImportSummary:
    """Import the file into db. on_progress(rows done, rows in total) is
    awaited after every chunk. Running it again with the same file
    continues an interrupted import, or does nothing if it finished."""
    file_hash = await asyncio.to_thread(file_sha256, path)
    connection = await asyncio.to_thread(open_old_database, path)
    try:
        state = await db.start_sqlite_import(file_hash)
        if state.finished_at is not None:
            return ImportSummary(finished_before=state.finished_at)
        summary = ImportSummary(resumed=state.chats_done or state.users_done_through > 0)
        reasons = await asyncio.to_thread(read_reasons, connection)

        if not state.chats_done:
            chats = await asyncio.to_thread(read_chats, connection, reasons)
            await db.merge_old_chats(file_hash, chats)
            summary.chats = len(chats)
            summary.blacklisted += sum(1 for chat in chats if chat.blacklisted_at)

        after_rowid = state.users_done_through
        done = (await asyncio.to_thread(count_users, connection)) - (
            await asyncio.to_thread(count_users, connection, after_rowid)
        )
        total = await asyncio.to_thread(count_users, connection)
        while True:
            users, last_rowid, rows_read = await asyncio.to_thread(
                read_user_chunk, connection, after_rowid, reasons
            )
            if last_rowid is None:
                break
            await db.merge_old_users(file_hash, users, last_rowid)
            summary.users += len(users)
            summary.blacklisted += sum(1 for user in users if user.blacklisted_at)
            after_rowid = last_rowid
            done += rows_read
            if on_progress:
                await on_progress(done, total)

        await db.finish_sqlite_import(file_hash)
        return summary
    finally:
        connection.close()
