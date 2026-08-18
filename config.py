"""Configuration, read once from the environment.

Everything the tool needs to locate its source data, its own store, and the
transliteration model lives here so nothing else has to reach into os.environ.
"""
import json
import os

# Source of Urdu-script transcripts. The tool NEVER writes here — it is opened
# read-only (see source.py). Defaults to the CPS annotation.db path used on the
# Hetzner box, so on that box the tool works with no configuration.
SOURCE_DB = os.environ.get(
    "VIDEO_TOOL_SOURCE_DB", "/root/annotation_tool_v2/data/annotation.db"
)

# The tool's own store: Roman Urdu transcripts + the search index. This file is
# derived data — deleting it and re-running loses nothing that can't be rebuilt.
DB_PATH = os.environ.get("VIDEO_TOOL_DB", "roman.db")

# How the transliteration model is reached:
#   "claude_cli" -> the authenticated `claude` CLI (Claude Code) — uses a Claude
#                   subscription, so transliteration is covered by the plan
#                   rather than billed per token. No API key needed.
#   "anthropic"  -> the Anthropic SDK / API directly (needs ANTHROPIC_API_KEY)
#   "openrouter" -> OpenRouter's OpenAI-compatible API (needs OPENROUTER_API_KEY)
# All three reach the same Haiku model; pick whichever credential the box has.
PROVIDER = os.environ.get("VIDEO_TOOL_PROVIDER", "anthropic").lower()

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get(
    "VIDEO_TOOL_OR_KEY", ""
)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# For PROVIDER=claude_cli: the CLI binary and a generous per-batch timeout (the
# CLI wraps each call in its own context, so a batch can take tens of seconds).
CLAUDE_BIN = os.environ.get("VIDEO_TOOL_CLAUDE_BIN", "claude")
CLI_TIMEOUT = int(os.environ.get("VIDEO_TOOL_CLI_TIMEOUT", "300"))

# Transliteration model. Transliteration is a high-volume, low-reasoning task,
# so the cheapest FAST model wins. On OpenRouter, gemini-2.5-flash-lite is the
# right default: measured on the live corpus it transliterated a 25-line batch
# in ~10s at ~230 output-tok/s with clean results, versus ~50-90s for
# deepseek-v4-flash (whose OpenRouter backends serve it at only ~50 tok/s —
# same output size, ~5x slower wall-clock, and some backends returned
# unparseable JSON). For anthropic/claude_cli the native Haiku id is used.
_DEFAULT_MODEL = "google/gemini-2.5-flash-lite" if PROVIDER == "openrouter" else "claude-haiku-4-5"
MODEL = os.environ.get("VIDEO_TOOL_MODEL", _DEFAULT_MODEL)

# How many segments to send to the model in one request. Larger batches amortise
# the fixed prompt overhead; too large and one bad segment stalls the whole call.
BATCH_SIZE = int(os.environ.get("VIDEO_TOOL_BATCH_SIZE", "25"))
# How many batches to run concurrently during bulk romanization. Each batch is
# an independent network round-trip with no shared state, so DB writes stay
# serialized on the caller's connection regardless of this value. With a
# reliable, well-provisioned endpoint (the gemini-2.5-flash-lite default),
# concurrency scales throughput almost linearly — measured on the live corpus:
# 1 -> ~210 lines/min, 4 -> ~600, 8 -> ~1680, with no errors. (An earlier
# deepseek-v4-flash backend instead stalled under concurrency, past any
# client-side timeout — hence the per-call wall-clock watchdog in
# transliterate.py, which makes a stuck call fail fast instead of wedging the
# job.) 8 is a good default for gemini; drop toward 1 if a provider rate-limits
# or misbehaves under load.
ROMANIZE_CONCURRENCY = int(os.environ.get("VIDEO_TOOL_ROMANIZE_CONCURRENCY", "8"))

# Where user feedback / bug reports are appended (one JSON object per line).
FEEDBACK_PATH = os.environ.get("VIDEO_TOOL_FEEDBACK", "feedback.jsonl")

# Transcription of a new YouTube video (not yet in the library) via Soniox ASR.
SONIOX_API_KEY = os.environ.get("SONIOX_API_KEY", "")
SONIOX_URL = "https://api.soniox.com/v1"
# YouTube bot-blocks datacenter IPs, so yt-dlp needs a cookies.txt exported from
# a logged-in browser. Point this at that file; without it, download fails with
# a clear message. See README.
YT_COOKIES = os.environ.get("VIDEO_TOOL_YT_COOKIES", "")
# Optional outbound proxy for yt-dlp. YouTube bot-blocks datacenter IPs even
# with cookies; routing through a residential/mobile proxy is the reliable fix.
# e.g. "http://user:pass@host:port" or "socks5://host:port". Empty = direct.
YT_PROXY = os.environ.get("VIDEO_TOOL_YT_PROXY", "")
# Optional PO-token provider (bgutil) base URL, used to satisfy YouTube's
# proof-of-origin checks. Empty = don't pass one.
YT_POT_BASE_URL = os.environ.get("VIDEO_TOOL_YT_POT_URL", "")
# Rough target length of a transcript segment, in seconds.
SEGMENT_SECONDS = float(os.environ.get("VIDEO_TOOL_SEGMENT_SECONDS", "18"))
# How many of a channel's newest uploads to scan when syncing (newest first).
# Generous by default so a long catch-up isn't truncated; already-transcribed
# ones are skipped, so a high cap is cheap.
CHANNEL_SCAN_LIMIT = int(os.environ.get("VIDEO_TOOL_CHANNEL_SCAN_LIMIT", "500"))

# Login. Credentials + session secret live in a JSON file OUTSIDE the repo
# (auth.json, gitignored), or in env vars. When neither is set, auth is OFF —
# so local dev and the tests aren't gated. The file avoids env-quoting issues
# with special characters in the password.
_AUTH_FILE = os.environ.get("VIDEO_TOOL_AUTH_FILE", "auth.json")
_auth = {}
if os.path.exists(_AUTH_FILE):
    try:
        with open(_AUTH_FILE, encoding="utf-8") as _f:
            _auth = json.load(_f)
    except (OSError, ValueError):
        _auth = {}
AUTH_USER = os.environ.get("VIDEO_TOOL_AUTH_USER", _auth.get("user", ""))
AUTH_PASSWORD = os.environ.get("VIDEO_TOOL_AUTH_PASSWORD", _auth.get("password", ""))
SECRET_KEY = os.environ.get("VIDEO_TOOL_SECRET", _auth.get("secret", "")) or "dev-insecure-key"
