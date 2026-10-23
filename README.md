# Summer AI Content Automation

AI-powered content generation, planning, scheduling and publishing for the "Summer" persona (Instagram/TikTok), with a human approving every post.

## Quick Start

### Requirements
- Python 3.12+ (3.13 recommended for development)
- Windows 11 or Linux

### Setup (Windows)
```powershell
# Create and activate virtual environment
py -3.13 -m venv .venv
.venv\Scripts\activate

# Install dependencies
pip install -r requirements-dev.txt

# Configure environment
copy .env.example .env
# Edit .env: set ANTHROPIC_API_KEY (console.anthropic.com) and/or MISTRAL_API_KEY (console.mistral.ai)

# Initialize database
python -m app.cli init-db

# Run server
uvicorn app.main:app --reload
```

### Setup (Linux)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY and/or MISTRAL_API_KEY
python -m app.cli init-db
uvicorn app.main:app --reload
```

### Access
- Dashboard: http://127.0.0.1:8000
- API docs: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

## Project Structure
```
.
├── app/
│   ├── api/           # API routes (/api/v1)
│   ├── web/           # Dashboard (Jinja2 templates)
│   ├── services/      # Business logic (ONLY place for business rules)
│   ├── prompts/       # Versioned prompt templates
│   ├── seed/          # Seed data (summer.json)
│   ├── models.py      # SQLAlchemy models
│   ├── schemas.py     # Pydantic schemas
│   ├── config.py      # Settings (pydantic-settings)
│   ├── db.py          # Database engine, session, UTCDateTime
│   ├── main.py        # FastAPI app factory
│   ├── platforms/     # Instagram + TikTok modules (plain functions)
│   ├── cli.py         # CLI commands (init-db, worker, plan-week, sync-media, notify-test, refresh-tokens)
│   └── seed.py        # Idempotent seeding
├── migrations/        # Alembic migrations
├── tests/             # Pytest suite
├── data/              # Gitignored (SQLite DB, media files)
├── docs/              # Documentation
└── requirements*.txt  # Dependencies
```

## Key Features
- **Persona-driven generation**: Summer persona with editable profile (tone, rules, restricted topics)
- **Asset management**: Upload, register Dropbox links, sync from local folder, tag/filter
- **Content generation**: Claude or Mistral, validated output, idempotent submissions, alternative variants
- **Content queue**: Draft → Approved → Scheduled → Published / Sent to drafts / Failed → Archived, with retries and an attempt history
- **Dashboard**: Server-rendered HTML forms, no JS frameworks

## Configuration
See `.env.example` for all options. Key variables:
- `ANTHROPIC_API_KEY` — Claude generation (console.anthropic.com; a Claude.ai subscription does NOT include API credits)
- `CLAUDE_MODEL` — Default `claude-opus-5-5`; cheaper: `claude-sonnet-5-5`
- `MISTRAL_API_KEY` / `MISTRAL_MODEL` — Mistral as alternative or fallback (default model `ministral-8b-latest`, works on the free tier)
- `LLM_PROVIDER` — `auto` (Anthropic if configured, Mistral fallback on provider failures), `anthropic`, or `mistral`
- `MEDIA_SYNC_DIR` — Optional path to Dropbox desktop sync folder
- `DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD` — Enable Basic auth (required for VPS)

## Testing
```bash
python -m pytest
```

## Documentation
- `docs/LIVE_PROOF.md` — real Instagram + TikTok drafts generated live (Mistral)
- `docs/DEMO.md` — Halloween demo script (Phase 1 flow) · `docs/DEMO_PHASE2.md` — planner, reminders, dry run, live
- `docs/PLATFORM_SETUP.md` — connect Instagram, TikTok, Telegram, Dropbox, Anthropic · `docs/DEPLOY.md` — server
- `docs/PLATFORM_API_NOTES.md` — what was verified in the official docs and what is still unproven
- `docs/PROGRESS.md` — build log with real test results
- `docs/DECISIONS.md` — Architecture decisions
- `docs/REFERENCE_RESEARCH.md` — Reference account research template

## Publishing, scheduling and engagement (Phase 2)

Everything is controlled by **one switch**, `PUBLISHING_MODE` in `.env` (default `off`):

| Mode | What the worker does | Needs |
|---|---|---|
| `off` | nothing automated (manual workflow: copy caption, post by hand, **Mark as posted**) | nothing |
| `reminders` | at each planned slot sends a **push with the full caption**; you post by hand, then **Mark as posted** | a push channel |
| `dry_run` | rehearses publishing: records "would publish" attempts, **sends nothing anywhere** | nothing |
| `live` | publishes approved posts to Instagram and sends videos to your TikTok drafts | server + HTTPS + connected accounts |

Rules that never bend:
- **A human approves every post.** The planner only schedules posts you approved; it never approves anything.
- **Posting window: 08:00–21:00** (UK time by default, per platform rule). Nothing is scheduled, reminded, delivered or published outside it
  (retries that would fall outside move to the next 08:00). *Publish now* outside the window needs an explicit tick-box.
- **Replies to comments are sent only when you press Send**, one at a time. No DMs, no automatic replies.
- `published` means a real platform post id **or** you pressed *Mark as posted*. TikTok videos land in your **drafts** first (status *sent to drafts*);
  you add the AI label and press Publish in the TikTok app.

**Weekly planner** (*Planner* page / `python -m app.cli plan-week`): 10 posts per week per platform, at least one a day, three days with two,
random times inside the window, and the **same posts mirrored on both platforms** (same day). `AUTO_PLAN_ENABLED=true` lets the worker plan once a day.

**Push notifications** (`NOTIFY_PROVIDER` = `telegram` / `ntfy` / `pushover`): post due, TikTok draft ready, post failed, account needs reconnecting,
new comments (hourly), empty planner slots, low disk. Test with **Accounts → Send test notification** or `python -m app.cli notify-test`.

**Engagement** (Instagram only): like/comment counts for posts published through the API, new comments (with an hourly push), AI-drafted replies
you edit and send. TikTok comments and metrics are not available to this kind of app.

**Accounts**: connect on the *Accounts* page (OAuth — you press *Allow* on Instagram/TikTok; no passwords). Tokens are encrypted at rest
(`TOKEN_ENCRYPTION_KEY`) and refreshed automatically.

### Run it
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000     # web (one process)
python -m app.cli worker                              # scheduler (one process; Ctrl+C / SIGTERM finish the current step, then exit)
```
Other commands: `init-db`, `sync-media` (refuses below `MIN_FREE_DISK_GB`), `plan-week`, `notify-test`, `refresh-tokens`.
Kill switch: `PUBLISHING_MODE=off` and restart. Server setup, HTTPS, backups, monitoring: `docs/DEPLOY.md`. Connecting the accounts: `docs/PLATFORM_SETUP.md`.

