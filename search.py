"""Search and browse the store.

The whole library is searchable immediately, before any transliteration, by
indexing the Urdu script itself. A Roman Urdu query is matched two ways and the
results are unioned:

  1. Roman index (segments_fts) — precise, covers segments already transliterated.
  2. Urdu index (urdu_fts) — full corpus. The Roman query is transliterated back
     to Urdu (once, then cached in query_cache) and matched here.

Both fold their side with the shared normaliser (normalize.py) and run a
prefix-AND FTS query, so "namaz roza" needs both words and partial words match.
Roman hits are listed first (higher precision); Urdu hits fill in the rest.
"""
import re

import normalize
import transliterate

# Pull an 11-char video id out of the common YouTube URL shapes, so a user can
# paste a link and land on that video's transcript.
_YT_ID = re.compile(
    r"(?:v=|/embed/|/live/|/shorts/|/v/|youtu\.be/)([A-Za-z0-9_-]{11})"
)
_BARE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_id(text):
    """The YouTube video id in `text` (a URL or a bare id), or None."""
    if not text:
        return None
    text = text.strip()
    m = _YT_ID.search(text)
    if m:
        return m.group(1)
    return text if _BARE_ID.match(text) else None


def find_video_by_youtube(conn, text):
    """Local video id for a pasted YouTube URL/id, or None if not in the library."""
    vid = youtube_id(text)
    if not vid:
        return None
    row = conn.execute(
        "SELECT id FROM videos WHERE youtube_url LIKE ? LIMIT 1", (f"%{vid}%",)
    ).fetchone()
    return row[0] if row else None


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


_HIT_COLS = "s.id, v.id, v.title, v.youtube_url, s.start_time, s.roman_text, s.urdu_text"

_ROMAN_SQL = f"""
SELECT {_HIT_COLS}
FROM segments_fts f JOIN segments s ON s.id = f.rowid JOIN videos v ON v.id = s.video_id
WHERE segments_fts MATCH ? ORDER BY bm25(segments_fts) LIMIT ?
"""

_URDU_SQL = f"""
SELECT {_HIT_COLS}
FROM urdu_fts f JOIN segments s ON s.id = f.rowid JOIN videos v ON v.id = s.video_id
WHERE urdu_fts MATCH ? ORDER BY bm25(urdu_fts) LIMIT ?
"""


def _resolve_query_urdu(conn, q, translit_query):
    """Roman query -> Urdu script, cached in query_cache. `conn` must be writable.

    A failed transliteration caches '' so a broken query isn't retried in a hot
    loop; the Roman index still answers on its own.
    """
    key = normalize.normalize(q)
    if not key:
        return ""
    row = conn.execute("SELECT urdu FROM query_cache WHERE roman_norm = ?", (key,)).fetchone()
    if row is not None:
        return row[0]
    fn = translit_query or transliterate.translit_query
    try:
        urdu = fn(q) or ""
    except Exception:
        urdu = ""
    conn.execute(
        "INSERT OR REPLACE INTO query_cache (roman_norm, urdu) VALUES (?, ?)", (key, urdu)
    )
    conn.commit()
    return urdu


def _fts_and(tokens):
    # tokens are already restricted to word chars by the normaliser, so building
    # the MATCH string by concatenation is safe.
    return " AND ".join(f"{t}*" for t in tokens)


def search(conn, q, limit=50, translit_query=None):
    """Return up to `limit` matching segments, best first. [] for an empty query.

    `translit_query` (roman -> urdu) is injectable for tests; production uses the
    configured provider. `conn` must be writable (the query transliteration is
    cached on first use)."""
    hits = []
    seen = set()

    def add(rows):
        for row in rows:
            if row[0] not in seen:
                seen.add(row[0])
                hits.append(_row_to_hit(row))

    roman_tokens = normalize.query_tokens(q)
    if roman_tokens:
        add(conn.execute(_ROMAN_SQL, (_fts_and(roman_tokens), limit)))

    urdu = _resolve_query_urdu(conn, q, translit_query)
    urdu_toks = normalize.urdu_tokens(urdu)
    if urdu_toks:
        add(conn.execute(_URDU_SQL, (_fts_and(urdu_toks), limit)))

    return hits[:limit]


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
