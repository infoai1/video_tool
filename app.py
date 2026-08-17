"""Web app: search across videos, and browse a video's Roman Urdu transcript.

Read-only against the store (roman.db). If the store doesn't exist yet, every
page explains how to build it rather than erroring.
"""
import datetime
import hmac
import json

import config
import db
import export
import jobs
import search
import transliterate
from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   session, url_for)

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# Auth is on only when a credential is configured (auth.json / env), so local
# dev and tests aren't gated.
AUTH_ENABLED = bool(config.AUTH_USER and config.AUTH_PASSWORD)
_OPEN_ENDPOINTS = {"login", "static", "health"}


def _eq(a, b):
    """Constant-time string compare that tolerates non-ASCII (compares bytes)."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


@app.before_request
def _require_login():
    if not AUTH_ENABLED or session.get("user"):
        return None
    if request.endpoint in _OPEN_ENDPOINTS:
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "authentication required"}), 401
    return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not AUTH_ENABLED:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        # Compare as bytes: hmac.compare_digest rejects non-ASCII str (the
        # password may contain characters like ¥ ×).
        if _eq(u, config.AUTH_USER) and _eq(p, config.AUTH_PASSWORD):
            session["user"] = u
            dest = request.args.get("next") or url_for("index")
            return redirect(dest if dest.startswith("/") else url_for("index"))
        error = "Wrong username or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login") if AUTH_ENABLED else url_for("index"))


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
    roman_terms, urdu_terms = [], []
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
                roman_terms, urdu_terms = search.query_highlight_terms(conn, q)
        finally:
            conn.close()
    return render_template(
        "index.html", q=q, hits=hits, no_store=False,
        youtube_not_found=youtube_not_found, active_job=active_job,
        roman_terms=roman_terms, urdu_terms=urdu_terms,
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
    # When arriving from a search ("Expand"), q highlights the term and jumps to
    # the match — so a writable connection (query transliteration gets cached).
    q = (request.args.get("q") or "").strip()
    conn = db.connect() if q else db.connect_ro()
    matched_ids, roman_terms, urdu_terms = [], [], []
    try:
        data = search.get_video(conn, video_id)
        if data is not None and q:
            matched_ids = search.video_matches(conn, video_id, q)
            roman_terms, urdu_terms = search.query_highlight_terms(conn, q)
    finally:
        conn.close()
    if data is None:
        abort(404)
    return render_template(
        "video.html", video=data, youtube_id=search.youtube_id(data["youtube_url"]),
        q=q, matched_ids=matched_ids, roman_terms=roman_terms, urdu_terms=urdu_terms,
    )


@app.route("/video/<int:video_id>/export.docx")
def video_export(video_id):
    """Download a video's transcript as .docx. Query params:
    script=roman|urdu|both (default both), timestamps=1|0 (default 1)."""
    if not db.exists():
        abort(404)
    script = request.args.get("script", "both")
    timestamps = request.args.get("timestamps", "1") != "0"
    conn = db.connect_ro()
    try:
        data = search.get_video(conn, video_id)
    finally:
        conn.close()
    if data is None:
        abort(404)
    blob = export.transcript_docx(data, script=script, timestamps=timestamps)
    safe = "".join(c for c in (data["title"] or "transcript") if c.isalnum() or c in " -_")[:60].strip()
    return app.response_class(
        blob,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{safe or "transcript"}.docx"'},
    )


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


@app.route("/api/video/<int:video_id>")
def api_video(video_id):
    """A video's transcript + which lines match a query, for inline expand on the
    search page (no navigation)."""
    if not db.exists():
        return jsonify({"error": "store not built yet"}), 503
    q = (request.args.get("q") or "").strip()
    conn = db.connect() if q else db.connect_ro()
    matched, roman_terms, urdu_terms = [], [], []
    try:
        data = search.get_video(conn, video_id)
        if data is None:
            return jsonify({"error": "no such video"}), 404
        if q:
            matched = set(search.video_matches(conn, video_id, q))
            roman_terms, urdu_terms = search.query_highlight_terms(conn, q)
    finally:
        conn.close()
    return jsonify({
        "id": data["id"],
        "title": data["title"],
        "youtube_id": search.youtube_id(data["youtube_url"]),
        "youtube_url": data["youtube_url"],
        "roman_terms": roman_terms,
        "urdu_terms": urdu_terms,
        "segments": [
            {
                "seg_id": s["segment_id"],
                "t": int(s["start_time"]),
                "ts": _hhmmss(s["start_time"]),
                "roman": s["roman_text"],
                "urdu": s["urdu_text"],
                "match": s["segment_id"] in matched,
            }
            for s in data["segments"]
        ],
    })


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
        job_id = jobs.enqueue_transcribe(conn, url)
    finally:
        conn.close()
    return jsonify({"ok": True, "job_id": job_id, "status": "queued"})


@app.route("/api/romanize_video", methods=["POST"])
def api_romanize_video():
    """Enqueue background romanization of a whole video, so the user can keep
    browsing while it runs. Returns the job to poll for progress."""
    if not db.exists():
        return jsonify({"ok": False, "error": "store not built yet"}), 503
    video_id = (request.get_json(silent=True) or {}).get("video_id")
    if not video_id:
        return jsonify({"ok": False, "error": "video_id required"}), 400
    conn = db.connect()
    try:
        data = search.get_video(conn, int(video_id))
        if data is None:
            return jsonify({"ok": False, "error": "no such video"}), 404
        pending = sum(1 for s in data["segments"] if s["roman_text"] is None)
        if pending == 0:
            return jsonify({"ok": True, "job_id": None, "status": "done", "progress": 100})
        active = jobs.active_romanize(conn, int(video_id))
        if active:
            return jsonify({"ok": True, "job_id": active[0], "status": active[1]})
        job_id = jobs.enqueue_romanize(conn, int(video_id), title=data["title"])
    finally:
        conn.close()
    return jsonify({"ok": True, "job_id": job_id, "status": "queued", "progress": 0})


@app.route("/api/romanize_all", methods=["POST"])
def api_romanize_all():
    """Enqueue a background pass that romanizes every pending line in the library."""
    if not db.exists():
        return jsonify({"ok": False, "error": "store not built yet"}), 503
    conn = db.connect()
    try:
        pending = conn.execute(
            "SELECT COUNT(*) FROM segments WHERE roman_text IS NULL"
        ).fetchone()[0]
        if pending == 0:
            return jsonify({"ok": True, "job_id": None, "status": "done", "progress": 100})
        active = jobs.active_romanize_all(conn)
        if active:
            return jsonify({"ok": True, "job_id": active[0], "status": active[1]})
        job_id = jobs.enqueue_romanize_all(conn)
    finally:
        conn.close()
    return jsonify({"ok": True, "job_id": job_id, "status": "queued", "progress": 0})


@app.route("/api/job/<int:job_id>")
def api_job(job_id):
    if not db.exists():
        return jsonify({"error": "store not built yet"}), 503
    conn = db.connect_ro()
    try:
        job = jobs.get(conn, job_id)
    finally:
        conn.close()
    if job is None:
        return jsonify({"error": "no such job"}), 404
    return jsonify(job)


@app.route("/api/jobs")
def api_jobs():
    if not db.exists():
        return jsonify({"jobs": []})
    conn = db.connect_ro()
    try:
        rows = jobs.recent(conn, limit=int(request.args.get("limit", 25)))
    finally:
        conn.close()
    return jsonify({"jobs": rows})


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
        stats["pending"] = stats["segments"] - stats["romanized"]
        # DeepSeek ≈ $0.00006 per segment.
        stats["est"] = round(stats["pending"] * 0.00006, 2)
        active_all = jobs.active_romanize_all(conn)
        active_all_job = active_all[0] if active_all else None
        job_list = jobs.recent(conn, limit=15)
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
    return render_template(
        "dashboard.html", no_store=False, stats=stats, job_list=job_list,
        active_all_job=active_all_job,
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
