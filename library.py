"""Creator's library: saved videos, segment bookmarks, tag "playlists", and a
searchable activity history.

All of it is keyed only to the store (one shared creator login), so there is no
per-user column — a save is a save. Tags on a save double as playlists: "every
save tagged darwin" is the Darwin playlist.

Everything here writes, so callers pass a writable connection (db.connect()).
"""
import datetime


def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _norm_tag(t):
    return " ".join((t or "").strip().lower().split())[:40]


# --- saving / bookmarking --------------------------------------------------

def toggle_video(conn, video_id, tags=None):
    """Save or unsave a whole video. Returns {"saved": bool, "bookmark_id": int|None}."""
    row = conn.execute(
        "SELECT id FROM bookmarks WHERE video_id = ? AND segment_id IS NULL", (video_id,)
    ).fetchone()
    if row:
        conn.execute("DELETE FROM bookmark_tags WHERE bookmark_id = ?", (row[0],))
        conn.execute("DELETE FROM bookmarks WHERE id = ?", (row[0],))
        conn.commit()
        return {"saved": False, "bookmark_id": None}
    conn.execute(
        "INSERT INTO bookmarks (video_id, segment_id, start_time, created_at) VALUES (?, NULL, NULL, ?)",
        (video_id, _now()),
    )
    bid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    _apply_tags(conn, bid, tags)
    conn.commit()
    return {"saved": True, "bookmark_id": bid}


def toggle_segment(conn, video_id, segment_id, start_time=None, tags=None):
    """Bookmark or un-bookmark one line. Returns {"saved": bool, "bookmark_id": int|None}."""
    row = conn.execute(
        "SELECT id FROM bookmarks WHERE video_id = ? AND segment_id = ?", (video_id, segment_id)
    ).fetchone()
    if row:
        conn.execute("DELETE FROM bookmark_tags WHERE bookmark_id = ?", (row[0],))
        conn.execute("DELETE FROM bookmarks WHERE id = ?", (row[0],))
        conn.commit()
        return {"saved": False, "bookmark_id": None}
    if start_time is None:
        r = conn.execute("SELECT start_time FROM segments WHERE id = ?", (segment_id,)).fetchone()
        start_time = r[0] if r else None
    conn.execute(
        "INSERT INTO bookmarks (video_id, segment_id, start_time, created_at) VALUES (?, ?, ?, ?)",
        (video_id, segment_id, start_time, _now()),
    )
    bid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    _apply_tags(conn, bid, tags)
    conn.commit()
    return {"saved": True, "bookmark_id": bid}


def delete(conn, bookmark_id):
    conn.execute("DELETE FROM bookmark_tags WHERE bookmark_id = ?", (bookmark_id,))
    conn.execute("DELETE FROM bookmarks WHERE id = ?", (bookmark_id,))
    conn.commit()


def _apply_tags(conn, bookmark_id, tags):
    for t in tags or []:
        t = _norm_tag(t)
        if t:
            conn.execute(
                "INSERT OR IGNORE INTO bookmark_tags (bookmark_id, tag) VALUES (?, ?)",
                (bookmark_id, t),
            )


def add_tag(conn, bookmark_id, tag):
    tag = _norm_tag(tag)
    if not tag:
        return False
    conn.execute(
        "INSERT OR IGNORE INTO bookmark_tags (bookmark_id, tag) VALUES (?, ?)", (bookmark_id, tag)
    )
    conn.commit()
    return True


def remove_tag(conn, bookmark_id, tag):
    conn.execute(
        "DELETE FROM bookmark_tags WHERE bookmark_id = ? AND tag = ?", (bookmark_id, _norm_tag(tag))
    )
    conn.commit()


def saved_state(conn, video_id, segment_ids=None):
    """What is already saved for a video, so the page can render stars filled:
    {"video": bool, "segments": [ids]}."""
    v = conn.execute(
        "SELECT 1 FROM bookmarks WHERE video_id = ? AND segment_id IS NULL", (video_id,)
    ).fetchone()
    segs = [
        r[0]
        for r in conn.execute(
            "SELECT segment_id FROM bookmarks WHERE video_id = ? AND segment_id IS NOT NULL",
            (video_id,),
        )
    ]
    return {"video": bool(v), "segments": segs}


# --- tags / playlists ------------------------------------------------------

def all_tags(conn):
    """Every tag with how many saves carry it — the creator's playlists."""
    return [
        {"tag": r[0], "count": r[1]}
        for r in conn.execute(
            "SELECT tag, COUNT(*) FROM bookmark_tags GROUP BY tag ORDER BY tag"
        )
    ]


# --- listing / browsing ----------------------------------------------------

def _date_clause(col, date_from, date_to, params):
    sql = ""
    if date_from:
        sql += f" AND {col} >= ?"
        params.append(date_from)
    if date_to:
        sql += f" AND {col} < ?"
        params.append(date_to + "T99")  # inclusive of the whole day (ISO strings)
    return sql


def list_saved(conn, tag=None, q=None, date_from=None, date_to=None, limit=500):
    """Saved videos and segment bookmarks, newest first. Optional filters:
    tag (playlist), q (title/note text), date range on when it was saved."""
    params = []
    where = "WHERE 1=1"
    if tag:
        where += " AND b.id IN (SELECT bookmark_id FROM bookmark_tags WHERE tag = ?)"
        params.append(_norm_tag(tag))
    if q:
        where += " AND (v.title LIKE ? OR b.note LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    where += _date_clause("b.created_at", date_from, date_to, params)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT b.id, b.video_id, b.segment_id, b.start_time, b.note, b.created_at,
               v.title, v.youtube_url,
               s.roman_text, s.urdu_text
        FROM bookmarks b
        JOIN videos v ON v.id = b.video_id
        LEFT JOIN segments s ON s.id = b.segment_id
        {where}
        ORDER BY b.created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    ids = [r[0] for r in rows]
    tagmap = _tags_for(conn, ids)
    return [
        {
            "id": r[0], "video_id": r[1], "segment_id": r[2], "start_time": r[3],
            "note": r[4], "created_at": r[5], "title": r[6], "youtube_url": r[7],
            "roman_text": r[8], "urdu_text": r[9],
            "is_segment": r[2] is not None,
            "tags": tagmap.get(r[0], []),
        }
        for r in rows
    ]


