"""What a crawler needs in order to read a lecture page.

The transcripts were always in the HTML of /video/<id>. What was missing was
anything telling a crawler what the page *is*: no description, no canonical
URL, no Open Graph, and no schema.org VideoObject tying the text to the
lecture it transcribes. Google's video indexing and the AI answer engines key
off exactly that markup, so the pages were readable and still invisible.

Everything here is derived from what the page already shows -- the title, the
transcript, and the clips cut from the lecture. No crawler is served a word a
visitor does not get.
"""
import json
import math
import re

SPEAKER = "Maulana Wahiduddin Khan"

# Said in the markup and shown on the page, in the same words. The transcripts
# are machine-made from the recordings; a line that reads oddly is a machine's
# mistake, never a change to what he said, and the recording is one tap away at
# the second it was spoken. An engine quoting us should carry that with it.
PROVENANCE = ("Machine transcription of the speaker's recorded words, aligned to the "
              "recording second by second. Every line links to that second in the video, "
              "so any quotation can be checked against him saying it.")

# The JSON-LD copy of the transcript goes to every visitor, not just crawlers,
# so it is capped. The longest lectures run past 150k characters; past this the
# extra text buys nothing -- the whole transcript is still in the HTML below --
# and costs every reader the download.
MAX_TRANSCRIPT = 120_000

# Key moments: enough to describe the lecture, not so many that the block
# dwarfs the page.
MAX_CLIPS = 40

# "Maulana Wahiduddin Khan", "| CPS" and friends are already the site's own
# heading; repeating them inside every title reads as spam to a crawler.
_RE_SPEAKER = re.compile(r"\b(maulana\s+)?wahiduddin\s+khan\b", re.I)


