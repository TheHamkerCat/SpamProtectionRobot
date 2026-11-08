from re import findall

from telethon import Button


def is_url(text: str) -> bool:
    """Check if a string contains a URL"""
    regex = r"""(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]
                [.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(
                \([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\
                ()<>]+\)))*\)|[^\s`!()\[\]{};:'".,<>?«»“”‘’]))""".strip()
    return bool([x[0] for x in findall(regex, text)])


def make_button(text: str, value: str):
    """URL button if value is a URL, else a callback button"""
    if is_url(value):
        return Button.url(text, value)
    return Button.inline(text, data=value)


def keyboard(buttons_list, row_width: int = 2) -> list:
    """Build button rows from (text, value) pairs.
    Ex: keyboard([["click here", "https://google.com"]])
    """
    buttons = [make_button(text, value) for text, value in buttons_list]
    return [
        buttons[i : i + row_width] for i in range(0, len(buttons), row_width)
    ]


def ikb(data: dict, row_width: int = 2) -> list:
    """Build button rows from a dict, {text: url or callback data}
    Ex: ikb({"click here": "this is callback data"})"""
    return keyboard(data.items(), row_width=row_width)