def _tags_for(conn, bookmark_ids):
    if not bookmark_ids:
        return {}
    out = {}
    qs = ",".join("?" for _ in bookmark_ids)
    for bid, tag in conn.execute(
        f"SELECT bookmark_id, tag FROM bookmark_tags WHERE bookmark_id IN ({qs}) ORDER BY tag",
        bookmark_ids,
    ):
        out.setdefault(bid, []).append(tag)
    return out


def list_romanized(conn, q=None, date_from=None, date_to=None, limit=500):
    """Videos that have Roman Urdu, most-recently-romanized first. Searchable by
    title, filterable by the date they were romanized."""
    params = []
    having = ""
    where = "WHERE s.roman_at IS NOT NULL"
    if q:
        where += " AND v.title LIKE ?"
        params.append(f"%{q}%")
    # date filter applies to the video's most recent romanization
    date_sql = _date_clause("MAX(s.roman_at)", date_from, date_to, [])
    if date_from or date_to:
        having = "HAVING 1=1" + date_sql
        if date_from:
            params.append(date_from)
        if date_to:
            params.append(date_to + "T99")
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT v.id, v.title, v.youtube_url,
               SUM(CASE WHEN s.roman_text IS NOT NULL THEN 1 ELSE 0 END),
               COUNT(*), MAX(s.roman_at)
        FROM videos v JOIN segments s ON s.video_id = v.id
        {where}
        GROUP BY v.id
        {having}
        ORDER BY MAX(s.roman_at) DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [
        {"id": r[0], "title": r[1], "youtube_url": r[2],
         "done": r[3], "total": r[4], "last": r[5]}
        for r in rows
    ]


def history(conn, q=None, date_from=None, date_to=None, limit=200):
    """A unified activity timeline the creator can scan or search: videos
    transcribed, videos romanized, and items saved — newest first.

    Each entry: {kind, when, title, video_id, detail}. `kind` is one of
    'transcribe', 'romanize', 'save'. Searchable by title/detail, date range.
    """
    events = []

    # transcription + romanize-all jobs
    jrows = conn.execute(
        """
        SELECT kind, COALESCE(updated_at, created_at), title, youtube_url, video_id, status, detail
        FROM jobs ORDER BY COALESCE(updated_at, created_at) DESC LIMIT 400
        """
    ).fetchall()
    for kind, when, title, url, vid, status, detail in jrows:
        label = title or url or "video"
        events.append({
            "kind": "transcribe" if kind == "transcribe" else "romanize",
            "when": when, "title": label, "video_id": vid,
            "detail": (status or "") + (f" — {detail}" if detail else ""),
        })

    # per-video romanization (from segments.roman_at), one event per video
    for vid, title, last, n in conn.execute(
        """
        SELECT v.id, v.title, MAX(s.roman_at), COUNT(s.roman_at)
        FROM videos v JOIN segments s ON s.video_id = v.id
        WHERE s.roman_at IS NOT NULL
        GROUP BY v.id ORDER BY MAX(s.roman_at) DESC LIMIT 400
        """
    ):
        events.append({
            "kind": "romanize", "when": last, "title": title or "video",
            "video_id": vid, "detail": f"{n} lines romanized",
        })

    # saves
    for vid, seg, when, title in conn.execute(
        """
        SELECT b.video_id, b.segment_id, b.created_at, v.title
        FROM bookmarks b JOIN videos v ON v.id = b.video_id
        ORDER BY b.created_at DESC LIMIT 400
        """
    ):
        events.append({
            "kind": "save", "when": when, "title": title or "video", "video_id": vid,
            "detail": "bookmarked a segment" if seg is not None else "saved the video",
        })

    # filter + sort
    qlow = (q or "").lower()

    def keep(e):
        if e["when"] is None:
            return False
        if date_from and e["when"] < date_from:
            return False
        if date_to and e["when"] >= date_to + "T99":
            return False
        if qlow and qlow not in (e["title"] or "").lower() and qlow not in (e["detail"] or "").lower():
            return False
        return True

    events = [e for e in events if keep(e)]
    events.sort(key=lambda e: e["when"], reverse=True)
    return events[:limit]


def saved_segment_set(conn, segment_ids):
    """Which of these segment ids are bookmarked (for rendering filled stars)."""
    ids = [int(i) for i in segment_ids if i is not None]
    if not ids:
        return set()
    qs = ",".join("?" for _ in ids)
    return {
        r[0]
        for r in conn.execute(
            f"SELECT segment_id FROM bookmarks WHERE segment_id IN ({qs})", ids
        )
    }


def saved_video_set(conn, video_ids):
    """Which of these video ids are saved whole (for rendering filled stars)."""
    ids = [int(i) for i in video_ids if i is not None]
    if not ids:
        return set()
    qs = ",".join("?" for _ in ids)
    return {
        r[0]
        for r in conn.execute(
            f"SELECT video_id FROM bookmarks WHERE segment_id IS NULL AND video_id IN ({qs})", ids
        )
    }


def counts(conn):
    """Small summary for the Library nav/heading."""
    saved = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
    tags = conn.execute("SELECT COUNT(DISTINCT tag) FROM bookmark_tags").fetchone()[0]
    return {"saved": saved, "tags": tags}
