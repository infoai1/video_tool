# video_tool — Session Handover

Paste this whole file into a new session to continue without losing context.
Last updated: 2026-08-20.

---

## What this is
A live web app that makes **Maulana Wahiduddin Khan's video lectures searchable in
Roman Urdu** (and browsable). Urdu-script transcripts → transliterated to Roman →
bilingual full-text search where a Roman query (`namaz`) matches Urdu (`نماز`).

- **Live URL:** https://video.spiritualmessage.org  (login required)
- **Repo:** github.com/infoai1/video_tool
- **Users:** ~10 people, sharing ONE login today.

## How to operate it (access model)
- **Production server** = a Hetzner box, reachable ONLY through the **Mycode MCP**
  (`mcp__Mycode__run_command`, `write_file`, `read_file`) = root shell. App lives at
  `/root/video_tool`. `run_command` has a ~60s wall-clock cap — for long tasks,
  launch detached (`setsid … >log 2>&1 &`) and poll the log.
- **Sandbox repo** (where you edit + run tests) = `/workspace/video_tool`. This is
  NOT the server. Editing here never touches production.
- **Deploy to prod** = on the box: `git fetch && git reset --hard <commit> &&
  systemctl restart video-tool video-tool-worker`. There is NO staging yet, so a
  bad deploy hits users directly — see Rules.

## Runtime facts (verify, don't trust blindly)
- **Services (systemd):** `video-tool` (gunicorn, `-b 127.0.0.1:5060`, 2 workers ×
  4 threads) and `video-tool-worker` (background jobs). nginx + Let's Encrypt front it.
- **DB:** SQLite `/root/video_tool/roman.db` (~672M, WAL). `connect()` RW, `connect_ro()`.
- **Runtime config:** `/root/video_tool/video_tool.env` (gitignored, systemd
  EnvironmentFile). Holds `VIDEO_TOOL_PROVIDER=openrouter`,
  `VIDEO_TOOL_MODEL=google/gemini-2.5-flash-lite`, `VIDEO_TOOL_ROMANIZE_CONCURRENCY=8`,
  `OPENROUTER_API_KEY`, `SONIOX_API_KEY`, Telegram alert creds, etc.
- **Secrets on box (never in git):** `auth.json` (login user/password/secret),
  `video_tool.env`, `youtube_cookies.txt`.
- **Transliteration:** gemini-2.5-flash-lite via OpenRouter (plain urllib). We learned
  the hard way that `deepseek-v4-flash` on OpenRouter is ~5× slower and some backends
  return unparseable JSON — do NOT switch back. `claude_cli`/`anthropic` providers also
  exist but are reserved for tiny per-video use, not bulk.
- **YouTube downloads are BLOCKED** from this datacenter IP even with valid cookies +
  the bgutil PO-token provider (a Docker container `bgutil-pot` on :4416 is running).
  The only real fix is a **residential proxy** → set `VIDEO_TOOL_YT_PROXY` (plumbing
  already in `transcribe._yt_cmd`).

## Git state (IMPORTANT)
- `main` = commit **5afd8d6** = EXACTLY what production runs. Treat main as prod truth.
- Feature branch `claude/roman-urdu-video-transcripts-6i4qlq` is AHEAD of main with
  hardening that is **committed but NOT deployed**: the poison-isolation fix, corrected
  `requirements.txt`, docstring fixes, and the backup scripts (`1c2f7d8`).
