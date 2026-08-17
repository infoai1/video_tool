"""Transcribe a new YouTube video with Soniox and add it to the store.

Used when someone pastes a link to a video that isn't in the library yet. The
flow is: yt-dlp downloads the audio, Soniox returns an Urdu transcript with word
timestamps, we split it into timestamped segments and insert them as a new video
(source='soniox') — after which it is searchable and browsable like any other,
with Roman Urdu produced on demand.

This runs in the worker process (see worker.py), never in a web request.

YouTube note: datacenter IPs are bot-blocked, so yt-dlp needs a browser
cookies.txt (config.YT_COOKIES). Without it, download fails with an actionable
message that surfaces on the dashboard.
"""
import datetime
import json
import os
import subprocess
import tempfile

import config
import db
import normalize


class TranscribeError(Exception):
    """A transcription failed in a way worth showing the user."""


def _now():
    return datetime.datetime.utcnow().isoformat() + "Z"


def _yt_cmd(extra):
    cmd = ["yt-dlp"]
    if config.YT_COOKIES and os.path.exists(config.YT_COOKIES):
        cmd += ["--cookies", config.YT_COOKIES]
    if config.YT_PROXY:
        cmd += ["--proxy", config.YT_PROXY]
    if config.YT_POT_BASE_URL:
        cmd += ["--extractor-args",
                f"youtubepot-bgutilhttp:base_url={config.YT_POT_BASE_URL}"]
    return cmd + extra


def _yt_error(stderr):
    s = (stderr or "").strip()
    if "Sign in to confirm" in s or "cookies" in s.lower():
        return (
            "YouTube requires authentication from this server. Export a browser "
            "cookies.txt and set VIDEO_TOOL_YT_COOKIES to its path (see README)."
        )
    last = s.splitlines()[-1] if s else "yt-dlp failed"
    return "yt-dlp: " + last[:200]


