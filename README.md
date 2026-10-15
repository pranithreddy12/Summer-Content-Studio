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
# Edit .env and add your ANTHROPIC_API_KEY from https://console.anthropic.com

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
# Edit .env and add your ANTHROPIC_API_KEY
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
- **Content generation**: Claude API with JSON schema validation, idempotency, variants
- **Content queue**: Draft → Approved → Scheduled → Archived lifecycle
- **Dashboard**: Server-rendered HTML forms, no JS frameworks

## Configuration
See `.env.example` for all options. Key variables:
- `ANTHROPIC_API_KEY` — Required for generation (get from console.anthropic.com)
- `CLAUDE_MODEL` — Default `claude-opus-5-5`; cheaper: `claude-sonnet-5-5`
- `MEDIA_SYNC_DIR` — Optional path to Dropbox desktop sync folder
- `DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD` — Enable Basic auth (required for VPS)

## Testing
```bash
python -m pytest
```

## Documentation
- `docs/DEMO.md` — Halloween demo script
- `docs/DECISIONS.md` — Architecture decisions
- `docs/REFERENCE_RESEARCH.md` — Reference account research template

## License
Proprietary — Kelly Summer AI Content Automation