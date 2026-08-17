"""Seed the store with a tiny hand-made sample, so you can try search + browse
without the source database or an API key.

    python scripts/demo_seed.py
    python cli.py serve

It inserts two short videos with Urdu-script lines and their Roman Urdu, going
through the real transliteration/index path (with a canned lookup instead of a
model call) so the FTS index is built exactly as a real run would build it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import normalize  # noqa: E402
import transliterate  # noqa: E402

# (youtube_url, title, [(start_time, urdu, roman), ...])
SAMPLE = [
    (
        "https://youtube.com/watch?v=demo1",
        "Shukr aur Bandagi",
        [
            (0.0, "اللہ کا شکر ادا کرنا بندے کا فرض ہے", "Allah ka shukr ada karna bande ka farz hai"),
            (12.0, "نماز اللہ کی بندگی کا سب سے بڑا ذریعہ ہے", "Namaz Allah ki bandagi ka sab se bara zariya hai"),
            (25.0, "تواکل کا مطلب ہے اللہ پر بھروسہ", "Tawakkul ka matlab hai Allah par bharosa"),
        ],
    ),
    (
        "https://youtube.com/watch?v=demo2",
        "Sabr ki Ahmiyat",
        [
            (0.0, "صبر ایمان کا حصہ ہے", "Sabr iman ka hissa hai"),
            (9.0, "مشکل وقت میں نماز اور صبر سے مدد لو", "Mushkil waqt mein namaz aur sabr se madad lo"),
            (20.0, "اللہ صبر کرنے والوں کے ساتھ ہے", "Allah sabr karne walon ke saath hai"),
        ],
    ),
]


def main():
    if db.exists():
        print(f"{db.config.DB_PATH} already exists — delete it first to reseed.")
        return 1
    db.init_db()
    conn = db.connect()
    romans = {}
    try:
        for url, title, lines in SAMPLE:
            conn.execute(
                "INSERT INTO videos (youtube_url, title) VALUES (?, ?)", (url, title)
            )
            vid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            for start, urdu, roman in lines:
                urdu_norm = normalize.normalize_urdu(urdu)
                conn.execute(
                    "INSERT INTO segments (video_id, start_time, urdu_text, urdu_norm) "
                    "VALUES (?, ?, ?, ?)",
                    (vid, start, urdu, urdu_norm),
                )
                sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO urdu_fts (rowid, urdu_norm) VALUES (?, ?)", (sid, urdu_norm)
                )
                romans[sid] = roman
        conn.commit()
    finally:
        conn.close()

    # Drive the real transliteration path with a canned lookup (no model call),
    # so roman_text + roman_norm + the FTS index are populated the normal way.
    n = transliterate.run(translit_batch=lambda batch: {sid: romans[sid] for sid, _u, _t in batch})
    print(f"Seeded {len(romans)} segments across {len(SAMPLE)} videos ({n} indexed).")
    print("Now run:  python cli.py serve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
