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


def current(conn, seg_id):
    """(video_id, the line as it is read today) or None."""
    row = conn.execute(
        "SELECT video_id, COALESCE(NULLIF(TRIM(roman_clean), ''), roman_text) "
        "FROM segments WHERE id = ?", (seg_id,)).fetchone()
    return (row[0], row[1] or "") if row else None


def apply(conn, seg_id, text, who="owner"):
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
        "INSERT INTO corrections (segment_id, video_id, was, now, who, at) VALUES (?, ?, ?, ?, ?, ?)",
        (seg_id, video_id, was, text, who,
         datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")))
    return True


def history(conn, video_id, limit=200):
    """What has been corrected in one lecture, newest first."""
    ensure_schema(conn)
    return [{"segment_id": r[0], "was": r[1], "now": r[2], "who": r[3], "at": r[4]}
            for r in conn.execute(
                "SELECT segment_id, was, now, who, at FROM corrections "
                "WHERE video_id = ? ORDER BY id DESC LIMIT ?", (video_id, limit))]


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


def window(conn, video_id, around=None, span=120, limit=60):
    """The lines to put in front of a corrector.

    `around` is a second in the lecture -- the moment a reader questioned. The
    window opens a little before it so the sentence has its run-up, and runs
    `span` seconds on. With no second, the lecture starts from the top.
    """
    start = max(0.0, float(around) - 20) if around is not None else 0.0
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT id, start_time, COALESCE(NULLIF(TRIM(roman_clean), ''), roman_text), "
        "       urdu_text, roman_clean IS NOT NULL AND TRIM(roman_clean) != '' "
        "FROM segments WHERE video_id = ? AND start_time >= ? AND start_time <= ? "
        "ORDER BY start_time LIMIT ?",
        (video_id, start, start + span, limit)).fetchall()
    # "was": the wording before the latest correction, so an Undo button on a
    # corrected line knows what to put back -- one row per segment, newest first.
    was = {}
    if rows:
        ids = [r[0] for r in rows]
        qm = ",".join("?" * len(ids))
        for seg_id, prev in conn.execute(
                f"SELECT segment_id, was FROM corrections WHERE segment_id IN ({qm}) "
                f"AND id IN (SELECT MAX(id) FROM corrections WHERE segment_id IN ({qm}) "
                f"GROUP BY segment_id)", ids + ids):
            was[seg_id] = prev
    return [{"id": r[0], "t": float(r[1] or 0), "text": r[2] or "", "urdu": r[3] or "",
             "corrected": bool(r[4]), "was": was.get(r[0], "")} for r in rows]


def glossary_apply(conn, wrong, right, who="owner", dry=False):
    """Fix one word everywhere the search index can find it.

    `dry=True` reports what would change without writing anything. A line a
    human corrected by hand (its latest `corrections.who` is neither
    "glossary:..." nor "revert") is left alone -- the glossary must never
    overwrite a human's judgement call.
    """
    wrong = (wrong or "").strip()
    right = (right or "").strip()
    if not wrong or not right:
        return {"error": "both words are required"}
    if " " in wrong or " " in right or "\t" in wrong or "\t" in right:
        return {"error": "one word at a time, please"}
    if wrong.lower() == right.lower():
        return {"error": "the two words are the same"}

    ensure_schema(conn)
    key = normalize.normalize(wrong)
    if not key or " " in key:
        return {"error": "not a single searchable word"}
    pattern = re.compile(r"(?<!\w)" + re.escape(wrong) + r"(?!\w)", re.I)

    def _replacement(m):
        word = m.group(0)
        rep = right
        if word[:1].isupper():
            rep = rep[:1].upper() + rep[1:]
        return rep

    changed, skipped, lecture_ids, sample = [], 0, set(), []
    for (seg_id,) in conn.execute(
            "SELECT rowid FROM segments_fts WHERE segments_fts MATCH ?", (key,)).fetchall():
        found = current(conn, seg_id)
        if found is None:
            continue
        video_id, text = found
        new_text, n = pattern.subn(_replacement, text)
        if n == 0:  # FTS is a prefilter -- a real word boundary may not match
            continue
        prior = last(conn, seg_id)
        if prior is not None and prior["who"] not in (None,) and \
                not str(prior["who"]).startswith("glossary:") and prior["who"] != "revert":
            skipped += 1
            continue
        changed.append((seg_id, video_id, text, new_text))
        lecture_ids.add(video_id)
        if len(sample) < 5:
            sample.append({"segment_id": seg_id, "before": text, "after": new_text})

    result = {"lines": len(changed), "lectures": len(lecture_ids),
              "skipped_human": skipped, "sample": sample}
    if dry:
        return result

    who_tag = f"glossary:{wrong}"
    for seg_id, video_id, text, new_text in changed:
        apply(conn, seg_id, new_text, who=who_tag)
    conn.execute(
        "INSERT INTO word_fixes (wrong, right, who, at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(wrong) DO UPDATE SET right=excluded.right, who=excluded.who, at=excluded.at",
        (wrong, right, who, datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")))
    return result


def word_fixes(conn):
    """Every word fix remembered, most recent first."""
    ensure_schema(conn)
    return [{"wrong": r[0], "right": r[1], "who": r[2], "at": r[3]}
            for r in conn.execute("SELECT wrong, right, who, at FROM word_fixes ORDER BY at DESC")]
