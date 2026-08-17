"""Transliterate Urdu-script segments to Roman Urdu with Claude Haiku.

Why Haiku: transliteration is high-volume and low-reasoning, so the cheapest
capable model is the right one — and one model transliterating the whole corpus
gives internally consistent spelling, which is exactly what makes the result
searchable (see normalize.py).

The run is:
  - batched   — many segments per request, to amortise the fixed prompt cost;
  - resumable — only segments with roman_text IS NULL are processed, and each
                batch is committed, so an interrupted run costs one batch, not
                a restart;
  - structured — the model must return JSON matching a schema, so we get a
                reliable id -> roman mapping instead of parsing prose.

The model call is injectable (`translit_batch`) so the engine can be tested
without the network or an API key, and it can be reached through either the
Anthropic API directly or OpenRouter (see config.PROVIDER).
"""
import json
import subprocess
import urllib.request

import config
import db
import normalize

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

_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "roman": {"type": "string"},
                },
                "required": ["id", "roman"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["segments"],
    "additionalProperties": False,
}

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


def _parse(text):
    """Extract {id: roman} from the model's reply, tolerating code fences or
    stray prose by taking the outermost JSON object."""
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j == -1:
        return {}
    data = json.loads(text[i : j + 1])
    return {int(item["id"]): item["roman"] for item in data.get("segments", [])}


def _anthropic_batch(batch, model):
    """Transliterate one batch via the Anthropic API (structured output).

    Imported lazily so the rest of the tool runs without the anthropic package.
    """
    import anthropic

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=model,
        max_tokens=8000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        messages=[{"role": "user", "content": _user_content(batch)}],
    )
    text = next(b.text for b in resp.content if b.type == "text")
    return _parse(text)


def _claude_cli_batch(batch, model):
    """Transliterate one batch via the authenticated `claude` CLI (Claude Code).

    Uses the box's Claude subscription — no API key, and calls are covered by
    the plan rather than billed per token. The CLI returns a JSON envelope whose
    `result` field holds the assistant text; we parse our JSON out of that.
    """
    proc = subprocess.run(
        [config.CLAUDE_BIN, "-p", "--model", model,
         "--output-format", "json", "--append-system-prompt", SYSTEM + _JSON_INSTRUCTION],
        input=_user_content(batch),
        capture_output=True,
        text=True,
        timeout=config.CLI_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI exited {proc.returncode}: {proc.stderr[:300]}")
    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude CLI error: {str(envelope)[:300]}")
    return _parse(envelope.get("result", ""))


def _openrouter_batch(batch, model):
    """Transliterate one batch via OpenRouter's OpenAI-compatible API.

    Uses only the standard library so the tool takes no extra dependency to run
    on a box that only has an OpenRouter key.
    """
    if not config.OPENROUTER_API_KEY:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set (needed for VIDEO_TOOL_PROVIDER=openrouter)."
        )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM + _JSON_INSTRUCTION},
            {"role": "user", "content": _user_content(batch)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 8000,
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
    return _parse(data["choices"][0]["message"]["content"])


def _default_translit_batch(batch, model=None):
    """Dispatch one batch to the configured provider. `batch` is (id, urdu, title)."""
    model = model or config.MODEL
    if config.PROVIDER == "openrouter":
        return _openrouter_batch(batch, model)
    if config.PROVIDER in ("claude_cli", "subscription"):
        return _claude_cli_batch(batch, model)
    return _anthropic_batch(batch, model)


def _write_roman(conn, seg_id, roman, model):
    """Store one segment's Roman Urdu and (re)index it for search."""
    norm = normalize.normalize(roman)
    conn.execute(
        "UPDATE segments SET roman_text = ?, roman_norm = ?, model = ? WHERE id = ?",
        (roman, norm, model, seg_id),
    )
    # rowid == segments.id, so re-indexing is delete-then-insert by id.
    conn.execute("DELETE FROM segments_fts WHERE rowid = ?", (seg_id,))
    conn.execute(
        "INSERT INTO segments_fts (rowid, roman_norm) VALUES (?, ?)", (seg_id, norm)
    )


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
