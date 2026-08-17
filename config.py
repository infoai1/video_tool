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

# Transliteration model. Haiku is deliberately chosen: transliteration is a
# high-volume, low-reasoning task, and Haiku is the cheapest capable model.
MODEL = os.environ.get("VIDEO_TOOL_MODEL", "claude-haiku-4-5")

# How many segments to send to the model in one request. Larger batches amortise
# the fixed prompt overhead; too large and one bad segment stalls the whole call.
BATCH_SIZE = int(os.environ.get("VIDEO_TOOL_BATCH_SIZE", "25"))
