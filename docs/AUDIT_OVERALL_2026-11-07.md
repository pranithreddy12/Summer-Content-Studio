# Overall audit - Phases 1, 2 and 3 (2026-11-07)

Scope: the offline Definition of Done of all three plans, cross-phase consistency, a security sweep over every route, and a fuzz for crash pages. Earlier passes: `AUDIT_2026-10-24.md` (Phase 2, plus its 2026-11-06 re-audit section) and the Phase 3 audit in `PROGRESS.md`.

## Result

| Area | Result |
|---|---|
| Phase 1 DoD (PLAN.md §20) | Offline items met. **#15 had no test:** added one (a second profile is one DB row, and generation uses its persona, not Summer's). Live generation is proven with Mistral only (`LIVE_PROOF.md`); Claude is still unproven (no funded key). |
| Phase 2 DoD (PLAN_PHASE2 §19 A) | Met (re-audit 2026-11-06). |
| Phase 3 DoD (PLAN_PHASE3 §17) | Met (audit 2026-11-06). |
| Models vs migrations | `alembic upgrade head` on an empty DB matches `models.py` exactly (0 diffs). Now a test. |
| `.env.example` vs `Settings` | `UPLOAD_RATE_LIMIT_PER_MINUTE` was missing: added. Now a test (nothing missing, nothing stale). |
| Deploy files | systemd units, backup script and Caddyfile point at commands and paths that exist. |
| Auth matrix | All 109 route/method pairs (all three phases) return 401 without or with wrong credentials, except `/health` and the signed `/m/{token}` (404 on a bad token), both intended. |
| CSRF | Every POST/PUT/PATCH/DELETE with a foreign `Origin` returns 403. |
| Secrets in git history | Only `sk-ant-REPLACE_ME`, the test fake `sk-ant-test-SECRET123`, and Telegram's public documentation example bot token (in an old test version). No real key. |
| Fuzz, JSON API | 6,360 requests: **44 crash pages, one cause.** An integer query parameter above SQLite's 64-bit range (`?page=2**70`) raised `OverflowError`. Fixed by one app-wide handler (422). Re-run: 0. |
| Fuzz, dashboard forms | 2,658 form posts with hostile values: 0 crash pages. |

## Found and fixed

1. **Two workers could revoke the Fanvue connection.** Nothing stopped a second `app.cli worker` (for example, started by hand while systemd's is running). Both would refresh the single-use Fanvue refresh token, and Fanvue's reuse detection revokes the connection. The worker now takes an OS file lock (`<db file>.worker.lock`) and a second one exits with code 1. The OS frees the lock even on a crash. Checked with two real processes on Windows; Linux uses `fcntl.flock` (not run here).
2. **Huge numbers in query strings were 500s** (44 fuzz hits on 8 routes). Now 422.
3. **`.env.example` missing `UPLOAD_RATE_LIMIT_PER_MINUTE`.**
4. **DoD #15 untested.**

Each has a regression test. Full suite: 688 passed.

## Still open (all phases)

**Needs Lee / live accounts:** Claude live generation (funded key); generation from a real image/video description; Phase 2 L1-L6 (server, Instagram post, TikTok inbox, unattended schedule, Telegram + Dropbox, engagement); Phase 3 L7-L10 (Fanvue connect, real post, inbox, links + digest); Lee to review the Terms wording ("sexually explicit content" vs Fanvue).

**Known minor items (deliberately left):** L2 sync dedupe vs moved files; L5 `/health` does not check schema version; L8 `?msg=` banner spoofing (behind Basic auth, escaped); L9 Instagram id after crash recovery (needs a live check); L11 top-up cap per run, not per day; login throttling (one IP behind Caddy); pruning of old metric rows.
