"""Build a .docx transcript for a video.

Options: which script(s) to include (roman / urdu / both) and whether to prefix
each line with its timestamp. Returns the .docx as bytes.

Roman lines that haven't been transliterated yet come through as "—"; export
Roman only after romanizing the video (the page offers that).
"""
import io


def _hhmmss(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def transcript_docx(video, script="both", timestamps=True):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    want_roman = script in ("roman", "both")
    want_urdu = script in ("urdu", "both")

    doc = Document()
    doc.add_heading(video["title"] or "Transcript", level=1)
    if video.get("youtube_url"):
        doc.add_paragraph(video["youtube_url"])

    for seg in video["segments"]:
        ts = _hhmmss(seg["start_time"])
        if want_roman:
            roman = seg.get("roman_text") or "—"
            p = doc.add_paragraph()
            if timestamps:
                run = p.add_run(f"[{ts}] ")
                run.bold = True
            p.add_run(roman)
        if want_urdu:
            urdu = seg.get("urdu_text") or ""
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT  # Urdu reads right-to-left
            if timestamps and not want_roman:
                run = p.add_run(f"[{ts}] ")
                run.bold = True
            p.add_run(urdu)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
