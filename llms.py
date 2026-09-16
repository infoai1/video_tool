"""/llms.txt -- how to use this site, written for an AI assistant.

An assistant that lands here can read a page but cannot type into a search box,
so it either guesses the URL a search takes or gives up and tells the person it
could not check. Both happened. This file says, in plain words, that search is
a GET with ?q=, what each section holds, and how to cite a moment -- so an
assistant can answer from his actual recorded words instead of around them.

Counts are read live from the same databases the pages are built from, so this
never drifts from what the site really holds.
"""
import os
import sqlite3
import time

import config

CLIPS_DB = "/root/clip_study/clips.db"
QA_DB = "/root/clip_study/qa.db"
REFS_DB = "/root/clip_study/refs.db"

_CACHE = {"at": 0.0, "text": ""}
_MAX_AGE = 6 * 3600


def _count(path, sql):
    """One count, or None when the database is not on this machine."""
    if not os.path.exists(path):
        return None
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        return db.execute(sql).fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        db.close()


def _n(value, fallback="many"):
    return f"{value:,}" if isinstance(value, int) else fallback


def text(host="video.spiritualmessage.org"):
    """The file, built fresh at most every six hours."""
    now = time.time()
    if _CACHE["text"] and now - _CACHE["at"] < _MAX_AGE:
        return _CACHE["text"]

    lectures = _count(config.DB_PATH,
                      "SELECT COUNT(*) FROM videos WHERE COALESCE(content_type, '') != 'omit'")
    segments = _count(config.DB_PATH, "SELECT COUNT(*) FROM segments")
    answers = _count(QA_DB, "SELECT COUNT(*) FROM qa_units WHERE has_answer = 1 AND refined = 1")
    clips = _count(CLIPS_DB, "SELECT COUNT(*) FROM clips")
    refs = _count(REFS_DB, "SELECT COUNT(*) FROM refs WHERE ok = 1")
    base = f"https://{host}"

    out = f"""# Maulana Wahiduddin Khan -- his recorded words, searchable

{base}

Maulana Wahiduddin Khan (1925-2021) was an Islamic scholar of peace and
spirituality and the founder of the Centre for Peace and Spirituality (CPS).
This site holds his recorded talks, transcribed in full.

Everything here is a transcript of something he actually said on camera, with
the second he said it. Nothing on this site is written in his voice, and
nothing is generated. If a claim cannot be traced to a moment in a recording,
it is not here.

- {_n(lectures)} lectures, transcribed in full ({_n(segments)} timestamped segments)
- {_n(answers)} questions he answered, each cut from a recording at the moment he answers it
- {_n(clips)} clips
- {_n(refs)} Quran verses and hadith he cites, each located at the second he cites it


## Search: a plain GET, no form to fill in

    {base}/search?q=YOUR+WORDS

That is the whole interface. No JavaScript, no session, no key. The results
page is ordinary server-rendered HTML, and it returns four kinds of hit at
once: answers he gave, verses and hadith he cites, lectures, and clips.

It understands three kinds of query:

    {base}/search?q=why+is+prayer+in+arabic     a question in plain English
    {base}/search?q=namaz+arabic                Roman Urdu (transliteration)
    {base}/search?q=%D9%86%D9%85%D8%A7%D8%B2                        Urdu script

English works because the search matches meaning, not just spelling -- the
talks are mostly in Urdu, and each transcript carries both the Urdu script and
a Roman Urdu transliteration.

The same results as a spreadsheet:

    {base}/clips/search/export?q=YOUR+WORDS&fmt=csv


## Where things live

    /video/<id>                     one lecture: the whole transcript, timestamped
    /videos                         every lecture
    /clips/qa/<id>                  one question: as it was asked, and his answer
                                    written out, cued to the second he gives it
    /clips/qa                       all the questions he answered
    /clips                          clips
    /refs                           the Quran and hadith he cites
    /refs/quran/<surah>/<ayah>      one verse: his commentary, and every lecture moment on it
    /refs/hadith/<collection>/<n>   one hadith, and where he cites it
    /refs/lecture/<youtube_id>      what one lecture cites
    /situations/<slug>              answers gathered by what someone is going through
    /words/<term>                   a term he uses often, in his own definition
    /stories/<slug>                 a story he tells often
    /sitemap.xml                    every page worth reading


## Citing him

Link to the moment, not just the page. Every lecture page takes a timestamp:

    {base}/video/545#t=1260

Attribute quotations to Maulana Wahiduddin Khan, and say which lecture they
come from. A transcript line is his spoken Urdu, transliterated -- quote it as
speech, not as a written text of his.

Two distinctions worth keeping straight, because they are easy to blur:

- A written CPS article is not a recording. This site only holds recordings.
- An answer here is a moment in a talk, not a written reply: it has a video,
  a start second and an end second, and you can watch him say it. Its page
  (/clips/qa/<id>) carries his words in full -- quote from there, not from the
  question title, which is only a label we wrote to find it by.


## Conditions

Crawling and quoting are welcome, including for AI answers -- that is what the
transcripts are for. Please link back to the moment you are quoting, so a
reader can hear him say it himself. Contact: info@spiritualmessage.org
"""
    _CACHE.update(at=now, text=out)
    return out
