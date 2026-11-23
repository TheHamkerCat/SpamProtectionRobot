"""Text clean-up before a spam check"""
import re
import unicodedata

# A word that is a link or an @username. Bare domains need a path
# ("site.com/x"), like in ARQ's model; "site.com" alone is a word.
LINK_WORD = re.compile(
    r"^(?:https?://|www\.|t\.me/|telegram\.(?:me|dog)/|@\w|[\w.-]+\.[a-z]{2,}/)", re.IGNORECASE
)
# Brackets and punctuation around a link: "(https://x.com)," is a link
WORD_EDGES = "()[]{}<>\"'.,!?:;"


def plain_letters(text: str) -> str:
    """Look-alike letters become plain letters: "𝙁𝙧𝙚𝙚 𝙘𝙧𝙮𝙥𝙩𝙤" becomes
    "Free crypto", full-width "ｔ．ｍｅ" becomes "t.me". Spammers use these
    letters to get past filters. NFKC is Unicode's own mapping for them,
    and normal text in any language stays the same."""
    return unicodedata.normalize("NFKC", text)


def has_words(text: str) -> bool:
    """True when the text has letters outside its links and @usernames.

    ARQ's model sees every link as the same word, so a text that is only
    links ("https://youtube.com/watch?v=...") scores like a scam link.
    Such a text has nothing the model can judge."""
    for word in text.split():
        if LINK_WORD.match(word.strip(WORD_EDGES)):
            continue
        if any(character.isalpha() for character in word):
            return True
    return False
