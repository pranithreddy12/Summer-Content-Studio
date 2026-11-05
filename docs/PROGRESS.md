# Build Progress Log

## M1 — Foundation ✅ Complete (2026-10-15)

**Files created:**
- Project setup: `requirements.txt`, `requirements-dev.txt`, `.gitignore`, `.env.example`, `pytest.ini`, `README.md`
- Core: `app/__init__.py`, `app/config.py`, `app/db.py`, `app/models.py`, `app/schemas.py`, `app/logging_setup.py`, `app/security.py`, `app/main.py`
- Seed & CLI: `app/seed.py`, `app/seed/summer.json`, `app/cli.py`
- API: `app/api/__init__.py`, `app/api/health.py`, `app/api/profiles.py`, `app/api/tags.py`, `app/api/dependencies.py`
- Services: `app/services/__init__.py`
- Prompts: `app/prompts/__init__.py`, `app/prompts/platforms.py`, `app/prompts/system_v1.j2`, `app/prompts/user_v1.j2`
- Web: `app/web/__init__.py`
- Migrations: `alembic.ini`, `migrations/` (env.py, `0001_initial`)
- Tests: `tests/conftest.py`, `tests/test_app.py`, `tests/test_profiles.py`

**Features done:**
- Config with pydantic-settings, SecretStr for API key
- SQLite with UTCDateTime, foreign keys pragma
- All ORM models + enums + JSON-as-Text fields with Pydantic deserialization
- JSON logging formatter
- FastAPI app factory with error handlers (ErrorOut format for HTTPException)
- Alembic migration 0001_initial (import fix: `from app.db import UTCDateTime`)
- Idempotent seeding (6 categories + Summer profile)
- `python -m app.cli init-db` works twice
- `/health` returns `{status, database, claude_configured, version}`
- Profiles API (list/get/patch with validation)
- Tags API (category filter)
- Basic auth + origin check + rate limiter scaffolding in security.py
- Prompt templates (system_v1.j2, user_v1.j2) + platforms.py

**Test results:** `python -m pytest tests/test_app.py tests/test_profiles.py` → **14 passed**

**Known issues:**
- Port 8000 blocked on this machine (use 8001 for manual testing)
- Alembic logging warning "name 'app' is not defined" in cli.py (non-blocking, migration succeeds)
- FastAPI 422 deprecation warning (HTTP_422_UNPROCESSABLE_ENTITY → _CONTENT)

**Next:** M2 — AI Service (test_claude_client.py, test_generation.py)

---

## M2 — AI Service

**Files created:**
- `app/services/claude_client.py` — GenerationError, ClaudeResult, ClaudeClient with full error mapping
- `app/services/generation.py` — check_output, generate_draft, regenerate
- `app/services/posts.py` — CRUD, transitions, variants, stats
- `app/services/assets.py` — upload, register, sync, filters, archive, file resolution
- `app/api/content.py` — generate/regenerate routes (placeholder → to be wired)
- `app/api/assets.py`, `app/api/posts.py` — route stubs
- `app/api/dependencies.py` — get_claude_client singleton

**M2 completion (2026-10-15):**
- Fixed JSON storage bug: `Text`-stored JSON columns (`handles`, `communication_rules`, `restricted_topics`, `hashtags`, `generation_input`) caused `sqlite3.ProgrammingError: dict not supported` and made Jinja iterate over JSON *strings* character-by-character in prompts. All switched to native SQLAlchemy `JSON` (migration `6fe077f2856a_0002_json_columns`); removed all manual `json.dumps` from seed.py, api/profiles.py, services/generation.py, services/posts.py.
- Added `ContentPost.variant_count` property (PostOut serialization).
- Fixed `app/cli.py init-db` to run Alembic first (create_all+stamp only as legacy fallback); dev DB stamped and migrated.
- Fixed test pollution: in-memory rate-limiter store is now cleared per test in conftest.
- **Test result: `python -m pytest` → 55 passed.** `init-db` runs twice idempotently; `alembic check` → "No new upgrade operations detected."

---

## M3 — Assets
**M3 completion (2026-10-15):**
- Implemented real `app/api/assets.py`: POST register (external_url/local_sync), POST upload (multipart, 1MB chunk streaming, magic-byte check, sha256 dedupe, server-generated filenames), POST sync, GET list with filters (q/tag-AND/media_type/storage_type/unused/include_inactive/limit≤100/offset), GET {id}, GET {id}/file (FileResponse via `resolve_asset_file` containment), PATCH, DELETE (archive).
- `AssetOut` now carries derived `preview_url` + `usage_count`.
- Tests: `tests/test_assets.py` (26 tests). Fixtures: `tests/fixtures/tiny.png`, `tiny.gif`, `fake.exe` (real 1×1 PNG/GIF bytes; distinct bytes for dedupe testing).
- Deviation found: identical uploads correctly dedupe by sha256 → initial `two_assets` fixture had to use different image bytes.
- Test result: `python -m pytest tests/test_assets.py` → 26 passed.

## M4 — Content queue ✅ Complete (2026-10-15)
- Introduced `AppError(code, message, status)` in `app/services/__init__.py`; all `services/posts.py` errors structured; handler registered in `app/main.py` → ErrorOut JSON shape.
- Fixed `create_post` bug: referenced non-existent `req.source` (PostCreate has no source field); manual posts now hard-set `source=manual`, `generation_input=None`.
- Implemented exact §11 PATCH transition rules: planned time on approved → scheduled; null on scheduled → approved; past date → 422 `validation_error`; content edits reset approved/scheduled → draft with `approved_at=None`; archived/published → 409 `invalid_transition`.
- `api/posts.py` rewritten: AppError propagated; added `planned_from/planned_to` filters (timezone-aware enforced).
- Tests: `tests/test_posts.py` 27 tests incl. restart-persistence (engine disposed, app rebuilt on same DB file), no-endpoint-yields-`published`.
- Full suite: **108 passed**.

