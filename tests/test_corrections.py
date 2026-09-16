"""Correcting a line has to reach the page, the search text and the index."""
import sqlite3

import pytest

import corrections
import normalize


@pytest.fixture()
def conn():
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE videos (id INTEGER PRIMARY KEY, title TEXT, youtube_url TEXT);"
        "CREATE TABLE segments (id INTEGER PRIMARY KEY, video_id INT, start_time REAL,"
        " roman_text TEXT, roman_clean TEXT, roman_norm TEXT, urdu_text TEXT);"
        "CREATE VIRTUAL TABLE segments_fts USING fts5(roman_norm, tokenize='unicode61');"
        "INSERT INTO videos (id, title, youtube_url) VALUES (7, 'Lecture Seven', 'https://y/abcdefghijk');"
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


def test_apply_then_revert_restores_everything(conn):
    corrections.apply(conn, 1, "namaz hai khusho ki zuban mein")
    prior = corrections.last(conn, 1)
    assert corrections.apply(conn, 1, prior["was"], who="revert") is True
    shown = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(roman_clean),''), roman_text) FROM segments WHERE id=1"
    ).fetchone()[0]
    assert shown == "namaz hai khasho ki zuban mein"
    assert _matches(conn, "khasho") == [1]
    hist = corrections.history(conn, 7)
    assert hist[0]["who"] == "revert"


def test_recent_is_newest_first_across_lectures(conn):
    corrections.apply(conn, 1, "one")
    corrections.apply(conn, 2, "two")
    r = corrections.recent(conn)
    assert [x["now"] for x in r] == ["two", "one"]


# --- word_matches / apply_word: variant-spelling bulk fix ---------------------

def test_word_matches_finds_variant_spellings(conn):
    # "line" and "liine" both fold to the same normalized key.
    conn.execute("INSERT INTO segments (id, video_id, start_time, roman_text, roman_norm) "
                 "VALUES (4, 7, 3700.0, 'ek aur liine hai', 'ek aur line hai')")
    conn.execute("INSERT INTO segments_fts (rowid, roman_norm) VALUES (4, 'ek aur line hai')")
    rows, variants, total = corrections.word_matches(conn, ["line"])
    assert total == 3   # segments 2, 3, 4
    assert variants == {"line", "liine"}
    ids = {r["segment_id"] for r in rows}
    assert ids == {2, 3, 4}


def test_word_matches_is_whole_word(conn):
    conn.execute("INSERT INTO segments (id, video_id, start_time, roman_text, roman_norm) "
                 "VALUES (4, 7, 3700.0, 'yeh topic hai toh sahi', 'yeh topic hai toh sahi')")
    conn.execute("INSERT INTO segments_fts (rowid, roman_norm) VALUES (4, 'yeh topic hai toh sahi')")
    rows, _variants, total = corrections.word_matches(conn, ["to"])
    assert total == 0      # "topic" and "toh" must not be touched


def test_apply_word_replaces_only_the_matched_variant_case_preserved(conn):
    conn.execute("INSERT INTO segments (id, video_id, start_time, roman_text, roman_norm) "
                 "VALUES (5, 7, 3800.0, 'Khasho se namaz parhna', 'khasho se namaz parhna')")
    conn.execute("INSERT INTO segments_fts (rowid, roman_norm) VALUES (5, 'khasho se namaz parhna')")
    r = corrections.apply_word(conn, ["khasho"])
    shown = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(roman_clean),''), roman_text) FROM segments WHERE id=5"
    ).fetchone()[0]
    assert shown == "Khasho se namaz parhna"   # already correct, whole-word match, nothing to change
    assert r["lines"] == 0


def test_apply_word_fixes_a_real_variant(conn):
    conn.execute("INSERT INTO segments (id, video_id, start_time, roman_text, roman_norm) "
                 "VALUES (5, 7, 3800.0, 'Namaaz se pehle wuzu', 'namaz se pehle wuzu')")
    conn.execute("INSERT INTO segments_fts (rowid, roman_norm) VALUES (5, 'namaz se pehle wuzu')")
    r = corrections.apply_word(conn, ["namaz"])
    shown = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(roman_clean),''), roman_text) FROM segments WHERE id=5"
    ).fetchone()[0]
    assert shown == "Namaz se pehle wuzu"   # case of the original kept
    assert r["lines"] == 1
    fixes = corrections.word_fixes(conn)
    assert fixes[0] == {"wrong": "namaaz", "right": "namaz", "who": "owner", "at": fixes[0]["at"]}


