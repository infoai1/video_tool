"""The markup a crawler reads on a lecture page.

These guard the three things that made the transcripts invisible: a page that
says what it is, one address it lives at, and a VideoObject that carries the
spoken words and the moments they were spoken at.
"""
import json

import seo


def _video(**kw):
    v = {"id": 7, "title": "Ramadan | Maulana Wahiduddin Khan", "year": 2008,
         "uploaded_at": None,
         "segments": [{"start_time": 0.5, "roman_text": "pehli baat"},
                      {"start_time": 61.0, "roman_text": "dusri baat", "urdu_text": "..."},
                      {"start_time": 3600.0, "roman_text": None, "urdu_text": "aakhri baat"}]}
    v.update(kw)
    return v


def _meta(**kw):
    clips = kw.pop("clips", [{"start": 60, "seconds": 120, "topic": "the purpose of fasting"}])
    qa = kw.pop("qa", [{"start": 900, "q": "Is fasting only about hunger?"}])
    return seo.video_meta(_video(**kw), "abc123", clips, qa, "https://video.spiritualmessage.org")


def test_title_drops_the_speaker_boilerplate():
    assert seo.clean_title("Ramadan | Maulana Wahiduddin Khan") == "Ramadan"
    assert seo.clean_title("   ") == "Untitled lecture"


def test_the_spoken_words_are_in_the_structured_data():
    node = json.loads(_meta()["jsonld"].replace("<\\/", "</"))[0]
    assert node["@type"] == "VideoObject"
    assert node["transcript"] == "pehli baat dusri baat aakhri baat"
    assert node["url"] == "https://video.spiritualmessage.org/video/7"
    assert node["embedUrl"] == "https://www.youtube.com/embed/abc123"


def test_transcript_is_capped_so_the_page_stays_light():
    long_video = _video(segments=[{"start_time": i, "roman_text": "word " * 100}
                                  for i in range(2000)])
    text = seo.transcript_text(long_video["segments"])
    assert len(text) < seo.MAX_TRANSCRIPT + 600


def test_key_moments_carry_a_start_an_end_and_a_link():
    node = json.loads(_meta()["jsonld"].replace("<\\/", "</"))[0]
    first = node["hasPart"][0]
    assert first["name"] == "the purpose of fasting"
    assert first["startOffset"] == 60 and first["endOffset"] == 180
    assert first["url"].endswith("/video/7#t=60")
    # the answered question from the same lecture is a moment too
    assert any(p["name"].startswith("Is fasting") for p in node["hasPart"])


def test_a_lecture_with_no_clips_still_gets_valid_markup():
    node = json.loads(_meta(clips=[], qa=[])["jsonld"].replace("<\\/", "</"))[0]
    assert "hasPart" not in node
    assert node["description"]


def test_the_year_stands_in_when_youtube_has_no_date():
    assert seo.upload_date(_video()) == "2008-01-01"
    assert seo.upload_date(_video(), "2009-03-04T10:00:00Z") == "2009-03-04"
    assert seo.upload_date(_video(year=None)) == ""


def test_runtime_is_read_off_the_last_timestamp():
    assert seo.duration_iso(_video()["segments"]) == "PT1H0M0S"
    assert seo.duration_iso([]) == ""


def test_the_description_fits_a_search_result():
    assert len(_meta()["description"]) <= 180


def test_nothing_can_close_the_script_tag_early():
    blob = _meta(title="Fasting </script><script>alert(1)</script>")["jsonld"]
    assert "</script>" not in blob
    assert json.loads(blob.replace("<\\/", "</"))
