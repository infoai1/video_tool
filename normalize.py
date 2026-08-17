"""Roman Urdu normalisation for search.

Roman Urdu has no fixed spelling: the same word appears as namaz / namaaz /
namāz, wo / vo, shukr / shukar. Search only works if the query and the stored
transcript are folded to a common form *before* they are compared. This module
is that fold, and it is deliberately applied to BOTH sides:

  - the corpus, when a segment is transliterated (stored as `roman_norm`), and
  - the query, when a user searches.

As long as the same rules run on both, variant spellings collide.

The fold is intentionally conservative — it removes the noise Roman Urdu writers
disagree on (diacritics, doubled letters, w/v, punctuation, case) without
collapsing genuinely different words. It is not a linguistic transliteration
scheme; it is a matching key.

Known limitation: it does NOT insert or drop short vowels, so shukr vs shukar
stay distinct. In practice this matters little, because a single model
transliterates the whole corpus and is internally consistent about which
spelling it uses — the query just has to match that one convention.
"""
import re
import unicodedata

# Characters that carry no distinguishing information once folded.
_NON_WORD = re.compile(r"[^a-z0-9\s]+")
_WS = re.compile(r"\s+")
_DOUBLE = re.compile(r"(.)\1+")  # any run of one repeated char -> single char


def _fold_token(tok):
    # v and w are used interchangeably in Roman Urdu (wo/vo, wuzu/vuzu).
    tok = tok.replace("v", "w")
    # namaaz -> namaz, allah -> alah, sunnat -> sunat. Applied to query too, so
    # the collapse is consistent on both sides even where it over-folds.
    tok = _DOUBLE.sub(r"\1", tok)
    return tok


def normalize(text):
    """Fold arbitrary Roman Urdu text to its search key.

    Returns a lowercase, space-separated string of folded tokens. Empty input
    (or input that is all punctuation) folds to the empty string.
    """
    if not text:
        return ""
    # NFKD then drop combining marks turns ā -> a, ṇ -> n, etc.
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = _NON_WORD.sub(" ", text)
    tokens = [_fold_token(t) for t in _WS.split(text) if t]
    return " ".join(t for t in tokens if t)


def query_tokens(text):
    """The normalised tokens of a query, in order, de-duplicated but stable.

    search.py turns these into an FTS prefix query. Returns [] for an empty or
    all-punctuation query so the caller can short-circuit.
    """
    return _dedup(normalize(text).split())


# --- Urdu-script normalisation -------------------------------------------------
# Search covers the whole library by indexing the Urdu script itself, so the
# Urdu side needs its own fold. ASR output varies in harakat (short-vowel marks)
# and in near-identical letter forms; folding those makes FTS tokens line up
# between the corpus and a query transliterated back into Urdu.

# Near-identical letters that should collapse to one form for matching.
_URDU_FOLD = str.maketrans(
    {
        "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",   # alef variants -> bare alef
        "ي": "ی", "ى": "ی", "ئ": "ی",             # arabic/alef-maksura/hamza-yeh -> farsi yeh
        "ك": "ک",                                   # arabic kaf -> keheh
        "ه": "ہ", "ة": "ہ",                         # arabic heh / teh-marbuta -> gol heh
        "ؤ": "و",                                   # hamza-waw -> waw
    }
)
# Tatweel and zero-width joiners/marks carry no matchable content.
_URDU_STRIP = re.compile(
    "[ـ​‌‍‎‏﻿­]"
)


def normalize_urdu(text):
    """Fold Urdu-script text to its search key: drop harakat, unify letter forms,
    strip joiners. Applied to the corpus at ingest and to a transliterated query."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    # Drop nonspacing marks (harakat / tashkeel) generically.
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.translate(_URDU_FOLD)
    text = _URDU_STRIP.sub("", text)
    return _WS.sub(" ", text).strip()


def urdu_tokens(text):
    """Deduped, order-preserving tokens of normalised Urdu, for an FTS query."""
    return _dedup(normalize_urdu(text).split())


def _dedup(tokens):
    seen = set()
    out = []
    for t in tokens:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out
