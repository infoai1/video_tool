"""Transliterate Urdu-script segments to Roman Urdu with Claude Haiku.

Why Haiku: transliteration is high-volume and low-reasoning, so the cheapest
capable model is the right one — and one model transliterating the whole corpus
gives internally consistent spelling, which is exactly what makes the result
searchable (see normalize.py).

Two entry points:
  - run()     — bulk/background fill of the whole backlog (batched, resumable).
  - ensure()  — on-demand: transliterate a specific set of segments right now
                (used when a search result or a video page needs Roman), cached
                so each segment is transliterated at most once.

The model is asked to return JSON ({segments:[{id,roman}]}) and the reply is
parsed tolerantly. It is reached through the configured provider (Anthropic,
the claude CLI, or OpenRouter — see config.PROVIDER), and the call is injectable
so the engine can be tested without the network or an API key.
"""
import datetime
import json
import re
import subprocess
import urllib.request

import config
import db
import normalize


def _now():
    return datetime.datetime.utcnow().isoformat() + "Z"

# The JSON-shape instruction appended for providers that don't take a schema
# (OpenRouter, the claude CLI). The Anthropic SDK path enforces it via _SCHEMA.
_JSON_INSTRUCTION = (
    '\n\nReturn ONLY JSON of the form '
    '{"segments":[{"id":<int>,"roman":<string>}]}, one entry per input id. '
    "No preamble, no explanation."
)

# Stable across every request, so it caches. Kept factual and specific: the
# model's job is transliteration (script conversion), NOT translation.
SYSTEM = """You transliterate Urdu-script text into Roman Urdu (Urdu written in \
the Latin alphabet), for a search index of Islamic lectures by Maulana \
Wahiduddin Khan.

Rules:
- Transliterate the sound, do NOT translate to English. "نماز" -> "namaz", \
never "prayer".
- Use plain ASCII letters only. No diacritics, no macrons, no Arabic letters.
- Keep the wording and order of the original; do not summarise, add, or drop words.
- Spell each recurring word the SAME way every time (e.g. always "namaz", not \
sometimes "namaaz") so the index is consistent.
- Keep digits as digits. If a segment is only music, noise, or empty, return an \
empty string for it.
- Return exactly one entry per input id."""

# For the reverse direction: turn a user's Roman Urdu search phrase back into
# Urdu script, so it can be matched against the Urdu index (search.py).
QUERY_SYSTEM = (
    "You convert a Roman Urdu search phrase into Urdu script. Output ONLY the "
    "Urdu words for the same phrase, space separated — no punctuation, no Latin "
    "letters, no explanation."
)

_PENDING_SQL = """
SELECT s.id, s.urdu_text, v.title
FROM segments s JOIN videos v ON v.id = s.video_id
WHERE s.roman_text IS NULL
ORDER BY s.id
LIMIT ?
"""


def _payload(batch):
    return {"segments": [{"id": sid, "urdu": urdu} for sid, urdu, _ in batch]}


def _user_content(batch):
    return "Transliterate every segment:\n" + json.dumps(_payload(batch), ensure_ascii=False)


# One {"id":N,"roman":"..."} entry, tolerant of unescaped inner quotes: the
# roman value runs (non-greedily) up to the quote that precedes a , ] or } —
# the only quote that is structurally meaningful. Cheaper models (DeepSeek)
# sometimes leave inner quotes or raw newlines unescaped; this salvages the
# well-formed entries instead of dropping the whole batch.
_ENTRY_RE = re.compile(
    r'"id"\s*:\s*(\d+)\s*,\s*"roman"\s*:\s*"(.*?)"\s*(?=[,}\]])', re.DOTALL
)


def _unescape(s):
    """Best-effort JSON string unescape for a salvaged roman value."""
    try:
        return json.loads('"' + s + '"')
    except Exception:
        return (s.replace('\\"', '"').replace("\\n", " ")
                 .replace("\\t", " ").replace("\\\\", "\\")).strip()


def _parse(text):
    """Extract {id: roman} from the model's reply. Take the outermost JSON
    object and parse it; if the model returned slightly-malformed JSON (an
    unescaped quote, a raw newline, a truncated tail), fall back to salvaging
    every complete {id, roman} entry by regex, so one bad entry doesn't lose
    the other 24. Un-returned ids simply get retried on the next pass."""
    i, j = text.find("{"), text.rfind("}")
    if i != -1 and j != -1:
        try:
            # strict=False tolerates raw control chars (newlines/tabs) in strings
            data = json.loads(text[i : j + 1], strict=False)
            out = {}
            for item in data.get("segments", []):
                try:
                    out[int(item["id"])] = item["roman"]
                except (KeyError, TypeError, ValueError):
                    pass
            if out:
                return out
        except (ValueError, AttributeError):
            pass
    return {int(m.group(1)): _unescape(m.group(2)) for m in _ENTRY_RE.finditer(text)}


