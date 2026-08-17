import json

import db
import transliterate


def _seed_urdu(path, rows):
    """rows: list of (urdu,). Returns list of segment ids in insertion order."""
    db.init_db(path)
    conn = db.connect(path)
    conn.execute("INSERT INTO videos (youtube_url, title) VALUES ('u', 'V')")
    vid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    ids = []
    for i, (urdu,) in enumerate(rows):
        conn.execute(
            "INSERT INTO segments (video_id, start_time, urdu_text) VALUES (?, ?, ?)",
            (vid, float(i), urdu),
        )
        ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.commit()
    conn.close()
    return ids


def test_run_writes_roman_norm_and_indexes(tmp_path):
    p = str(tmp_path / "roman.db")
    _seed_urdu(p, [("a",), ("b",)])
    canned = {"a": "Namaaz", "b": "Roza"}
    n = transliterate.run(
        translit_batch=lambda batch: {sid: canned[u] for sid, u, _t in batch},
        db_path=p,
    )
    assert n == 2
    conn = db.connect_ro(p)
    # roman_text preserves the model's spelling; roman_norm is folded for search.
    roman, norm = conn.execute(
        "SELECT roman_text, roman_norm FROM segments WHERE urdu_text = 'a'"
    ).fetchone()
    assert roman == "Namaaz"
    assert norm == "namaz"
    # FTS row exists and matches the folded token.
    hit = conn.execute(
        "SELECT rowid FROM segments_fts WHERE segments_fts MATCH 'namaz'"
    ).fetchone()
    assert hit is not None
    conn.close()


def test_run_is_resumable_and_skips_done(tmp_path):
    p = str(tmp_path / "roman.db")
    _seed_urdu(p, [("a",), ("b",), ("c",)])
    calls = []

    def batch_fn(batch):
        calls.append([u for _s, u, _t in batch])
        return {sid: u.upper() for sid, u, _t in batch}

    transliterate.run(limit=1, batch_size=1, translit_batch=batch_fn, db_path=p)
    assert transliterate.status(p) == (1, 2)
    # Second run continues where the first stopped — never re-processes 'a'.
    transliterate.run(translit_batch=batch_fn, db_path=p)
    assert transliterate.status(p) == (3, 0)
    assert calls[0] == ["a"]
    assert "a" not in sum(calls[1:], [])


def test_parse_tolerates_code_fences_and_prose():
    fenced = '```json\n{"segments":[{"id":7,"roman":"namaz"}]}\n```'
    assert transliterate._parse(fenced) == {7: "namaz"}
    noisy = 'Here you go:\n{"segments":[{"id":1,"roman":"shukr"}]}\nHope that helps.'
    assert transliterate._parse(noisy) == {1: "shukr"}
    assert transliterate._parse("no json here") == {}


def test_claude_cli_provider_parses_envelope(monkeypatch):
    # With PROVIDER=claude_cli, a batch goes through the CLI, whose JSON envelope
    # holds the (possibly fenced) assistant text in `result`.
    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = json.dumps(
            {"is_error": False, "result": '```json\n{"segments":[{"id":5,"roman":"sabr"}]}\n```'}
        )

    monkeypatch.setattr(transliterate.config, "PROVIDER", "claude_cli")
    monkeypatch.setattr(transliterate.subprocess, "run", lambda *a, **k: FakeProc())
    out = transliterate._default_translit_batch([(5, "urdu", "title")], "claude-haiku-4-5")
    assert out == {5: "sabr"}


def test_ensure_transliterates_only_missing(tmp_path):
    p = str(tmp_path / "roman.db")
    ids = _seed_urdu(p, [("a",), ("b",)])
    conn = db.connect(p)
    # Pre-fill the first segment as if already done.
    transliterate._write_roman(conn, ids[0], "already", "test")
    conn.commit()
    calls = []

    def batch_fn(batch):
        calls.append([sid for sid, _u, _t in batch])
        return {sid: u.upper() for sid, u, _t in batch}

    out = transliterate.ensure(conn, ids, translit_batch=batch_fn)
    assert out[ids[0]] == "already"       # untouched
    assert out[ids[1]] == "B"             # transliterated on demand
    assert calls == [[ids[1]]]            # only the missing one was sent
    conn.close()


def test_translit_query_uses_injected_completion():
    urdu = transliterate.translit_query("namaz", complete=lambda system, user: "نماز")
    assert urdu == "نماز"


def test_unreturned_id_stays_pending_and_loop_terminates(tmp_path):
    # A segment the model never returns must not be lost, and must not spin the
    # engine forever: it stays pending and run() terminates.
    p = str(tmp_path / "roman.db")
    _seed_urdu(p, [("a",), ("b",)])

    def never_returns_b(batch):
        return {sid: u.upper() for sid, u, _t in batch if u != "b"}

    transliterate.run(batch_size=2, translit_batch=never_returns_b, db_path=p)
    done, pending = transliterate.status(p)
    assert done == 1 and pending == 1
