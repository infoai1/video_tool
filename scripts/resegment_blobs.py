#!/usr/bin/env python3
"""Re-segment blob transcripts using Soniox word-level timestamps.

Some source `video_segments` rows hold an entire lecture in ONE row (a "blob"),
so search returns an hour-long wall of text. But those rows also carry
`soniox_word_tokens` — per-word start_ms already computed. This splits each blob
into sentence-sized segments with REAL timestamps, replacing the blob in roman.db.

No re-transcription, no network. roman_text is left NULL; the app's ensure()
romanizes each new line on demand (or a later bulk pass).

Usage:
  resegment_blobs.py --db <roman.db> --source <annotation.db> [--dry-run] [--limit N]

busy_timeout lets writes queue behind the live app instead of erroring.
Writes are per-video transactions.
"""
import argparse, json, re, sqlite3, sys, os, datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import normalize  # noqa: E402  (same normalize the app uses, for identical search)

MAX_WORDS = 18          # hard cap on a sentence's length
MIN_WORDS = 3           # don't emit sub-fragments
PAUSE_MS = 1500         # a >1.5s gap between words also ends a sentence
END_PUNCT = re.compile(r'[।．.?!॥]$')  # danda + latin/fullwidth stops


def words_from_tokens(tokens):
    """Reconstruct (word, start_ms) from Soniox character tokens.

    A token with is_word_start=True begins a new word; others append to it.
    """
    words, cur, start = [], "", None
    for t in tokens:
        if t.get("is_word_start") and cur:
            words.append((cur, start)); cur, start = "", None
        if start is None:
            start = t.get("start_ms", 0)
        cur += t.get("word", "")
    if cur:
        words.append((cur, start))
    return words


def sentences(words):
    """Group words into (start_sec, text) on punctuation, length cap, or pause."""
    out, buf, s_start, prev_ms = [], [], None, None
    for w, ms in words:
        if s_start is None:
            s_start = ms
        gap = (ms - prev_ms) if prev_ms is not None else 0
        # a long pause ends the *previous* sentence before adding this word
        if buf and gap > PAUSE_MS and len(buf) >= MIN_WORDS:
            out.append((s_start / 1000.0, " ".join(buf)))
            buf, s_start = [], ms
        buf.append(w)
        prev_ms = ms
        if (END_PUNCT.search(w) and len(buf) >= MIN_WORDS) or len(buf) >= MAX_WORDS:
            out.append((s_start / 1000.0, " ".join(buf)))
            buf, s_start = [], None
    if buf:
        out.append(((s_start or 0) / 1000.0, " ".join(buf)))
    return out


def coarse_segments(db):
    """roman.db segments belonging to a blobby video (<=5 segments)."""
    return db.execute("""
        SELECT s.id, s.video_id, s.start_time, v.source_video_id
        FROM segments s JOIN videos v ON v.id = s.video_id
        WHERE s.video_id IN (
            SELECT video_id FROM segments GROUP BY video_id HAVING count(*) <= 5)
    """).fetchall()


def token_row(src, source_video_id, start_time):
    """The source video_segments row nearest this roman segment's start_time."""
    return src.execute("""
        SELECT soniox_word_tokens FROM video_segments
        WHERE video_id = ? AND soniox_word_tokens IS NOT NULL
              AND length(soniox_word_tokens) > 10
        ORDER BY abs(COALESCE(start_time,0) - ?) LIMIT 1
    """, (source_video_id, start_time or 0)).fetchone()


def replace_segment(db, seg_id, video_id, sents, now):
    """Delete one blob segment (+its FTS) and insert the timestamped sentences."""
    db.execute("DELETE FROM segments WHERE id = ?", (seg_id,))
    db.execute("DELETE FROM urdu_fts WHERE rowid = ?", (seg_id,))
    db.execute("DELETE FROM segments_fts WHERE rowid = ?", (seg_id,))
    made = 0
    for st, txt in sents:
        un = normalize.normalize_urdu(txt)
        cur = db.execute(
            "INSERT OR IGNORE INTO segments "
            "(video_id, start_time, urdu_text, urdu_norm, created_at) "
            "VALUES (?, ?, ?, ?, ?)", (video_id, st, txt, un, now))
        if cur.rowcount:
            db.execute("INSERT INTO urdu_fts (rowid, urdu_norm) VALUES (?, ?)",
                       (cur.lastrowid, un))
            made += 1
    return made


def run(db_path, src_path, dry_run, limit):
    db = sqlite3.connect(db_path, timeout=30)
    db.execute("PRAGMA busy_timeout=30000")
    src = sqlite3.connect(src_path)  # read-only source
    now = datetime.datetime.utcnow().isoformat()
    vids_fixed = segs_removed = segs_made = skipped_no_tokens = skipped_no_gain = 0
    for seg_id, video_id, start_time, svid in coarse_segments(db):
        row = token_row(src, svid, start_time)
        if not row:
            skipped_no_tokens += 1
            continue
        try:
            toks = json.loads(row[0])
        except Exception:
            skipped_no_tokens += 1
            continue
        sents = sentences(words_from_tokens(toks))
        if len(sents) <= 1:                       # no finer breakdown available
            skipped_no_gain += 1
            continue
        if not dry_run:
            made = replace_segment(db, seg_id, video_id, sents, now)
            db.commit()
        else:
            made = len(sents)
        vids_fixed += 1; segs_removed += 1; segs_made += made
        if limit and vids_fixed >= limit:
            break
    db.close(); src.close()
    print(f"{'DRY-RUN ' if dry_run else ''}resegment complete")
    print(f"  blob segments replaced : {segs_removed}")
    print(f"  timestamped lines made : {segs_made}")
    print(f"  skipped (no word data) : {skipped_no_tokens}")
    print(f"  skipped (no finer split): {skipped_no_gain}")


def _selftest():
    toks = [{"word": "aa", "is_word_start": True, "start_ms": 1000},
            {"word": "b", "is_word_start": False, "start_ms": 1100},
            {"word": "two", "is_word_start": True, "start_ms": 1500},
            {"word": "three.", "is_word_start": True, "start_ms": 1800},
            {"word": "four", "is_word_start": True, "start_ms": 9000},
            {"word": "five", "is_word_start": True, "start_ms": 9200},
            {"word": "six.", "is_word_start": True, "start_ms": 9400}]
    w = words_from_tokens(toks)
    assert w[0] == ("aab", 1000), w
    s = sentences(w)
    assert len(s) == 2 and abs(s[0][0] - 1.0) < 1e-6 and abs(s[1][0] - 9.0) < 1e-6, s
    print("selftest OK")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db"); ap.add_argument("--source")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        _selftest(); sys.exit(0)
    run(a.db, a.source, a.dry_run, a.limit)
