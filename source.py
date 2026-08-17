"""Read Urdu-script transcripts from the source annotation.db.

This is the CPS transcript database the shukr app also serves from. We only
read it, and we open it read-only so there is no way to corrupt it.

Schema we rely on (as used by the shukr app):
  videos(id, youtube_url, title)
  video_segments(video_id, start_time, text, soniox_text)

`text` is the curated transcript (covers a minority of videos); `soniox_text`
is the ASR transcript (covers ~96%). We prefer curated text and fall back to
ASR — exactly the shukr app's rule — so no video is left unreachable.
"""
import os
import sqlite3

import config

# COALESCE(NULLIF(TRIM(text),''), soniox_text): curated text wins when present
# and non-blank, else the ASR transcript. Repeated in WHERE so blank-on-both
# segments are skipped.
_TRANSCRIPT = "COALESCE(NULLIF(TRIM(s.text), ''), s.soniox_text)"

_SEGMENTS_SQL = f"""
SELECT v.id, v.youtube_url, v.title, s.start_time, {_TRANSCRIPT} AS urdu
FROM video_segments s
JOIN videos v ON v.id = s.video_id
WHERE {_TRANSCRIPT} IS NOT NULL AND TRIM({_TRANSCRIPT}) <> ''
ORDER BY v.id, s.start_time
"""


def _connect_ro(path):
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"source transcript DB not found at {path!r}. Set VIDEO_TOOL_SOURCE_DB "
            f"to the annotation.db path (see .env.example)."
        )
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def iter_segments(path=None, limit=None):
    """Yield source segments as dicts, ordered by video then timestamp.

    Each dict: source_video_id, youtube_url, video_title, start_time, urdu_text.
    `limit` caps the number of rows — useful for a pilot run over a subset.
    """
    conn = _connect_ro(path or config.SOURCE_DB)
    try:
        cur = conn.execute(_SEGMENTS_SQL)
        n = 0
        for vid, url, title, start, urdu in cur:
            yield {
                "source_video_id": vid,
                "youtube_url": url,
                "video_title": title,
                "start_time": start,
                "urdu_text": urdu,
            }
            n += 1
            if limit and n >= limit:
                return
    finally:
        conn.close()


def counts(path=None):
    """(videos, segments-with-transcript) in the source, for status reporting."""
    conn = _connect_ro(path or config.SOURCE_DB)
    try:
        videos = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        segs = conn.execute(
            f"SELECT COUNT(*) FROM video_segments s "
            f"WHERE {_TRANSCRIPT} IS NOT NULL AND TRIM({_TRANSCRIPT}) <> ''"
        ).fetchone()[0]
        return videos, segs
    finally:
        conn.close()
