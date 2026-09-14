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


def test_complete_with_retry_recovers_from_transient_failure(monkeypatch):
    # a model call that fails twice then succeeds must be retried, not given up on
    calls = {"n": 0}

    def flaky(system, user, model, max_tokens):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("simulated blip")
        return '{"segments":[{"id":1,"roman":"ok"}]}'

    monkeypatch.setattr(transliterate, "_complete", flaky)
    out = transliterate._complete_with_retry("sys", "usr", "model", 100, attempts=3)
    assert out == '{"segments":[{"id":1,"roman":"ok"}]}'
    assert calls["n"] == 3


def test_complete_watchdog_enforces_wall_clock_deadline(monkeypatch):
    # some OpenRouter backends have been observed to hold a connection open far
    # past any socket-level timeout; the watchdog must still give up on time.
    import time

    def hangs_forever(system, user, model, max_tokens):
        time.sleep(10)
        return "unreachable"

    monkeypatch.setattr(transliterate, "_complete", hangs_forever)
    t0 = time.time()
    try:
        transliterate._complete_watchdog("s", "u", "m", 10, deadline=0.3)
        assert False, "watchdog did not raise on timeout"
    except transliterate._CallTimeout:
        assert time.time() - t0 < 2.0


def test_watchdog_abandoned_calls_do_not_exhaust_capacity(monkeypatch):
    """Regression for a real production incident: the watchdog originally
    pulled its worker thread from a small SHARED ThreadPoolExecutor. Once
    enough calls got abandoned (a genuinely stuck backend, confirmed live via
    py-spy — threads blocked inside ssl.read()/_read_chunked, not spinning),
    every pool slot was permanently occupied, so every *subsequent* call
    queued behind them and timed out immediately without ever actually
    running — making a slow-but-alive endpoint look completely dead. Each
    call must get its own dedicated thread so abandoned ones can never block
    a later, unrelated call."""
    import time

    hang_count = {"n": 0}

    def mostly_hangs(system, user, model, max_tokens):
        hang_count["n"] += 1
        if hang_count["n"] <= 6:
            time.sleep(30)  # simulates a permanently-stuck call
            return "unreachable"
        return "fast response"

    monkeypatch.setattr(transliterate, "_complete", mostly_hangs)
    for _ in range(6):  # fire and abandon 6 "stuck" calls
        try:
            transliterate._complete_watchdog("s", "u", "m", 10, deadline=0.3)
        except transliterate._CallTimeout:
            pass
    t0 = time.time()
    result = transliterate._complete_watchdog("s", "u", "m", 10, deadline=5)
    dt = time.time() - t0
    assert result == "fast response"
    assert dt < 2.0, "call was blocked behind abandoned threads! took %.2fs" % dt


def test_romanize_all_isolates_poison_segments(tmp_path):
    """A single segment that always fails the model (a 19k-char bulk-text
    outlier) must not take its healthy batch-mates down with it: while the
    endpoint is clearly up (other batches succeed), a wholesale-failed chunk is
    retried one segment at a time, so every good segment is romanized and only
    the genuine poison is skipped — even when a good segment is bundled in the
    same batch as poison."""
    conn, _ = _seed_video(str(tmp_path / "roman.db"))  # 3 good segments (video 1)
    # 30 more good segments so a full all-good batch exists in the round, then a
    # handful of poison ones interleaved at the tail.
    conn.execute("INSERT INTO videos (youtube_url, title) VALUES ('https://youtu.be/zzzzzzzzzzz', 'V2')")
    vid2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for i in range(30):
        conn.execute(
            "INSERT INTO segments (video_id, start_time, urdu_text, urdu_norm) VALUES (?,?,?,?)",
            (vid2, i, "good", "good"),
        )
    for i in range(5):
        conn.execute(
            "INSERT INTO segments (video_id, start_time, urdu_text, urdu_norm) VALUES (?,?,?,?)",
            (vid2, 100 + i, "poison", "poison"),
        )
    conn.commit()

    def call(batch):
        # any batch containing poison fails wholesale (as it would after the
        # real _complete_with_retry gives up); an all-healthy batch succeeds.
        if any(u == "poison" for _sid, u, _t in batch):
            return {}
        return {sid: "roman" for sid, _u, _t in batch}

    transliterate.romanize_all(conn, translit_batch=call, concurrency=2)
    good_done = conn.execute(
        "SELECT COUNT(*) FROM segments WHERE urdu_text != 'poison' AND roman_text IS NOT NULL"
    ).fetchone()[0]
    poison_done = conn.execute(
        "SELECT COUNT(*) FROM segments WHERE urdu_text = 'poison' AND roman_text IS NOT NULL"
    ).fetchone()[0]
    # all 33 healthy segments romanized; all 5 poison skipped (never romanized)
    assert good_done == 33 and poison_done == 0, (good_done, poison_done)
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


def test_parse_tolerates_malformed_json():
    # clean JSON parses normally
    assert transliterate._parse(
        '{"segments":[{"id":1,"roman":"namaz"},{"id":2,"roman":"sabr"}]}'
    ) == {1: "namaz", 2: "sabr"}
    # an unescaped inner quote used to raise and kill the whole batch — now the
    # good entries are salvaged instead
    assert transliterate._parse(
        '{"segments":[{"id":5,"roman":"aap "zara" sochiye"},{"id":6,"roman":"theek"}]}'
    ) == {5: 'aap "zara" sochiye', 6: "theek"}
    # a truncated tail (max_tokens) keeps the complete entries
    assert transliterate._parse(
        '{"segments":[{"id":9,"roman":"done"},{"id":10,"roman":"cut off'
    ) == {9: "done"}
    # a raw newline inside a string is tolerated
    assert transliterate._parse(
        '{"segments":[{"id":7,"roman":"line one\nline two"}]}'
    ) == {7: "line one\nline two"}
    # never raises, even on garbage
    assert transliterate._parse("not json at all") == {}


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