## M5 — Dashboard ✅ Complete (2026-10-15)
- Fixed middleware: rewritten inline in `main.py` as `auth_and_csrf_middleware` (constant-time basic auth via `secrets.compare_digest`, WWW-Authenticate header, /health exempt) + origin/referer check for non-/api unsafe methods → 403.
- Fixed dashboard template rendering: starlette 1.8 removed the old `TemplateResponse(name, dict)` signature → added `_template_response()` wrapper (new signature), sed-migrated 12 call sites.
- Fixed generate/regenerate dashboard routes to inject the Claude client via `Depends(get_claude_client)` — previously they read `dependency_overrides` manually and silently fell back to the **real Anthropic API** during tests (one real 401 call happened before this fix; root cause recorded).
- generate error re-render now includes `platforms`/`content_types` context (template UndefinedError fix).
- post_detail banner now also shows the "NOT published automatically" notice on drafts.
- Tests: `tests/test_dashboard.py` 16 tests (all pages 200 with real data, generate POST → 303 to post page, missing-key error page, asset-new POST, static CSS, profile edit round-trip); `tests/test_security.py` 13 tests (key-leak scan on /health/profiles/500/validation errors, no traceback on 500, `safe_relpath` rejections, `resolve_asset_file` containment 404, `.exe` upload 415, basic auth 401/200//health-open, cross-origin POST 403).
- Full suite: **137 passed**.

## M6 — Validation & handover ✅ Complete (2026-10-15)
- Live smoke test with real uvicorn server (port 8001): `/health` 200 `{"status":"ok","database":"ok","claude_configured":true,"version":"0.1.0"}`, dashboard `/`, `/posts`, `/api/v1/profiles`, `/static/style.css` all 200. (Port 8000 remains blocked on this machine.)
- `git init` + initial commit (M1 had skipped it; required for secret scan).
- `pip freeze > requirements.lock` (alembic 1.20.0, anthropic 1.14.0, fastapi, sqlalchemy, etc.).
- Secret scan: `grep -ri sk-ant` over code/docs → only `.env.example` placeholder (`sk-ant-REPLACE_ME`) and PLAN/test-fixture text. No `.env` committed.
- Docs written: `docs/DEMO.md` (Halloween walkthrough), `docs/DECISIONS.md` (plan decisions + 12 build deviations + honest limitations), `docs/REFERENCE_RESEARCH.md` (no-scraping statement + human-fillable style-notes template).
- Full final suite: **137 passed** (`python -m pytest`).
- External blockers still open: real Anthropic credits for live generation smoke (criteria 5–7), real Halloween assets in the Dropbox folder (demo uses whatever is provided — descriptions written by humans only).

## Frontend redesign � dark editorial + sidebar (2026-10-16)

Full visual/structural redesign of the dashboard. No framework, no CDN, no new dependency � still hand-written CSS + tiny inline JS. Routes, form fields, ids, POST targets, PRG flow, flash handling and the signed-origin/CSRF and Basic-auth middleware are all unchanged, so the security posture (PLAN 14) holds.

Files rewritten:
- `app/web/static/style.css` � new design system (~850 lines): dark warm-charcoal surfaces, ember/amber accent, serif display headings (system `ui-serif`), CSS-grid shell, status/badge/chip/button/form/table/asset-grid/detail components, responsive breakpoints (1080 / 900). Replaces the old light warm palette.
- `app/web/templates/base.html` � sidebar shell: brand mark, section nav (Overview/Media/Generate/Queue/Profiles) with inline SVG icons + active-state detection via `request.url.path.startswith(...)`, Phase-1 footnote, footer. Flash include and `{% block scripts %}` preserved.
- All page templates restyled to a `page-head` (eyebrow + serif h1 + lede + action buttons) pattern: `overview`, `assets`, `asset_new`, `asset_detail`, `generate`, `posts`, `post_detail`, `profile`. Existing copy relied on by tests kept verbatim (e.g. "Claude only knows what you write here", "NOT published automatically", "Use this version", "Upload", status badges).
- JS unchanged in behavior: content-type filter + submit-disable (generate), tab switch (asset-new), live char count (post detail), tag-suggest chips (asset detail), confirm() on archive.

Verified: full suite **156 passed**. Live smoke on uvicorn (port 8001): `/`, `/assets`, `/assets/new`, `/assets/{id}`, `/generate`, `/posts`, `/posts/{id}`, `/profiles`, `/profiles/{id}`, `/static/style.css` all render; desktop (1440) + mobile (760) checked via Playwright screenshots. Fixed one responsive nit (grid `min-height` stretch left dead space under the collapsed sidebar � resolved with `align-content:start` + `min-height:0` on the 900px breakpoint).

## Fix - dashboard MP4 upload rejected (2026-10-18)

Report: uploading an MP4 at `/assets/new` returned `{"error":{"code":"validation_error","message":"body.file: Input should be a valid bytes"}}`.

Cause: `POST /assets/new` (`app/web/routes.py`) declared the uploaded file as `file: Optional[bytes] = Form(None)`. FastAPI cannot decode a multipart file part into `bytes`, so request validation failed before the handler ran. The API endpoint `POST /api/v1/assets/upload` already used `UploadFile` and worked for MP4.

Fix:
- `app/web/routes.py` - `asset_create` now takes `file: Optional[UploadFile] = File(None)` and the service receives the `UploadFile`; also re-render the generate form with a human-readable message instead of the raw error dict.
- `tests/test_assets.py` - added `test_dashboard_upload_mp4_via_form` (303 -> `/assets/{id}`, `media_type=video`, `mime_type=video/mp4`, `storage_type=upload`) and `test_dashboard_upload_png_via_form`.

Verified:
- `python -m pytest` -> 158 passed, exit 0.
- Live uvicorn on a scratch port with a scratch DB: `POST /assets/new` with an MP4 -> `303 /assets/1?msg=Asset uploaded successfully&kind=ok`, detail page 200, `media_assets` row `('clip.mp4', 'video', 'video/mp4', 'upload', 96 bytes)`.

Known limitation: the long-running dev server on port `127.0.0.1:8000` still serves the pre-fix module (its POST still returns the old 422). It must be restarted to pick up the fix; the socket is held by PID 31176, which `taskkill` reports as no longer existing, so a reboot may be needed to free port 8000 (port 8001+ are free).

## Phase 2 — B0 Close-out ✅ Complete (2026-10-21)
- Baseline recorded: `python -m pytest` → **193 passed** (matches PLAN_PHASE2 §0 exactly).
- Outstanding ~46 uncommitted files committed in 4 chunks: (1) AI provider router + vision input (`a556823`), (2) random drafts + asset delete (`6e00bba`), (3) dashboard redesign (`8242a0e`), (4) Phase 2 plan v3 + docs (`8b7397f`).
- Added `dist/` to `.gitignore`.
- `git status` clean. Post-handover work is now in the repo; Phase 2 build starts from these commits.

**Next:** B1 — Foundations (cryptography, PUBLISHING_MODE config, WAL, migration 0003, window service, mark-posted).

## Phase 2 — B1 Foundations ✅ Complete (2026-10-21)
**Files changed:** `requirements.txt` (+cryptography 50.0.2), `requirements.lock`, `.env.example`, `app/config.py` (all §5 Phase-2 settings, validators, `publishing_mode()` helper, `notify_configured`, `live_publishing_ready`), `app/db.py` (WAL + busy_timeout), `app/models.py` (+`publishing`/`sent_to_drafts` statuses, `AccountStatus`, 8 new tables, 13 new content_posts columns), `migrations/versions/96d2f6d84905_0003_phase2_publishing.py` (hand-reviewed autogenerate; `app.db.UTCDateTime` NameError fixed with `from app.db import UTCDateTime`; FK named `fk_content_posts_platform_account`; `publish_attempt_count` server_default '0'; posting_rules seeded inactive for Summer × instagram/tiktok), `app/services/window.py` (NEW: `in_window`/`next_window_start`/`clamp_to_window`, end inclusive, DST-safe), `app/services/publishing.py` (NEW B1 slice: `get_rule`, `connected_account`, `asset_is_publishable`, `preflight_reasons`, `check_publishable`, `render_caption`), `app/services/posts.py` (Phase-2 TRANSITIONS incl. `mark_posted`/`plan`/`claim`... + `mark_posted()`; failed is archivable; publishing untouchable; edit failed → draft; user planned time sets `planned_by='user'`), `app/schemas.py` (+`MarkPostedRequest`), `app/api/posts.py` (+POST `/{id}/mark-posted`), `app/main.py` (/health adds `publishing_mode`, `worker_last_seen`, `worker_ok`), `app/web/routes.py` (+POST `/posts/{id}/posted`), `app/web/templates/post_detail.html` (manual-post panel: full caption text box + Copy (clipboard JS) / Download media / Mark as posted form).
**Test results:** new files `tests/test_window.py` (14), `tests/test_migrations.py` (2: empty-DB upgrade + Phase-1 DB upgrade without loss), `tests/test_publishing.py` (B1 slice: mark_posted flows, pre-flight, window boundary 21:00:00/21:00:01, external_url → media_not_publishable, caption_too_long, dashboard panel + route). Full suite: **228 passed**.
**Verified:** `alembic upgrade head` on dev DB clean; `alembic check` → "No new upgrade operations detected"; dev DB posting_rules seeded inactive (instagram/tiktok, 08:00–21:00, Europe/London).
**Known issues:** DST note — 25 Oct 2026 08:00 local is already GMT (08:00 UTC); window boundary tests adjusted accordingly.

**Next:** B2 — Planner (services/planner.py property tests, posting_rules API, /planner + /calendar, mirroring).

## Phase 2 — B2 Planner ✅ Complete (2026-10-21)
**Files created:** `app/services/planner.py` (`make_slots` pure property-tested slot generator: 7 days, day counts = min_per_day + remainder on distinct random days, minute precision in-window, gap retries (max 50, evenly spaced fallback), lead-time drop, DST-safe; `plan_week` idempotent: day targets reduced by already-scheduled/published, oldest approved first, group_key mirroring to same local day, auto_topup drafts-only capped, slots_unfilled push deduped per day; `validate_rule_values` 422 rules), `app/services/notifications.py` (notify() never raises; dedupe by unique event_key; providers telegram/ntfy/pushover/none; secrets never in message bodies), `app/api/planner.py` (GET/PUT rules validated, POST /plan, GET /week), `app/web/templates/planner.html` + `calendar.html` (server-rendered grids, planner times marked, out-of-window flagged, TikTok drafts note), `app/platforms/__init__.py` (PublishResult/TokenSet/PlatformError/http_client), staff nav items.
**Files changed:** `app/services/random_draft.py` (+`both_platforms`: video → Instagram+TikTok pair sharing group_key, images → Instagram only, drafts only), `app/schemas.py`(+ both_platforms, PostOut Phase-2 fields incl. group_key), `app/cli.py` (+`plan-week` command), `app/main.py` (+planner router), `app/web/routes.py` (+/planner, /planner/rules/{id}, /planner/plan PRG, /calendar; queue filter adds publishing/sent_to_drafts), `generate.html` (+"Create Instagram + TikTok pair" card), `style.css` (planner/calendar grids, phone breakpoints), `app/db.py` make_engine gained poolclass arg (tests use NullPool vs SQLite pool exhaustion), `tests/conftest.py` (+tracking sessionmaker: every session closed at teardown).
**Test results:** `tests/test_planner.py` (12: property tests over 25 seeds incl. Lee's exact pattern “exactly 3 days with 2”, DST week 23–29 Oct, seed determinism, lead drop, validation 422 cases, idempotency, oldest-first, user-planned untouched, group mirroring same local day, pushes deduped), `tests/test_notifications.py` (6: provider request shapes via MockTransport, none-provider, duplicate-key dedupe, failure never raises, URL only with PUBLIC_BASE_URL), `tests/test_dashboard.py` +6 (planner/calendar pages, Plan-button PRG flow incl. second run “0 slots filled”, both_platforms pair). Full suite: **255 passed**.
**Verified:** “Plan next 7 days” test flow on 10 approved posts → exactly 10 scheduled, all in-window; second run idempotent.

**Next:** B3 — notifications wiring into worker loop + reminder mode (post_due pushes), minimal cli worker. (Note: notifications service + several worker pieces landed early in B1/B2 commits; B3 adds the scheduler skeleton and reminder wiring.)

## Phase 2 — B3 slice + validation fixes (2026-10-22)
Validation of B1–B3 found: B3 working-tree broke overview/post-detail (`settings` missing from template context; 6 failing tests), mirroring did not hold (7/10 IG/TikTok pairs on different days), re-planning scheduled 11 posts/week, ~0.5% of slots at 20:59–21:00, failed pushes were never retried and reminders were marked sent without a push, worker banner inverted + dead `/cli` link, post-detail banner said "NOT published automatically in live mode".
**Fixed:** `settings` injected in `_template_response`; shared `scheduler.worker_status` (used by `/health` + overview); planner: shared per-week day pattern, sibling-day pinning, weekly budget cap, 15-min window end margin; `notify()` retries failed keys; reminders mark `reminded_at` only after a successful push, carry the full caption, `cli worker` refuses `reminders` mode without a push channel; blank credential env = unset; conftest blanks mode/credentials.
**Tests:** `tests/test_phase2_regressions.py` (9) — 5 of them fail on commit c44791b, all pass now. Full suite: **264 passed**.
**Still unverified:** 375 px phone layout (no evidence recorded for B2 pages); live ntfy push; `cli worker` loop never run end-to-end. PLAN_PHASE2.md patched to v3.1.


## Phase 2 — B4 Platform modules + API notes (2026-10-23)
**Rule 10 done first:** `docs/PLATFORM_API_NOTES.md` — Instagram (Instagram Login, host `graph.instagram.com`, v26.0) VERIFIED-DOC from the official content-publishing, business-login and comment-moderation pages. TikTok pages returned HTTP 503 on the day, so chunk/status details are SECONDARY (search excerpts of the official pages) and the OAuth v2 token/refresh/user-info calls are UNVERIFIED; every unverified item is in the live checklist at the bottom of the notes (L2/L3).
**Files:** `app/platforms/__init__.py` (+`send` transport→PlatformError, `json_or_empty`, `callback_url`, lazy `publisher()`), `app/platforms/instagram.py` (authorize / code→short→60-day token / refresh / container→poll→publish / check_status / comments / metrics / reply), `app/platforms/tiktok.py` (authorize / token / refresh / chunked inbox upload / status poll / mock-only direct mode with creator_info + privacy + is_aigc / unsupported metrics), `app/services/publishing.py` (pre-flight: Instagram images must be JPEG, TikTok video only), `app/logging_setup.py` (httpx INFO URL logging silenced — tokens ride in query strings), `PLAN_PHASE2.md` (§7 signatures gain `external_user_id`, `bad_request` code).
**Findings from the docs that change behaviour:** Instagram images must be JPEG (PNG/WebP rejected pre-flight); docs say poll status once a minute for ≤ 5 minutes (not 10 s); the guide contradicts itself on the daily cap (100 vs 50); TikTok chunk count is floor(size/chunk) with the remainder in the final chunk and a 1000-chunk cap (bug found by a test: >10 GB files now use 64 MB chunks).
**Tests:** `test_platform_instagram.py` (30), `test_platform_tiktok.py` (25), `test_platform_media_rules.py` (8); existing publishing fixtures switched to a JPEG asset. Full suite: **327 passed** (baseline before B4: 264).
**Known issues / not verified:** nothing here has touched a real platform; module-level `_sleep` polling blocks the worker up to 5 min per IG publish; `check_status` for Instagram returns the container id (no permalink).
**Next:** B5 — accounts/OAuth + publishing service (dry run), signed media route.


## Phase 2 — B5 Accounts/OAuth + publishing service (2026-10-23)
**Files created:** `app/services/accounts.py` (Fernet encrypt/decrypt, start/complete OAuth with single-use 10-min state, upsert per profile+platform, `get_valid_token` with 5-min refresh skew, `mark_attention` + once-a-day push, `disconnect`, `sanitize`), `app/services/media_urls.py` (HMAC-signed 1 h tokens), `app/api/accounts.py` (+ `/oauth/{platform}/start|callback`), `app/api/media_public.py` (`GET|HEAD /m/{token}`), `app/api/publishing.py` (publish / retry / attempts), `app/web/templates/accounts.html`, tests `test_accounts.py` (23), `test_media_public.py` (10), `test_publish_live.py` (26).
**Files changed:** `app/services/publishing.py` (`publish_post`: modes, atomic claim, attempt rows, token refresh, platform call with `on_ref`, success/inbox/retry/fail, backoff 5/15/60 min clamped into the window, sanitized errors, `retry_post`; pre-flight account check only in live), `app/services/scheduler.py` (`due_publish_posts`, dry_run/live dispatch, one bad post never stops the loop), `app/main.py` (routers, `/m/` auth exemption), `app/web/routes.py` + `post_detail.html` + `base.html` (accounts page, publish panel with pre-flight reasons, attempts table, Publish/Retry forms, nav), `app/cli.py` (`refresh-tokens`), `app/schemas.py`, `app/api/dependencies.py` (`get_platform_http`), `app/logging_setup.py` (secret-fragment redaction), `tests/conftest.py` (`env`, `phase2` fixtures).
**Test results:** full suite **394 passed** (B4 baseline 327).
**Proof run (real CLI, scratch DB, `httpx.Client.send` patched to abort):** `PUBLISHING_MODE=dry_run python -m app.cli worker --once` on 3 due reels -> `dry_runs: 3`, 3 attempts (`trigger=dry_run`, `success=NULL`, "would publish reel to instagram (26 chars, media 'c0.mp4')"), posts stay `scheduled` with `publish_attempt_count=0`, zero HTTP; a second loop adds no rows.
**Not verified:** nothing has touched a real platform or a real browser session (OAuth callback is proven against MockTransport only); the accounts page and publish panel were not viewed at 375 px; `PUBLISHING_MODE=live` worker loop has not run for real.
**Next:** B6 - full worker (stuck-claim recovery, token refresh, planner tick, `worker_ok`), B7 engagement, B8 docs/hardening.


## Phase 2 — B6 Worker complete (2026-10-23)
**Files changed:** `app/services/scheduler.py` (rewritten: isolated stages, `recover_stuck`, `refresh_due_tokens`, `planner_tick`, `reminders`, `publish_due`, `run_forever`), `app/services/publishing.py` (`_apply_success` extracted, `recover_stuck_claim`, `fail_preflight`, `PERSISTENT_PREFLIGHT`, `STUCK_AFTER`), `app/services/posts.py` (`preflight_fail` transitions), `app/cli.py` (worker uses `run_forever` + signal handlers + one shared httpx client), `tests/test_scheduler.py` (34), `tests/manual/kill_restart.py`, `tests/manual/dryrun_proof.py`, PLAN_PHASE2.md v3.3.
**Test results:** full suite **428 passed** (B5 baseline 394).
**Proof run — kill and restart (real worker processes, fake Instagram server, `Popen.kill()` mid-publish; `tests/manual/kill_restart.py`):**
- A (platform had published before the crash): restart → recovery asks the platform, finds the container `PUBLISHED` → post `published`, **0 new container, 0 media_publish calls** (one attempt row, success).
- B (platform had NOT published): restart → recovery re-queues (attempt 1 `timeout: interrupted`, ref `C1` kept) → retry creates container 2 → exactly **1** media_publish → post `published`. No post lost, none published twice.
**Defect found while building:** a live post with no connected account (or other persistent pre-flight problem) was skipped silently every minute forever — now fails visibly with a push and can be retried.
**Not verified:** a real platform, SIGTERM on Linux (Windows tested only: `Popen.kill`/`terminate`; the signal handler path is unit-covered via the stop event, not by delivering a signal), accounts/publish pages at 375 px.
**Next:** B7 - Instagram engagement (metrics, comments, reply drafts), B8 - docs/hardening/handover.


## Phase 2 — B7 Instagram engagement (2026-10-23)
**Files created:** `app/services/engagement.py` (sync of metrics + comments, `draft_reply`, `send_reply`, `ignore_comment`, totals), `app/api/engagement.py` (`GET /posts/{id}/metrics`, `GET /comments`, `POST /comments/{id}/draft-reply|send-reply|ignore`, `POST /engagement/sync`), `app/prompts/reply_v1.j2` + `render_reply_system/user`, `app/web/templates/engagement.html`, `tests/test_engagement.py` (34).
**Files changed:** `app/services/scheduler.py` (live-only `engagement_stage`, `engagement_at` carried in the heartbeat row), `app/web/routes.py` (`/engagement` + draft/send/ignore/sync forms; overview unanswered count; post-detail metrics), `app/web/templates/{base,overview,post_detail}.html`, `app/schemas.py`, `app/main.py`, `app/web/static/style.css` (layout fix).
**Test results:** full suite **462 passed** (B6 baseline 428). Key behaviours covered: dedupe, own comments skipped, only recent API-published Instagram posts tracked, one failing post never stops the others (no partial rows), hourly digest without secrets, prompt isolation of the untrusted comment, draft trimmed/declined/LLM-error paths and log rows, single send (second send 409, failed send restores status, rejected token expires the account), live-mode gating, API validation (422/404/409), dashboard flows, worker cadence (every 6 min, live only, failure contained).
**Visual check (375 px phone viewport, real browser pane, scratch DB with a published post, 3 comments, a failed post, a connected account):** engagement, accounts, overview, post detail (failed + published + scheduled), planner, calendar, queue, assets, generate, profiles — all 12 pages measured with `scrollWidth`. **Found and fixed a real defect:** the overview page was 641 px wide on a 375 px screen (`.shell` grid column `1fr` let a table stretch the page; now `minmax(0, 1fr)`); after the fix all 12 pages are exactly 375 px wide. Engagement cards, accounts cards and the publish panel were looked at in screenshots and read well; Retry / Send / Draft / Ignore buttons are full-size tap targets.
**Not verified:** a real Instagram comment/reply (mocked only; fields `username`, `like_count`, `comments_count` are UNVERIFIED in PLATFORM_API_NOTES), real LLM reply quality (fake LLM in tests; run a few real drafts in L6), the footer still says "Phase 1".
**Next:** B8 - docs (PLATFORM_SETUP, DEPLOY, DEMO_PHASE2, README), security checklist with evidence, production Basic-auth guard, secret scan, delivery zip.


## Phase 2 - B8 Docs, hardening, handover (2026-10-23)
**Code:** `Settings.production_problems()` + start-up refusal and `openapi_url` off in production (`app/config.py`, `app/main.py`); `ensure_free_disk` in `sync_media_dir` + CLI handling of `AppError` (`app/services/assets.py`, `app/cli.py`); mode-aware sidebar/footer and overview badge; `/m/{token}` hidden from the OpenAPI schema (duplicate operation id warning); version 0.2.0; `tests/conftest.py` sets `MIN_FREE_DISK_GB=0`.
**Docs:** `docs/PLATFORM_SETUP.md`, `docs/DEPLOY.md`, `docs/DEMO_PHASE2.md`, `docs/SECURITY_CHECKLIST.md`, README (modes, rules, planner, notifications, costs, commands, limitations), `docs/PLATFORM_API_NOTES.md` (Instagram `/me` + `account_type` now VERIFIED-DOC; rclone notes), PLAN_PHASE2.md v3.5. Instagram's get-started page was re-read for the setup guide; TikTok's and rclone's `copy` semantics could not be confirmed (marked).
**Tests:** `tests/test_hardening.py` (15): production guard (5), disk guard + sync safety (6), planner never approves (+top-up drafts only), end-to-end secret-leak scan, footer. **Mutation check:** disabling `sanitize()` makes the leak scan fail on "'LONG-TOKEN-SECRET' leaked into responses". Full suite **477 passed** (B7 baseline 462).
**Defects found while testing the tests:** the first version of the leak scan read the wrong DB file when an earlier test had changed `DATABASE_URL` (it passed vacuously in the full run, caught because the dump was empty) - it now dumps through the app's own engine.
**Scans (2026-10-23):** `git grep sk-ant` -> `.env.example` placeholder + PLAN text only; no Telegram-token-shaped strings; no `.env`/`*.db`/`rclone.conf` tracked; nothing in history. `requirements.lock` equals `pip freeze`.
**Deliverables:** `dist/summer-ai-content-phase2-v0.2.0.zip` (extracted to a temp folder, full suite run there, secret-scanned) and `dist/Summer-AI-Phase2-Build-Report.html`.
**Not done / honest gaps:** no real platform was touched; no dependency vulnerability scan; SIGTERM only exercised on Windows; Telegram, Claude replies, rclone read-only scopes, Instagram Creator publishing and TikTok app review are all live (L-milestone) checks.
**Next:** L0-L6 (needs Lee: server, Meta/TikTok apps, Telegram, Dropbox, Anthropic key).


## Phase 2 - B8 follow-up: deploy/ reconciliation (2026-10-23)
A `deploy/` folder (server-setup.sh, Caddyfile, systemd units/timers, backup.sh) and a rewritten `docs/DEPLOY.md` (paths under `/srv/summer`, itsmesummer.app) appeared in the working tree while B8 was being built; my own DEPLOY.md was overwritten before commit 78eec42, so that commit contains the other version. Reviewed side by side: the two agree (paths, units, ports, `--workers 1 --proxy-headers`, timers, backup, `TimeoutStopSec=330`).
**Fixed:**
- `.gitattributes` forces LF for `*.sh`, `*.service`, `*.timer`, `Caddyfile` and `deploy/*` (with `core.autocrlf=true` a checkout would otherwise produce CRLF and break the scripts on Linux). `deploy/` is now committed and part of the delivery zip.
- **`requirements.lock` was not installable anywhere else.** `pip freeze` had written `jiter` and `pydantic_core` as `name @ file:///C:/Users/...win_amd64.whl` (Windows-only local wheels, Python 3.14). Replaced by `jiter==0.17.0` and `pydantic-core==2.50.1` and re-checked with `pip install --dry-run --python-version 3.12 --platform manylinux2014_x86_64 --only-binary=:all:` (Ubuntu 24.04): all 47 pins resolve to Linux wheels. (My earlier note "requirements.lock equals pip freeze" was true but the freeze itself was non-portable.)
- `docs/DEPLOY.md`: step 5 and the update step install from `requirements.lock`; the Dropbox section says the folder must be in Lee's own Dropbox (a folder only shared with him must be added first) and qualifies the read-only scope claim as unconfirmed; section 13 adds a troubleshooting table.
- `tests/manual/build_delivery_zip.py` (new, reproducible) builds the zip and fails if the lock has a machine-local path, `deploy/` is missing or CRLF, a real `.env` secret or forbidden file is inside; then runs the suite from an extracted copy.
**Verified:** zip `summer-ai-content-phase2-v0.2.0.zip` (139 files): lock portable, deploy/ present and LF, real `.env` secrets not found, forbidden names none, key-shaped strings none, **477 passed** from the extracted copy.
**Still open (from the review, not requested):** `/privacy` and `/terms` pages for the Meta/TikTok developer apps are not built and the app's login would block them (serve them from Caddy instead); `Restart=on-failure` loops on a config error; `backup.service` depends on the script's executable bit; step 5's `tar` also ships internal files (CLAUDE.md, PROGRESS.md); `curl | sudo bash` for rclone; `deploy` user has passwordless sudo.


## Phase 2 - public Privacy / Terms pages for the developer apps (2026-10-24)
Meta and TikTok developer apps ask for a Privacy Policy URL, a Terms of Service URL and (Meta) data-deletion instructions; the app's login would block them, so they are static files served by Caddy on the apex domain.
**Added:** `deploy/site/{index,privacy,terms}.html` (plain-language DRAFT text that matches what the app really does: data handled, AI providers, hosting/backups, deletion by e-mail, `#data-deletion` anchor; no scripts, no external resources, noindex, responsive, light/dark); the apex block of `deploy/Caddyfile` now serves `/var/www/summer-site` (`try_files {path}.html {path}`, `file_server`, nosniff + strict CSP, no reverse proxy, no login) while `studio.` stays the app; `.gitattributes` covers `deploy/**`; `docs/DEPLOY.md` step 5 copies the pages and section 12 explains URLs to enter in each portal, the five placeholders (`[[OPERATOR NAME]]`, `[[CONTACT EMAIL]]`, `[[DATE]]`, `[[DELETION DAYS]]`, `[[GOVERNING LAW]]`), the pre-publish check (`grep -n '\[\[' ...` must print nothing) and how to update; troubleshooting row; `docs/PLATFORM_SETUP.md` tells Lee which URLs to enter (field names unconfirmed for TikTok).
**Tests:** `tests/test_site_pages.py` (14): well-formed HTML, no JS/forms/external loads, noindex + responsive, cross links and the deletion anchor, placeholders exactly the documented five (and each explained in DEPLOY.md), every processor the app talks to is named in the policy, the policy's "14 days" matches `backup.sh`, Caddyfile blocks (apex static with no proxy/auth, studio proxies the app), docs agree with Caddyfile, LF endings, the app itself serves no `/privacy` or `/terms`.
**Checked in a browser:** all three pages are exactly 375 px wide at phone width; the placeholders are visible on purpose.
**Not verified:** Caddy itself was not run (not installed here; the config is only checked structurally by the tests), so the first real check is `curl -sI https://itsmesummer.app/terms` on the server. The texts are **drafts, not legal advice**: Lee must fill the placeholders and have them reviewed before submitting either developer app; each portal's exact field names are unconfirmed.


## Audit fixes - Highs and Mediums (2026-10-24, uncommitted until reviewed)
Source: `docs/AUDIT_2026-10-24.md`. Every fix has a regression test in `tests/test_audit_fixes.py` (30 tests); full suite: 521 passed.
**High:** H1 profile edit stores lists again (`routes.py`); H2 dashboard Save sends only changed fields, times are shown/parsed in `APP_TIMEZONE` via a `local` Jinja filter, bad time input is reported; H3 the LLM/sync handlers are plain `def` (threadpool); H4 a retry first asks the platform about the previous attempt's `platform_ref` (TikTok poll 60 s -> 3 min); H5 CSRF origin check covers `/api` and rejects `Origin: null`; H6 `init-db` exits non-zero instead of stamping a tracked database.
**Medium:** M1 `/assets/{asset_id:int}`; M2 worker releases a backlog one post per `min_gap_minutes` per profile/platform (live posts and reminders); M3 planner skips posts pre-flight would refuse (live/dry_run only); M4 `apply_variant` refuses publishing/sent_to_drafts, returns failed to draft; M5 tag filter uses `EXISTS` per tag; M6 separate rate-limit buckets; M7 dashboard generate validation -> flash; M8 pre-flight checks caption + hashtags; M9 engagement sync every 30 min; M10 TikTok refresh window 2 h; M11 heartbeat stamped at the end of the iteration and between posts; M12 `logger` defined; M13 temp upload removed on cancellation; M14 security headers, `PUBLISHING_MODE=live` counts as production (password required, docs off); M15 dict flash message; M16 blank idempotency key = none; M17 `_pick_minutes` constructs valid gaps; M18 explicit null / bad ids -> 422, draft-reply maps `GenerationError`.
**Behaviour changes to know about:** with `PUBLISHING_MODE=live` the app now refuses to start without dashboard credentials (even if `APP_ENV` is development); cross-origin API calls from a browser page are rejected (curl/scripts without an `Origin` header are unaffected); the dashboard shows planned times in `APP_TIMEZONE` (they were UTC before).
**Not done:** login throttling (behind Caddy every client shares one IP, so a naive limiter could lock Lee out), pruning of old metric rows (now ~48/day/post), `delete`/upload handlers that await stay `async`, the 11 Low items, repairing already-corrupted profile rows (none exist outside dev databases).


## Phase 3 plan written (2026-11-06)
**Files:** `PLAN_PHASE3.md` (new). No code changed.
**Basis:** Fanvue's official API docs + `openapi-v1.json` and Community Guidelines, checked 2026-11-06 (summary table in PLAN_PHASE3 §0); Lee's H1/H2 answers. H3-H10 were never put to Lee, so the plan states defaults marked CONFIRM.
**Scope:** Fanvue posting, tracking links for IG/TikTok bios, fan inbox with AI-drafted human-sent replies + PPV, weekly report. Offline milestones C0-C6, live L7-L10.
**Next:** C0 (API notes + decisions) when the user says go; send Lee the short H3/H5/H6/H7 question list and the Fanvue developer-app steps (PLAN_PHASE3 §13).


## Phase 3 - C0 API notes and decisions (2026-11-06)
**Files changed:** `docs/PLATFORM_API_NOTES.md` (new Fanvue section: every call in PLAN_PHASE3 §6-§10 with method, path, fields, status and a live checklist), `docs/DECISIONS.md` (Phase 3 defaults for H3-H10, polling vs webhooks, worker-only token refresh, auto-send gate), `PLAN_PHASE3.md` v1.1.
**Found while reading the spec (plan corrected):** the token endpoint needs the client id/secret as an HTTP Basic header (`client_secret_basic`), not in the form body; the inbox should use `filter=not_answered` (fan spoke last) rather than `unread`; v1 message paging is `limit` (1-50); the upload completion body uses capitalised `PartNumber`/`ETag`; `/users/me` returns `email`, which we will not store.
**Tests:** no code changed; full suite **521 passed** (0 failed); `/health` 200.
**Unverified (live checklist):** real `expires_in`, `publishedAt` on immediate posts, `tips`/earnings-source object shapes, `not_answered` semantics, PPV as seen by a fan.
**Next:** C1 - config, enums, migration 0004, `fanvue_only` rule, Fanvue caption rule, seeded inactive posting rule.


## Phase 3 - C1 foundations (2026-11-06)
**Files changed:** `app/config.py` (Fanvue settings with validators: no `write:creator`, PPV min <= max, weekday/hour ranges, blank credentials = unset), `.env.example`, `app/models.py` (`Platform.fanvue`, `ContentType.fanvue_post`, `media_assets.fanvue_only/fanvue_media_uuid`, `content_posts.fanvue_audience/fanvue_price_cents`, `model_profiles.fan_rules`, tables `fan_chats`, `fan_messages`, `tracking_links`), `migrations/versions/5096f3308c1a_0004_phase3_fanvue.py` (hand-reviewed: import fixed, `server_default` for `fanvue_only`, inactive Fanvue rule seeded, downgrade keeps posts), `app/services/posts.py` (`ensure_asset_allowed` at create/edit/approve), `app/services/generation.py`, `app/services/random_draft.py`, `app/services/publishing.py` (`fanvue_only_media` pre-flight + reason + persistent; Fanvue caption without hashtags), `app/prompts/platforms.py` (Fanvue rule, hashtag cap 0), `tests/conftest.py` (blank Fanvue credentials).
**Tests:** new `tests/test_phase3_rules.py` (21) and `tests/test_migrations.py` extended (Phase 2 DB with an asset row upgrades to 0004 and downgrades cleanly; seeded rules checked). Two old tests' fake objects got the new attributes. Full suite **543 passed** (C0 baseline 521). `alembic check`: models and migrations match. **Mutation check:** with the guard disabled, 5 of the 21 new tests fail.
**Note for local dev databases:** run `python -m alembic upgrade head` before starting the app on an existing `data/app.db` (new columns).
**Not verified:** nothing touches Fanvue yet; there is no UI to set "Fanvue only" (C4) and no API field for audience/price (C2/C4).
**Next:** C2 - `app/platforms/fanvue.py` (OAuth with PKCE + Basic auth, rotating refresh, multipart upload, publish, check_status), Accounts card, `allow_refresh=False` for the web side.


## Phase 3 - C2 Fanvue platform module (2026-11-06)
**Files created:** `app/platforms/fanvue.py` (PKCE authorize URL with system scopes, token exchange + refresh with `client_secret_basic`, `/v1/users/me` -> uuid + handle only, 5-step multipart upload with polling, `publish` reusing the asset's uploaded media, `check_status` by post uuid or `media:` ref, `fetch_metrics`, error mapping 401/403/410/429/503/5xx, one short `Retry-After` retry, version header on every API call), `tests/test_platform_fanvue.py` (35).
**Files changed:** `app/platforms/__init__.py` (publisher knows fanvue), `app/services/accounts.py` (fanvue platform + env, PKCE verifier/challenge stored in `oauth_states`, worker-only refresh via `is_worker`), `app/services/scheduler.py` (Fanvue refresh window 20 min, retry 2 min), `app/cli.py` (worker sets `is_worker`; `refresh-tokens` skips Fanvue), `app/services/publishing.py` (`fanvue_offer_problem` pre-flight: audience values, price inside PPV range), `app/web/templates/accounts.html` (copy; the Fanvue card comes from `accounts.PLATFORMS`).
**Tests:** full suite **578 passed** (C1 baseline 543). Covered end to end against a fake Fanvue: connect flow through `/oauth/fanvue/start|callback` with a real S256 check, rotated refresh token persisted, rejected refresh -> account expired, web side never refreshes, multipart upload byte-exact with no auth header on part PUTs, live publish marks the post published and keeps the media uuid, a 5xx after upload retries without re-uploading and creates exactly one post, dry-run worker "would publish fanvue_post to fanvue" with **0 HTTP**. **Mutation checks:** web-side refresh allowed, secret in the token body, wrong PKCE verifier, uploaded media uuid not kept: each makes 1-3 tests fail.
**Not verified (live checklist, L7/L8):** everything against the real Fanvue (no sandbox); real `expires_in`; whether `startDate` on `GET /v1/posts` filters by creation time; signed-URL part ETag header name casing on Fanvue's storage.
**Next:** C3 - fan inbox: sync `not_answered` chats, escalation rules, AI drafts, send + PPV, retention purge, worker stage.


## Phase 3 - C3 fan inbox (2026-11-06)
**Files created:** `app/services/fan_inbox.py` (sync, `needs_escalation`, `draft_problem`, `draft_reply`, `auto_draft`, `send_reply` with PPV, ignore/reopen, `purge_old`), `app/prompts/fan_reply_v1.j2`, `tests/test_fan_inbox.py` (63).
**Files changed:** `app/platforms/fanvue.py` (`list_waiting_chats`, `get_messages`, `send_message`), `app/prompts/__init__.py` (fan reply renderers; fan text in `<message>` tags with angle brackets neutralised), `app/services/scheduler.py` (`fan_inbox_stage` every `FANVUE_INBOX_POLL_SECONDS` in live mode, `fan_purge_stage` daily; both timestamps carried in the heartbeat).
**Tests:** full suite **640 passed** (C2 baseline 578). **Mutation checks** (each caught): sync never escalates (3 fail), messages marked read (1), no send claim (1), purge drops escalations (1), drafts unchecked (1), partial chat list closes chats (1).
**Defects found by the tests/audit and fixed before commit:** "I'm 16 btw" did not escalate (age pattern needed "yo/years"); AI-written escalation reasons were going into the push; auto-draft would log a failure every 2 minutes with no AI key; the worker built the cached AI client even with nothing to draft, which leaked into a later dashboard test (order-dependent failure).
**Not verified:** real Fanvue chats (`not_answered` semantics, message `type` values in practice), real AI draft quality, Telegram delivery of the new pushes.
**Next:** C4 - dashboard `/fans`, `/fans/{id}`, Fanvue-only checkbox, post-detail Fanvue fields, API routes (§11), 375 px check.


## Phase 3 - C4 dashboard + API (2026-11-06)
**Files created:** `app/api/fans.py` (`/api/v1/fans/chats`, `/chats/{id}`, `/draft`, `/send`, `/ignore`, `/reopen`, `/sync` live-only), `app/web/templates/fans.html`, `app/web/templates/fan_chat.html`, `tests/test_fans_ui.py` (17).
**Files changed:** `app/schemas.py` (fan schemas; `AssetUpdate/Out.fanvue_only`; `PostUpdate/Out.fanvue_audience/fanvue_price_cents`), `app/services/assets.py`, `app/api/assets.py`, `app/services/posts.py` (offer fields Fanvue-only + PPV range + re-approval; overview counts include every platform), `app/services/fan_inbox.py` (a manual sync racing the worker no longer 500s), `app/web/routes.py` (`/fans` pages and forms; asset "Fanvue only" checkbox; post audience/price fields; one platform list for pickers), `app/web/templates/{base,asset_detail,post_detail}.html`, `app/main.py`.
**Tests:** full suite **657 passed** (C3 baseline 640). Covered: API flows + validation + live-only sync, pages escape fan text (`<script>` stays text), the handle never reaches inline JS, real-browser form values (`asset_id=""`, `$9.99`, `inf`, `nan`), CSRF on the new POSTs (403), Fanvue-only checkbox via API and form, price/audience via API and form.
**Browser check (real browser pane, 375 px, scratch DB, dry-run mode):** `/fans` 375 px (379 = viewport, no overflow), `/fans/1` (escalated, 50-char handle) 375 px with the handle wrapping, `/fans/2` (draft + open paid-media panel) 375 px, Fanvue post page 375 px showing the 9.99 price; Send disabled outside live mode. Screenshots looked right.
**Audit findings fixed:** Fanvue missing from Generate and Queue pickers (posts could not be generated from the UI); overview per-platform table ignored Fanvue; sync race -> IntegrityError 500; warning banner class did not exist.
**Not done:** sidebar badge (see DECISIONS); real Fanvue/LLM.
**Next:** C5 - tracking links + `/fanvue` report page + weekly digest.
