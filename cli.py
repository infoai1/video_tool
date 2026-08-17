"""Command-line entry point.

    python cli.py ingest [--limit N]         copy source transcripts into the store
    python cli.py transliterate [--limit N]  fill in Roman Urdu with Claude Haiku
    python cli.py status                     what's in the source and the store
    python cli.py serve [--port P]           run the search + browse web app

`ingest` then `transliterate` is the normal build sequence; both are resumable,
so a large corpus can be done in chunks with --limit.
"""
import argparse
import sys

import config


def cmd_ingest(args):
    import ingest

    print("Ingesting from", config.SOURCE_DB, "…", flush=True)
    try:
        v, s = ingest.ingest(limit=args.limit)
    except FileNotFoundError as exc:
        print("error:", exc, file=sys.stderr)
        return 1
    print(f"Ingested {v} new video(s), {s} new segment(s) into {config.DB_PATH}.")
    return 0


def cmd_transliterate(args):
    import transliterate

    done_before, pending = transliterate.status()
    if pending == 0:
        print("Nothing to transliterate — run `ingest` first, or the backlog is empty.")
        return 0
    target = min(pending, args.limit) if args.limit else pending
    print(f"Transliterating {target} segment(s) with {config.MODEL} …", flush=True)

    def progress(done):
        print(f"  {done}/{target}", end="\r", flush=True)

    n = transliterate.run(limit=args.limit, batch_size=args.batch_size, progress=progress)
    print(f"\nTransliterated {n} segment(s).")
    return 0


def cmd_status(args):
    import source
    import transliterate
    import db

    print("Source DB:", config.SOURCE_DB)
    try:
        sv, ss = source.counts()
        print(f"  videos: {sv:,}   segments with transcript: {ss:,}")
    except FileNotFoundError as exc:
        print("  (unreachable:", exc, ")")

    print("Store DB:", config.DB_PATH)
    if not db.exists():
        print("  (not built yet — run `ingest`)")
        return 0
    conn = db.connect_ro()
    try:
        vids = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        segs = conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
    finally:
        conn.close()
    done, pending = transliterate.status()
    pct = (100.0 * done / segs) if segs else 0.0
    print(f"  videos: {vids:,}   segments: {segs:,}")
    print(f"  transliterated: {done:,} ({pct:.1f}%)   pending: {pending:,}")
    if pending:
        # Rough order-of-magnitude estimate; Haiku list price ~ $1/$5 per MTok,
        # ~120 tokens round-trip per short segment including prompt overhead.
        est = pending * 120 * 3e-6
        print(f"  rough cost to finish with {config.MODEL}: ~${est:,.2f}")
    return 0


def cmd_serve(args):
    from app import app

    print(f"Serving on http://{args.host}:{args.port}  (Ctrl-C to stop)")
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="cli.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("ingest", help="copy source transcripts into the store")
    pi.add_argument("--limit", type=int, default=None, help="cap segments (pilot runs)")
    pi.set_defaults(func=cmd_ingest)

    pt = sub.add_parser("transliterate", help="fill in Roman Urdu with Claude")
    pt.add_argument("--limit", type=int, default=None, help="cap segments this run")
    pt.add_argument("--batch-size", type=int, default=None, help="segments per request")
    pt.set_defaults(func=cmd_transliterate)

    ps = sub.add_parser("status", help="show source + store counts and progress")
    ps.set_defaults(func=cmd_status)

    pv = sub.add_parser("serve", help="run the web app")
    pv.add_argument("--host", default="127.0.0.1")
    pv.add_argument("--port", type=int, default=5060)
    pv.add_argument("--debug", action="store_true")
    pv.set_defaults(func=cmd_serve)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