- **The box still runs 5afd8d6** — do not deploy the ahead-commits without the staging
  path (they're low-risk, but discipline first). Backups scripts were placed on the box
  as standalone files via `git show`, without changing the app commit.

## Current status
- **Romanization: ~99.8% complete** (426k/427k lines). The last ~670 are giant
  bulk-text outlier segments; harmless. Runs as background job on the worker.
- **Backups: LIVE** (this session). `scripts/backup.sh` daily 03:15 UTC (online SQLite
  `.backup` of roman.db + secrets tarball, integrity-checked, gzip, 14-deep retention,
  logs `docs/BACKUP_LOG.md`, Telegram alert on failure). `scripts/restore_test.sh`
  weekly Sun 04:15 (restores newest backup to scratch, asserts integrity + non-empty).
  Both verified working. Backups in `/root/video_tool/backups/` (gitignored).
- **Test suite: 46/46 green** (run `cd /workspace/video_tool && python -m pytest -q`;
  pytest is NOT installed in prod and there is no CI yet).

### To restore the DB from backup (disaster runbook)
```
systemctl stop video-tool video-tool-worker
gunzip -c /root/video_tool/backups/db/roman-<STAMP>.db.gz > /root/video_tool/roman.db
rm -f /root/video_tool/roman.db-wal /root/video_tool/roman.db-shm
systemctl start video-tool video-tool-worker
```

## The agreed roadmap (priority order: SAFETY → ACCOUNTS → BOOKS)
User decisions locked in:
- Accounts: **proper per-user accounts + Admin/Viewer roles**.
- Book search UX: **one unified search with a Books / Videos / Both filter**.
- Full roadmap doc: artifact "Search Platform Roadmap".

## PENDING WORK (the actual to-do list)

### A. Safety / reliability  (do first; protects live users)
- [x] Automated backups + restore test  ← DONE this session
- [ ] **Staging environment** (e.g. staging.video.spiritualmessage.org on a diff port)
      — the single highest-value remaining item; enables safe deploys.
- [ ] **CI test gate** — run the pytest suite on every push; block deploy if red.
- [ ] **Monitoring/alerts** — uptime ping on /health + error logging (app currently
      logs NOTHING beyond systemd start/stop — real gap).
- [ ] Formalize `main` as the deploy branch + a written rollback runbook.
- [ ] Commit sanitized infra to repo (systemd units, nginx, `.env.example` matching
      reality) + a `DEPLOY.md`. Currently tribal knowledge.

### B. Security fixes (found in a critical audit; all real, evidence in code)
- [ ] **CSRF protection** — every mutating POST (`/api/romanize_all`, `/api/transcribe`,
      `/api/upload_cookies`, saves, deletes) is an unprotected session-cookie POST.
- [ ] **XSS sink** in `highlight()` (`static/app.js:51`, dup `index.html:197`): writes
      `innerHTML` from text that includes LLM/ASR output — escape before wrapping `<mark>`.
- [ ] **Login rate-limiting / lockout** — none; brute-forceable; no failed-attempt log.
- [ ] **Session cookie `Secure` flag** — not set (`SESSION_COOKIE_SECURE`).
- [ ] **Gate `/api/upload_cookies` to admins** — any user can currently overwrite the
      server's YouTube cookies.

### C. Availability hardening
- [ ] **`translit_query()` runs a blocking LLM call inside the web request with NO
      watchdog** — a slow provider can hang request threads (only 8 total). Wrap it like
      `_complete_with_retry`/watchdog. `/api/romanize` (`ensure()`) has retry but each
      attempt can still burn ~45s.
- [ ] **Add `PRAGMA busy_timeout`** in `db.py connect()` — worker write batches vs web
      writes can throw "database is locked" → user 500s; nothing retries.
- [ ] Deduplicate ~200 lines of near-identical JS between `index.html` and `video.html`.

### D. Accounts (workstream 2)
- [ ] `users` table (username, hashed pw, role, active), migration.
- [ ] Login accepts individual accounts; keep shared login working during cutover.
- [ ] Roles: Viewer (search/read/export/save) vs Admin (transcribe, romanize-all,
      cookies, sync, user mgmt).
- [ ] Admin console page (add/deactivate/reset/roles).
- [ ] Onboard the ~10 users; retire the shared password.

### E. Book & Al-Risala search (workstream 3)  — DATA ALREADY DIGITIZED
- Source: `annotation_tool_v2/data/annotation.db` (read-only) has **221 books**
  (212 Urdu + 9 English, incl. 20+ yrs Al-Risala) and **59,825 paragraphs**, already
  FTS-indexed, with a `transliterated_text` column.
- [ ] Ingest paragraphs (book·chapter·page·Urdu·Roman) into the search store.
- [ ] Same Urdu-FTS + Roman-FTS + normalization as videos.
- [ ] Unified search with a **Books / Videos / Both** filter.
- [ ] Book-hit UI (book·chapter·page + highlighted passage) reusing save/export/romanize.
- [ ] Phase 2: English, per-book browse, lecture↔book cross-links.

### F. Loose ends
- [ ] Transcribe two requested videos once a proxy exists:
      `uS8cxd8ApSA` (Imaan and Marefat, Jul 28 2008),
      `emHZjUnLsmI` (Man and The Religion, Jul 31 2008).
      Both are in annotation.db as id 2267/2268 but never transcribed (no data to import).
- [ ] Deploy the pending ahead-of-main hardening (poison fix etc.) via staging once it exists.
- [ ] ~70 legacy videos have bulk-text (no per-line timestamps); real fix is Soniox
      re-transcription (needs the proxy). No hidden timestamped source exists for them.

## RULES (do not break the live platform)
1. **Never `git reset --hard` / restart on the box without a fresh backup** (backups
   run daily, but take a manual one via `bash scripts/backup.sh` before any deploy).
2. **Edit + test in `/workspace/video_tool`; the box only changes on an explicit deploy.**
3. **Read-only queries on the box are fine; writes/restarts need care.** `db.connect_ro()`
   for anything that just reads.
4. When staging exists, deploy = feature → staging → verify → main → box. Until then,
   deploy only tiny, individually-reversible diffs, and confirm `/health` after.
5. `roman.db` holds irreplaceable user data — treat it as precious.

## First things to do in a new session
1. `cd /workspace/video_tool && git fetch origin && git log --oneline -5` — see state.
2. `python -m pytest -q` — confirm 46/46 still green.
3. On the box (Mycode): `tail docs/BACKUP_LOG.md`, `systemctl is-active video-tool
   video-tool-worker`, `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5060/health`.
4. Then pick up Pending §A: **staging environment** (next highest-value item).