# --- provider completions: (system, user) text in -> assistant text out -------
# One shared shape keeps the three providers interchangeable and serves both
# jobs: batch transliteration (JSON in the reply) and query transliteration
# (plain Urdu in the reply). No response_format / schema — some models (DeepSeek)
# return null content when json_object mode is forced; _parse handles the rest.


def _anthropic_complete(system, user, model, max_tokens):
    import anthropic

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    return next((b.text for b in resp.content if b.type == "text"), "")


def _claude_cli_complete(system, user, model, max_tokens):
    """Via the authenticated `claude` CLI — uses the Claude subscription, no key."""
    proc = subprocess.run(
        [config.CLAUDE_BIN, "-p", "--model", model,
         "--output-format", "json", "--append-system-prompt", system],
        input=user,
        capture_output=True,
        text=True,
        timeout=config.CLI_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI exited {proc.returncode}: {proc.stderr[:300]}")
    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude CLI error: {str(envelope)[:300]}")
    return envelope.get("result", "") or ""


def _openrouter_complete(system, user, model, max_tokens):
    """Via OpenRouter's OpenAI-compatible API — standard library only."""
    if not config.OPENROUTER_API_KEY:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set (needed for VIDEO_TOOL_PROVIDER=openrouter)."
        )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        config.OPENROUTER_URL,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.load(r)
    return data["choices"][0]["message"].get("content") or ""


def _complete(system, user, model, max_tokens):
    if config.PROVIDER == "openrouter":
        return _openrouter_complete(system, user, model, max_tokens)
    if config.PROVIDER in ("claude_cli", "subscription"):
        return _claude_cli_complete(system, user, model, max_tokens)
    return _anthropic_complete(system, user, model, max_tokens)


def _default_translit_batch(batch, model=None):
    """Dispatch one batch to the configured provider. `batch` is (id, urdu, ...)."""
    text = _complete(SYSTEM + _JSON_INSTRUCTION, _user_content(batch), model or config.MODEL, 8000)
    return _parse(text)


def translit_query(roman, model=None, complete=None):
    """Turn a Roman Urdu search phrase into Urdu script (for the Urdu index).

    `complete` is injectable for tests; defaults to the configured provider.
    Returns '' if the model gives nothing usable.
    """
    fn = complete or (lambda s, u: _complete(s, u, model or config.MODEL, 200))
    return (fn(QUERY_SYSTEM, roman) or "").strip()


def _write_roman(conn, seg_id, roman, model):
    """Store one segment's Roman Urdu and (re)index it for search."""
    norm = normalize.normalize(roman)
    conn.execute(
        "UPDATE segments SET roman_text = ?, roman_norm = ?, model = ?, roman_at = ? WHERE id = ?",
        (roman, norm, model, _now(), seg_id),
    )
    # rowid == segments.id, so re-indexing is delete-then-insert by id.
    conn.execute("DELETE FROM segments_fts WHERE rowid = ?", (seg_id,))
    conn.execute(
        "INSERT INTO segments_fts (rowid, roman_norm) VALUES (?, ?)", (seg_id, norm)
    )


def ensure(conn, ids, translit_batch=None, model=None):
    """On-demand: make sure each segment id has Roman Urdu, transliterating the
    missing ones now, and return {id: roman_or_None}.

    This is what the web app calls when a search result or a video line needs
    Roman that hasn't been produced yet. Each segment is transliterated at most
    once — already-done segments are returned from the store untouched. `conn`
    must be writable.
    """
    ids = [int(i) for i in ids]
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, urdu_text, roman_text FROM segments WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    have = {r[0]: r[2] for r in rows}
    missing = [(r[0], r[1], None) for r in rows if r[2] is None]
    if missing:
        model = model or config.MODEL
        call = translit_batch or (lambda b: _default_translit_batch(b, model))
        for i in range(0, len(missing), config.BATCH_SIZE):
            chunk = missing[i : i + config.BATCH_SIZE]
            romans = call(chunk)
            for seg_id, _urdu, _t in chunk:
                if seg_id in romans:
                    _write_roman(conn, seg_id, romans[seg_id], model)
                    have[seg_id] = romans[seg_id]
        conn.commit()
    return {i: have.get(i) for i in ids}


