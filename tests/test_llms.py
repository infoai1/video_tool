"""/llms.txt -- the one thing an assistant cannot work out on its own."""
import llms


_REAL = object()


def _text(monkeypatch, counts=_REAL):
    """The file, with every count forced to `counts` (None = database missing)."""
    monkeypatch.setattr(llms, "_CACHE", {"at": 0.0, "text": ""})
    if counts is not _REAL:
        monkeypatch.setattr(llms, "_count", lambda path, sql: counts)
    return llms.text("video.spiritualmessage.org")


def test_it_says_search_is_a_get_with_q(monkeypatch):
    t = _text(monkeypatch, counts=2242)
    assert "https://video.spiritualmessage.org/search?q=" in t
    # an English question, Roman Urdu and Urdu script are each shown working
    assert "why+is+prayer+in+arabic" in t and "namaz+arabic" in t


def test_it_shows_how_to_cite_a_moment(monkeypatch):
    assert "/video/545#t=1260" in _text(monkeypatch, counts=1)


def test_counts_come_from_the_databases(monkeypatch):
    assert "2,242 lectures" in _text(monkeypatch, counts=2242)


def test_a_missing_database_costs_a_number_not_the_file(monkeypatch):
    t = _text(monkeypatch, counts=None)
    assert "many lectures" in t
    assert "/search?q=" in t


def test_it_is_cached_between_requests(monkeypatch):
    calls = []
    monkeypatch.setattr(llms, "_CACHE", {"at": 0.0, "text": ""})
    monkeypatch.setattr(llms, "_count", lambda p, s: calls.append(1) or 1)
    llms.text()
    first = len(calls)
    llms.text()
    assert len(calls) == first
