"""Web app: search across videos, and browse a video's Roman Urdu transcript.

Read-only against the store (roman.db). If the store doesn't exist yet, every
page explains how to build it rather than erroring.
"""
import db
import search
import transliterate
from flask import Flask, abort, jsonify, render_template, request

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
    if q:
        # Writable: search caches the query's Urdu transliteration on first use.
        conn = db.connect()
        try:
            hits = search.search(conn, q, limit=60)
        finally:
            conn.close()
    return render_template("index.html", q=q, hits=hits, no_store=False)


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


@app.route("/health")
def health():
    return jsonify({"status": "ok", "store": db.exists()})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5060, debug=True)
