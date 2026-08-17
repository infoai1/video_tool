# video_tool — Roman Urdu Video Transcripts

Generate **searchable Roman Urdu transcripts** from Maulana's video lectures.

The videos already have Urdu-script transcripts (from ASR). This tool converts
them to **Roman Urdu** (Urdu written in the Latin alphabet) with Claude Haiku,
indexes them, and serves a small web app where you can:

- **search across all videos** in Roman Urdu — spelling doesn't have to be exact
  (`namaz`, `namaaz`, `namāz` all match), and each result links straight to that
  moment on YouTube; and
- **browse a video's full transcript**, line by line with timestamps, with an
  in-page filter.

## Searchable everywhere, transliterated on demand

The whole library is searchable **immediately after ingest**, before any
transliteration, and Roman Urdu is produced **lazily** — only for the segments a
user actually looks at. This avoids a giant upfront transliteration job.

How search covers 100% of the corpus with (almost) nothing transliterated yet:

- Ingest indexes the **Urdu script itself** (`urdu_fts`). That's a cheap one-time
  copy of what's already in `annotation.db`, so every video is searchable at once.
- A Roman Urdu **query** is transliterated *back* to Urdu (once, then cached) and
  matched against that Urdu index — so `namaz` finds `نماز` across the library.
- Segments that already have Roman are also matched on the **Roman index**
  (`segments_fts`), which is more precise; the two result sets are unioned.

When a result or a video page is shown, the frontend asks `/api/romanize` to
transliterate just those lines (cached, so each segment is done at most once).
The page shows Urdu instantly and Roman fills in a few lines at a time.

```
annotation.db          roman.db (this tool owns it)
(Urdu-script,    ─►    videos / segments / urdu_fts   ← searchable now
 read-only)      ingest                     │
                          search (Urdu ∪ Roman) ─┐    web app
                          on demand: /api/romanize ┘ → DeepSeek → roman + roman_fts
```

- **Source**: `annotation.db` — the CPS transcript database (the shukr app reads
  the same one). Opened **read-only**; never written to.
- **Store**: `roman.db` — this tool's own SQLite file. Derived data: delete it
  and rebuild any time. Holds the Urdu + Roman search indexes and the query cache.

| File | Role |
|------|------|
| `source.py` | read Urdu-script segments from `annotation.db` (read-only) |
| `ingest.py` | copy source segments in **and index the Urdu script** for search |
| `transliterate.py` | Urdu → Roman (`ensure()` on demand, `run()` for bulk); + query transliteration |
| `normalize.py` | fold spelling variance for search — Roman **and** Urdu, corpus **and** query |
| `search.py` | Urdu ∪ Roman FTS search + per-video transcript retrieval |
| `app.py` / `templates/` / `static/app.js` | web app: search, browse, on-demand romanize |
| `cli.py` | `ingest` / `transliterate` / `status` / `serve` |

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then edit paths / API key
```

Configuration (all via env, see `.env.example`):

- `VIDEO_TOOL_SOURCE_DB` — path to `annotation.db` (default is the Hetzner path).
- `VIDEO_TOOL_DB` — where to keep `roman.db` (default `roman.db`).
- `VIDEO_TOOL_PROVIDER` — how the model is reached:
  - `claude_cli` — the authenticated `claude` CLI (Claude **subscription**);
    transliteration is covered by the plan, no API key. Recommended where the
    CLI is logged in (e.g. the CPS box).
  - `anthropic` — Anthropic API directly (needs `ANTHROPIC_API_KEY`).
  - `openrouter` — OpenRouter API (needs `OPENROUTER_API_KEY`).
- `VIDEO_TOOL_MODEL` — leave unset to get Haiku for the chosen provider
  (`claude-haiku-4-5` natively / via the CLI, `anthropic/claude-haiku-4.5` on
  OpenRouter).

All three reach the same Haiku model and need no extra Python dependency
(`claude_cli` shells out to the CLI; `openrouter` uses the standard library).

## Try it without the source DB or an API key

```bash
python scripts/demo_seed.py     # loads two short sample videos
python cli.py serve             # http://127.0.0.1:5060
```

## Build the real thing

```bash
python cli.py status     # what's in the source and the store
python cli.py ingest     # copy ALL Urdu segments in + index them → searchable now
python cli.py serve      # run the app; Roman fills in on demand as pages are viewed
```

That's it for the demand-based setup: after `ingest`, the whole library is
searchable and transliteration happens lazily via the web app. No bulk job.

**Optional bulk fill.** If you'd rather have Roman ready everywhere up front
(e.g. for the fastest page loads), run the resumable bulk pass — cheap with
DeepSeek:

```bash
VIDEO_TOOL_PROVIDER=openrouter VIDEO_TOOL_MODEL=deepseek/deepseek-v4-flash \
  python cli.py transliterate            # drains the backlog; --limit N to chunk
```

`status` prints a rough cost estimate before you commit to the whole corpus.

## On-demand transliteration & cost

Demand-based transliteration means the "large job" never has to run at once —
each video costs a transliteration only when someone opens it, and only once
(it's cached). **DeepSeek** (`deepseek/deepseek-v4-flash` via OpenRouter) is the
recommended on-demand model: ~$0.00006 per segment, so a whole video is a
fraction of a cent, and the full ~427k-segment corpus would be only a few
dollars if you ever chose to bulk-fill it.

Set the on-demand model with `VIDEO_TOOL_PROVIDER` + `VIDEO_TOOL_MODEL` (see
below). The `/api/romanize` endpoint caps work per request and the frontend
requests Roman in small chunks, so pages stay responsive.

## Tests

```bash
pip install pytest
pytest
```

The tests cover normalisation, search, the store, ingest, and the transliteration
engine — all without the network or the source DB (the model call is injected).
