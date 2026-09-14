"""Retry the stubborn blank lines ONE per request (batch size 1) so an embedded
quote in one line can't drop its neighbours from a shared JSON reply."""
import config
import db
import transliterate

conn = db.connect()
rows = conn.execute(
    "SELECT s.id, s.urdu_text FROM segments s "
    "WHERE s.roman_text IS NULL "
    "  AND (s.roman_clean IS NULL OR TRIM(s.roman_clean) = '')"
).fetchall()
print(f"{len(rows)} lines to retry singly", flush=True)
ok = 0
for sid, urdu in rows:
    try:
        res = transliterate._default_translit_batch([(sid, urdu, None)], config.MODEL)
        roman = res.get(sid)
        if roman:
            transliterate._write_roman(conn, sid, roman, config.MODEL)
            conn.commit()
            ok += 1
        else:
            print(f"  [empty] id={sid}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"  [err] id={sid}: {str(e)[:100]}", flush=True)
print(f"done: {ok}/{len(rows)} written", flush=True)
