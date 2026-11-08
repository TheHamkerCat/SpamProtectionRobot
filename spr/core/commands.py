import re

# Set once at startup with set_bot_username(), after the bot logs in
_bot_username = None


def set_bot_username(username: str):
    global _bot_username
    _bot_username = username


def get_bot_username() -> str:
    if _bot_username is None:
        raise RuntimeError("set_bot_username() must be called first")
    return _bot_username


def command(*names: str):
    """Pattern for events.NewMessage: matches "/name" and "/name@ThisBot",
    with or without arguments, case insensitive. "/name@OtherBot" does
    not match. The bot username is read at match time, so handlers can
    be declared before the bot logs in."""
    joined = "|".join(re.escape(name) for name in names)

    def match(text: str):
        username = re.escape(get_bot_username())
        return re.match(
            rf"^/(?:{joined})(?:@{username})?(?:\s|$)",
            text,
            re.IGNORECASE,
        )

    return match


def get_args(event) -> list:
    """Command and its arguments, split by whitespace.
    Ex: "/anti_nsfw enable" -> ["/anti_nsfw", "enable"]"""
    return event.raw_text.split()