def test_apply_word_ticked_subset_only(conn):
    conn.execute("INSERT INTO segments (id, video_id, start_time, roman_text, roman_norm) "
                 "VALUES (4, 7, 3700.0, 'ek aur liine hai', 'ek aur line hai'),"
                 " (6, 7, 3900.0, 'dusri liine bhi', 'dusri line bhi')")
    conn.execute("INSERT INTO segments_fts (rowid, roman_norm) VALUES"
                 " (4, 'ek aur line hai'), (6, 'dusri line bhi')")
    r = corrections.apply_word(conn, ["line"], ids=[4])
    assert r["lines"] == 1
    assert corrections.history(conn, 7)[0]["segment_id"] == 4
    # segment 6 (liine), not ticked, untouched
    assert conn.execute("SELECT roman_text FROM segments WHERE id=6").fetchone()[0] == "dusri liine bhi"


def test_apply_word_skips_a_hand_corrected_line(conn):
    corrections.apply(conn, 2, "dusri seedhi line", who="junaid")   # a person's own judgement call
    r = corrections.apply_word(conn, ["line"])
    assert r["skipped_human"] == 1
    assert r["lines"] == 0


def test_apply_word_writes_a_word_fix_and_corrections(conn):
    conn.execute("INSERT INTO segments (id, video_id, start_time, roman_text, roman_norm) "
                 "VALUES (4, 7, 3700.0, 'ek aur liine hai', 'ek aur line hai')")
    conn.execute("INSERT INTO segments_fts (rowid, roman_norm) VALUES (4, 'ek aur line hai')")
    r = corrections.apply_word(conn, ["line"])
    fixes = corrections.word_fixes(conn)
    assert {"wrong": "liine", "right": "line"} in [
        {"wrong": f["wrong"], "right": f["right"]} for f in fixes]
    hist = corrections.history(conn, 7)
    assert all(h["who"] == "glossary:line" for h in hist)
    assert len(hist) == r["lines"]


def test_word_matches_rejects_more_than_one_word(conn):
    rows, variants, total = corrections.word_matches(conn, ["two words"])
    assert rows == [] and variants == set() and total == 0


# --- multi-spelling word_matches / apply_word ----------------------------------

def test_word_matches_unions_several_typed_spellings(conn):
    conn.execute("INSERT INTO segments (id, video_id, start_time, roman_text, roman_norm) "
                 "VALUES (4, 7, 3700.0, 'khusho se dua', 'khusho se dua'),"
                 " (5, 7, 3800.0, 'khashu ke baad', 'khashu ke baad')")
    conn.execute("INSERT INTO segments_fts (rowid, roman_norm) VALUES"
                 " (4, 'khusho se dua'), (5, 'khashu ke baad')")
    rows, variants, total = corrections.word_matches(conn, ["khushu", "khusho", "khashu"])
    assert total == 2
    assert variants == {"khusho", "khashu"}
    assert {r["segment_id"] for r in rows} == {4, 5}


def test_apply_word_replaces_every_variant_with_the_first_spelling(conn):
    conn.execute("INSERT INTO segments (id, video_id, start_time, roman_text, roman_norm) "
                 "VALUES (4, 7, 3700.0, 'Khusho se dua', 'khusho se dua'),"
                 " (5, 7, 3800.0, 'khashu ke baad', 'khashu ke baad')")
    conn.execute("INSERT INTO segments_fts (rowid, roman_norm) VALUES"
                 " (4, 'khusho se dua'), (5, 'khashu ke baad')")
    r = corrections.apply_word(conn, ["khushu", "khusho", "khashu"])
    assert r["lines"] == 2
    shown4 = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(roman_clean),''), roman_text) FROM segments WHERE id=4"
    ).fetchone()[0]
    shown5 = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(roman_clean),''), roman_text) FROM segments WHERE id=5"
    ).fetchone()[0]
    assert shown4 == "Khushu se dua"   # initial capital preserved
    assert shown5 == "khushu ke baad"
    fixes = {f["wrong"] for f in corrections.word_fixes(conn)}
    assert fixes == {"khusho", "khashu"}


def test_apply_word_variant_equal_to_replacement_is_a_no_op(conn):
    conn.execute("INSERT INTO segments (id, video_id, start_time, roman_text, roman_norm) "
                 "VALUES (4, 7, 3700.0, 'khushu se dua', 'khushu se dua')")
    conn.execute("INSERT INTO segments_fts (rowid, roman_norm) VALUES (4, 'khushu se dua')")
    r = corrections.apply_word(conn, ["khushu", "khusho"])
    assert r["lines"] == 0
    assert corrections.word_fixes(conn) == []


def test_apostrophe_does_not_leak_into_the_variant(conn):
    conn.execute("INSERT INTO segments (id, video_id, start_time, roman_text, roman_norm) "
                 "VALUES (4, 7, 3700.0, \"khushu', phir dua\", 'khushu phir dua')")
    conn.execute("INSERT INTO segments_fts (rowid, roman_norm) VALUES (4, 'khushu phir dua')")
    rows, variants, total = corrections.word_matches(conn, ["khushu"])
    assert total == 1
    assert variants == {"khushu"}   # not "khushu'"
    r = corrections.apply_word(conn, ["khushu"])
    shown = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(roman_clean),''), roman_text) FROM segments WHERE id=4"
    ).fetchone()[0]
    assert shown == "khushu', phir dua"   # already correct -- punctuation untouched
    assert r["lines"] == 0


