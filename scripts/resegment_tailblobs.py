#!/usr/bin/env python3
"""Split blob segments that have NO next segment to bound them.

Two classes the gap-based passes miss:
  1. single-segment videos (the whole lecture in one line), and
  2. the LAST segment of a video (no next start_time to measure its span).

We have no end marker, so timestamps are estimated from speaking rate
(~140 wpm): each sentence's start = prior start + prior sentence's words / WPS.
Approximate but monotonic and readable — far better than a wall.

Usage: resegment_tailblobs.py --db <roman.db> [--dry-run] [--selftest]
Writes segments + urdu_fts (roman_text NULL, filled on-demand). STAGING first.
"""
import argparse, re, sqlite3, sys, os, datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import normalize  # noqa: E402

WPS = 140 / 60.0          # words per second (~140 wpm)
MAX_WORDS = 18
MIN_CHARS_SINGLE = 800    # a 1-segment video's line counts as a wall past this
MIN_CHARS_TAIL = 2000     # a video's last line counts as a wall past this
SENT_SPLIT = re.compile(r'(?<=[۔।.?!])\s+')


def split_sentences(text):
    parts = [p.strip() for p in SENT_SPLIT.split(text or "") if p.strip()]
    out = []
    for p in parts:                       # cap very long run-ons by word count
        w = p.split()
        if len(w) <= MAX_WORDS:
            out.append(p)
        else:
            for i in range(0, len(w), MAX_WORDS):
                out.append(" ".join(w[i:i + MAX_WORDS]))
    return out


def timed(sentences, start):
    """(start_sec, text) with cumulative timing from `start` at WPS."""
    out, t = [], float(start or 0)
    for s in sentences:
        out.append((t, s))
        t += max(1.5, len(s.split()) / WPS)
    return out


def targets(db):
    """Segments that are single-video-blobs or long last-lines, with no gap fix."""
    rows = db.execute("""
        WITH cnt AS (SELECT video_id, COUNT(*) n, MAX(start_time) mx FROM segments GROUP BY video_id)
        SELECT s.id, s.video_id, s.start_time, s.urdu_text, c.n
        FROM segments s JOIN cnt c ON c.video_id = s.video_id AND c.mx = s.start_time
    """).fetchall()
    for sid, vid, st, txt, n in rows:
        L = len(txt or "")
        if (n == 1 and L > MIN_CHARS_SINGLE) or (n > 1 and L > MIN_CHARS_TAIL):
            yield sid, vid, st, txt


def replace(db, sid, vid, sents, now):
    db.execute("DELETE FROM segments WHERE id = ?", (sid,))
    db.execute("DELETE FROM urdu_fts WHERE rowid = ?", (sid,))
    db.execute("DELETE FROM segments_fts WHERE rowid = ?", (sid,))
    made = 0
    for st, txt in sents:
        un = normalize.normalize_urdu(txt)
        cur = db.execute("INSERT OR IGNORE INTO segments "
                         "(video_id, start_time, urdu_text, urdu_norm, created_at) VALUES (?,?,?,?,?)",
                         (vid, st, txt, un, now))
        if cur.rowcount:
            db.execute("INSERT INTO urdu_fts (rowid, urdu_norm) VALUES (?, ?)", (cur.lastrowid, un))
            made += 1
    return made


def run(db_path, dry):
    db = sqlite3.connect(db_path, timeout=30)
    db.execute("PRAGMA busy_timeout=30000")
    now = datetime.datetime.utcnow().isoformat()
    vids = made = skipped = 0
    for sid, vid, st, txt in targets(db):
        sents = timed(split_sentences(txt), st)
        if len(sents) <= 1:
            skipped += 1
            continue
        if not dry:
            made += replace(db, sid, vid, sents, now)
            db.commit()
        else:
            made += len(sents)
        vids += 1
    db.close()
    print(f"{'DRY-RUN ' if dry else ''}tail-blob resegment complete")
    print(f"  blob lines replaced : {vids}")
    print(f"  timestamped lines   : {made}")
    print(f"  skipped (no split)  : {skipped}")


def _selftest():
    s = split_sentences("Ek. Do teen char paanch. " + " ".join(["x"] * 40))
    assert s[0] == "Ek." and s[1] == "Do teen char paanch.", s
    assert len(s) >= 4, s                       # 40-word run-on gets word-capped
    t = timed(["aa bb cc", "dd ee"], 10)
    assert t[0][0] == 10.0 and t[1][0] > 10.0, t  # cumulative, monotonic
    print("selftest OK")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        _selftest(); sys.exit(0)
    run(a.db, a.dry_run)
