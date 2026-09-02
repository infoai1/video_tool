"""Thin client for the shared semantic-retrieval service (Option A).

Meaning-based hits come from a separate warm process (BGE-M3 over the 409k-vector
matrix). This module is deliberately dumb: one HTTP GET, short timeout, and any
failure returns [] so the search box degrades to keyword-only and NEVER breaks.
Toggle with SEMANTIC_SEARCH=0.
"""
import os, json, urllib.request, urllib.parse

URL = os.environ.get("SEMANTIC_URL", "http://127.0.0.1:5065/retrieve")
TIMEOUT = float(os.environ.get("SEMANTIC_TIMEOUT", "1.8"))


def enabled():
    return os.environ.get("SEMANTIC_SEARCH", "1") != "0"


def retrieve(q, k=20):
    """[{youtube_id,start,title,score,text}] or [] on any problem."""
    if not enabled() or not q:
        return []
    try:
        url = f"{URL}?{urllib.parse.urlencode({'q': q, 'k': k})}"
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            return json.load(r).get("hits", [])
    except Exception:
        return []  # keyword-only fallback; search must never fail on semantic
