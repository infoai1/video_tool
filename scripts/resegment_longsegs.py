#!/usr/bin/env python3
"""Two-pass re-segmentation of long (>30s implied duration) roman.db segments.

Generalizes resegment_blobs.py (which only handled whole-video blobs, <=5
segments/video) to ANY segment whose implied duration exceeds 30s, regardless
of how many segments its video has.

Pass "exact": segment's source row (annotation.db video_segments) has
soniox_word_tokens -> split precisely at word timestamps (reuses the proven
words_from_tokens/sentences logic from resegment_blobs.py).

Pass "estimated": no word tokens available -> split urdu_text into sentences
on punctuation (danda/./?/!) + a word cap, and spread them EVENLY across the
segment's own span [start_time, next_segment_start). Approximate but
monotonic, never exceeds the segment's span.

Both passes: per-video transaction, delete old segment from segments +
urdu_fts + segments_fts, insert new rows the same way (roman_text left NULL,
filled on demand by the app -- same as resegment_blobs.py).

Usage:
  resegment_longsegs.py --db roman.db --source annotation.db --mode exact|estimated [--dry-run] [--limit N]
  resegment_longsegs.py --selftest

SAFETY: point --db at the STAGING roman.db only.
"""
import argparse, json, re, sqlite3, sys, os, datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import normalize  # noqa: E402

MAX_WORDS = 18
MIN_WORDS = 3
PAUSE_MS = 1500
LONG_SEC = 30.0
END_PUNCT = re.compile(r'[।۔.?!॥]')  # danda (urdu ۔ + devanagari ।) + latin stops
# split-on regex: keep the punctuation attached to preceding sentence
_SENT_SPLIT = re.compile(r'(?<=[۔।.?!॥])\s+')


# ---------- shared word/sentence splitter (exact pass), from resegment_blobs.py ----------

def words_from_tokens(tokens):
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


def sentences_from_words(words):
    """Group words into (start_sec, text) on punctuation, length cap, or pause."""
    out, buf, s_start, prev_ms = [], [], None, None
    for w, ms in words:
        if s_start is None:
            s_start = ms
        gap = (ms - prev_ms) if prev_ms is not None else 0
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


# ---------- estimated-pass sentence splitter (text only, no timings) ----------

def sentences_from_text(text):
    """Split urdu_text into sentence-ish chunks on punctuation + a word cap."""
    raw = [p.strip() for p in _SENT_SPLIT.split(text.strip()) if p.strip()]
    if not raw:
        return []
    out = []
    for chunk in raw:
        words = chunk.split()
        if len(words) <= MAX_WORDS:
            out.append(chunk)
        else:
            # too-long chunk (no punctuation): cut on word cap
            for i in range(0, len(words), MAX_WORDS):
                out.append(" ".join(words[i:i + MAX_WORDS]))
    # merge any tiny trailing fragment into the previous one
    merged = []
    for c in out:
        if merged and len(c.split()) < MIN_WORDS:
            merged[-1] = merged[-1] + " " + c
        else:
            merged.append(c)
    return merged


def spread_evenly(texts, start_time, span_end):
    """Assign each text a timestamp spread evenly across [start_time, span_end)."""
    n = len(texts)
    if n <= 1:
        return [(start_time, t) for t in texts]
    span = max(span_end - start_time, 0.0)
    step = span / n
    return [(start_time + i * step, t) for i, t in enumerate(texts)]


# ---------- db access ----------

def long_segments(db):
    """(id, video_id, source_video_id, start_time, urdu_text, next_start) for
    every roman.db segment whose implied duration (gap to next segment's
    start_time within the same video) exceeds LONG_SEC. Last segment of a
    video has no 'next' to measure against, so it's excluded (unknowable)."""
    return db.execute("""
        SELECT s.id, s.video_id, v.source_video_id, s.start_time, s.urdu_text,
               (SELECT MIN(s2.start_time) FROM segments s2
                WHERE s2.video_id = s.video_id AND s2.start_time > s.start_time) AS next_start
        FROM segments s JOIN videos v ON v.id = s.video_id
        WHERE next_start IS NOT NULL AND (next_start - s.start_time) > ?
    """, (LONG_SEC,)).fetchall()


