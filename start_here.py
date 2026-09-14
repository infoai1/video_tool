"""The landing page's "Start here" answers and section counts.

Start here = three Q&A units rotated weekly from the most-played. Play data is
the Umami `play` event (kind=qa, title) the shared shell logs on every play
button (shukr-app/shell.py _EVENTS_JS); it lives in the self-hosted umami
postgres container. Fallbacks, in order: most-searched queries in roman.db
search_log matched to question titles; then /asked/pool.json order; then a
fixed id list. The pick is cached in data/start_here.json with an ISO-week
stamp and recomputed when the week changes.
"""
import datetime
import json
import os
import re
import sqlite3
import subprocess
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "start_here.json")
QA_DB = "/root/clip_study/qa.db"
CLIPS_DB = "/root/clip_study/clips.db"
ROMAN_DB = os.path.join(HERE, "roman.db")
FRAMES = "/root/shukr-app/static/frames"
SERVABLE = "has_answer=1 AND refined=1 AND question_title!='' AND youtube_id!=''"
FALLBACK_IDS = [153, 1890, 3230, 199, 1116, 1476]   # the hero's own People-asked list
POOL = 9   # rotate three at a time through the top nine


def _omitted_yids():
    yids = set()
    try:
        db = sqlite3.connect(f"file:{ROMAN_DB}?mode=ro", uri=True)
        for (url,) in db.execute("SELECT youtube_url FROM videos WHERE content_type='omit'"):
            m = re.search(r"(?:v=|youtu\.be/|/)([A-Za-z0-9_-]{11})(?:[?&#]|$)", url or "")
            if m:
                yids.add(m.group(1))
        db.close()
    except Exception:
        pass
    return yids


def _played_titles():
    """[(title, plays)] from umami, 90 days, kind=qa. Empty on any failure."""
    sql = ("select t.string_value, count(*) c from event_data k "
           "join event_data t on t.website_event_id=k.website_event_id and t.data_key='title' "
           "where k.data_key='kind' and k.string_value='qa' and k.created_at>now()-interval '90 days' "
           "group by 1 order by 2 desc limit 40")
    try:
        out = subprocess.run(["docker", "exec", "umami-db-1", "psql", "-U", "umami", "-d", "umami",
                              "-At", "-F", "\t", "-c", sql], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        if "\t" in line:
            t, c = line.rsplit("\t", 1)
            rows.append((t.strip(), int(c)))
    return rows


def _searched_titles():
    try:
        db = sqlite3.connect(f"file:{ROMAN_DB}?mode=ro", uri=True)
        rows = db.execute("SELECT q, count(*) c FROM search_log WHERE length(q)>3 "
                          "GROUP BY q ORDER BY c DESC LIMIT 40").fetchall()
        db.close()
        return rows
    except Exception:
        return []


def _pool_ids():
    try:
        with urllib.request.urlopen("http://127.0.0.1:5065/asked/pool.json", timeout=3) as r:
            return [i["id"] for i in json.load(r).get("items", []) if i.get("id")]
    except Exception:
        return []


def _units(db, where, args, omit, limit):
    sql = f"SELECT id, question_title, youtube_id, q_start, a_end, seconds, asker, city FROM qa_units WHERE {SERVABLE} AND {where}"
    out = []
    for row in db.execute(sql, args):
        if row[2] in omit:
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _row_dict(r):
    i, title, yid, qs, ae, secs, asker, city = r
    frame = f"{FRAMES}/qa_{i}.jpg"
    return {"id": i, "title": title, "yid": yid, "start": int(qs or 0), "end": int(ae or 0) + 1,
            "dur": "%d:%02d" % divmod(int(secs or 0), 60), "asker": asker or "", "city": city or "",
            "thumb": f"/clips/frames/qa_{i}.jpg" if os.path.exists(frame) else f"https://i.ytimg.com/vi/{yid}/hqdefault.jpg"}


def compute():
    """Top-POOL servable units by plays, padded from fallbacks. Returns (source, rows)."""
    omit = _omitted_yids()
    db = sqlite3.connect(f"file:{QA_DB}?mode=ro", uri=True)
    picked, seen, source = [], set(), "umami"
    try:
        for title, _n in _played_titles():
            for r in _units(db, "question_title=?", (title,), omit, 1):
                if r[0] not in seen:
                    seen.add(r[0]); picked.append(r)
            if len(picked) >= POOL:
                break
        if len(picked) < 3:
            source = "search_log"
            for q, _n in _searched_titles():
                for r in _units(db, "question_title LIKE ?", (f"%{q}%",), omit, 1):
                    if r[0] not in seen:
                        seen.add(r[0]); picked.append(r)
                if len(picked) >= POOL:
                    break
        if len(picked) < 3:
            source = "pool"
            for i in _pool_ids() + FALLBACK_IDS:
                if i in seen:
                    continue
                for r in _units(db, "id=?", (i,), omit, 1):
                    seen.add(r[0]); picked.append(r)
                if len(picked) >= POOL:
                    break
    finally:
        db.close()
    return source, [_row_dict(r) for r in picked]


def week():
    y, w, _ = datetime.date.today().isocalendar()
    return f"{y}-W{w:02d}"


def pick():
    """Three units for this ISO week, from the cached pool; recompute when the week changes."""
    data = None
    try:
        with open(CACHE) as f:
            data = json.load(f)
    except Exception:
        pass
    if not data or data.get("week") != week() or len(data.get("rows") or []) < 3:
        source, rows = compute()
        if len(rows) < 3:   # never blank the page; keep last week's if we have it
            if data and len(data.get("rows") or []) >= 3:
                return data["pick"]
            return rows
        y, w, _ = datetime.date.today().isocalendar()
        off = (w * 3) % len(rows)
        three = (rows + rows)[off:off + 3]
        data = {"week": week(), "source": source, "rows": rows, "pick": three}
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE + ".tmp", "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(CACHE + ".tmp", CACHE)
    return data["pick"]


def counts():
    """Section tile numbers, from the DBs. Caller caches for a day."""
    omit = _omitted_yids()
    n = {"qa": 0, "clips": 0}
    try:
        db = sqlite3.connect(f"file:{QA_DB}?mode=ro", uri=True)
        ins = ",".join("'%s'" % y for y in sorted(omit))
        n["qa"] = db.execute(f"SELECT count(*) FROM qa_units WHERE {SERVABLE}" +
                             (f" AND youtube_id NOT IN ({ins})" if ins else "")).fetchone()[0]
        db.close()
    except Exception:
        pass
    try:
        rdb = sqlite3.connect(f"file:{ROMAN_DB}?mode=ro", uri=True)
        vids = [str(v) for (v,) in rdb.execute(
            "SELECT source_video_id FROM videos WHERE content_type='omit' AND source_video_id IS NOT NULL")]
        rdb.close()
        db = sqlite3.connect(f"file:{CLIPS_DB}?mode=ro", uri=True)
        n["clips"] = db.execute("SELECT count(*) FROM clips" +
                                (f" WHERE video_id NOT IN ({','.join(vids)})" if vids else "")).fetchone()[0]
        db.close()
    except Exception:
        pass
    return n


if __name__ == "__main__":
    src, rows = compute()
    assert len(rows) >= 3 and all(r["yid"] and r["title"] and r["end"] > r["start"] for r in rows), rows
    p = pick()
    assert len(p) == 3 and len({r["id"] for r in p}) == 3
    c = counts()
    assert c["qa"] > 2000 and c["clips"] > 20000, c
    print(src, [r["title"] for r in p], c)
