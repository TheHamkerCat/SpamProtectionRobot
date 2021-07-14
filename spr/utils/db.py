# Ignore repetitions

from json import dumps, loads

from spr import conn

c = conn.curor()

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

# Reports in SPAM/NSFW log channel
c.execute(
    """
        CREATE
        TABLE
        IF NOT EXISTS
        reports
        (message_id, upvoters, downvoters)
        """
)


def user_exists(user_id: int) -> bool:
    """CHECK IF A USER EXISTS IN DB"""
    return c.execute(
        """
            SELECT *
            FROM users
            WHERE user_id=?
            """,
        (user_id,),
    ).fetchone()


def chat_exists(chat_id: int) -> bool:
    """CHECK IF A CHAT EXISTS IN DB"""
    return c.execute(
        """
            SELECT *
            FROM users
            WHERE chat_id=?
            """,
        (chat_id,),
    ).fetchone()


def add_user(user_id: int):
    """ADD A USER IN DB"""
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
    """ADD A CHAT IN DB"""
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
    """UPDATE SPAM DATA OF A USER"""
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


# Each nsfw media user sends, adds 2 spam count and
# decrements his reputation by 10


def increment_nsfw_count(user_id: int):
    """INCREMENT NSFW MESSAGES COUNT OF A USER"""
    c.execute(
        """
            UPDATE users
            SET nsfw_count = nsfw_count + 1,
                reputation = reputation - 1
            WHERE user_id=?
            """,
        (user_id,),
    )
    # no need to commit changes, the function below does that.
    return [update_spam_data(user_id, 100) for _ in range(2)]


def increment_reputation(user_id: int):
    """INCREMENT REPUTATION OF A USER"""
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
    """DECREMENT REPUTATION OF A USER"""
    c.execute(
        """
            UPDATE users
            SET reputation = reputation - 1
            WHERE user_id=?
            """,
        (user_id,),
    )
    return conn.commit()


def blacklist_user(user_id: int):
    """BLACKLIST A USER"""
    c.execute(
        """
            UPDATE users
            SET blacklisted = 1
            WHERE user_id=?
            """,
        (user_id,),
    )
    return conn.commit()


def blacklist_chat(chat_id: int):
    """BLACKLIST A CHAT"""
    c.execute(
        """
            UPDATE chats
            SET blacklisted = 1
            WHERE chat_id=?
            """,
        (chat_id,),
    )
    return conn.commit()


def whitelist_user(user_id: int):
    """WHITELIST A USER"""
    c.execute(
        """
            UPDATE users
            SET blacklisted = 0
            WHERE user_id=?
            """,
        (user_id,),
    )
    return conn.commit()


def whitelist_chat(chat_id: int):
    """WHITELIST A CHAT"""
    c.execute(
        """
            UPDATE chats
            SET blacklisted = 0
            WHERE chat_id=?
            """,
        (chat_id,),
    )
    return conn.commit()


def is_user_blacklisted(user_id: int) -> bool:
    """CHECK IF A USER IS BLACKLISTED"""
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
    """CHECK IF A CHAT IS BLACKLISTED"""
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
    """CHECK IF SPAM PROTECTION IS ENABLED IN A CHAT"""
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
    """CHECK IF NSFW DETECTION IS ENABLED IN A CHAT"""
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
    """ENABLE NSFW DETECTION IN A CHAT"""
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
    """DISABLE NSFW DETECTION IN A CHAT"""
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
    """ENABLE SPAM PROTECTION IN A CHAT"""
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
    """DISABLE SPAM PROTECTION IN A CHAT"""
    c.execute(
        """
            UPDATE chats
            SET spam_enabled = 0
            WHERE chat_id=?
            """,
        (chat_id,),
    )
    return conn.commit()


def add_report(message_id: int):
    """ADD A NEW SPAM/NSFW DETECTION REPORT TO DB"""
    c.execute(
        """
            INSERT
            INTO reports
            VALUES (?, ?, ?)
            """,
        (message_id, "[]", "[]"),
    )
    return c.commit()


def upvote(message_id: int, user_id: int):
    """UPVOTE A DETECTION REPORT"""
    data = c.execute(
        """
            SELECT upvoters
            FROM reports
            WHERE message_id=?
            """,
        (message_id,),
    ).fetchone()[0]
    data = loads(data)
    data.append(user_id)
    data = dumps(data)
    c.execute(
        """
            UPDATE reports
            SET upvoters = ?
            WHERE message_id = ?
            """,
        (data, message_id),
    )

    return await increment_reputation(user_id)


def downvote(message_id: int, user_id: int):
    """DOWNVOTE A DETECTION REPORT"""
    data = c.execute(
        """
            SELECT downvoters
            FROM reports
            WHERE message_id=?
            """,
        (message_id,),
    ).fetchone()[0]
    data = loads(data)
    data.append(user_id)
    data = dumps(data)
    c.execute(
        """
            UPDATE reports
            SET downvoters = ?
            WHERE message_id = ?
            """,
        (data, message_id),
    )
    return await increment_reputation(user_id)


def user_voted(message_id: int, user_id: int) -> bool:
    """CHECK IF A USER VOTED TO A DETECTION REPORT"""
    upvoters = c.execute(
        """
            SELECT upvoters
            FROM reports
            WHERE message_id=?
            """,
        (message_id,),
    ).fetchone()[0]
    upvoters = loads(upvoters)

    if user_id in upvoters:
        return True

    downvoters = c.execute(
        """
            SELECT downvoters
            FROM reports
            WHERE message_id=?
            """,
        (message_id,),
    ).fetchone()[0]
    downvoters = loads(downvoters)

    if user_id in downvoters:
        return True

    return False
