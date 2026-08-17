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
    seen = set()
    out = []
    for t in normalize(text).split():
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out
