"""Pull Urdu-script segments from the source into the tool's own store.

Copies videos and their timestamped segments into roman.db with roman_text left
NULL — transliteration fills that in later. Idempotent: re-running only inserts
what is missing (UNIQUE constraints do the de-duping), so an interrupted ingest
just resumes.
"""
import datetime

import db
import normalize
import source


def ingest(limit=None, source_path=None, db_path=None):
    """Copy source segments into the store, indexing the Urdu script for search.

    Returns (new_videos, new_segments)."""
    db.init_db(db_path)
    conn = db.connect(db_path)
    new_videos = 0
    new_segments = 0
    now = datetime.datetime.utcnow().isoformat()
    # Cache source_video_id -> local video id so we resolve each video once.
    video_ids = {}
    try:
        for row in source.iter_segments(path=source_path, limit=limit):
            svid = row["source_video_id"]
            local_id = video_ids.get(svid)
            if local_id is None:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO videos (source_video_id, youtube_url, title) "
                    "VALUES (?, ?, ?)",
                    (svid, row["youtube_url"], row["video_title"]),
                )
                if cur.rowcount:
                    new_videos += 1
                local_id = conn.execute(
                    "SELECT id FROM videos WHERE source_video_id = ?", (svid,)
                ).fetchone()[0]
                video_ids[svid] = local_id

            urdu_norm = normalize.normalize_urdu(row["urdu_text"])
            cur = conn.execute(
                "INSERT OR IGNORE INTO segments "
                "(video_id, start_time, urdu_text, urdu_norm, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (local_id, row["start_time"], row["urdu_text"], urdu_norm, now),
            )
            if cur.rowcount:
                new_segments += 1
                # Index the Urdu now so the segment is searchable before any
                # transliteration. rowid == segments.id.
                seg_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO urdu_fts (rowid, urdu_norm) VALUES (?, ?)",
                    (seg_id, urdu_norm),
                )
        conn.commit()
    finally:
        conn.close()
    return new_videos, new_segments
