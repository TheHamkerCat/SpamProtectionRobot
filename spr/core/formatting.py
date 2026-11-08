"""HTML helpers. The bot sends every message in HTML parse mode, so all
text that users control (names, messages, reasons) must go through esc()."""
from html import escape


def esc(value) -> str:
    """Escape any value for Telegram HTML"""
    return escape(str(value))


def pre(text) -> str:
    """Escaped preformatted block"""
    return f"<pre>{esc(text)}</pre>"
