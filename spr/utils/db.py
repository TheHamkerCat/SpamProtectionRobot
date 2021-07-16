# Ignore repetitions

from json import dumps, loads
from time import time

from spr import conn

c = conn.cursor()

c.execute(
    """
        CREATE
        TABLE
        IF NOT EXISTS
        users
        (user_id, spam_data, nsfw_count, reputation, blacklisted)
        """
)

c.execute(
    """
        CREATE
        TABLE
        IF NOT EXISTS
        chats
        (chat_id, spam_enabled, nsfw_enabled, blacklisted)
        """
)

# For reports in SPAM log channel
c.execute(
    """
        CREATE
        TABLE
        IF NOT EXISTS
        reports
        (message_id, upvote, downvote, user_id)
        """
)

# For false NSFW reports
c.execute(
    """
        CREATE
        TABLE
        IF NOT EXISTS
        ignored_media
        (file_id, time)
        """
)

# For blacklist reasons
c.execute(
    """
        CREATE
        TABLE
        IF NOT EXISTS
        reasons
        (id, reason, time)
        """
)


def user_exists(user_id: int) -> bool:
    """
    CHECK IF A USER EXISTS IN DB
    """
    return c.execute(
        """
            SELECT *
            FROM users
            WHERE user_id=?
            """,
        (user_id,),
    ).fetchone()


def chat_exists(chat_id: int) -> bool:
    """
    CHECK IF A CHAT EXISTS IN DB
    """
    return c.execute(
        """
            SELECT *
            FROM chats
            WHERE chat_id=?
            """,
        (chat_id,),
    ).fetchone()


def add_user(user_id: int):
    """
    ADD A USER IN DB
    """
    c.execute(
        """
            INSERT
            INTO users
            VALUES (?, ?, ?, ?, ?)
            """,
        (user_id, "[]", 0, 0, 0),
    )
    return conn.commit()


def add_chat(chat_id: int):
    """
    ADD A CHAT IN DB
    """
    c.execute(
        """
            INSERT
            INTO chats
            VALUES (?, ?, ?, ?)
            """,
        (chat_id, 1, 1, 0),
    )
    return conn.commit()


# the below function below gets called on each message update,
# and stores the spam prediction of that message,
# It stores last 50 messages sent by a user.


def update_spam_data(user_id: int, spam_value: float):
    """
    UPDATE SPAM DATA OF A USER
    """
    data = c.execute(
        """
            SELECT spam_data
            FROM users
            WHERE user_id=?
            """,
        (user_id,),
    ).fetchone()[0]
    data = loads(data)
    data = data or []
    if len(data) >= 50:
        data = data[1:50]
    data.append(spam_value)
    data = [
        i for i in data if isinstance(i, float) or isinstance(i, int)
    ]
    data = dumps(data)
    c.execute(
        """
            UPDATE users
            SET spam_data=?
            WHERE user_id=?
            """,
        (data, user_id),
    )
    return conn.commit()


def get_user_trust(user_id: int) -> float:
    """
    GET TRUST PREDICTION OF A USER
    """
    data = c.execute(
        """
            SELECT spam_data
            FROM users
            WHERE user_id=?
            """,
        (user_id,),
    ).fetchone()[0]
    data = loads(data)
    return (
        100 if not data else round((100 - (sum(data) / len(data))), 4)
    )


# Each nsfw media user sends, adds 2 spam count and
# decrements his reputation by 10


def increment_nsfw_count(user_id: int):
    """
    INCREMENT NSFW MESSAGES COUNT OF A USER
    """
    c.execute(
        """
            UPDATE users
            SET nsfw_count = nsfw_count + 1,
                reputation = reputation - 10
            WHERE user_id=?
            """,
        (user_id,),
    )
    # no need to commit changes, the function below does that.
    return [update_spam_data(user_id, 100) for _ in range(2)]


def get_nsfw_count(user_id: int):
    """
    GET NSFW MESSAGES COUNT OF A USER
    """
    return c.execute(
        """
            SELECT nsfw_count
            FROM users
            WHERE user_id=?
            """,
        (user_id,),
    ).fetchone()[0]


def get_reputation(user_id: int) -> int:
    """
    GET REPUTATION OF A USER
    """
    return c.execute(
        """
            SELECT reputation
            FROM users
            WHERE user_id=?
            """,
        (user_id,),
    ).fetchone()[0]


def increment_reputation(user_id: int):
    """
    INCREMENT REPUTATION OF A USER
    """
    c.execute(
        """
            UPDATE users
            SET reputation = reputation + 1
            WHERE user_id=?
            """,
        (user_id,),
    )
    return conn.commit()


def decrement_reputation(user_id: int):
    """
    DECREMENT REPUTATION OF A USER
    """
    c.execute(
        """
            UPDATE users
            SET reputation = reputation - 1
            WHERE user_id=?
            """,
        (user_id,),
    )
    return conn.commit()


def blacklist_user(user_id: int, reason: str):
    """
    BLACKLIST A USER
    """
    c.execute(
        """
            UPDATE users
            SET blacklisted = 1
            WHERE user_id=?
            """,
        (user_id,),
    )
    c.execute(
        """
            INSERT
            INTO reasons
            VALUES (?, ?, ?)
            """,
        (user_id, reason, time()),
    )
    return conn.commit()


