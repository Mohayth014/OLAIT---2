import unicodedata
from typing import Tuple

# Tamil Unicode building blocks
TAMIL_CONSONANTS = set("கஙசஞடணதநபமயரலவழளறனஜஷஸஹ")
# Dependent vowel signs (matras), pulli (virama) and the au length mark:
# none of these may appear without a consonant before them.
TAMIL_SIGNS = set("ாிீுூெேைொோௌ்ௗ")
TAMIL_AU_LENGTH_MARK = "ௗ"   # 'ௗ', follows 'ெ' in the decomposed form of 'ௌ'
TAMIL_E_SIGN = "ெ"

# Invisible joiner characters. Tesseract emits U+200C after every pulli; left in,
# "தமிழ்‌" would not match a search for "தமிழ்".
ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"), None)

WORD_PUNCTUATION = ".,;:!?\"'()[]{}<>–—-‘’“”"

TAMIL_INDEPENDENT_VOWELS = {
    "அ": "a", "ஆ": "aa", "இ": "i", "ஈ": "ii", "உ": "u", "ஊ": "uu",
    "எ": "e", "ஏ": "ee", "ஐ": "ai", "ஒ": "o", "ஓ": "oo", "ஔ": "au",
}
TAMIL_CONSONANT_ROMAN = {
    "க": "k", "ங": "ng", "ச": "s", "ஞ": "nj", "ட": "t", "ண": "n",
    "த": "th", "ந": "n", "ப": "p", "ம": "m", "ய": "y", "ர": "r",
    "ல": "l", "வ": "v", "ழ": "zh", "ள": "l", "ற": "r", "ன": "n",
    "ஜ": "j", "ஷ": "sh", "ஸ": "s", "ஹ": "h",
}
TAMIL_DEPENDENT_VOWELS = {
    "ா": "aa", "ி": "i", "ீ": "ii", "ு": "u", "ூ": "uu", "ெ": "e",
    "ே": "ee", "ை": "ai", "ொ": "o", "ோ": "oo", "ௌ": "au",
}


def is_tamil_char(c: str) -> bool:
    return "஀" <= c <= "௿"


def normalize_text(text: str) -> str:
    """
    Canonical form for all stored Tamil text: Unicode NFC, zero-width joiners removed,
    whitespace collapsed. Spelling is never changed here (historical forms are preserved).
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text).translate(ZERO_WIDTH)
    return " ".join(text.split())


def transliterate_tamil(text: str) -> str:
    """Return a deterministic Tanglish rendering of Tamil text."""
    text = normalize_text(text)
    result = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in TAMIL_INDEPENDENT_VOWELS:
            result.append(TAMIL_INDEPENDENT_VOWELS[char])
        elif char in TAMIL_CONSONANT_ROMAN:
            roman = TAMIL_CONSONANT_ROMAN[char]
            if index + 1 < len(text) and text[index + 1] == "்":
                result.append(roman)
                index += 1
            elif index + 1 < len(text) and text[index + 1] in TAMIL_DEPENDENT_VOWELS:
                result.append(roman + TAMIL_DEPENDENT_VOWELS[text[index + 1]])
                index += 1
            else:
                result.append(roman + "a")
        elif char != "்":
            result.append(char)
        index += 1
    return "".join(result)


def is_valid_tamil_word(word: str) -> bool:
    """
    Structural check: only Tamil letters, and every vowel sign / pulli follows a consonant.
    This says nothing about spelling; it rejects impossible letter sequences.
    """
    if not word or not all(is_tamil_char(c) for c in word):
        return False
    prev = ""
    # NFC keeps two-part vowel signs (ொ, ோ, ௌ) composed, so each sign is one code point.
    for c in unicodedata.normalize("NFC", word):
        if c in TAMIL_SIGNS and prev not in TAMIL_CONSONANTS:
            if not (c == TAMIL_AU_LENGTH_MARK and prev == TAMIL_E_SIGN):
                return False
        prev = c
    return True


def validate_text_layer(text: str, min_valid: float) -> Tuple[bool, float]:
    """
    Decides whether a PDF's embedded text layer can be trusted instead of running OCR.

    Legacy-font PDFs (Bamini, TSCII, broken ToUnicode maps) often decode to mostly
    Tamil-block characters but in impossible orders, or with Latin letters inside
    Tamil words ("தமBழ்"), so a character-ratio test is not enough. A layer is
    trusted only if at least `min_valid` of its Tamil words are structurally valid.

    Returns (trusted, score).
    """
    words = [normalize_text(w.strip(WORD_PUNCTUATION)) for w in text.split()]
    tamil_words = [w for w in words if w and any(is_tamil_char(c) for c in w)]
    if not tamil_words:
        return False, 0.0
    score = sum(is_valid_tamil_word(w) for w in tamil_words) / len(tamil_words)
    return score >= min_valid, round(score, 4)
