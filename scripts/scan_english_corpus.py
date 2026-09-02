#!/usr/bin/env python3
"""Full-corpus classify: flag ASR-mangled English lines. READ-ONLY on prod.

Reads roman.db (never writes it). Writes labels to a sidecar english_scan.db
(segment_id, label). Resumable (skips already-labeled) and parallel.

Label = CLEAN | MANGLED. MANGLED = English written phonetically through Urdu
so it reads as garbage ("da prafit vaz", "peesifai", "akarding to islamiak").
"""
import json, re, sqlite3, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROMAN = "/root/video_tool/roman.db"
SCAN = "/root/video_tool/english_scan.db"
KEY = re.search(r'OPENROUTER_API_KEY=(.*)',
                open("/root/video_tool/video_tool.env").read()).group(1).strip().strip('"')
MODEL = "google/gemini-2.5-flash-lite"
WORKERS = 8
BATCH = 25

SYS = """For each numbered lecture-transcript line output "<n>:<label>".
Labels: CLEAN (fine Roman-Urdu, or fine English, or normal Arabic/Islamic terms
like da'wa masjid), MANGLED (English written phonetically through Urdu so it
reads as garbage, e.g. "da prafit vaz", "peesifai", "riflixats",
"akarding to islamiak tichings", "yu kin andrstnd dis").
Output only the labels, one per line, nothing else."""


def call(msg):
    body = json.dumps({"model": MODEL,
                       "messages": [{"role": "system", "content": SYS},
                                    {"role": "user", "content": msg}],
                       "temperature": 0}).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
                                 body, {"Authorization": "Bearer " + KEY,
                                        "Content-Type": "application/json"})
    for attempt in range(4):
        try:
            return json.load(urllib.request.urlopen(req, timeout=120))["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))


def classify_batch(rows):
    """rows = [(id, text), ...] -> [(id, label), ...]"""
    msg = "\n".join(f"{j+1}. {t}" for j, (_, t) in enumerate(rows))
    resp = call(msg)
    out = []
    for ln in resp.splitlines():
        if ":" in ln:
            a, b = ln.split(":", 1); a = a.strip().rstrip(".")
            if a.isdigit():
                idx = int(a) - 1
                if 0 <= idx < len(rows):
                    lab = "MANGLED" if "MANGLED" in b.upper() else "CLEAN"
                    out.append((rows[idx][0], lab))
    return out


def main():
    scan = sqlite3.connect(SCAN)
    scan.execute("CREATE TABLE IF NOT EXISTS labels (segment_id INTEGER PRIMARY KEY, label TEXT)")
    scan.commit()
    done = {r[0] for r in scan.execute("SELECT segment_id FROM labels")}

    r = sqlite3.connect(ROMAN)
    rows = r.execute(
        "SELECT id, COALESCE(NULLIF(TRIM(roman_text),''), urdu_text) t FROM segments "
        "WHERE length(COALESCE(roman_text,urdu_text))>30").fetchall()
    r.close()
    todo = [(i, t) for i, t in rows if i not in done]
    print(f"{len(rows)} total, {len(done)} already labeled, {len(todo)} to do", flush=True)

    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    t0 = time.time(); n_done = len(done); n_mangled = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(classify_batch, b): b for b in batches}
        for k, fut in enumerate(as_completed(futs)):
            try:
                res = fut.result()
            except Exception as e:
                print("batch ERR", e, flush=True); continue
            scan.executemany("INSERT OR REPLACE INTO labels VALUES (?,?)", res)
            scan.commit()
            n_done += len(res); n_mangled += sum(1 for _, l in res if l == "MANGLED")
            if k % 40 == 0:
                rate = (n_done - len(done)) / max(time.time() - t0, 1)
                eta = (len(todo) - (n_done - len(done))) / max(rate, 1) / 60
                print(f"[{n_done}/{len(rows)}] mangled_so_far={n_mangled} "
                      f"{rate:.0f} lines/s ETA {eta:.0f}min", flush=True)
    scan.close()
    print(f"DONE. labeled={n_done} mangled={n_mangled} "
          f"({100*n_mangled/max(n_done,1):.1f}%)", flush=True)


if __name__ == "__main__":
    main()
