"""Background job queue (transcription), stored in the `jobs` table.

The web app enqueues jobs and reads their status; worker.py claims and runs them.
Claiming is atomic (a conditional UPDATE) so it is safe even if more than one
worker ever runs.
"""
import datetime


def _now():
    return datetime.datetime.utcnow().isoformat() + "Z"


def enqueue(conn, url, title=None, kind="transcribe"):
    now = _now()
    cur = conn.execute(
        "INSERT INTO jobs (kind, youtube_url, title, status, detail, created_at, updated_at) "
        "VALUES (?, ?, ?, 'queued', 'queued', ?, ?)",
        (kind, url, title, now, now),
    )
    conn.commit()
    return cur.lastrowid


def active_for_url(conn, url):
    """An unfinished job for this URL, as (id, status), or None."""
    return conn.execute(
        "SELECT id, status FROM jobs WHERE youtube_url = ? AND status IN ('queued', 'running') "
        "ORDER BY id DESC LIMIT 1",
        (url,),
    ).fetchone()


def get(conn, job_id):
    return conn.execute(
        "SELECT id, kind, youtube_url, title, status, detail, created_at, updated_at "
        "FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()


def recent(conn, limit=20):
    return conn.execute(
        "SELECT id, kind, youtube_url, title, status, detail, created_at, updated_at "
        "FROM jobs ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def claim(conn):
    """Atomically take the oldest queued job. Returns its id, or None."""
    row = conn.execute("SELECT id FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
    if row is None:
        return None
    cur = conn.execute(
        "UPDATE jobs SET status = 'running', detail = 'starting…', updated_at = ? "
        "WHERE id = ? AND status = 'queued'",
        (_now(), row[0]),
    )
    conn.commit()
    return row[0] if cur.rowcount else None  # rowcount 0 => another worker won it


def update(conn, job_id, status=None, detail=None):
    sets, args = ["updated_at = ?"], [_now()]
    if status is not None:
        sets.append("status = ?")
        args.append(status)
    if detail is not None:
        sets.append("detail = ?")
        args.append(detail)
    args.append(job_id)
    conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", args)
    conn.commit()
