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


def test_export_docx_variants(tmp_path):
    conn, vid = _seed_video(str(tmp_path / "roman.db"))
    transliterate.romanize_video(conn, vid, translit_batch=lambda b: {s: "roman" for s, _u, _t in b})
    video = search.get_video(conn, vid)
    conn.close()
    for script in ("both", "roman", "urdu"):
        for ts in (True, False):
            blob = export.transcript_docx(video, script=script, timestamps=ts)
            assert blob[:2] == b"PK" and len(blob) > 500  # a real .docx (zip) with content


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
