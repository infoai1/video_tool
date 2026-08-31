"""User-uploaded audio → karaoke transcript ingest (network-free).

Monkeypatches soniox_transcribe so no paid API is called; verifies the upload is
inserted as source='user_upload' with per-word timings stored for karaoke.
"""
import json

import db
import transcribe


# A tiny fake Soniox transcript: word tokens with ms timings, ~18s+ so it splits.
_FAKE = {
    "tokens": [
        {"text": "اللہ ", "start_ms": 0, "end_ms": 500},
        {"text": "کا ", "start_ms": 500, "end_ms": 900},
        {"text": "شکر ", "start_ms": 900, "end_ms": 1500},
        {"text": "نماز ", "start_ms": 19000, "end_ms": 19600},
        {"text": "اور ", "start_ms": 19600, "end_ms": 20000},
        {"text": "صبر ", "start_ms": 20000, "end_ms": 20800},
    ]
}


def test_ingest_upload_stores_source_and_word_tokens(tmp_path, monkeypatch):
    path = str(tmp_path / "roman.db")
    db.init_db(path)
    conn = db.connect(path)

    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"not really audio")  # ingest only needs the path to exist

    monkeypatch.setattr(transcribe, "soniox_transcribe", lambda p, on_status=None: _FAKE)

    video_id, n = transcribe.ingest_upload(conn, str(audio), "My Clip")
    assert n >= 1

    src, uploaded_at, apath = conn.execute(
        "SELECT source, uploaded_at, audio_path FROM videos WHERE id=?", (video_id,)
    ).fetchone()
    assert src == "user_upload"
    assert uploaded_at  # timestamp set → drives the badge
    assert apath == str(audio)

    rows = conn.execute(
        "SELECT urdu_text, word_tokens FROM segments WHERE video_id=? ORDER BY start_time",
        (video_id,),
    ).fetchall()
    assert rows, "segments were inserted"
    # First segment carries real per-word [start_ms, end_ms] pairs for karaoke.
    toks = json.loads(rows[0][1])
    assert toks and all(len(pair) == 2 and pair[1] >= pair[0] for pair in toks)
    # The transcript is searchable via urdu_fts like any other video.
    (fts_count,) = conn.execute("SELECT COUNT(*) FROM urdu_fts").fetchone()
    assert fts_count == len(rows)


def test_normalize_converts_any_format_to_mp3(tmp_path):
    """A real (tiny) wav is converted to mp3 by ffmpeg; garbage is rejected."""
    import subprocess

    import pytest

    wav = tmp_path / "note.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3", str(wav)],
        capture_output=True, check=True,
    )
    out = transcribe.normalize_upload_audio(str(wav))
    assert out.endswith(".mp3") and not wav.exists()  # converted, original removed

    junk = tmp_path / "junk.xyz"
    junk.write_bytes(b"this is not audio at all")
    with pytest.raises(transcribe.TranscribeError):
        transcribe.normalize_upload_audio(str(junk))


def test_transcript_csv_has_timestamp_urdu_roman():
    import export

    video = {"title": "T", "segments": [
        {"start_time": 0.0, "urdu_text": "اللہ کا شکر", "roman_text": "Allah ka shukr"},
        {"start_time": 65.0, "urdu_text": "نماز", "roman_text": None},
    ]}
    blob = export.transcript_csv(video)
    text = blob.decode("utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0] == "timestamp,urdu,roman"
    assert lines[1].startswith("00:00,") and "Allah ka shukr" in lines[1]
    assert lines[2].startswith("01:05,") and "نماز" in lines[2]
