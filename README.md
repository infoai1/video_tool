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

## Why Roman Urdu

The transcripts are in Urdu script, but a large share of viewers search in Roman
Urdu — and Roman-Urdu queries can't keyword-match Urdu-script text at all. A
Roman Urdu transcript closes that gap. Because one model transliterates the whole
corpus, its spelling is internally consistent, and a normaliser (`normalize.py`)
folds the remaining spelling variance on both the corpus and the query so they
meet.

## How it fits together

```
annotation.db            roman.db (this tool owns it)
(Urdu-script,      ─►    videos / segments (urdu + roman + search index)
 read-only)        ingest        └─ transliterate (Claude Haiku) ─► web app
```

- **Source**: `annotation.db` — the CPS transcript database (the shukr app reads
  the same one). Opened **read-only**; never written to.
- **Store**: `roman.db` — this tool's own SQLite file. Derived data: delete it
  and rebuild any time. Holds the Roman Urdu and an FTS5 search index.

| File | Role |
|------|------|
| `source.py` | read Urdu-script segments from `annotation.db` (read-only) |
| `ingest.py` | copy source segments into `roman.db` |
| `transliterate.py` | Urdu → Roman Urdu with Claude Haiku (batched, resumable) |
| `normalize.py` | fold Roman Urdu spelling variance for search (corpus + query) |
| `search.py` | FTS5 search + per-video transcript retrieval |
| `app.py` / `templates/` | Flask web app (search + browse) |
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
python cli.py status                 # what's in the source and the store
python cli.py ingest                 # copy Urdu-script segments into roman.db
python cli.py transliterate          # fill in Roman Urdu with Claude Haiku
python cli.py serve                  # run the search + browse app
```

Both `ingest` and `transliterate` are **resumable** and take `--limit`, so you
can pilot on a subset first:

```bash
python cli.py ingest --limit 2000
python cli.py transliterate --limit 2000
```

`status` prints a rough cost estimate for finishing the backlog before you
commit to the whole corpus.

## Scale & cost

The full corpus is large (~2,200 videos, hundreds of thousands of segments).
Transliteration is batched (`VIDEO_TOOL_BATCH_SIZE`, default 25 segments per
request) and resumable, so it can run in chunks. Haiku is the cheapest capable
model for this; `cli.py status` gives an order-of-magnitude cost estimate for
what's left. **Start with a `--limit` pilot**, check quality in the web app,
then run the rest.

## Tests

```bash
pip install pytest
pytest
```

The tests cover normalisation, search, the store, ingest, and the transliteration
engine — all without the network or the source DB (the model call is injected).
