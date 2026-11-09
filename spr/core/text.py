"""Text clean-up before a spam check"""
import unicodedata


def plain_letters(text: str) -> str:
    """Look-alike letters become plain letters: "𝙁𝙧𝙚𝙚 𝙘𝙧𝙮𝙥𝙩𝙤" becomes
    "Free crypto", full-width "ｔ．ｍｅ" becomes "t.me". Spammers use these
    letters to get past filters. NFKC is Unicode's own mapping for them,
    and normal text in any language stays the same."""
    return unicodedata.normalize("NFKC", text)
