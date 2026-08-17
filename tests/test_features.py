import db
import export
import normalize
import search
import transliterate


def _seed_video(path):
    db.init_db(path)
    conn = db.connect(path)
    conn.execute("INSERT INTO videos (youtube_url, title) VALUES ('https://youtu.be/abcdefghijk', 'V')")
    vid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for i, urdu in enumerate(["اللہ کا شکر", "نماز اور صبر", "ذکر دل سے"]):
        norm = normalize.normalize_urdu(urdu)
        conn.execute(
            "INSERT INTO segments (video_id, start_time, urdu_text, urdu_norm) VALUES (?,?,?,?)",
            (vid, i * 10.0, urdu, norm),
        )
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO urdu_fts (rowid, urdu_norm) VALUES (?,?)", (sid, norm))
    conn.commit()
    return conn, vid


def test_romanize_video_reports_progress_and_fills(tmp_path):
    conn, vid = _seed_video(str(tmp_path / "roman.db"))
    seen = []
    done, total = transliterate.romanize_video(
        conn, vid,
        progress=lambda d, t: seen.append((d, t)),
        translit_batch=lambda b: {sid: u.upper() for sid, u, _t in b},
    )
    assert (done, total) == (3, 3)
    assert seen[-1] == (3, 3) and seen[0] == (0, 3)  # starts at 0, ends at total
    roman = [r[0] for r in conn.execute("SELECT roman_text FROM segments WHERE video_id=?", (vid,))]
    assert all(roman)
    conn.close()


def test_romanize_all_drains_backlog(tmp_path):
    conn, vid = _seed_video(str(tmp_path / "roman.db"))  # 3 pending segments
    seen = []
    done, total = transliterate.romanize_all(
        conn, progress=lambda d, t: seen.append((d, t)),
        translit_batch=lambda b: {s: "x" for s, _u, _t in b},
    )
    assert (done, total) == (3, 3)
    pending = conn.execute("SELECT COUNT(*) FROM segments WHERE roman_text IS NULL").fetchone()[0]
    assert pending == 0 and seen[0] == (0, 3) and seen[-1] == (3, 3)
    conn.close()


def test_romanize_all_endpoint_and_requeue(tmp_path, monkeypatch):
    import jobs

    conn, vid = _seed_video(str(tmp_path / "roman.db"))
    # a stale 'running' job is resumed on worker start
    jid = jobs.enqueue_romanize_all(conn)
    jobs.update(conn, jid, status="running")
    jobs.requeue_running(conn)
    assert jobs.get(conn, jid)["status"] == "queued"
    # claim prefers non-bulk work first
    jid2 = jobs.enqueue_romanize(conn, vid)
    assert jobs.claim(conn) == jid2  # the per-video job jumps ahead of romanize_all
    conn.close()

    monkeypatch.setattr(__import__("app").db.config, "DB_PATH", str(tmp_path / "roman.db"))
    import app as appmod
    r = appmod.app.test_client().post("/api/romanize_all")
    assert r.get_json()["ok"] is True


def test_export_docx_variants(tmp_path):
    conn, vid = _seed_video(str(tmp_path / "roman.db"))
    transliterate.romanize_video(conn, vid, translit_batch=lambda b: {s: "roman" for s, _u, _t in b})
    video = search.get_video(conn, vid)
    conn.close()
    for script in ("both", "roman", "urdu"):
        for ts in (True, False):
            blob = export.transcript_docx(video, script=script, timestamps=ts)
            assert blob[:2] == b"PK" and len(blob) > 500  # a real .docx (zip) with content


def test_video_matches_and_expand_page(tmp_path, monkeypatch):
    p = str(tmp_path / "roman.db")
    conn, vid = _seed_video(p)  # lines: "اللہ کا شکر", "نماز اور صبر", "ذکر دل سے"
    conn.execute("INSERT INTO query_cache (roman_norm, urdu) VALUES ('namaz', 'نماز')")
    conn.commit()
    assert len(search.video_matches(conn, vid, "namaz")) == 1  # only the نماز line
    conn.close()

    import app as appmod

    monkeypatch.setattr(appmod.db.config, "DB_PATH", p)
    r = appmod.app.test_client().get(f"/video/{vid}?t=10&q=namaz")
    assert r.status_code == 200
    body = r.data.decode()
    assert 'class="line match"' in body and "Back to results" in body and "m-count" in body


def test_library_saves_tags_and_tabs(tmp_path, monkeypatch):
    import library

    p = str(tmp_path / "roman.db")
    conn, vid = _seed_video(p)
    conn.execute("UPDATE segments SET roman_text='r', roman_at='2026-08-17T10:00:00Z'")
    conn.commit()
    seg = conn.execute("SELECT id FROM segments ORDER BY start_time").fetchall()[1][0]

    # whole-video save toggles; a re-toggle removes it
    assert library.toggle_video(conn, vid, tags=["Darwin"])["saved"] is True
    assert library.toggle_video(conn, vid)["saved"] is False
    library.toggle_video(conn, vid, tags=["darwin"])
    library.toggle_segment(conn, vid, seg, start_time=10.0, tags=["darwin"])

    st = library.saved_state(conn, vid)
    assert st["video"] is True and seg in st["segments"]
    assert len(library.list_saved(conn, tag="darwin")) == 2   # video + segment
    assert library.list_romanized(conn, q="V")[0]["done"] == 3
    assert {e["kind"] for e in library.history(conn)} >= {"save", "romanize"}
    conn.close()

    import app as appmod
    monkeypatch.setattr(appmod.db.config, "DB_PATH", p)
    c = appmod.app.test_client()
    # segment save endpoint toggles, tag endpoint sticks
    assert c.post("/api/save/segment", json={"video_id": vid, "segment_id": seg}).get_json()["saved"] is False
    j = c.post("/api/save/segment", json={"video_id": vid, "segment_id": seg}).get_json()
    assert j["saved"] is True
    assert c.post(f"/api/bookmark/{j['bookmark_id']}/tag", json={"tag": "science"}).get_json()["ok"]
    for tab in ("saved", "romanized", "history"):
        assert c.get(f"/library?tab={tab}").status_code == 200
    assert b"science" in c.get("/library?tab=saved").data


def test_auth_gate(monkeypatch):
    import app as appmod

    monkeypatch.setattr(appmod, "AUTH_ENABLED", True)
    monkeypatch.setattr(appmod.config, "AUTH_USER", "cpsvideos")
    # non-ASCII password must work (regression: hmac.compare_digest rejects
    # non-ASCII str; we compare bytes).
    monkeypatch.setattr(appmod.config, "AUTH_PASSWORD", "vfmdyi-#+(/@¥×#2")
    client = appmod.app.test_client()

    # protected page redirects to login; API returns 401
    assert client.get("/").status_code == 302
    assert client.get("/api/jobs").status_code == 401
    # health stays open
    assert client.get("/health").status_code == 200
    # wrong creds rejected, right creds let us in
    assert b"Wrong" in client.post("/login", data={"username": "x", "password": "y"}).data
    r = client.post("/login", data={"username": "cpsvideos", "password": "vfmdyi-#+(/@¥×#2"})
    assert r.status_code == 302
    assert client.get("/health").status_code == 200
