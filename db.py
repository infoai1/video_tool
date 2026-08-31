"""The tool's own SQLite store (roman.db): Roman Urdu transcripts + search index.

Kept entirely separate from the source annotation.db (see source.py). This file
is derived data — every row can be rebuilt from the source plus the model, so it
is safe to delete and regenerate.

Layout
------
videos     one row per video (its YouTube URL + title)
segments   one row per timestamped clip: the Urdu-script source text and its
           normalised form (`urdu_norm`), plus — once transliterated — its Roman
           Urdu (`roman_text`) and normalised form (`roman_norm`).
urdu_fts   FTS5 index over urdu_norm, rowid = segments.id. Built at INGEST for
           the whole corpus, so every video is searchable immediately, before
           any transliteration. This is what makes "searchable everywhere now"
           work: a Roman query is transliterated to Urdu and matched here.
segments_fts  FTS5 index over roman_norm, rowid = segments.id. Filled as
           segments are transliterated (on demand or in bulk); improves recall
           and ranking for content that already has Roman.
query_cache  roman_norm -> Urdu, so each distinct query is transliterated once.
"""
import os
import sqlite3

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id              INTEGER PRIMARY KEY,
    source_video_id INTEGER UNIQUE,   -- id in the source annotation.db (NULL if transcribed here)
    youtube_url     TEXT,
    title           TEXT,
    source          TEXT DEFAULT 'annotation',  -- 'annotation' | 'soniox' | 'user_upload'
    added_at        TEXT,
    uploaded_at     TEXT,                         -- set for source='user_upload' (drives the badge)
    audio_path      TEXT                          -- local audio file for user uploads (served for karaoke)
);

CREATE TABLE IF NOT EXISTS segments (
    id          INTEGER PRIMARY KEY,
    video_id    INTEGER NOT NULL REFERENCES videos(id),
    start_time  REAL NOT NULL,
    urdu_text   TEXT NOT NULL,        -- source Urdu-script transcript
    urdu_norm   TEXT,                 -- normalised urdu_text, for search
    roman_text  TEXT,                 -- Roman Urdu, NULL until transliterated
    roman_norm  TEXT,                 -- normalised roman_text, for search
    model       TEXT,                 -- which model produced roman_text
    roman_at    TEXT,                 -- when roman_text was produced (for the dashboard)
    created_at  TEXT,
    word_tokens TEXT,                 -- JSON [[start_ms,end_ms],...] per Soniox word, for karaoke (uploads only)
    UNIQUE (video_id, start_time)     -- makes ingest idempotent / resumable
);

-- Background jobs (currently: transcribe a new YouTube video via Soniox). The
-- web app enqueues; a separate worker processes. Powers the dashboard.
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,        -- 'transcribe' | 'romanize' | 'upload'
    youtube_url TEXT,
    title       TEXT,
    audio_path  TEXT,                 -- for 'upload' jobs: the saved audio file to transcribe
    video_id    INTEGER,              -- for 'romanize' jobs
    status      TEXT NOT NULL,        -- queued | running | done | error
    detail      TEXT,                 -- progress note or error message
    progress    REAL DEFAULT 0,       -- 0..100, for the progress bar
    created_at  TEXT,
    updated_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_segments_video ON segments(video_id);
-- Partial index over the not-yet-transliterated backlog, so bulk transliterate
-- finds its next batch without scanning the whole table.
CREATE INDEX IF NOT EXISTS idx_segments_todo ON segments(id) WHERE roman_text IS NULL;

CREATE VIRTUAL TABLE IF NOT EXISTS urdu_fts
    USING fts5(urdu_norm, tokenize = 'unicode61');

CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts
    USING fts5(roman_norm, tokenize = 'unicode61');

-- roman_norm(query) -> transliterated Urdu, so a repeated query costs nothing.
CREATE TABLE IF NOT EXISTS query_cache (
    roman_norm TEXT PRIMARY KEY,
    urdu       TEXT
);

-- Creator's saved items. segment_id NULL = the whole video is saved; a non-NULL
-- segment_id = a bookmark on one line (start_time snapshotted so it can be
-- jumped to and displayed even standalone). Tags group saves into "playlists".
CREATE TABLE IF NOT EXISTS bookmarks (
    id          INTEGER PRIMARY KEY,
    video_id    INTEGER NOT NULL REFERENCES videos(id),
    segment_id  INTEGER,          -- NULL = whole-video save; else a line bookmark
    start_time  REAL,             -- snapshot of the segment's time (for jump/label)
    note        TEXT,
    created_at  TEXT
);
-- At most one whole-video save per video, and one bookmark per (video, segment).
CREATE UNIQUE INDEX IF NOT EXISTS idx_bm_video ON bookmarks(video_id) WHERE segment_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_bm_seg   ON bookmarks(video_id, segment_id) WHERE segment_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS bookmark_tags (
    bookmark_id INTEGER NOT NULL REFERENCES bookmarks(id) ON DELETE CASCADE,
    tag         TEXT NOT NULL,
    PRIMARY KEY (bookmark_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_bmtag_tag ON bookmark_tags(tag);

-- what visitors searched, one row per search (for a usage report)
CREATE TABLE IF NOT EXISTS search_log (
    id      INTEGER PRIMARY KEY,
    q       TEXT NOT NULL,
    results INTEGER,
    at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_log_at ON search_log(at);
"""


def connect(path=None):
    """Open the store read-write, with WAL so the web app can read while a
    transliteration run writes. Foreign keys on so the schema's references hold."""
    conn = sqlite3.connect(path or config.DB_PATH)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def connect_ro(path=None):
    """Open the store read-only — used by the web app, which must never write."""
    p = path or config.DB_PATH
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


# Columns added after the first release; ADDed to stores that predate them so an
# existing roman.db upgrades in place instead of needing a rebuild.
_MIGRATIONS = [
    ("videos", "source", "TEXT DEFAULT 'annotation'"),
    ("videos", "added_at", "TEXT"),
    ("videos", "uploaded_at", "TEXT"),
    ("videos", "audio_path", "TEXT"),
    ("segments", "roman_at", "TEXT"),
    ("segments", "word_tokens", "TEXT"),
    ("jobs", "video_id", "INTEGER"),
    ("jobs", "progress", "REAL DEFAULT 0"),
    ("jobs", "audio_path", "TEXT"),
]


def _migrate(conn):
    for table, column, decl in _MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db(path=None):
    """Create the schema if it does not exist, and migrate an older store in
    place. Safe to call repeatedly."""
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def exists(path=None):
    return os.path.exists(path or config.DB_PATH)
