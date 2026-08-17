"""Configuration, read once from the environment.

Everything the tool needs to locate its source data, its own store, and the
transliteration model lives here so nothing else has to reach into os.environ.
"""
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
#   "anthropic"  -> the Anthropic SDK / API directly (needs ANTHROPIC_API_KEY)
#   "openrouter" -> OpenRouter's OpenAI-compatible API (needs OPENROUTER_API_KEY)
# OpenRouter exists because some deployments (e.g. the CPS box) already have an
# OpenRouter key and no direct Anthropic key; the same Haiku model is reached
# either way.
PROVIDER = os.environ.get("VIDEO_TOOL_PROVIDER", "anthropic").lower()

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get(
    "VIDEO_TOOL_OR_KEY", ""
)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Transliteration model. Haiku is deliberately chosen: transliteration is a
# high-volume, low-reasoning task, and Haiku is the cheapest capable model. The
# default id differs per provider (native vs OpenRouter slug).
_DEFAULT_MODEL = "anthropic/claude-haiku-4.5" if PROVIDER == "openrouter" else "claude-haiku-4-5"
MODEL = os.environ.get("VIDEO_TOOL_MODEL", _DEFAULT_MODEL)

# How many segments to send to the model in one request. Larger batches amortise
# the fixed prompt overhead; too large and one bad segment stalls the whole call.
BATCH_SIZE = int(os.environ.get("VIDEO_TOOL_BATCH_SIZE", "25"))
