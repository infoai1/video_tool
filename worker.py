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


def process(job_id):
    conn = db.connect()
    try:
        row = jobs.get(conn, job_id)
        url = row[2]

        def on_status(msg):
            jobs.update(conn, job_id, detail=msg)

        video_id, n = transcribe.transcribe_and_ingest(conn, url, on_status)
        if n == 0:
            jobs.update(conn, job_id, status="done", detail=f"already in library (video {video_id})")
        else:
            jobs.update(conn, job_id, status="done",
                        detail=f"transcribed {n} segments (video {video_id})")
    except transcribe.TranscribeError as exc:
        jobs.update(conn, job_id, status="error", detail=str(exc)[:400])
    except Exception as exc:  # noqa: BLE001 — never let one job kill the worker
        jobs.update(conn, job_id, status="error", detail=f"unexpected: {str(exc)[:300]}")
    finally:
        conn.close()


def main(poll_seconds=5):
    db.init_db()
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