def token_row(src, source_video_id, start_time):
    return src.execute("""
        SELECT soniox_word_tokens FROM video_segments
        WHERE video_id = ? AND soniox_word_tokens IS NOT NULL
              AND length(soniox_word_tokens) > 10
        ORDER BY abs(COALESCE(start_time,0) - ?) LIMIT 1
    """, (source_video_id, start_time or 0)).fetchone()


def replace_segment(db, seg_id, video_id, sents, now):
    """Delete one long segment (+ its FTS rows) and insert timestamped sentences."""
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


def run(db_path, src_path, mode, dry_run, limit):
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA busy_timeout=30000")
    src = sqlite3.connect(src_path) if src_path else None
    now = datetime.datetime.utcnow().isoformat()
    segs_replaced = segs_made = skipped_no_tokens = skipped_no_gain = 0
    rows = long_segments(db)
    for seg_id, video_id, svid, start_time, urdu_text, next_start in rows:
        if mode == "exact":
            row = token_row(src, svid, start_time) if svid else None
            if not row:
                skipped_no_tokens += 1
                continue
            try:
                toks = json.loads(row[0])
            except Exception:
                skipped_no_tokens += 1
                continue
            sents = sentences_from_words(words_from_tokens(toks))
            if len(sents) <= 1:
                skipped_no_gain += 1
                continue
        else:  # estimated
            has_tokens = svid and token_row(src, svid, start_time) if src else None
            if has_tokens:
                continue  # exact pass owns this one
            texts = sentences_from_text(urdu_text)
            if len(texts) <= 1:
                skipped_no_gain += 1
                continue
            sents = spread_evenly(texts, start_time, next_start)

        if not dry_run:
            made = replace_segment(db, seg_id, video_id, sents, now)
            db.commit()
        else:
            made = len(sents)
        segs_replaced += 1; segs_made += made
        if limit and segs_replaced >= limit:
            break
    db.close()
    if src:
        src.close()
    print(f"{'DRY-RUN ' if dry_run else ''}resegment ({mode}) complete")
    print(f"  candidate long segments  : {len(rows)}")
    print(f"  segments replaced        : {segs_replaced}")
    print(f"  timestamped lines made   : {segs_made}")
    print(f"  skipped (no word data)   : {skipped_no_tokens}")
    print(f"  skipped (no finer split) : {skipped_no_gain}")


def _selftest():
    # exact-pass word/sentence splitter (same as resegment_blobs.py)
    toks = [{"word": "aa", "is_word_start": True, "start_ms": 1000},
            {"word": "b", "is_word_start": False, "start_ms": 1100},
            {"word": "two", "is_word_start": True, "start_ms": 1500},
            {"word": "three.", "is_word_start": True, "start_ms": 1800},
            {"word": "four", "is_word_start": True, "start_ms": 9000},
            {"word": "five", "is_word_start": True, "start_ms": 9200},
            {"word": "six.", "is_word_start": True, "start_ms": 9400}]
    w = words_from_tokens(toks)
    assert w[0] == ("aab", 1000), w
    s = sentences_from_words(w)
    assert len(s) == 2 and abs(s[0][0] - 1.0) < 1e-6 and abs(s[1][0] - 9.0) < 1e-6, s

    # estimated-pass text splitter
    text = "یہ پہلا جملہ ہے۔ یہ دوسرا جملہ ہے۔ یہ تیسرا جملہ ہے۔"
    parts = sentences_from_text(text)
    assert len(parts) == 3, parts
    spread = spread_evenly(parts, 100.0, 130.0)
    assert spread[0][0] == 100.0
    assert abs(spread[1][0] - 110.0) < 1e-6
    assert abs(spread[2][0] - 120.0) < 1e-6
    assert all(100.0 <= t < 130.0 for t, _ in spread)

    # long word-capped chunk with no punctuation
    long_text = " ".join(f"w{i}" for i in range(40))
    parts2 = sentences_from_text(long_text)
    assert len(parts2) == 3 and all(len(p.split()) <= MAX_WORDS for p in parts2), parts2

    print("selftest OK")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db"); ap.add_argument("--source")
    ap.add_argument("--mode", choices=["exact", "estimated"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        _selftest(); sys.exit(0)
    if not a.db or not a.mode:
        ap.error("--db and --mode are required (unless --selftest)")
    run(a.db, a.source, a.mode, a.dry_run, a.limit)
