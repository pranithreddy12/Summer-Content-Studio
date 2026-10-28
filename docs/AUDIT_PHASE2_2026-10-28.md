# Phase 2 audit - 2026-10-28

Scope: build-complete criteria 1-13 of `PLAN_PHASE2.md` section 19, the security checklist (section 15), deploy files, docs and tests, at commit `e034ddb` + 3 small uncommitted test fixes.
Not in scope: live criteria 14-20 (need Lee's server and accounts). Nothing here proves anything against a real platform.

## Result

**Build-complete: met.** 521 tests pass. Two defects found in the test/proof tooling (not the app) and fixed; deploy artefacts and docs are consistent; no new application bugs.

## What was run

| Check | Result |
|---|---|
| Full suite | 519 pass, **2 fail** on first run -> both were date time-bombs in `tests/test_planner.py` (hard-coded `2026-10-26` week; the real clock passed it, `update_post` refuses past times and the API planner drops slots before "now + lead"). Fixed (dates moved to March 2027). 521 pass. |
| pyflakes (`app/`) | No undefined names; only unused imports (cosmetic). |
| bandit (`app/`) | 0 medium; 1 "high" = known false positive (prompt templates deliberately un-escaped, not HTML). |
| `requirements.lock` resolved for Linux / CPython 3.12 (Ubuntu 24.04), binary wheels only | Resolves cleanly (`pip install --dry-run`): the server will get the same versions. Note: the lock also contains `pytest`, `colorama`, `truststore` (dev/Windows extras; harmless). |
| `.env.example` vs `config.py` | Only `UPLOAD_RATE_LIMIT_PER_MINUTE` missing from `.env.example`. |
| Docs vs code | Every `python -m app.cli <cmd>` in the docs exists; every ALL_CAPS setting named in DEPLOY/PLATFORM_SETUP/README exists in `config.py`. |
| `deploy/` (Caddyfile, 5 units, setup + backup scripts) | Paths, users and env files agree with DEPLOY.md; `--proxy-headers` with `127.0.0.1` allow-list is correct behind Caddy. |
| Manual proof `dryrun_proof.py` | Passes after fix: 3 "would publish" attempts, zero HTTP, no status change, idempotent second loop. |
| Manual proof `kill_restart.py` | Passes after fix: killed mid-publish -> A: went public -> marked published, **0** extra publishes; B: not public -> **1** publish, no duplicate. |

## Findings

| # | Sev | Finding | Status |
|---|---|---|---|
| P1 | Medium | **Test time-bombs.** Two planner tests used absolute dates that expired on 2026-10-27, so the suite went red without any code change. Any test that mixes a fixed date with the real clock (`update_post`, API planner) will rot the same way. | fixed (2 tests); the rest use an injected `now` or write the DB directly |
| P2 | Low | **Both manual proofs depended on the wall clock.** With no seeded posting rule the hard 08:00-21:00 window applied, so run at 07:36 UTC they showed "0 due" (a false negative that looks like a bug). `kill_restart.py` also broke when `PUBLISHING_MODE=live` started requiring dashboard credentials (audit M14). | fixed in both scripts |
| P3 | Low | **Backups cover the database only.** `backup.sh` copies `app.db` (14 nightly copies, same disk). Uploaded media (`MEDIA_UPLOAD_DIR`) and `.env` (holds `TOKEN_ENCRYPTION_KEY`) are not in it; DigitalOcean droplet backups (weekly, DEPLOY.md) are the only protection for uploads, and a lost `.env` makes stored tokens unreadable (reconnect needed). Fine for launch if Lee's media lives in Dropbox; say so in the handover. | open (documented risk) |
| P4 | Low | **No SSH hardening in `server-setup.sh`** (no `PasswordAuthentication no`, no fail2ban). Key-only droplet creation covers most of it; add two lines if desired. | open |
| P5 | Low | In the "did not go public" recovery path the retry creates a **new** Instagram container instead of reusing the finished one (wasteful, not unsafe: still one public post). | open, accepted |
| P6 | Info | Still outstanding from the earlier audit: login throttling, metric-row pruning, Low items L1-L11 (`docs/AUDIT_2026-10-24.md`). | open |

## Definition of Done, section A (build-complete)

| # | Criterion | Verdict |
|---|---|---|
| 1 | Phase-1 data upgrades without loss | met (`test_migrations.py`, `test_app.py`) |
| 2 | Planner: pattern, mirroring, window, never approves, idempotent | met (`test_planner.py`; min-gap bug fixed in the 10-24 pass) |
| 3 | Window hard everywhere | met (`test_window.py`, scheduler/publishing tests) |
| 4 | Reminder mode | met (mocked push; live ntfy push not re-run in this audit) |
| 5 | Dry run: zero platform calls | met (tests + `dryrun_proof.py` re-run today) |
| 6 | Notifications: dedupe, no secrets, 3 providers (mocked) | met (`test_notifications.py`, `test_hardening.py` leak scan) |
| 7 | Platform modules vs documented APIs; unverified listed | met on paper; **unverified until a real account** (`PLATFORM_API_NOTES.md`) |
| 8 | Accounts/OAuth secure, tokens never exposed | met (`test_accounts.py`, leak scan) |
| 9 | No double publish on crash/concurrency | met (`kill_restart.py` re-run today, 4-worker probe, H4 fix) |
| 10 | Human approval for every publish and reply | met (tests; CSRF now covers the API too) |
| 11 | IG engagement (mocked) | met (`test_engagement.py`) |
| 12 | Dashboard on a phone viewport | met earlier (375 px check recorded); templates changed since (time display only), not re-checked in a browser |
| 13 | Docs present | met |

## Before sending to Lee

1. Commit the 3 test/proof fixes; rebuild and re-verify the delivery zip (it predates the audit fixes).
2. Re-check one dashboard page at 375 px if you want criterion 12 re-proven after the template edits.
3. Everything else is the live ladder in `docs/SETUP_PROMPT.md`.
