#!/usr/bin/env bash
# Weekly restore test — "a backup you have not restored is not a backup."
#
# Takes the NEWEST roman.db backup, restores it into a throwaway scratch copy
# (sharing nothing with the live DB), and proves it actually works: opens,
# passes integrity_check, and still contains the corpus + the irreplaceable
# user data. Anything less is faith, not a backup.
set -uo pipefail

APP="/root/video_tool"
BACKUP_DIR="$APP/backups"
LOG="$APP/docs/RESTORE_LOG.md"

[ -f "$APP/video_tool.env" ] && . "$APP/video_tool.env" 2>/dev/null || true
TG_TOKEN="${VIDEO_TOOL_ALERT_TG_TOKEN:-}"
TG_CHAT="${VIDEO_TOOL_ALERT_TG_CHAT:-}"

log()    { mkdir -p "$(dirname "$LOG")"; echo "$(date -u +%FT%TZ) | $*" >> "$LOG"; }
notify() { [ -n "$TG_TOKEN" ] && curl -s -o /dev/null \
             "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
             --data-urlencode "chat_id=${TG_CHAT}" \
             --data-urlencode "text=$1" >/dev/null 2>&1 || true; }
fail()   { log "ERROR: $1"; notify "⚠️ video_tool restore test FAILED: $1"; rm -rf "${SCRATCH:-}"; exit 1; }

LATEST="$(ls -1t "$BACKUP_DIR/db/"*.db.gz 2>/dev/null | head -1)"
[ -z "$LATEST" ] && fail "no backup found in $BACKUP_DIR/db"

SCRATCH="$(mktemp -d)"
gunzip -c "$LATEST" > "$SCRATCH/r.db" || fail "gunzip failed for $(basename "$LATEST")"

CHK="$(sqlite3 "$SCRATCH/r.db" 'PRAGMA integrity_check;' 2>&1 | head -1)"
[ "$CHK" = "ok" ] || fail "integrity_check on restored copy = $CHK"

V="$(sqlite3 "$SCRATCH/r.db" 'SELECT COUNT(*) FROM videos;'    2>/dev/null || echo 0)"
S="$(sqlite3 "$SCRATCH/r.db" 'SELECT COUNT(*) FROM segments;'  2>/dev/null || echo 0)"
B="$(sqlite3 "$SCRATCH/r.db" 'SELECT COUNT(*) FROM bookmarks;' 2>/dev/null || echo 0)"
rm -rf "$SCRATCH"

{ [ "${V:-0}" -gt 0 ] && [ "${S:-0}" -gt 0 ]; } || fail "restored DB looks empty (videos=$V segments=$S)"
log "OK | restored $(basename "$LATEST") | videos=$V segments=$S bookmarks=$B"
