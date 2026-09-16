"""/crawled -- which transcripts the search engines and AI crawlers have read.

The transcripts are machine-made, so some lines carry the wrong word: khasho
for khusho, wah for woh. Every line is his, but not every line is exactly what
he said.

Which of the 2,242 lectures to correct first was, until now, a guess. It need
not be: a page a crawler has already read is a page whose errors are being
quoted back at people, and a page nobody has fetched can wait. This reads the
web server's own log and lists the transcripts the crawlers went to, most-read
first, each one a click from the text and the second it starts.

Read-only, and never fatal: a missing or unreadable log costs the page its
numbers, not the site.
"""
import os
import re
import sqlite3
from collections import OrderedDict

LOGS = ("/var/log/nginx/access.log", "/var/log/nginx/access.log.1")
MAX_BYTES = 40_000_000       # ~2-3 days of this server's traffic

# The crawlers worth naming. A user-agent is a claim, not a proof -- anyone can
# send one -- so where a crawler publishes its addresses we check them, and
# where it does not we say the visit is unverified rather than pretend.
BOTS = (
    ("Google", r"Googlebot|Google-Extended|GoogleOther", (r"^66\.249\.",)),
    ("Bing", r"bingbot|BingPreview", (r"^40\.77\.", r"^157\.55\.", r"^207\.46\.", r"^13\.66\.")),
    ("ChatGPT", r"GPTBot|OAI-SearchBot|ChatGPT-User", ()),
    ("Claude", r"ClaudeBot|Claude-User|anthropic-ai", ()),
    ("Perplexity", r"PerplexityBot|Perplexity-User", ()),
    ("Meta", r"meta-externalagent|FacebookBot", ()),
    ("Apple", r"Applebot", ()),
    ("Yandex", r"YandexBot", ()),
    ("Amazon", r"Amazonbot", ()),
    ("ByteDance", r"Bytespider", ()),
)
_BOTS = [(name, re.compile(pat, re.I), tuple(re.compile(p) for p in ips)) for name, pat, ips in BOTS]

# Only the pages that carry his words: a lecture transcript, or one answer.
_LINE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<when>[^\]]+)\] "(?:GET|HEAD) '
    r'(?P<path>/(?:video/\d+|clips/qa/\d+))(?:[?#]\S*)? [^"]*" (?P<status>\d{3}) '
    r'\S+ "[^"]*" "(?P<ua>[^"]*)"')


def _tail(path, max_bytes=MAX_BYTES):
    """The end of a log file, from the start of a whole line."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # drop the half line we landed in
            return f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []


def _bot(ua, ip):
    """(name, verified) for a crawler we know, else None."""
    for name, pat, ip_pats in _BOTS:
        if pat.search(ua):
            if not ip_pats:
                return name, None          # nothing published to check against
            return name, any(p.match(ip) for p in ip_pats)
    return None


def _titles(paths, roman_db, qa_db):
    """{path: (what it is, its title, the second it starts, where to correct it)}.

    An answer is a moment inside a lecture, so correcting it means opening that
    lecture's transcript at that second -- not the answer's own address.
    """
    out = {}
    lec = sorted({int(p.split("/")[-1]) for p in paths if p.startswith("/video/")})
    qa = sorted({int(p.split("/")[-1]) for p in paths if p.startswith("/clips/qa/")})
    if lec and os.path.exists(roman_db):
        try:
            db = sqlite3.connect(f"file:{roman_db}?mode=ro", uri=True)
            qm = ",".join("?" * len(lec))
            for vid, title in db.execute(
                    f"SELECT id, title FROM videos WHERE id IN ({qm})", lec):
                out[f"/video/{vid}"] = ("Lecture", title or "Untitled lecture", 0)
            db.close()
        except sqlite3.Error:
            pass
    if qa and os.path.exists(qa_db):
        try:
            db = sqlite3.connect(f"file:{qa_db}?mode=ro", uri=True)
            qm = ",".join("?" * len(qa))
            for uid, qt, ttl, start, vid in db.execute(
                    f"SELECT id, question_title, title, q_start, video_id FROM qa_units "
                    f"WHERE id IN ({qm})", qa):
                out[f"/clips/qa/{uid}"] = ("Answer",
                                           f"{qt or 'A question'} — {ttl or ''}".strip(" —"),
                                           int(start or 0))
            db.close()
        except sqlite3.Error:
            pass
    return out


def rows(roman_db, qa_db):
    """One row per page a crawler read, most-read first."""
    hits = {}
    totals = OrderedDict((name, 0) for name, _, _ in BOTS)
    first_seen = last_seen = ""
    for path in LOGS:
        for line in _tail(path):
            m = _LINE.match(line)
            if not m:
                continue
            got = _bot(m.group("ua"), m.group("ip"))
            if not got:
                continue
            name, verified = got
            if verified is False:      # says Google, is not Google
                continue
            p, when = m.group("path"), m.group("when")
            h = hits.setdefault(p, {"path": p, "n": 0, "bots": {}, "last": ""})
            h["n"] += 1
            h["bots"][name] = h["bots"].get(name, 0) + 1
            if when > h["last"]:
                h["last"] = when
            totals[name] = totals.get(name, 0) + 1
            if not first_seen or when < first_seen:
                first_seen = when
            if when > last_seen:
                last_seen = when

    meta = _titles(hits, roman_db, qa_db)
    out = []
    for h in hits.values():
        kind, title, start = meta.get(h["path"], ("Page", h["path"], 0))
        out.append({**h, "kind": kind, "title": title, "start": start,
                    "who": ", ".join(f"{b} ×{n}" for b, n in
                                     sorted(h["bots"].items(), key=lambda kv: -kv[1]))})
    out.sort(key=lambda r: (-r["n"], r["title"]))
    result = {"rows": out, "totals": {k: v for k, v in totals.items() if v},
              "first": first_seen, "last": last_seen,
              "lectures": sum(1 for r in out if r["kind"] == "Lecture"),
              "answers": sum(1 for r in out if r["kind"] == "Answer"),
              "visits": sum(r["n"] for r in out)}
    return result
