import db
import search
import transliterate


def _build(tmp_path):
    p = str(tmp_path / "roman.db")
    db.init_db(p)
    conn = db.connect(p)
    conn.execute("INSERT INTO videos (youtube_url, title) VALUES ('https://y/1', 'Shukr')")
    v1 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO videos (youtube_url, title) VALUES ('https://y/2', 'Sabr')")
    v2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    seg = {}
    for vid, start, urdu, roman in [
        (v1, 0.0, "u1", "Allah ka shukr ada karo"),
        (v1, 30.0, "u2", "Namaz parho aur sabr karo"),
        (v2, 5.0, "u3", "Sabr iman ka hissa hai"),
    ]:
        conn.execute(
            "INSERT INTO segments (video_id, start_time, urdu_text) VALUES (?, ?, ?)",
            (vid, start, urdu),
        )
        seg[conn.execute("SELECT last_insert_rowid()").fetchone()[0]] = roman
    conn.commit()
    conn.close()
    transliterate.run(translit_batch=lambda b: {s: seg[s] for s, _u, _t in b}, db_path=p)
    return p, v1, v2


def test_search_finds_by_roman_word(tmp_path):
    p, v1, _ = _build(tmp_path)
    conn = db.connect_ro(p)
    hits = search.search(conn, "shukr")
    assert len(hits) == 1
    assert hits[0]["video_id"] == v1
    assert "shukr" in hits[0]["roman_text"].lower()
    conn.close()


def test_search_variant_spelling_matches(tmp_path):
    p, _, _ = _build(tmp_path)
    conn = db.connect_ro(p)
    # Corpus has "Namaz"; query uses a different spelling that folds the same.
    assert search.search(conn, "namaaz")
    conn.close()


def test_search_prefix_and_multiword_and(tmp_path):
    p, _, _ = _build(tmp_path)
    conn = db.connect_ro(p)
    # Both words appear only in the second segment.
    hits = search.search(conn, "namaz sabr")
    assert len(hits) == 1
    assert "Namaz" in hits[0]["roman_text"]
    # Prefix: "sab" should still match "sabr".
    assert search.search(conn, "sab")
    conn.close()


def test_empty_query_returns_nothing(tmp_path):
    p, _, _ = _build(tmp_path)
    conn = db.connect_ro(p)
    assert search.search(conn, "   ") == []
    conn.close()


def test_get_video_returns_ordered_transcript(tmp_path):
    p, v1, _ = _build(tmp_path)
    conn = db.connect_ro(p)
    data = search.get_video(conn, v1)
    assert data["title"] == "Shukr"
    assert [s["start_time"] for s in data["segments"]] == [0.0, 30.0]
    assert data["segments"][0]["timestamp_url"].endswith("t=0s")
    assert search.get_video(conn, 999) is None
    conn.close()


def test_list_videos_reports_coverage(tmp_path):
    p, _, _ = _build(tmp_path)
    conn = db.connect_ro(p)
    vids = {v["title"]: v for v in search.list_videos(conn)}
    assert vids["Shukr"]["segments"] == 2 and vids["Shukr"]["done"] == 2
    conn.close()


def test_timestamp_url_respects_existing_query():
    assert search.youtube_timestamp_url("https://y/w?v=1", 90) == "https://y/w?v=1&t=90s"
    assert search.youtube_timestamp_url("https://y/x", 5) == "https://y/x?t=5s"
