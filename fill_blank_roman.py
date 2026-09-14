"""One-off: romanize ONLY the lines that display blank on the site
(roman_text IS NULL AND roman_clean empty). Network calls run concurrently;
all DB writes happen on the main thread (SQLite is single-writer).

Safety: MAX_LINES caps how many lines we will bill for this run.
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import db
import transliterate

MAX_LINES = int(sys.argv[1]) if len(sys.argv) > 1 else 12000  # hard cap
BATCH = config.BATCH_SIZE          # 25
WORKERS = 8

conn = db.connect()
ids_urdu = conn.execute(
    "SELECT s.id, s.urdu_text FROM segments s "
    "WHERE s.roman_text IS NULL "
    "  AND (s.roman_clean IS NULL OR TRIM(s.roman_clean) = '') "
    "ORDER BY s.id"
).fetchall()

ids_urdu = ids_urdu[:MAX_LINES]
total = len(ids_urdu)
print(f"[start] {total} blank lines to romanize | model={config.MODEL} "
      f"| batch={BATCH} workers={WORKERS} | cap={MAX_LINES}", flush=True)

batches = [ids_urdu[i:i + BATCH] for i in range(0, total, BATCH)]
# batch items must be (id, urdu, title) 3-tuples for _payload
work_batches = [[(r[0], r[1], None) for r in b] for b in batches]

done = 0
written = 0
t0 = time.time()


def call(b):
    return transliterate._default_translit_batch(b, config.MODEL)


with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs = {ex.submit(call, b): b for b in work_batches}
    for fut in as_completed(futs):
        b = futs[fut]
        done += len(b)
        try:
            romans = fut.result()
        except Exception as e:  # noqa: BLE001
            print(f"[warn] batch failed: {str(e)[:120]}", flush=True)
            continue
        for seg_id, _urdu, _t in b:
            if seg_id in romans and romans[seg_id] is not None:
                transliterate._write_roman(conn, seg_id, romans[seg_id], config.MODEL)
                written += 1
        conn.commit()
        if done % 250 < BATCH:
            rate = done / max(time.time() - t0, 1)
            print(f"[progress] {done}/{total} processed, {written} written "
                  f"| {rate:.0f} lines/s", flush=True)

conn.commit()
print(f"[done] processed={done} written={written} elapsed={time.time()-t0:.0f}s",
      flush=True)