def get_blacklist_event(id: int):
    """
    GET REASON AND TIME FOR A BLACKLIST EVENT
    """
    return c.execute(
        """
            SELECT reason, time
            FROM reasons
            WHERE id = ?
            """,
        (id,),
    ).fetchone()


def blacklist_chat(chat_id: int, reason: str):
    """
    BLACKLIST A CHAT
    """
    c.execute(
        """
            UPDATE chats
            SET blacklisted = 1
            WHERE chat_id=?
            """,
        (chat_id,),
    )
    c.execute(
        """
            INSERT
            INTO reasons
            VALUES (?, ?, ?)
            """,
        (chat_id, reason, time()),
    )
    return conn.commit()


def whitelist_user(user_id: int):
    """
    WHITELIST A USER
    """
    c.execute(
        """
            UPDATE users
            SET blacklisted = 0
            WHERE user_id=?
            """,
        (user_id,),
    )
    c.execute(
        """
            DELETE
            FROM reasons
            WHERE id = ?
            """,
        (user_id,),
    )
    return conn.commit()


def whitelist_chat(chat_id: int):
    """
    WHITELIST A CHAT
    """
    c.execute(
        """
            UPDATE chats
            SET blacklisted = 0
            WHERE chat_id=?
            """,
        (chat_id,),
    )
    c.execute(
        """
            DELETE
            FROM reasons
            WHERE id = ?
            """,
        (chat_id,),
    )
    return conn.commit()


def is_user_blacklisted(user_id: int) -> bool:
    """
    CHECK IF A USER IS BLACKLISTED
    """
    return bool(
        c.execute(
            """
            SELECT blacklisted
            FROM users
            WHERE user_id=?
            """,
            (user_id,),
        ).fetchone()[0]
    )


def is_chat_blacklisted(chat_id: int) -> bool:
    """
    CHECK IF A CHAT IS BLACKLISTED
    """
    return bool(
        c.execute(
            """
            SELECT blacklisted
            FROM chats
            WHERE chat_id=?
            """,
            (chat_id,),
        ).fetchone()[0]
    )


def is_spam_enabled(chat_id: int) -> bool:
    """
    CHECK IF SPAM PROTECTION IS ENABLED IN A CHAT
    """
    return bool(
        c.execute(
            """
            SELECT spam_enabled
            FROM chats
            WHERE chat_id=?
            """,
            (chat_id,),
        ).fetchone()[0]
    )


def is_nsfw_enabled(chat_id: int) -> bool:
    """
    CHECK IF NSFW DETECTION IS ENABLED IN A CHAT
    """
    return bool(
        c.execute(
            """
            SELECT nsfw_enabled
            FROM chats
            WHERE chat_id=?
            """,
            (chat_id,),
        ).fetchone()[0]
    )


def enable_nsfw(chat_id: int):
    """
    ENABLE NSFW DETECTION IN A CHAT
    """
    c.execute(
        """
            UPDATE chats
            SET nsfw_enabled = 1
            WHERE chat_id=?
            """,
        (chat_id,),
    )
    return conn.commit()


def disable_nsfw(chat_id: int):
    """
    DISABLE NSFW DETECTION IN A CHAT
    """
    c.execute(
        """
            UPDATE chats
            SET nsfw_enabled = 0
            WHERE chat_id=?
            """,
        (chat_id,),
    )
    return conn.commit()


def enable_spam(chat_id: int):
    """
    ENABLE SPAM PROTECTION IN A CHAT
    """
    c.execute(
        """
            UPDATE chats
            SET spam_enabled = 1
            WHERE chat_id=?
            """,
        (chat_id,),
    )
    return conn.commit()


def disable_spam(chat_id: int):
    """
    DISABLE SPAM PROTECTION IN A CHAT
    """
    c.execute(
        """
            UPDATE chats
            SET spam_enabled = 0
            WHERE chat_id=?
            """,
        (chat_id,),
    )
    return conn.commit()


def upvote(message_id: int, user_id: int):
    """
    UPVOTE A DETECTION REPORT
    """
    c.execute(
        """
            INSERT
            INTO reports
            VALUES (?, ?, ?, ?)
            """,
        (message_id, 1, 0, user_id),
    )
    return increment_reputation(user_id)


def downvote(message_id: int, user_id: int):
    """
    DOWNVOTE A DETECTION REPORT
    """
    c.execute(
        """
            INSERT
            INTO reports
            VALUES (?, ?, ?, ?)
            """,
        (message_id, 0, 1, user_id),
    )
    return increment_reputation(user_id)


def user_voted(message_id: int, user_id: int) -> bool:
    """
    CHECK IF A USER VOTED TO A DETECTION REPORT
    """
    return bool(
        c.execute(
            """
            SELECT *
            FROM reports
            WHERE message_id=? AND user_id=?
            """,
            (message_id, user_id),
        ).fetchone()
    )


def ignore_nsfw(file_id: str):
    """
    IGNORE NSFW FALSE REPORTS
    """
    c.execute(
        """
            INSERT
            INTO
            ignored_media
            VALUES (?, ?)
            """,
        (file_id, int(time())),
    )
    return conn.commit()


def is_nsfw_downvoted(file_id: str) -> bool:
    """
    CHECK IF NSFW IS MARKED AS FALSE IN DB
    """
    return c.execute(
        """
            SELECT *
            FROM ignored_media
            WHERE file_id = ?
            """,
        (file_id,),
    ).fetchone()
