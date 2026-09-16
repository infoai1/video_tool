"""Correcting a line has to reach the page, the search text and the index."""
import sqlite3

import pytest

import corrections


@pytest.fixture()
def conn():
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE segments (id INTEGER PRIMARY KEY, video_id INT, start_time REAL,"
        " roman_text TEXT, roman_clean TEXT, roman_norm TEXT, urdu_text TEXT);"
        "CREATE VIRTUAL TABLE segments_fts USING fts5(roman_norm, tokenize='unicode61');"
        "INSERT INTO segments (id, video_id, start_time, roman_text, roman_norm, urdu_text) VALUES"
        " (1, 7, 3475.0, 'namaz hai khasho ki zuban mein', 'namaz hai khasho ki zuban mein', 'اردو'),"
        " (2, 7, 3480.0, 'dusri line', 'dusri line', ''),"
        " (3, 7, 3600.0, 'bahut baad ki line', 'bahut baad ki line', '');"
        "INSERT INTO segments_fts (rowid, roman_norm) VALUES"
        " (1, 'namaz hai khasho ki zuban mein'), (2, 'dusri line'), (3, 'bahut baad ki line');")
    db.commit()
    yield db
    db.close()


def _matches(conn, word):
    return [r[0] for r in conn.execute(
        "SELECT rowid FROM segments_fts WHERE segments_fts MATCH ?", (word,))]


def test_a_correction_is_read_and_searched(conn):
    assert corrections.apply(conn, 1, "namaz hai khusho ki zuban mein") is True
    shown = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(roman_clean),''), roman_text) FROM segments WHERE id=1"
    ).fetchone()[0]
    assert shown == "namaz hai khusho ki zuban mein"
    assert _matches(conn, "khusho") == [1]
    assert _matches(conn, "khasho") == []      # the mishearing is gone from search too


def test_the_original_wording_is_kept(conn):
    corrections.apply(conn, 1, "namaz hai khusho ki zuban mein", who="junaid")
    [row] = corrections.history(conn, 7)
    assert row["was"] == "namaz hai khasho ki zuban mein"
    assert row["now"] == "namaz hai khusho ki zuban mein"
    assert row["who"] == "junaid" and row["at"]


def test_an_unchanged_line_is_not_touched(conn):
    assert corrections.apply(conn, 1, "namaz hai khasho ki zuban mein") is False
    assert corrections.apply(conn, 1, "  namaz hai khasho ki zuban mein  ") is False
    assert corrections.history(conn, 7) == []


def test_an_empty_box_never_erases_a_line(conn):
    assert corrections.apply(conn, 1, "") is False
    assert corrections.apply(conn, 1, "   ") is False
    assert conn.execute("SELECT roman_text FROM segments WHERE id=1").fetchone()[0]


def test_an_unknown_line_is_refused(conn):
    assert corrections.apply(conn, 999, "anything") is False


def test_a_correction_can_itself_be_corrected(conn):
    corrections.apply(conn, 1, "pehli koshish")
    corrections.apply(conn, 1, "doosri koshish")
    hist = corrections.history(conn, 7)
    assert [h["now"] for h in hist] == ["doosri koshish", "pehli koshish"]  # newest first
    assert hist[0]["was"] == "pehli koshish"
    assert _matches(conn, "koshish") == [1]    # indexed once, not twice


def test_the_window_opens_before_the_moment_asked_about(conn):
    lines = corrections.window(conn, 7, around=3480, span=120)
    ids = [l["id"] for l in lines]
    # 3475 comes before the moment asked about and is still shown -- a sentence
    # needs its run-up; 3600 is past the end of the window.
    assert ids == [1, 2]
    assert lines[0]["urdu"] == "اردو"
    assert lines[0]["corrected"] is False


def test_the_window_marks_what_has_been_corrected(conn):
    corrections.apply(conn, 1, "namaz hai khusho ki zuban mein")
    lines = corrections.window(conn, 7, around=3475)
    assert lines[0]["corrected"] is True
    assert lines[1]["corrected"] is False


def test_with_no_moment_the_lecture_starts_at_the_top(conn):
    lines = corrections.window(conn, 7, around=None, span=100)
    assert lines == [] or lines[0]["t"] < 100
