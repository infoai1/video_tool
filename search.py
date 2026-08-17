"""Search and browse the Roman Urdu store.

Search folds the query with the same normaliser used on the corpus (normalize.py)
and runs it against the FTS5 index as a prefix-AND query, so "namaz roza" finds
segments containing both, and partial words still match. Only the transcript is
indexed, so a hit means the words were actually spoken in that clip. Results are
ranked by BM25.
"""
import normalize


def youtube_timestamp_url(youtube_url, start_time):
    """Append a t= parameter so the link opens the video at that moment."""
    if not youtube_url:
        return youtube_url
    sep = "&" if "?" in youtube_url else "?"
    return f"{youtube_url}{sep}t={int(start_time)}s"


def _row_to_hit(row):
    seg_id, vid, title, url, start, roman, urdu = row
    return {
        "segment_id": seg_id,
        "video_id": vid,
        "video_title": title,
        "youtube_url": url,
        "start_time": start,
        "timestamp_url": youtube_timestamp_url(url, start),
        "roman_text": roman,
        "urdu_text": urdu,
    }


_SEARCH_SQL = """
SELECT s.id, v.id, v.title, v.youtube_url, s.start_time, s.roman_text, s.urdu_text
FROM segments_fts f
JOIN segments s ON s.id = f.rowid
JOIN videos v ON v.id = s.video_id
WHERE segments_fts MATCH ?
ORDER BY bm25(segments_fts)
LIMIT ?
"""


def search(conn, q, limit=50):
    """Return up to `limit` matching segments, best first. [] if the query is empty."""
    tokens = normalize.query_tokens(q)
    if not tokens:
        return []
    # tokens are already [a-z0-9] only (normalize strips everything else), so
    # building the MATCH string by concatenation is safe here.
    match = " AND ".join(f"{t}*" for t in tokens)
    rows = conn.execute(_SEARCH_SQL, (match, limit)).fetchall()
    return [_row_to_hit(r) for r in rows]


def get_video(conn, video_id):
    """A video's metadata plus its full transcript, ordered by timestamp.

    Returns None if the video id is unknown. Segments not yet transliterated
    still appear (roman_text is None) so the page reflects real coverage.
    """
    meta = conn.execute(
        "SELECT id, title, youtube_url FROM videos WHERE id = ?", (video_id,)
    ).fetchone()
    if meta is None:
        return None
    rows = conn.execute(
        "SELECT id, start_time, roman_text, urdu_text FROM segments "
        "WHERE video_id = ? ORDER BY start_time",
        (video_id,),
    ).fetchall()
    url = meta[2]
    segments = [
        {
            "segment_id": sid,
            "start_time": start,
            "timestamp_url": youtube_timestamp_url(url, start),
            "roman_text": roman,
            "urdu_text": urdu,
        }
        for sid, start, roman, urdu in rows
    ]
    return {"id": meta[0], "title": meta[1], "youtube_url": url, "segments": segments}


def list_videos(conn, limit=200):
    """Videos with their segment and transliterated-segment counts, for the index."""
    rows = conn.execute(
        """
        SELECT v.id, v.title, v.youtube_url,
               COUNT(s.id) AS segments,
               COUNT(s.roman_text) AS done
        FROM videos v LEFT JOIN segments s ON s.video_id = v.id
        GROUP BY v.id
        ORDER BY v.title
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {"id": r[0], "title": r[1], "youtube_url": r[2], "segments": r[3], "done": r[4]}
        for r in rows
    ]
