#!/usr/bin/env python3
"""Tell Bing, Yandex and the other IndexNow engines that our pages exist.

Google has no equivalent -- its only door is Search Console, which needs a
human signed in. IndexNow needs no account: a key served from our own domain
proves the submission came from us, and Bing's index is what several of the AI
answer engines read.

Usage:
    python3 scripts/indexnow.py --dry-run     # show what would be sent
    python3 scripts/indexnow.py               # submit

The URL list is read from our own live sitemap, so this never drifts from what
we actually publish.
"""
import argparse
import glob
import json
import os
import re
import sys
import urllib.error
import urllib.request

HOST = "video.spiritualmessage.org"
ENDPOINT = "https://api.indexnow.org/indexnow"
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
BATCH = 10_000  # the protocol's ceiling for one request


def key_and_location():
    """Our IndexNow key, read from the file we publish it at."""
    files = sorted(glob.glob(os.path.join(STATIC, "indexnow-*.txt")))
    if not files:
        sys.exit("no indexnow-<key>.txt in static/ -- nothing proves the submission is ours")
    path = files[-1]
    with open(path, encoding="utf-8") as f:
        key = f.read().strip()
    # The engines only accept URLs from the key file's own directory, so the
    # key is served from the root (app.py) even though it is stored in static/.
    return key, f"https://{HOST}/{key}.txt"


def urls(sitemap=f"https://{HOST}/sitemap.xml"):
    with urllib.request.urlopen(sitemap, timeout=60) as r:
        xml = r.read().decode("utf-8")
    return re.findall(r"<loc>([^<]+)</loc>", xml)


def submit(key, key_location, batch):
    body = json.dumps({"host": HOST, "key": key, "keyLocation": key_location,
                       "urlList": batch}).encode("utf-8")
    req = urllib.request.Request(ENDPOINT, data=body,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read().decode("utf-8", "replace")[:300]
    except urllib.error.HTTPError as e:  # the body says which URL or key it refused
        return e.code, e.read().decode("utf-8", "replace")[:300]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="submit only the first N URLs")
    args = ap.parse_args()

    key, key_location = key_and_location()
    found = urls()
    if args.limit:
        found = found[:args.limit]
    lectures = sum(1 for u in found if "/video/" in u)
    print(f"{len(found)} URLs ({lectures} lecture transcripts), key at {key_location}")
    if args.dry_run:
        for u in found[:5]:
            print("  ", u)
        print("   ...")
        return
    for i in range(0, len(found), BATCH):
        status, text = submit(key, key_location, found[i:i + BATCH])
        print(f"batch {i // BATCH + 1}: HTTP {status} {text}")


if __name__ == "__main__":
    main()
