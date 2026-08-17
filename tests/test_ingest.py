import sqlite3

import db
import ingest


def _make_source(path):
    """Build a minimal annotation.db-shaped source database."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE videos (id INTEGER PRIMARY KEY, youtube_url TEXT, title TEXT);
        CREATE TABLE video_segments (
            video_id INTEGER, start_time REAL, text TEXT, soniox_text TEXT
        );
        """
    )
    conn.execute("INSERT INTO videos VALUES (1, 'https://y/1', 'V1')")
    conn.execute("INSERT INTO videos VALUES (2, 'https://y/2', 'V2')")
    conn.executemany(
        "INSERT INTO video_segments (video_id, start_time, text, soniox_text) VALUES (?,?,?,?)",
        [
            (1, 0.0, "curated line", "asr line"),   # curated text wins
            (1, 10.0, "", "asr only line"),          # blank curated -> ASR fallback
            (1, 20.0, None, None),                    # blank on both -> skipped
            (2, 0.0, "  ", "second video asr"),       # whitespace curated -> ASR
        ],
    )
    conn.commit()
    conn.close()


def test_ingest_prefers_curated_falls_back_and_skips_blank(tmp_path):
    src = str(tmp_path / "annotation.db")
    dst = str(tmp_path / "roman.db")
    _make_source(src)

    v, s = ingest.ingest(source_path=src, db_path=dst)
    assert v == 2
    assert s == 3  # the both-blank segment is skipped

    conn = db.connect_ro(dst)
    texts = {
        r[0]: r[1]
        for r in conn.execute("SELECT start_time, urdu_text FROM segments WHERE video_id = 1")
    }
    assert texts[0.0] == "curated line"     # curated preferred
    assert texts[10.0] == "asr only line"   # fell back to ASR
    assert 20.0 not in texts                # blank-on-both skipped
    conn.close()


def test_ingest_is_idempotent(tmp_path):
    src = str(tmp_path / "annotation.db")
    dst = str(tmp_path / "roman.db")
    _make_source(src)
    ingest.ingest(source_path=src, db_path=dst)
    v, s = ingest.ingest(source_path=src, db_path=dst)  # second pass
    assert (v, s) == (0, 0)  # nothing new
