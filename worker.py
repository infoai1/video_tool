"""Job worker: claim queued transcription jobs and run them.

Runs as its own long-lived process (systemd service video-tool-worker) so the
minutes-long download + Soniox transcription never touches a web request. One
job at a time, which is what a single Soniox pipeline wants.

    python worker.py
"""
import time

import db
import jobs
import transcribe
import transliterate


def _run_transcribe(conn, job_id, job):
    def on_status(msg):
        jobs.update(conn, job_id, detail=msg)

    video_id, n = transcribe.transcribe_and_ingest(conn, job["youtube_url"], on_status)
    if n == 0:
        jobs.update(conn, job_id, status="done", progress=100,
                    detail=f"already in library (video {video_id})")
    else:
        jobs.update(conn, job_id, status="done", progress=100,
                    detail=f"transcribed {n} segments (video {video_id})")


def _progress_updater(conn, job_id, label="{done}/{total}"):
    last = [0.0]

    def on_progress(done, total):
        pct = round((100.0 * done / total) if total else 100.0, 1)
        # avoid a DB write on every tiny step of a huge job
        if pct - last[0] >= 0.1 or pct >= 100:
            last[0] = pct
            jobs.update(conn, job_id, detail=label.format(done=done, total=total), progress=pct)

    return on_progress


def _run_romanize(conn, job_id, job):
    done, total = transliterate.romanize_video(
        conn, job["video_id"], progress=_progress_updater(conn, job_id))
    jobs.update(conn, job_id, status="done", progress=100, detail=f"romanized {done}/{total} lines")


def _run_romanize_all(conn, job_id, job):
    done, total = transliterate.romanize_all(
        conn, progress=_progress_updater(conn, job_id, "{done} / {total} lines"))
    jobs.update(conn, job_id, status="done", progress=100, detail=f"romanized {done}/{total} lines")


def process(job_id):
    conn = db.connect()
    try:
        job = jobs.get(conn, job_id)
        if job["kind"] == "romanize":
            _run_romanize(conn, job_id, job)
        elif job["kind"] == "romanize_all":
            _run_romanize_all(conn, job_id, job)
        else:
            _run_transcribe(conn, job_id, job)
    except transcribe.TranscribeError as exc:
        jobs.update(conn, job_id, status="error", detail=str(exc)[:400])
    except Exception as exc:  # noqa: BLE001 — never let one job kill the worker
        jobs.update(conn, job_id, status="error", detail=f"unexpected: {str(exc)[:300]}")
    finally:
        conn.close()


def main(poll_seconds=5):
    db.init_db()
    # A job left 'running' means a previous worker was interrupted — resume it.
    conn = db.connect()
    try:
        jobs.requeue_running(conn)
    finally:
        conn.close()
    while True:
        conn = db.connect()
        try:
            job_id = jobs.claim(conn)
        finally:
            conn.close()
        if job_id is None:
            time.sleep(poll_seconds)
            continue
        process(job_id)


if __name__ == "__main__":
    main()
