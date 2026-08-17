import db
import jobs
import transcribe


def test_segment_transcript_groups_by_time():
    # tokens ~1s apart; with a 3s target we expect ~3 tokens per segment.
    tokens = [{"text": f"w{i} ", "start_ms": i * 1000, "end_ms": i * 1000 + 900} for i in range(7)]
    segs = transcribe.segment_transcript({"tokens": tokens}, seconds=3)
    assert len(segs) >= 2
    assert segs[0][0] == 0.0                      # first segment starts at 0
    assert all(text.strip() for _s, text in segs)  # no empty segments
    assert segs[1][0] >= 3.0                        # second starts a few seconds in


def test_segment_transcript_fallback_without_tokens():
    assert transcribe.segment_transcript({"text": "salaam"}) == [(0.0, "salaam")]
    assert transcribe.segment_transcript({"text": "  "}) == []


def test_jobs_enqueue_claim_and_finish(tmp_path):
    p = str(tmp_path / "roman.db")
    db.init_db(p)
    conn = db.connect(p)
    jid = jobs.enqueue_transcribe(conn, "https://youtu.be/abcdefghijk")
    assert jobs.active_for_url(conn, "https://youtu.be/abcdefghijk")[0] == jid

    claimed = jobs.claim(conn)
    assert claimed == jid
    # a second claim finds nothing queued
    assert jobs.claim(conn) is None
    # claimed job is no longer "active" as queued... it's running
    assert jobs.active_for_url(conn, "https://youtu.be/abcdefghijk")[1] == "running"

    jobs.update(conn, jid, status="done", detail="transcribed 10 segments", progress=100)
    row = jobs.get(conn, jid)
    assert row["status"] == "done" and "10 segments" in row["detail"] and row["progress"] == 100
    conn.close()


def test_dashboard_and_transcribe_routes(tmp_path, monkeypatch):
    p = str(tmp_path / "roman.db")
    db.init_db(p)
    monkeypatch.setattr(db.config, "DB_PATH", p)
    import app as appmod

    client = appmod.app.test_client()
    assert client.get("/dashboard").status_code == 200
    # queue a transcription for a not-yet-present video
    r = client.post("/api/transcribe", json={"url": "https://youtu.be/abcdefghijk"})
    j = r.get_json()
    assert j["ok"] and j["status"] == "queued"
    # a non-YouTube string is rejected
    assert client.post("/api/transcribe", json={"url": "hello"}).status_code == 400
    # the job now shows on the jobs API
    assert any(x["id"] == j["job_id"] for x in client.get("/api/jobs").get_json()["jobs"])
