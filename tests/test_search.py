import db
import normalize
import search
import transliterate


def _build(tmp_path):
    """Build a store with both indexes populated, the way ingest + transliterate
    would. Returns (path, v1, v2)."""
    p = str(tmp_path / "roman.db")
    db.init_db(p)
    conn = db.connect(p)
    conn.execute("INSERT INTO videos (youtube_url, title) VALUES ('https://y/1', 'Shukr')")
    v1 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO videos (youtube_url, title) VALUES ('https://y/2', 'Sabr')")
    v2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    seg = {}
    for vid, start, urdu, roman in [
        (v1, 0.0, "اللہ کا شکر", "Allah ka shukr ada karo"),
        (v1, 30.0, "نماز اور صبر", "Namaz parho aur sabr karo"),
        (v2, 5.0, "صبر ایمان", "Sabr iman ka hissa hai"),
    ]:
        norm = normalize.normalize_urdu(urdu)
        conn.execute(
            "INSERT INTO segments (video_id, start_time, urdu_text, urdu_norm) VALUES (?, ?, ?, ?)",
            (vid, start, urdu, norm),
        )
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO urdu_fts (rowid, urdu_norm) VALUES (?, ?)", (sid, norm))
        seg[sid] = roman
    conn.commit()
    conn.close()
    # transliterate (fills roman + roman_fts) with a canned lookup — no network.
    transliterate.run(translit_batch=lambda b: {s: seg[s] for s, _u, _t in b}, db_path=p)
    return p, v1, v2


_NO_URDU = lambda q: ""  # noqa: E731 — Roman-path-only stub for tests


def test_search_finds_by_roman_word(tmp_path):
    p, v1, _ = _build(tmp_path)
    conn = db.connect(p)
    hits = search.search(conn, "shukr", translit_query=_NO_URDU)
    assert len(hits) == 1
    assert hits[0]["video_id"] == v1
    assert "shukr" in hits[0]["roman_text"].lower()
    conn.close()


def test_search_variant_spelling_matches(tmp_path):
    p, _, _ = _build(tmp_path)
    conn = db.connect(p)
    assert search.search(conn, "namaaz", translit_query=_NO_URDU)
    conn.close()


def test_search_urdu_index_covers_untransliterated(tmp_path):
    # Full-corpus path: a segment need not have Roman for search to find it — the
    # query is transliterated to Urdu and matched against the Urdu index.
    p = str(tmp_path / "roman.db")
    db.init_db(p)
    conn = db.connect(p)
    conn.execute("INSERT INTO videos (youtube_url, title) VALUES ('u', 'V')")
    vid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    norm = normalize.normalize_urdu("نماز کا بیان")
    conn.execute(
        "INSERT INTO segments (video_id, start_time, urdu_text, urdu_norm) VALUES (?,?,?,?)",
        (vid, 0.0, "نماز کا بیان", norm),
    )
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO urdu_fts (rowid, urdu_norm) VALUES (?, ?)", (sid, norm))
    conn.commit()
    # No roman_text at all; query transliteration maps "namaz" -> "نماز".
    hits = search.search(conn, "namaz", translit_query=lambda q: "نماز")
    assert len(hits) == 1
    assert hits[0]["roman_text"] is None  # found via Urdu, not yet transliterated
    conn.close()


def test_query_transliteration_is_cached(tmp_path):
    p, _, _ = _build(tmp_path)
    conn = db.connect(p)
    calls = []

    def once(q):
        calls.append(q)
        return "نماز"

    search.search(conn, "namaz", translit_query=once)
    search.search(conn, "namaz", translit_query=once)  # second time: from cache
    assert calls == ["namaz"]
    conn.close()


def test_search_prefix_and_multiword_and(tmp_path):
    p, _, _ = _build(tmp_path)
    conn = db.connect(p)
    hits = search.search(conn, "namaz sabr", translit_query=_NO_URDU)
    assert len(hits) == 1
    assert "Namaz" in hits[0]["roman_text"]
    assert search.search(conn, "sab", translit_query=_NO_URDU)
    conn.close()


def test_empty_query_returns_nothing(tmp_path):
    p, _, _ = _build(tmp_path)
    conn = db.connect(p)
    assert search.search(conn, "   ", translit_query=_NO_URDU) == []
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
