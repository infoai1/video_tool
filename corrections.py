"""Fixing a transcript line, everywhere it is read.

The transcripts are machine-made. The machine hears khasho for khusho, wah for
woh, and a reader who knows the recording can hear the difference at once. Up
to now there was nowhere to put that knowledge.

A line is read in three places, and a correction that reaches only one of them
makes things worse, not better:

    segments.roman_clean   what the page shows
    segments.roman_norm    what search matches on
    segments_fts           the index search actually scans

So a correction writes all three, in one transaction, and keeps what the line
said before -- a correction can be wrong too, and nothing here is destructive.
"""
import datetime
import re
import sqlite3
import uuid

import normalize

SCHEMA = """
CREATE TABLE IF NOT EXISTS corrections (
    id         INTEGER PRIMARY KEY,
    segment_id INTEGER NOT NULL,
    video_id   INTEGER,
    was        TEXT,
    now        TEXT,
    who        TEXT,
    at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_corrections_segment ON corrections(segment_id);
CREATE INDEX IF NOT EXISTS idx_corrections_video ON corrections(video_id);
CREATE TABLE IF NOT EXISTS word_fixes (
    wrong TEXT PRIMARY KEY,
    right TEXT NOT NULL,
    who   TEXT,
    at    TEXT
);
"""


def ensure_schema(conn):
    conn.executescript(SCHEMA)
    # batch groups the lines one apply_word() call touched, so undo_batch() can
    # revert all of them together. Added after the original table shipped, so
    # guard with a PRAGMA check -- runs once, safe on a database that already
    # has the column.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(corrections)")}
    if "batch" not in cols:
        conn.execute("ALTER TABLE corrections ADD COLUMN batch TEXT")


def current(conn, seg_id):
    """(video_id, the line as it is read today) or None."""
    row = conn.execute(
        "SELECT video_id, COALESCE(NULLIF(TRIM(roman_clean), ''), roman_text) "
        "FROM segments WHERE id = ?", (seg_id,)).fetchone()
    return (row[0], row[1] or "") if row else None


def apply(conn, seg_id, text, who="owner", batch=None):
    """Correct one line. Returns True when something changed.

    `conn` must be writable. The caller commits -- several lines corrected
    together should stand or fall together.
    """
    text = (text or "").strip()
    found = current(conn, seg_id)
    if found is None or not text:
        return False
    video_id, was = found
    if text == (was or "").strip():
        return False

    ensure_schema(conn)
    norm = normalize.normalize(text)
    conn.execute("UPDATE segments SET roman_clean = ?, roman_norm = ? WHERE id = ?",
                 (text, norm, seg_id))
    # rowid == segments.id, so re-indexing is delete-then-insert by id.
    conn.execute("DELETE FROM segments_fts WHERE rowid = ?", (seg_id,))
    conn.execute("INSERT INTO segments_fts (rowid, roman_norm) VALUES (?, ?)", (seg_id, norm))
    conn.execute(
        "INSERT INTO corrections (segment_id, video_id, was, now, who, at, batch) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (seg_id, video_id, was, text, who,
         datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), batch))
    return True


def history(conn, video_id, limit=200):
    """What has been corrected in one lecture, newest first."""
    ensure_schema(conn)
    return [{"segment_id": r[0], "was": r[1], "now": r[2], "who": r[3], "at": r[4]}
            for r in conn.execute(
                "SELECT segment_id, was, now, who, at FROM corrections "
                "WHERE video_id = ? ORDER BY id DESC LIMIT ?", (video_id, limit))]


def recent(conn, limit=30):
    """Corrections across the whole library, newest first -- history()'s
    lecture-agnostic twin, same table, same ordering, just no video filter."""
    ensure_schema(conn)
    return [{"id": r[0], "segment_id": r[1], "video_id": r[2], "was": r[3], "now": r[4],
             "who": r[5], "at": r[6]}
            for r in conn.execute(
                "SELECT id, segment_id, video_id, was, now, who, at FROM corrections "
                "ORDER BY id DESC LIMIT ?", (limit,))]


