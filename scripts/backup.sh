#!/usr/bin/env bash
# Daily backup of video_tool's store (roman.db) + secrets.
#
# WHY roman.db MUST be backed up (unlike shukr.db, which the house backup
# deliberately excludes as freely-rebuildable vectors): roman.db holds data that
# is NOT freely recoverable —
#   * user data: saves, playlists, bookmarks (bookmarks / bookmark_tags) —
#     irreplaceable, created by the ~10 live users;
#   * ~427k romanized lines that cost real money and hours to regenerate.
# So it is source-grade data here and gets a real, integrity-checked backup.
#
# Safe to run against the LIVE database: uses SQLite's online .backup API, which
# is consistent even under concurrent writes from the app/worker.
#
# Secrets (Telegram creds) are read from the gitignored env, never hardcoded, so
# this script is safe to commit. Alerts reuse the box's existing ops bot.
set -euo pipefail

APP="/root/video_tool"
BACKUP_DIR="$APP/backups"
DB="$APP/roman.db"
LOG="$APP/docs/BACKUP_LOG.md"
TS="$(date -u +%Y%m%d-%H%M%S)"
MIN_FREE_GB=3
KEEP_DB=14
KEEP_SECRETS=8

# optional Telegram alerts — creds from the gitignored env only
[ -f "$APP/video_tool.env" ] && . "$APP/video_tool.env" 2>/dev/null || true
TG_TOKEN="${VIDEO_TOOL_ALERT_TG_TOKEN:-}"
TG_CHAT="${VIDEO_TOOL_ALERT_TG_CHAT:-}"

log()    { mkdir -p "$(dirname "$LOG")"; echo "$(date -u +%FT%TZ) | $*" >> "$LOG"; }
notify() { [ -n "$TG_TOKEN" ] && curl -s -o /dev/null \
             "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
             --data-urlencode "chat_id=${TG_CHAT}" \
             --data-urlencode "text=$1" >/dev/null 2>&1 || true; }
fail()   { log "ERROR: $1"; notify "⚠️ video_tool backup FAILED: $1"; exit 1; }

mkdir -p "$BACKUP_DIR/db" "$BACKUP_DIR/secrets"

# disk guard — never fill the box
FREE_GB=$(df --output=avail -BG / | tail -1 | tr -dc '0-9')
[ "${FREE_GB:-0}" -lt "$MIN_FREE_GB" ] && fail "low disk: ${FREE_GB}G free (< ${MIN_FREE_GB}G)"

# 1. consistent online backup, integrity-checked on the COPY (not the live file)
OUT="$BACKUP_DIR/db/roman-$TS.db"
sqlite3 "$DB" ".backup '$OUT'" || fail "sqlite .backup failed"
CHK="$(sqlite3 "$OUT" 'PRAGMA integrity_check;' 2>&1 | head -1)"
[ "$CHK" = "ok" ] || { rm -f "$OUT"; fail "integrity_check on backup = $CHK"; }
gzip -f "$OUT"
DBSIZE=$(du -h "$OUT.gz" | cut -f1)

# 2. secrets + runtime config (small, private) — the things NOT in git
tar czf "$BACKUP_DIR/secrets/secrets-$TS.tar.gz" -C "$APP" \
    auth.json video_tool.env youtube_cookies.txt 2>/dev/null || true
chmod 600 "$BACKUP_DIR/secrets/secrets-$TS.tar.gz" 2>/dev/null || true

# 3. code-safety note: warn (don't fail) if local commits aren't on GitHub
cd "$APP"
git fetch origin --quiet 2>/dev/null || true
UNPUSHED=$(git log --oneline '@{u}..' 2>/dev/null | wc -l | tr -d ' ' || echo "?")

# 4. retention
ls -1t "$BACKUP_DIR/db/"*.db.gz       2>/dev/null | tail -n "+$((KEEP_DB+1))"      | xargs -r rm -f
ls -1t "$BACKUP_DIR/secrets/"*.tar.gz 2>/dev/null | tail -n "+$((KEEP_SECRETS+1))" | xargs -r rm -f

DBCOUNT=$(ls "$BACKUP_DIR/db/"*.db.gz 2>/dev/null | wc -l | tr -d ' ')
log "OK | db=roman-$TS.db.gz ($DBSIZE) | kept=$DBCOUNT | unpushed=$UNPUSHED | free=${FREE_GB}G"