def run(limit=None, batch_size=None, model=None, translit_batch=None,
        progress=None, db_path=None):
    """Transliterate pending segments. Returns the number written.

    limit         cap on segments processed this run (None = drain the backlog).
    batch_size    segments per model request (default config.BATCH_SIZE).
    translit_batch  injectable model call (batch -> {id: roman}); defaults to Claude.
    progress      optional callback(done_so_far) after each batch, for a CLI meter.
    """
    db.init_db(db_path)
    batch_size = batch_size or config.BATCH_SIZE
    model = model or config.MODEL
    call = translit_batch or (lambda batch: _default_translit_batch(batch, model))
    conn = db.connect(db_path)
    done = 0
    try:
        while limit is None or done < limit:
            take = batch_size if limit is None else min(batch_size, limit - done)
            rows = conn.execute(_PENDING_SQL, (take,)).fetchall()
            if not rows:
                break
            batch = [(r[0], r[1], r[2]) for r in rows]
            romans = call(batch)
            wrote = 0
            for seg_id, _urdu, _title in batch:
                if seg_id in romans:
                    _write_roman(conn, seg_id, romans[seg_id], model)
                    wrote += 1
            conn.commit()
            if wrote == 0:
                # The model returned nothing usable for this batch; stop rather
                # than spin forever on the same rows.
                break
            done += wrote
            if progress:
                progress(done)
    finally:
        conn.close()
    return done


def romanize_video(conn, video_id, progress=None, translit_batch=None, model=None):
    """Transliterate all of a video's pending segments, reporting progress.

    `progress(done, total)` is called after each batch (done/total counted over
    the whole video, including already-transliterated lines). Returns (done, total).
    Used by the background romanize job so the user can keep browsing.
    """
    total = conn.execute(
        "SELECT COUNT(*) FROM segments WHERE video_id = ?", (video_id,)
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT id, urdu_text FROM segments WHERE video_id = ? AND roman_text IS NULL "
        "ORDER BY start_time",
        (video_id,),
    ).fetchall()
    done = total - len(rows)
    if progress:
        progress(done, total)
    model = model or config.MODEL
    call = translit_batch or (lambda b: _default_translit_batch(b, model))
    for i in range(0, len(rows), config.BATCH_SIZE):
        chunk = [(r[0], r[1], None) for r in rows[i : i + config.BATCH_SIZE]]
        romans = call(chunk)
        for seg_id, _u, _t in chunk:
            if seg_id in romans:
                _write_roman(conn, seg_id, romans[seg_id], model)
        conn.commit()
        done += len(chunk)
        if progress:
            progress(done, total)
    return done, total


def romanize_all(conn, progress=None, translit_batch=None, model=None):
    """Transliterate every pending segment across the whole library, reporting
    progress against the full corpus. Resumable (only NULL roman_text) and
    idempotent. Returns (done, total)."""
    total = conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
    done = conn.execute("SELECT COUNT(*) FROM segments WHERE roman_text IS NOT NULL").fetchone()[0]
    model = model or config.MODEL
    call = translit_batch or (lambda b: _default_translit_batch(b, model))
    if progress:
        progress(done, total)
    while True:
        rows = conn.execute(
            "SELECT id, urdu_text FROM segments WHERE roman_text IS NULL ORDER BY id LIMIT ?",
            (config.BATCH_SIZE,),
        ).fetchall()
        if not rows:
            break
        chunk = [(r[0], r[1], None) for r in rows]
        romans = call(chunk)
        wrote = 0
        for seg_id, _u, _t in chunk:
            if seg_id in romans:
                _write_roman(conn, seg_id, romans[seg_id], model)
                wrote += 1
        conn.commit()
        if wrote == 0:  # persistent failure on this batch — stop rather than spin
            break
        done += wrote
        if progress:
            progress(done, total)
    return done, total


def status(db_path=None):
    """(done, pending) transliteration counts, for the CLI status command."""
    if not db.exists(db_path):
        return 0, 0
    conn = db.connect_ro(db_path)
    try:
        done = conn.execute(
            "SELECT COUNT(*) FROM segments WHERE roman_text IS NOT NULL"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM segments WHERE roman_text IS NULL"
        ).fetchone()[0]
        return done, pending
    finally:
        conn.close()
