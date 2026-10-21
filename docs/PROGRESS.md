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