# Summer AI Content Automation

AI-powered social media content generation for the "Summer" persona (Instagram/TikTok).

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
│   ├── cli.py         # CLI commands (init-db, sync-media)
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
- **Content queue**: Draft → Approved → Scheduled → Archived lifecycle
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
- `docs/DEMO.md` — Halloween demo script
- `docs/DECISIONS.md` — Architecture decisions
- `docs/REFERENCE_RESEARCH.md` — Reference account research template

## Random drafts from your library ("Surprise me")
On the **Generate** page, the "Surprise me" card (or `POST /api/v1/content/generate-random`) picks random **unused** images/videos, shows them to the AI, and saves a draft for each (1-5 per click; the platform's content type is chosen from the media type; campaign "Auto" uses the asset's category tag, else Lifestyle). Nothing is published - you review and approve each draft.
- It only uses **uploaded or synced files**. A Dropbox *share link* can't be listed or opened, so to draw from Dropbox: install the Dropbox desktop app (folder set to "Available offline") **or** download the Dropbox folder to your computer, then set `MEDIA_SYNC_DIR` to that folder in `.env` and click **Sync** in the Media Library. Sub-folders named like a campaign (e.g. `Halloween/`) auto-tag the files.
- Videos under 1 KB (failed uploads) are skipped.

## Removing assets
- **Archive** hides an asset from the library but keeps everything (`DELETE /api/v1/assets/{id}`).
- **Delete permanently** (Delete button on the library card / asset page, or `DELETE /api/v1/assets/{id}?permanent=true`) removes an **uploaded** file or **Dropbox link** for good. If posts still use it, you must confirm (`&force=true`): those posts keep their text and lose the media.
- **Synced Dropbox files can't be deleted from the app** (your Dropbox folder is never changed, and a deleted row would be re-imported by the next Sync). Archive them instead.

## Known limitations (Phase 1)
- **No publishing.** Approved/Scheduled means "ready"; posting to Instagram/TikTok/X is Phase 2.
- **No Dropbox API.** Add media by upload, by pointing `MEDIA_SYNC_DIR` at the Dropbox desktop folder (mark it "Available offline"), or by pasting links.
- **What the AI can see:** uploaded or synced images, and 3 frames from uploaded/synced videos (needs ffmpeg on PATH or `FFMPEG_PATH`). **Dropbox links can't be opened by the AI** - upload the file or write a description. Frames are sent to your AI provider; set `ENABLE_IMAGE_INPUT=false` to turn this off.
- **Review every draft.** Captions are AI output (check hashtags, tone, platform rules); apply platform AI-content labels when posting.
- **Live generation verified with Mistral only** (`docs/LIVE_PROOF.md`). The Claude path is covered by mocked tests and needs a funded `ANTHROPIC_API_KEY` to verify live.
- Single shared login (Basic auth), in-memory rate limiter, SQLite: run a single server process.

## Deploying to a VPS
Set `DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD`, `APP_ENV=production`, run behind HTTPS (Caddy/Nginx), one uvicorn worker, back up `data/`. For Postgres set `DATABASE_URL=postgresql+psycopg://...`, add `psycopg[binary]`, run `python -m app.cli init-db`.

## License
Proprietary - delivered to the client (Lee).