def last(conn, seg_id):
    """The most recent correction of one line, or None."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT segment_id, video_id, was, now, who, at FROM corrections "
        "WHERE segment_id = ? ORDER BY id DESC LIMIT 1", (seg_id,)).fetchone()
    if row is None:
        return None
    return {"segment_id": row[0], "video_id": row[1], "was": row[2], "now": row[3],
            "who": row[4], "at": row[5]}


_WORD_RE = re.compile(r"[A-Za-z']+")


def _human_touched(conn, seg_id):
    """True when the latest correction on this line was a person's judgement
    call, not a glossary fix or a revert -- the one guard a bulk word fix
    must never override."""
    prior = last(conn, seg_id)
    return (prior is not None and prior["who"] is not None and
            not str(prior["who"]).startswith("glossary:") and prior["who"] != "revert")


def word_matches(conn, words, limit=500):
    """Every line in the library that has any of `words` in any variant spelling.

    `words` is a list of tokens (owner-typed spellings, e.g. ["khushu",
    "khusho"]) -- variant spelling is also whatever normalize.py already
    folds together (v/w, doubled letters, diacritics), the same fold
    search.py matches queries on. The union of both is searched: one FTS
    key per distinct normalize() of a token. Returns (rows, variants,
    total): rows capped at `limit`, ordered by lecture title then
    start_time; variants is the set of actual spellings found (used both
    to highlight and to know what to replace).

    A line a human corrected by hand is still listed (so the owner can see
    it) but flagged "human" -- the caller must never offer it for a bulk fix.
    """
    ensure_schema(conn)
    keys = {k for k in (normalize.normalize(w) for w in words) if k and " " not in k}
    if not keys:
        return [], set(), 0
    match_expr = " OR ".join(keys)
    cand = conn.execute(
        "SELECT s.id, s.video_id, v.title, v.youtube_url, s.start_time, "
        "COALESCE(NULLIF(TRIM(s.roman_clean),''), s.roman_text) "
        "FROM segments_fts f JOIN segments s ON s.id = f.rowid JOIN videos v ON v.id = s.video_id "
        "WHERE segments_fts MATCH ? ORDER BY v.title, s.start_time", (match_expr,)).fetchall()
    rows, variants = [], set()
    for seg_id, video_id, title, yt_url, start, text in cand:
        text = text or ""
        found = [w.strip("'") for w in _WORD_RE.findall(text)
                 if normalize.normalize(w.strip("'")) in keys and w.strip("'")]
        if not found:
            continue  # FTS is a prefilter -- a real word boundary may not match
        variants.update(w.lower() for w in found)
        rows.append({"segment_id": seg_id, "video_id": video_id, "title": title,
                     "youtube_url": yt_url, "start_time": start, "text": text,
                     "matched": found, "human": _human_touched(conn, seg_id)})
    return rows[:limit], variants, len(rows)


def apply_word(conn, words, ids=None, who="owner"):
    """Replace every matched variant spelling with `words[0]`, the owner's chosen spelling.

    `words` is a list -- the first token is the replacement, the rest are
    variants to find (in addition to whatever normalize.py already folds
    together). `ids`, when given, restricts the fix to those segment ids
    (the owner's ticked subset) -- always intersected with a fresh
    word_matches() lookup, never the client's say-so alone. Human-corrected
    lines are always skipped. One word_fixes row per variant actually
    replaced. All lines changed by this one call share a fresh batch id
    (returned as "batch"), so undo_batch() can revert the whole group.
    """
    words = [w.strip() for w in (words or []) if w and w.strip()]
    if not words or any(" " in w or "\t" in w for w in words):
        return {"error": "one word at a time, please"}
    word = words[0]
    rows, _variants, _total = word_matches(conn, words)
    if ids is not None:
        ids = set(ids)
        rows = [r for r in rows if r["segment_id"] in ids]

    def _replacement(m):
        rep = word
        return rep[:1].upper() + rep[1:] if m.group(0)[:1].isupper() else rep

    changed, skipped, lecture_ids, used_variants = [], 0, set(), set()
    for r in rows:
        if r["human"]:
            skipped += 1
            continue
        pattern = re.compile(
            "|".join(r"(?<!\w)" + re.escape(v) + r"(?!\w)" for v in set(r["matched"])), re.I)
        new_text, n = pattern.subn(_replacement, r["text"])
        if n == 0 or new_text == r["text"]:
            continue
        changed.append((r["segment_id"], new_text))
        lecture_ids.add(r["video_id"])
        used_variants.update(v.lower() for v in r["matched"] if v.lower() != word.lower())

    batch = uuid.uuid4().hex[:12] if changed else None
    for seg_id, new_text in changed:
        apply(conn, seg_id, new_text, who=f"glossary:{word}", batch=batch)
    if used_variants:
        at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        for variant in used_variants:
            conn.execute(
                "INSERT INTO word_fixes (wrong, right, who, at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(wrong) DO UPDATE SET right=excluded.right, who=excluded.who, at=excluded.at",
                (variant, word, who, at))
    return {"lines": len(changed), "lectures": len(lecture_ids), "skipped_human": skipped, "batch": batch}


def word_fixes(conn):
    """Every word fix remembered, most recent first."""
    ensure_schema(conn)
    return [{"wrong": r[0], "right": r[1], "who": r[2], "at": r[3]}
            for r in conn.execute("SELECT wrong, right, who, at FROM word_fixes ORDER BY at DESC")]


def undo_batch(conn, batch):
    """Revert every line one apply_word() call changed, newest line first.

    A no-op for a falsy or unknown batch (old rows have batch NULL and are
    simply not undoable as a group -- their per-line history is untouched).
    """
    if not batch:
        return 0
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT segment_id, was FROM corrections WHERE batch = ? ORDER BY id DESC", (batch,)).fetchall()
    n = 0
    for seg_id, was in rows:
        if apply(conn, seg_id, was, who="revert"):
            n += 1
    return n


def word_matches_fixable(conn, words, limit=300):
    """word_matches(), minus lines already fixed by hand -- what the fix-words
    page is allowed to offer for a bulk fix. The engine still returns those
    lines (flagged "human") to whoever calls word_matches() directly."""
    rows, variants, total = word_matches(conn, words, limit=limit)
    return [r for r in rows if not r["human"]], variants, total

# The same word, spelled other ways. Roman Urdu has no fixed spelling, so the
# machine wrote namaz/namaaz/namazi and khushu/khusho/khushi. Matching on the
# consonants finds them all -- and inevitably finds different words that happen
# to share consonants (dua/due, khushu/khushi), which is why this only ever
# OFFERS: the owner taps the ones that are the same word.
# ponytail: consonant matching over-offers; upgrade: rank by edit distance if
# the list ever gets long enough to be annoying.
_VOWELS = re.compile(r"[aeiou']")
_DOUBLED = re.compile(r"(.)\1+")


def _skeleton(w):
    return _DOUBLED.sub(r"\1", _VOWELS.sub("", (w or "").lower()))


def spellings_like(conn, word, limit=12):
    """[(spelling, times it appears)] for other spellings of `word`, commonest first."""
    word = (word or "").strip().lower()
    if len(word) < 2:
        return []
    try:
        conn.execute("CREATE VIRTUAL TABLE temp.vocab USING fts5vocab(main, 'segments_fts', 'row')")
    except sqlite3.Error:
        pass  # already created on this connection
    want = _skeleton(word)
    try:
        rows = [(t, n) for t, n in conn.execute(
            "SELECT term, cnt FROM temp.vocab WHERE term GLOB ?", (word[:2] + "*",))
            if t != word and _skeleton(t) == want]
    except sqlite3.Error:
        return []
    rows.sort(key=lambda r: -r[1])
    return rows[:limit]