def list_channel_videos(url, limit=None):
    """List a channel's or playlist's videos WITHOUT downloading — newest first,
    capped at `limit` — as [{"id":..., "title":...}].

    Uses yt-dlp's flat playlist listing, so it's fast (metadata only) even for a
    large channel. Accepts a channel URL (…/@handle, …/channel/UC…, optionally
    with /videos) or a playlist URL. Raises TranscribeError on failure.
    """
    limit = limit or config.CHANNEL_SCAN_LIMIT
    proc = subprocess.run(
        _yt_cmd(["--flat-playlist", "--playlist-end", str(limit),
                 "--print", "%(id)s\t%(title)s", url]),
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise TranscribeError(_yt_error(proc.stderr))
    out, seen = [], set()
    for line in proc.stdout.splitlines():
        if "\t" not in line:
            continue
        vid, title = line.split("\t", 1)
        vid = vid.strip()
        if vid and vid not in seen:
            seen.add(vid)
            out.append({"id": vid, "title": title.strip() or vid})
    return out


def youtube_metadata(url):
    proc = subprocess.run(
        _yt_cmd(["-j", "--skip-download", url]),
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise TranscribeError(_yt_error(proc.stderr))
    d = json.loads(proc.stdout)
    return {"id": d.get("id"), "title": d.get("title") or d.get("id"), "duration": d.get("duration")}


def download_audio(url, dest_dir):
    out = os.path.join(dest_dir, "audio.%(ext)s")
    proc = subprocess.run(
        _yt_cmd(["-f", "bestaudio/best", "-x", "--audio-format", "mp3", "-o", out, url]),
        capture_output=True, text=True, timeout=1800,
    )
    if proc.returncode != 0:
        raise TranscribeError(_yt_error(proc.stderr))
    for f in sorted(os.listdir(dest_dir)):
        if f.startswith("audio."):
            return os.path.join(dest_dir, f)
    raise TranscribeError("audio download produced no file")


def soniox_transcribe(audio_path, on_status=None):
    """Upload audio to Soniox, wait for the async job, return the transcript dict."""
    if not config.SONIOX_API_KEY:
        raise TranscribeError("SONIOX_API_KEY is not set.")
    import time

    import requests  # only the worker needs this

    h = {"Authorization": f"Bearer {config.SONIOX_API_KEY}"}
    with open(audio_path, "rb") as f:
        r = requests.post(f"{config.SONIOX_URL}/files", headers=h,
                          files={"file": ("audio.mp3", f)}, timeout=1800)
    if r.status_code not in (200, 201):
        raise TranscribeError(f"soniox upload {r.status_code}: {r.text[:200]}")
    file_id = r.json()["id"]

    r = requests.post(
        f"{config.SONIOX_URL}/transcriptions",
        headers={**h, "Content-Type": "application/json"},
        json={"file_id": file_id, "model": "stt-async-v4",
              "enable_language_identification": True, "language_hints": ["ur", "hi"]},
        timeout=60,
    )
    if r.status_code not in (200, 201):
        raise TranscribeError(f"soniox job {r.status_code}: {r.text[:200]}")
    tid = r.json()["id"]

    for i in range(360):  # up to ~30 min
        time.sleep(5)
        st = requests.get(f"{config.SONIOX_URL}/transcriptions/{tid}", headers=h, timeout=30).json()
        status = st.get("status", "?")
        if on_status and i % 6 == 0:
            on_status(f"transcribing with Soniox… ({status})")
        if status == "completed":
            break
        if status in ("error", "failed"):
            raise TranscribeError(f"soniox failed: {str(st)[:200]}")
    else:
        raise TranscribeError("soniox transcription timed out")

    return requests.get(f"{config.SONIOX_URL}/transcriptions/{tid}/transcript", headers=h, timeout=120).json()


def segment_transcript(transcript, seconds=None):
    """Turn a Soniox transcript into [(start_seconds, text)] segments of roughly
    `seconds` each, using word timestamps when present."""
    seconds = seconds or config.SEGMENT_SECONDS
    tokens = transcript.get("tokens")
    if not tokens:
        text = (transcript.get("text") or "").strip()
        return [(0.0, text)] if text else []

    segs, cur, start = [], [], None
    for t in tokens:
        s = t.get("start_ms")
        e = t.get("end_ms", s)
        cur.append(t.get("text", ""))
        if s is None:
            continue
        if start is None:
            start = s
        if e is not None and (e - start) / 1000.0 >= seconds:
            segs.append((start / 1000.0, "".join(cur).strip()))
            cur, start = [], None
    if cur and start is not None:
        segs.append((start / 1000.0, "".join(cur).strip()))
    return [(st, tx) for st, tx in segs if tx]


def existing_video(conn, youtube_id):
    row = conn.execute(
        "SELECT id FROM videos WHERE youtube_url LIKE ? LIMIT 1", (f"%{youtube_id}%",)
    ).fetchone()
    return row[0] if row else None


def transcribe_and_ingest(conn, url, on_status=None):
    """Full pipeline. Returns (video_id, segment_count). Raises TranscribeError."""
    db.init_db()
    meta = youtube_metadata(url)
    yt_id = meta["id"]
    if not yt_id:
        raise TranscribeError("could not read the YouTube video id from that link")

    found = existing_video(conn, yt_id)
    if found:
        return found, 0  # already in the library

    if on_status:
        on_status("downloading audio…")
    with tempfile.TemporaryDirectory() as d:
        audio = download_audio(url, d)
        transcript = soniox_transcribe(audio, on_status)

    segs = segment_transcript(transcript)
    if not segs:
        raise TranscribeError("Soniox returned no transcript for this video")

    if on_status:
        on_status(f"saving {len(segs)} segments…")
    now = _now()
    canonical = f"https://www.youtube.com/watch?v={yt_id}"
    conn.execute(
        "INSERT INTO videos (youtube_url, title, source, added_at) VALUES (?, ?, 'soniox', ?)",
        (canonical, meta["title"], now),
    )
    video_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for start, text in segs:
        norm = normalize.normalize_urdu(text)
        conn.execute(
            "INSERT OR IGNORE INTO segments "
            "(video_id, start_time, urdu_text, urdu_norm, created_at) VALUES (?, ?, ?, ?, ?)",
            (video_id, start, text, norm, now),
        )
        seg_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO urdu_fts (rowid, urdu_norm) VALUES (?, ?)", (seg_id, norm))
    conn.commit()
    return video_id, len(segs)