def clean_title(title):
    """The lecture title as a human would say it, without the boilerplate."""
    t = (title or "").strip()
    t = _RE_SPEAKER.sub(" ", t)
    t = re.sub(r"\s*[|\-–—]\s*[|\-–—]\s*", " · ", t)
    t = re.sub(r"\s*\|\s*", " · ", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ·-–—,|")
    return t or "Untitled lecture"


def transcript_text(segments, limit=MAX_TRANSCRIPT):
    """The spoken words as one block: the transliteration, else the Urdu."""
    out, n = [], 0
    for s in segments or ():
        line = (s.get("roman_text") or s.get("urdu_text") or "").strip()
        if not line:
            continue
        out.append(line)
        n += len(line) + 1
        if n >= limit:
            break
    return " ".join(out)


def duration_iso(segments):
    """ISO-8601 runtime, read off the last segment that has a timestamp.

    The source database has no reliable duration column, and the last segment
    starts a few seconds before the lecture ends -- so this is a floor, never
    an overstatement.
    """
    last = 0.0
    for s in segments or ():
        t = s.get("start_time")
        if t:
            last = max(last, float(t))
    if last < 1:
        return ""
    secs = int(math.ceil(last))
    return f"PT{secs // 3600}H{(secs % 3600) // 60}M{secs % 60}S"


def upload_date(video, published_at=None):
    """The date to publish as uploadDate, or "" when we do not know one.

    `published_at` (YouTube's own date, when the source database has it) wins.
    Otherwise a lecture only carries the year it was delivered, and January 1st
    is the conventional stand-in for a year-only date.
    """
    for raw in (published_at, video.get("uploaded_at")):
        raw = (raw or "").strip()
        m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
        if m:
            return m.group(1)
    year = video.get("year")
    if year and str(year).isdigit() and 1900 < int(year) < 2100:
        return f"{int(year)}-01-01"
    return ""


def key_moments(clips, qa):
    """Clips and answered questions from this lecture, in timeline order.

    These are the "key moments" a search engine can offer straight into the
    middle of a lecture, so a seeker lands on the minute that answers them.
    """
    moments = []
    for c in clips or ():
        name = (c.get("topic") or "").strip()
        if name:
            moments.append({"start": int(c.get("start") or 0), "name": name,
                            "seconds": int(c.get("seconds") or 0)})
    for a in qa or ():
        name = (a.get("q") or "").strip()
        if name:
            moments.append({"start": int(a.get("start") or 0), "name": name, "seconds": 0})
    moments.sort(key=lambda m: m["start"])
    seen, out = set(), []
    for m in moments:
        key = m["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out[:MAX_CLIPS]


def description(title, moments, transcript):
    """One sentence for the search result, ~155 characters.

    Google shows roughly that much; what matters is that it says whose words
    these are and that the page carries the whole transcript.
    """
    lead = f"Read the full transcript of “{title}”, a lecture by {SPEAKER}, with timestamps."
    room = 165 - len(lead)
    tail = ""
    for m in moments:
        name = m["name"]
        if len(name) > 60:
            continue
        candidate = (tail + "; " + name) if tail else (" Covers: " + name)
        if len(candidate) + 1 > room:
            continue  # a long topic must not cost the shorter ones their place
        tail = candidate
    if tail:
        lead += tail + "."
    elif transcript and room > 40:
        lead += " " + transcript[:room - 2].rsplit(" ", 1)[0] + "…"
    return lead


def _clip_nodes(moments, canonical, total_secs):
    nodes = []
    for i, m in enumerate(moments):
        start = m["start"]
        if m["seconds"]:
            end = start + m["seconds"]
        elif i + 1 < len(moments):
            end = moments[i + 1]["start"]
        else:
            end = total_secs or (start + 120)
        if end <= start:
            end = start + 60
        nodes.append({
            "@type": "Clip",
            "name": m["name"],
            "startOffset": start,
            "endOffset": end,
            "url": f"{canonical}#t={start}",
        })
    return nodes


def video_meta(video, youtube_id, clips, qa, base_url, published_at=None):
    """Everything the <head> of a lecture page needs, ready to render.

    Returns a dict: title, description, canonical, image, jsonld. `jsonld` is
    already safe to drop inside a <script> tag.
    """
    base = (base_url or "").rstrip("/")
    canonical = f"{base}/video/{video.get('id')}"
    title = clean_title(video.get("title"))
    segments = video.get("segments") or []
    transcript = transcript_text(segments)
    moments = key_moments(clips, qa)
    desc = description(title, moments, transcript)
    image = f"https://i.ytimg.com/vi/{youtube_id}/maxresdefault.jpg" if youtube_id else ""

    node = {
        "@context": "https://schema.org",
        "@type": "VideoObject",
        "name": f"{title} — {SPEAKER}",
        "description": desc,
        "url": canonical,
        "inLanguage": ["ur", "en"],
        "isFamilyFriendly": True,
        "creator": {"@type": "Person", "name": SPEAKER},
        "publisher": {"@type": "Organization", "name": "Centre for Peace and Spirituality"},
    }
    upload = upload_date(video, published_at)
    if upload:
        node["uploadDate"] = upload
    if image:
        node["thumbnailUrl"] = [image, f"https://i.ytimg.com/vi/{youtube_id}/hqdefault.jpg"]
    if youtube_id:
        node["embedUrl"] = f"https://www.youtube.com/embed/{youtube_id}"
        node["contentUrl"] = f"https://www.youtube.com/watch?v={youtube_id}"
    dur = duration_iso(segments)
    if dur:
        node["duration"] = dur
    if transcript:
        # The property that makes the spoken words searchable as the video's own
        # words rather than as loose text that happens to sit near a player.
        node["transcript"] = transcript
        # ... and the caveat that travels with it wherever it is quoted.
        node["creditText"] = PROVENANCE
        if youtube_id:
            node["isBasedOn"] = {"@type": "VideoObject",
                                 "url": f"https://www.youtube.com/watch?v={youtube_id}"}
    total_secs = 0
    for s in segments:
        if s.get("start_time"):
            total_secs = max(total_secs, int(float(s["start_time"])))
    clip_nodes = _clip_nodes(moments, canonical, total_secs)
    if clip_nodes:
        node["hasPart"] = clip_nodes

    crumbs = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": base + "/"},
            {"@type": "ListItem", "position": 2, "name": "Videos", "item": base + "/videos"},
            {"@type": "ListItem", "position": 3, "name": title, "item": canonical},
        ],
    }
    blob = json.dumps([node, crumbs], ensure_ascii=False)
    # "</script>" inside a JSON string would end the block early.
    blob = blob.replace("</", "<\\/")
    return {"title": f"{title} — {SPEAKER}", "description": desc,
            "canonical": canonical, "image": image, "jsonld": blob}
