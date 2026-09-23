import pytest

from backend.config import TEXT_LAYER_MIN_VALID
from backend.pipeline.tamil_processor import normalize_text, is_valid_tamil_word, transliterate_tamil, validate_text_layer

CORRECT_TAMIL = """தமிழ் மொழி மிகவும் பழமையானது.
அறிவு ஒரு பெரிய செல்வம்.
கற்க கசடற கற்பவை கற்றபின் நிற்க அதற்குத் தக."""

# Text layer extracted from a PDF whose font has a broken ToUnicode map (seen in Phase 0).
GARBLED_LAYER = """தமBழ்
ம
ெ
ாழB மBகÒம் பழ
ம
ை
யான».
அறBÒ ஒ
ப
ெ
ரிய
ச
ெல்வம்."""


def test_normalize_strips_zero_width_joiners():
    # Tesseract emits U+200C after pulli; it must not survive into stored text.
    assert normalize_text("தமிழ்‌ மொழி") == "தமிழ் மொழி"


def test_normalize_collapses_whitespace():
    assert normalize_text("  தமிழ்   மொழி \n ") == "தமிழ் மொழி"


def test_normalize_composes_two_part_vowel_signs():
    decomposed = "மொழி"  # ெ + ா typed separately
    assert normalize_text(decomposed) == "மொழி"


def test_transliterate_tamil_returns_tanglish():
    assert transliterate_tamil("தமிழ் மொழி") == "thamizh mozhi"


@pytest.mark.parametrize("word", ["தமிழ்", "மொழி", "பழமையானது", "கௌரவம்", "அறிவு", "ஔவையார்", "ஸ்ரீ"])
def test_valid_words(word):
    assert is_valid_tamil_word(word)


@pytest.mark.parametrize("word", [
    "தமBழ்",   # Latin letter inside a Tamil word
    "ெபரிய",   # vowel sign with no consonant before it
    "ாழ",      # word starting with a vowel sign
    "அ்",      # pulli on a vowel
    "",
])
def test_invalid_words(word):
    assert not is_valid_tamil_word(word)


def test_text_layer_accepts_correct_tamil():
    trusted, score = validate_text_layer(CORRECT_TAMIL, TEXT_LAYER_MIN_VALID)
    assert trusted and score == 1.0


def test_text_layer_rejects_garbled_legacy_font():
    trusted, score = validate_text_layer(GARBLED_LAYER, TEXT_LAYER_MIN_VALID)
    assert not trusted and score < 0.7


def test_text_layer_rejects_non_tamil():
    trusted, score = validate_text_layer("Hello world, no Tamil here.", TEXT_LAYER_MIN_VALID)
    assert not trusted and score == 0.0
