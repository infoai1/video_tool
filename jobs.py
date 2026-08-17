"""Background job queue, stored in the `jobs` table.

Two kinds:
  - 'transcribe' — download a new YouTube video and transcribe it with Soniox.
  - 'romanize'   — transliterate a whole video to Roman Urdu in the background,
                   with progress, so the user can keep browsing.

The web app enqueues and reads status; worker.py claims and runs them. Claiming
is atomic (a conditional UPDATE), safe even if more than one worker runs.
"""
import datetime

COLS = ["id", "kind", "youtube_url", "title", "video_id", "status", "detail",
        "progress", "created_at", "updated_at"]
_SELECT = f"SELECT {', '.join(COLS)} FROM jobs"


def _now():
    return datetime.datetime.utcnow().isoformat() + "Z"


def as_dict(row):
    return dict(zip(COLS, row)) if row else None


def enqueue_transcribe(conn, url, title=None):
    now = _now()
    cur = conn.execute(
        "INSERT INTO jobs (kind, youtube_url, title, status, detail, created_at, updated_at) "
        "VALUES ('transcribe', ?, ?, 'queued', 'queued', ?, ?)",
        (url, title, now, now),
    )
    conn.commit()
    return cur.lastrowid


def enqueue_romanize(conn, video_id, title=None):
    now = _now()
    cur = conn.execute(
        "INSERT INTO jobs (kind, video_id, title, status, detail, created_at, updated_at) "
        "VALUES ('romanize', ?, ?, 'queued', 'queued', ?, ?)",
        (video_id, title, now, now),
    )
    conn.commit()
    return cur.lastrowid


def active_for_url(conn, url):
    return conn.execute(
        "SELECT id, status FROM jobs WHERE youtube_url = ? AND status IN ('queued','running') "
        "ORDER BY id DESC LIMIT 1",
        (url,),
    ).fetchone()


def active_romanize(conn, video_id):
    return conn.execute(
        "SELECT id, status FROM jobs WHERE kind='romanize' AND video_id = ? "
        "AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",
        (video_id,),
    ).fetchone()


def get(conn, job_id):
    return as_dict(conn.execute(f"{_SELECT} WHERE id = ?", (job_id,)).fetchone())


def recent(conn, limit=20):
    return [as_dict(r) for r in conn.execute(f"{_SELECT} ORDER BY id DESC LIMIT ?", (limit,))]


def claim(conn):
    """Atomically take the oldest queued job. Returns its id, or None."""
    row = conn.execute("SELECT id FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
    if row is None:
        return None
    cur = conn.execute(
        "UPDATE jobs SET status='running', detail='starting…', updated_at=? "
        "WHERE id=? AND status='queued'",
        (_now(), row[0]),
    )
    conn.commit()
    return row[0] if cur.rowcount else None


def update(conn, job_id, status=None, detail=None, progress=None):
    sets, args = ["updated_at = ?"], [_now()]
    if status is not None:
        sets.append("status = ?")
        args.append(status)
    if detail is not None:
        sets.append("detail = ?")
        args.append(detail)
    if progress is not None:
        sets.append("progress = ?")
        args.append(progress)
    args.append(job_id)
    conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", args)
    conn.commit()