### Phase 2 configuration (see `.env.example` for all)
`PUBLISHING_MODE`, `PUBLIC_BASE_URL` (https), `TOKEN_ENCRYPTION_KEY`, `MEDIA_URL_SECRET`, `INSTAGRAM_APP_ID/SECRET`, `TIKTOK_CLIENT_KEY/SECRET`,
`TIKTOK_POST_MODE` (`inbox`), `WORKER_INTERVAL_SECONDS`, `PUBLISH_MAX_ATTEMPTS`, `AUTO_PLAN_ENABLED`, `PLANNER_RUN_HOUR`, `NOTIFY_PROVIDER` + its credentials, `MIN_FREE_DISK_GB`.
With `APP_ENV=production` the app **refuses to start** unless `DASHBOARD_USERNAME` and a `DASHBOARD_PASSWORD` of 12+ characters are set (and `/docs` is off).

### Costs to expect
Server ≈ a few euros to ~US$12 a month (+ backups), domain ≈ US$10–15 a year, Anthropic API = a few cents per caption (set a monthly limit);
Telegram/ntfy, Meta, TikTok and Dropbox are free. Prices change — check when you buy.

## Random drafts from your library ("Surprise me")
On the **Generate** page, the "Surprise me" card (or `POST /api/v1/content/generate-random`) picks random **unused** images/videos, shows them to the AI, and saves a draft for each (1-5 per click; the platform's content type is chosen from the media type; campaign "Auto" uses the asset's category tag, else Lifestyle). Nothing is published - you review and approve each draft.
- It only uses **uploaded or synced files**. A Dropbox *share link* can't be listed or opened, so to draw from Dropbox: install the Dropbox desktop app (folder set to "Available offline") **or** download the Dropbox folder to your computer, then set `MEDIA_SYNC_DIR` to that folder in `.env` and click **Sync** in the Media Library. Sub-folders named like a campaign (e.g. `Halloween/`) auto-tag the files.
- Videos under 1 KB (failed uploads) are skipped.

## Removing assets
- **Archive** hides an asset from the library but keeps everything (`DELETE /api/v1/assets/{id}`).
- **Delete permanently** (Delete button on the library card / asset page, or `DELETE /api/v1/assets/{id}?permanent=true`) removes an **uploaded** file or **Dropbox link** for good. If posts still use it, you must confirm (`&force=true`): those posts keep their text and lose the media.
- **Synced Dropbox files can't be deleted from the app** (your Dropbox folder is never changed, and a deleted row would be re-imported by the next Sync). Archive them instead.

## Known limitations
- **Publishing needs the accounts and a server.** Until Instagram/TikTok are connected and `PUBLISHING_MODE=live`, use `reminders` or the manual workflow. The platform integrations are tested against mocks and the platforms' documentation (`docs/PLATFORM_API_NOTES.md` lists every unverified detail); **nothing has been posted to a real account yet** (live proof: `docs/LIVE_PROOF.md`).
- **Instagram:** images must be **JPEG**; there is no API flag for the AI label (add it in the app); only API-published posts get metrics/comments.
- **TikTok:** videos go to your **drafts** (you press Publish and add the AI label); photo posts and direct public posting are not supported (TikTok requires an audited app for the latter).
- **X (Twitter) and Fanvue are not part of Phase 2.**
- **What the AI can see:** uploaded or synced images, and 3 frames from uploaded/synced videos (needs ffmpeg on PATH or `FFMPEG_PATH`). **Dropbox links can't be opened** - the server copies your Dropbox folder (see `docs/DEPLOY.md`) or you upload files. Frames are sent to your AI provider; `ENABLE_IMAGE_INPUT=false` turns this off.
- **Review every draft.** Captions are AI output (check hashtags, tone, platform rules); apply platform AI-content labels when posting.
- **Claude (Anthropic) live generation is unverified** (no funded key yet); Mistral was verified live (`docs/LIVE_PROOF.md`).
- Single shared login (Basic auth), in-memory rate limiter, SQLite: run **one** web process and **one** worker.

## Deploying to a server
See `docs/DEPLOY.md` (Ubuntu, systemd, Caddy HTTPS, Dropbox copy timer, backups, uptime monitoring, going live step by step) and `docs/PLATFORM_SETUP.md` (accounts, click by click). Postgres later: `DATABASE_URL=postgresql+psycopg://...`, add `psycopg[binary]`, run `python -m app.cli init-db`.

## License
Proprietary - delivered to the client (Lee).