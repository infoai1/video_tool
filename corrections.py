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


def count(conn):
    ensure_schema(conn)
    return conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]


def window(conn, video_id, around=None, span=120, limit=60):
    """The lines to put in front of a corrector.

    `around` is a second in the lecture -- the moment a reader questioned. The
    window opens a little before it so the sentence has its run-up, and runs
    `span` seconds on. With no second, the lecture starts from the top.
    """
    start = max(0.0, float(around) - 20) if around is not None else 0.0
    rows = conn.execute(
        "SELECT id, start_time, COALESCE(NULLIF(TRIM(roman_clean), ''), roman_text), "
        "       urdu_text, roman_clean IS NOT NULL AND TRIM(roman_clean) != '' "
        "FROM segments WHERE video_id = ? AND start_time >= ? AND start_time <= ? "
        "ORDER BY start_time LIMIT ?",
        (video_id, start, start + span, limit)).fetchall()
    return [{"id": r[0], "t": float(r[1] or 0), "text": r[2] or "", "urdu": r[3] or "",
             "corrected": bool(r[4])} for r in rows]
