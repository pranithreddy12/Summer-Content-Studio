# Final audit, Phases 2 and 3 - 2026-11-10

Scope: the repository at `2620431` (master) plus the deployed layout described in `deploy/`. Builds on `AUDIT_PHASE2_2026-10-28.md`, `AUDIT_OVERALL_2026-11-07.md` and `AUDIT_PHASE3_2026-11-10.md`; this pass re-verifies the invariants and reviews everything changed since the overall audit (Instagram Unified Login, profile fan rules, Dropbox folder link, Fanvue race fixes). No real Instagram, TikTok, Fanvue, Dropbox or Anthropic call was made.

## Evidence
| Check | Result |
|---|---|
| Full test suite | **701 passed, 0 failed** (junit count). `test_security.py` repeated 6x: 0 failures. |
| App creates, CLI lists worker / sync-media / plan-week / refresh-tokens / notify-test | OK |
| Tracked secrets: `.env`, keys, DB files in git | none tracked; `.env` is git-ignored |
| Secret patterns (Anthropic, Meta, Telegram, private keys) in tracked files and in every commit on `origin/master` | none |
| Authorship on `origin/master` | 33 commits, all `pranithreddy12`; **0** `Co-Authored-By` trailers |
| Probe on a scratch DB (20 checks) | all pass: 9 pages/APIs return 401 without login; `/health` open; `/m/<forged>` refused; publish in `off` mode 409; fan sync outside live mode 409; unconfigured OAuth start and bad OAuth state give clean 4xx; missing file and path traversal give 404; `X-Frame-Options` and `nosniff` set |
| httpx request-URL logging silenced (tokens can sit in query strings) | yes (`app/logging_setup.py`) |
| Basic auth: constant-time compare; production needs a 12+ character password | yes |

## Invariants re-verified
- Nothing publishes without approval; the kill switch is `PUBLISHING_MODE=off` + restart.
- Fanvue: no `write:creator`; Fanvue-only media blocked at draft, approve, publish and random draft; replies only on a button press; PPV price range-checked; escalation before and after the AI; fan text not in logs or pushes.
- Instagram: images must be JPEG; `external_url` media is never publishable; token errors mark the account for reconnect.
- Dropbox link: fixed Dropbox hosts only, file names never used as paths (uuid names), size and magic-byte checks, disk guard, the refresh token never appears in an error message.

## Findings (none blocks go-live; none changed in code this pass)
| # | Sev | Finding | Recommendation |
|---|---|---|---|
| 1 | **Medium (action for you)** | The local development `.env` has a stray line near the top (line 5) with a pasted Instagram access token and a label, not in `KEY=value` form. python-dotenv warns it cannot parse it. It is not in git (verified), but it is a live-looking secret in plain text, and a malformed line can make settings below it silently ignored. | Delete that line. Treat the token as exposed and regenerate it in Meta if it was ever shared. Run the same parse check on the server `.env` (`python -c "from dotenv import dotenv_values; print(len(dotenv_values('/srv/summer/app/.env')))"` should print the number of settings with no warning). |
| 2 | Low | `summer-web`, `summer-worker` and the sync unit run as the unprivileged `summer` user but have no systemd hardening (`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem`). | Add them after testing on the server; `ProtectSystem=strict` needs `ReadWritePaths=/srv/summer`. |
| 3 | Low | The nightly backup copies the SQLite DB only, to the same disk. Uploaded media and `.env` (which holds `TOKEN_ENCRYPTION_KEY`) are not backed up. Losing the key means every platform must be reconnected. | Keep an offline copy of `.env` (already in the checklist); add an off-server copy of `/srv/summer/backups` (Dropbox via rclone is enough). |
| 4 | Low | `requirements.txt` uses `>=` ranges, so a rebuild can pull newer versions than the ones tested. | After the first good deploy run `pip freeze > requirements.lock` on the server and keep it in git. |
| 5 | Low | Dropbox link: setting only some of the four `DROPBOX_*` values silently leaves it off. The folder is re-listed on every pull (no cache), and a post stuck in `failed` keeps its pulled file until it is published or archived. | Set all four or none. Fine at this volume; add a listing cache only if the folder is very large. |
| 6 | Info | `test_dashboard_generate_rate_limited` failed once earlier under `-x`; it could not be reproduced in 6 targeted runs and 3 full runs. It restores `ANTHROPIC_API_KEY` at the end instead of in a `finally`, so a failure there can cascade into other tests. | Wrap the restore in `try/finally` if it ever recurs. |
| 7 | Info | `deploy/summer-media-sync.service` still says `dropbox:Summer`. Use either the rclone sync or the Dropbox folder link for the same folder, not both. | Replace the folder name before enabling the timer, or leave the timer off if the folder link is used. |

## Not provable offline (unchanged)
Every platform call shape (Instagram Unified Login, TikTok, Fanvue, Dropbox shared-link) is doc- or forum-derived until the live milestones; legal-page placeholders need Lee's answers; Fanvue H3-H9 answers; auto-send stays unbuilt by design.
