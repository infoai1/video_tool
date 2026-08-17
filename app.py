"""Web app: search across videos, and browse a video's Roman Urdu transcript.

Read-only against the store (roman.db). If the store doesn't exist yet, every
page explains how to build it rather than erroring.
"""
import datetime
import json

import config
import db
import jobs
import search
import transliterate
from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

app = Flask(__name__)


def _hhmmss(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


app.jinja_env.filters["hhmmss"] = _hhmmss


@app.route("/")
def index():
    q = (request.args.get("q") or "").strip()
    if not db.exists():
        return render_template("index.html", q=q, hits=None, no_store=True)
    hits = []
    youtube_not_found = False
    active_job = None
    if q:
        # Writable: search caches the query's Urdu transliteration on first use.
        conn = db.connect()
        try:
            # A pasted YouTube link jumps straight to that video's transcript.
            if search.youtube_id(q):
                vid = search.find_video_by_youtube(conn, q)
                if vid:
                    return redirect(url_for("video", video_id=vid))
                youtube_not_found = True
                active = jobs.active_for_url(conn, q)
                active_job = active[0] if active else None
            else:
                hits = search.search(conn, q, limit=60)
        finally:
            conn.close()
    return render_template(
        "index.html", q=q, hits=hits, no_store=False,
        youtube_not_found=youtube_not_found, active_job=active_job,
    )


@app.route("/videos")
def videos():
    if not db.exists():
        return render_template("videos.html", videos=None, no_store=True)
    conn = db.connect_ro()
    try:
        vids = search.list_videos(conn)
    finally:
        conn.close()
    return render_template("videos.html", videos=vids, no_store=False)


@app.route("/video/<int:video_id>")
def video(video_id):
    if not db.exists():
        abort(404)
    conn = db.connect_ro()
    try:
        data = search.get_video(conn, video_id)
    finally:
        conn.close()
    if data is None:
        abort(404)
    return render_template("video.html", video=data)


@app.route("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    if not db.exists():
        return jsonify({"error": "store not built yet", "results": []}), 503
    conn = db.connect()
    try:
        hits = search.search(conn, q, limit=int(request.args.get("limit", 60)))
    finally:
        conn.close()
    return jsonify({"query": q, "count": len(hits), "results": hits})


@app.route("/api/romanize", methods=["POST"])
def api_romanize():
    """On-demand transliteration for the segments a page is showing. The frontend
    posts the segment ids that lack Roman; we transliterate the missing ones
    (cached, so at most once) and return {id: roman}."""
    if not db.exists():
        return jsonify({"error": "store not built yet", "roman": {}}), 503
    ids = (request.get_json(silent=True) or {}).get("ids") or []
    ids = [int(i) for i in ids][:50]  # bound the work per request
    conn = db.connect()
    try:
        out = transliterate.ensure(conn, ids)
    finally:
        conn.close()
    return jsonify({"roman": {str(k): v for k, v in out.items()}})


@app.route("/api/transcribe", methods=["POST"])
def api_transcribe():
    """Enqueue a Soniox transcription for a pasted YouTube link that isn't in the
    library yet. A worker picks it up; progress shows on the dashboard."""
    if not db.exists():
        return jsonify({"ok": False, "error": "store not built yet"}), 503
    url = ((request.get_json(silent=True) or {}).get("url") or "").strip()
    yt = search.youtube_id(url)
    if not yt:
        return jsonify({"ok": False, "error": "not a YouTube link"}), 400
    conn = db.connect()
    try:
        if search.find_video_by_youtube(conn, url):
            return jsonify({"ok": False, "error": "already in the library"}), 409
        active = jobs.active_for_url(conn, url)
        if active:
            return jsonify({"ok": True, "job_id": active[0], "status": active[1]})
        job_id = jobs.enqueue(conn, url)
    finally:
        conn.close()
    return jsonify({"ok": True, "job_id": job_id, "status": "queued"})


@app.route("/api/jobs")
def api_jobs():
    if not db.exists():
        return jsonify({"jobs": []})
    conn = db.connect_ro()
    try:
        rows = jobs.recent(conn, limit=int(request.args.get("limit", 25)))
    finally:
        conn.close()
    keys = ["id", "kind", "youtube_url", "title", "status", "detail", "created_at", "updated_at"]
    return jsonify({"jobs": [dict(zip(keys, r)) for r in rows]})


@app.route("/dashboard")
def dashboard():
    if not db.exists():
        return render_template("dashboard.html", no_store=True)
    conn = db.connect_ro()
    try:
        stats = {
            "videos": conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0],
            "videos_soniox": conn.execute(
                "SELECT COUNT(*) FROM videos WHERE source = 'soniox'"
            ).fetchone()[0],
            "segments": conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0],
            "romanized": conn.execute(
                "SELECT COUNT(*) FROM segments WHERE roman_text IS NOT NULL"
            ).fetchone()[0],
        }
        stats["pct"] = (100.0 * stats["romanized"] / stats["segments"]) if stats["segments"] else 0.0
        job_rows = jobs.recent(conn, limit=15)
        romanized_rows = conn.execute(
            """
            SELECT v.id, v.title, COUNT(s.roman_text), COUNT(*), MAX(s.roman_at)
            FROM videos v JOIN segments s ON s.video_id = v.id
            WHERE s.roman_at IS NOT NULL
            GROUP BY v.id ORDER BY MAX(s.roman_at) DESC LIMIT 15
            """
        ).fetchall()
    finally:
        conn.close()
    jkeys = ["id", "kind", "youtube_url", "title", "status", "detail", "created_at", "updated_at"]
    return render_template(
        "dashboard.html", no_store=False, stats=stats,
        job_list=[dict(zip(jkeys, r)) for r in job_rows],
        romanized=[
            {"id": r[0], "title": r[1], "done": r[2], "total": r[3], "last": r[4]}
            for r in romanized_rows
        ],
    )


@app.route("/feedback")
def feedback_page():
    return render_template("feedback.html", sent=False)


@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    """Record a feature request or bug report as one JSON line on disk."""
    data = request.get_json(silent=True) or request.form
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "message is required"}), 400
    record = {
        "ts": datetime.datetime.utcnow().isoformat() + "Z",
        "kind": (data.get("kind") or "feedback")[:20],
        "message": message[:5000],
        "email": (data.get("email") or "").strip()[:200],
        "page": (data.get("page") or "")[:300],
        "ip": request.headers.get("X-Real-IP") or request.remote_addr,
    }
    with open(config.FEEDBACK_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return jsonify({"ok": True})


@app.route("/health")
def health():
    return jsonify({"status": "ok", "store": db.exists()})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5060, debug=True)
