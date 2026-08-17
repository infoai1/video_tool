import json

import config
import db
import search


def test_youtube_id_extraction():
    for text, expect in [
        ("https://www.youtube.com/watch?v=H-kx5TldzfQ", "H-kx5TldzfQ"),
        ("https://youtu.be/H-kx5TldzfQ?t=30", "H-kx5TldzfQ"),
        ("https://www.youtube.com/embed/H-kx5TldzfQ", "H-kx5TldzfQ"),
        ("H-kx5TldzfQ", "H-kx5TldzfQ"),
        ("namaz", None),
        ("", None),
    ]:
        assert search.youtube_id(text) == expect


def test_find_video_by_youtube(tmp_path):
    p = str(tmp_path / "roman.db")
    db.init_db(p)
    conn = db.connect(p)
    conn.execute(
        "INSERT INTO videos (youtube_url, title) VALUES ('https://www.youtube.com/watch?v=H-kx5TldzfQ', 'V')"
    )
    vid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    assert search.find_video_by_youtube(conn, "https://youtu.be/H-kx5TldzfQ") == vid
    assert search.find_video_by_youtube(conn, "https://youtu.be/zzzzzzzzzzz") is None
    assert search.find_video_by_youtube(conn, "just words") is None
    conn.close()


def test_feedback_endpoint_writes_record(tmp_path, monkeypatch):
    fb = str(tmp_path / "feedback.jsonl")
    monkeypatch.setattr(config, "FEEDBACK_PATH", fb)
    # app reads config.FEEDBACK_PATH at request time, and db.exists() gates it.
    monkeypatch.setattr(db, "exists", lambda *a, **k: True)
    import app as appmod

    client = appmod.app.test_client()
    r = client.post("/api/feedback", json={"kind": "bug", "message": "search broke", "email": "a@b.c"})
    assert r.get_json()["ok"] is True
    # empty message is rejected
    assert client.post("/api/feedback", json={"message": "  "}).status_code == 400

    with open(fb, encoding="utf-8") as f:
        rec = json.loads(f.readline())
    assert rec["kind"] == "bug" and rec["message"] == "search broke" and rec["email"] == "a@b.c"