# --- batch / undo_batch: reverting one bulk fix as a group ---------------------

def test_apply_word_records_a_shared_batch_on_every_changed_line(conn):
    conn.execute("INSERT INTO segments (id, video_id, start_time, roman_text, roman_norm) "
                 "VALUES (4, 7, 3700.0, 'ek aur liine hai', 'ek aur line hai'),"
                 " (6, 7, 3900.0, 'dusri liine bhi', 'dusri line bhi')")
    conn.execute("INSERT INTO segments_fts (rowid, roman_norm) VALUES"
                 " (4, 'ek aur line hai'), (6, 'dusri line bhi')")
    r = corrections.apply_word(conn, ["line"])
    assert r["lines"] == 2 and r["batch"]
    batches = {row[0] for row in conn.execute(
        "SELECT batch FROM corrections WHERE segment_id IN (4, 6)")}
    assert batches == {r["batch"]}


def test_apply_word_with_no_changes_returns_no_batch(conn):
    r = corrections.apply_word(conn, ["nonexistentword"])
    assert r["lines"] == 0
    assert r["batch"] is None


def test_undo_batch_restores_every_line_and_leaves_others_alone(conn):
    conn.execute("INSERT INTO segments (id, video_id, start_time, roman_text, roman_norm) "
                 "VALUES (4, 7, 3700.0, 'ek aur liine hai', 'ek aur line hai'),"
                 " (6, 7, 3900.0, 'dusri liine bhi', 'dusri line bhi')")
    conn.execute("INSERT INTO segments_fts (rowid, roman_norm) VALUES"
                 " (4, 'ek aur line hai'), (6, 'dusri line bhi')")
    corrections.apply(conn, 2, "an unrelated hand fix", who="junaid")
    r = corrections.apply_word(conn, ["line"])
    n = corrections.undo_batch(conn, r["batch"])
    assert n == 2
    shown4 = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(roman_clean),''), roman_text) FROM segments WHERE id=4"
    ).fetchone()[0]
    shown6 = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(roman_clean),''), roman_text) FROM segments WHERE id=6"
    ).fetchone()[0]
    assert shown4 == "ek aur liine hai" and shown6 == "dusri liine bhi"
    # the unrelated hand fix on segment 2, outside this batch, is untouched
    shown2 = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(roman_clean),''), roman_text) FROM segments WHERE id=2"
    ).fetchone()[0]
    assert shown2 == "an unrelated hand fix"


def test_undo_batch_on_unknown_or_none_batch_is_a_no_op(conn):
    corrections.apply(conn, 1, "namaz hai khusho ki zuban mein")
    assert corrections.undo_batch(conn, None) == 0
    assert corrections.undo_batch(conn, "nope-not-a-real-batch") == 0
    shown = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(roman_clean),''), roman_text) FROM segments WHERE id=1"
    ).fetchone()[0]
    assert shown == "namaz hai khusho ki zuban mein"   # unchanged


# --- word_matches_fixable: what the fix-words page is allowed to offer ---------

def test_word_matches_fixable_excludes_hand_corrected_lines(conn):
    corrections.apply(conn, 2, "dusri seedhi line", who="junaid")
    rows, _variants, _total = corrections.word_matches(conn, ["line"])
    assert any(r["segment_id"] == 2 and r["human"] for r in rows)   # engine still flags it
    fixable, _variants, _total = corrections.word_matches_fixable(conn, ["line"])
    assert all(r["segment_id"] != 2 for r in fixable)   # the page never offers it


def test_other_spellings_are_offered_not_applied(conn):
    """The machine lists look-alikes; only the ones passed in are rewritten.

    dua/due and namaz/namazi share consonants but are different words, so
    spellings_like may offer them -- apply_word must touch only what it is given.
    """
    for sid, txt in ((10, "namaz padhna"), (11, "Namaaz ka waqt"), (12, "namazi log")):
        nrm = normalize.normalize(txt)
        conn.execute("INSERT INTO segments (id, video_id, start_time, roman_text, roman_norm, urdu_text)"
                     " VALUES (?, 7, ?, ?, ?, '')", (sid, sid, txt, nrm))
        conn.execute("INSERT INTO segments_fts (rowid, roman_norm) VALUES (?, ?)", (sid, nrm))
    offered = dict(corrections.spellings_like(conn, "namaz"))
    assert "namazi" in offered                       # offered, because consonants match

    corrections.apply_word(conn, ["namaz", "namaaz"], who="owner")
    shown = lambda i: conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(roman_clean),''), roman_text) FROM segments WHERE id=?", (i,)).fetchone()[0]
    assert shown(11) == "Namaz ka waqt"              # chosen spelling fixed, capital kept
    assert shown(12) == "namazi log"                 # never chosen, never touched
