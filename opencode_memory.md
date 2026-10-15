# OpenCode Memory - Summer AI Content Automation Phase 1

## Project Overview
Building a FastAPI + Jinja2 application for AI-powered social media content generation for the "Summer" persona (Instagram/TikTok). Uses Anthropic Claude API for content generation.

## Plan Source
- **Primary spec**: `PLAN.md` (detailed 800+ line build plan)
- **Builder instructions**: `CLAUDE.md`

## Key Architecture Decisions (from PLAN.md)
- Python 3.13 (venv), must also run on 3.12+ Linux
- FastAPI + Jinja2 + HTML forms (no React, Redis, Celery, Docker, background workers)
- SQLite (dev) → Postgres-ready via Alembic migrations
- Business logic in `app/services/` only; API/dashboard routes are thin
- No secrets in code/git; `.env` gitignored, `.env.example` placeholders only
- All timestamps UTC, displayed in `APP_TIMEZONE`
- Optional HTTP Basic auth when `DASHBOARD_USERNAME`+`DASHBOARD_PASSWORD` set
- Idempotency keys on all creation endpoints
- No fake functionality - no publishing, no Dropbox API, no scraping

## Milestones (M1-M6)
| Milestone | Focus | Done When |
|-----------|-------|-----------|
| M1 | Foundation: venv, config, db, models, logging, main, alembic, seeds, cli, health, profiles, tags APIs, tests | `python -m app.cli init-db` works twice; server serves `/health`; profiles API shows Summer; tests green |
| M2 | AI Service: prompts, claude_client, generation, content routes, rate limiter, tests | Tests green with fakes; manual smoke with real key generates IG reel + TikTok video |
| M3 | Assets: upload, register, sync, filters, archive, file serving, cli sync-media, tests | Upload/preview/filter/sync work via API; tests green |
| M4 | Content Queue: CRUD, transitions, variants apply, stats, posts API, tests | Full lifecycle draft→approved→scheduled→archived→restored via API; restart persistence test green |
| M5 | Dashboard: templates, routes, style.css, Basic auth + origin check, tests | Every page works in browser with real data; error states visible |
| M6 | Validation & Handover: full test run, Halloween demo, docs, requirements.lock, secret scan | All §20 criteria checked with evidence |

## Current State
- Project folder is empty (no code, no git repo)
- Need to start from M1 - Foundation

## Non-negotiable Rules
1. No fake functionality
2. No secrets in code/git
3. Never invent image content - only use human-written descriptions
4. Every milestone ends green (pytest passes, app starts)
5. Tests never call real Claude API
6. No new dependencies without documenting in `docs/DECISIONS.md`
7. No React, Redis, Celery, HTMX, Docker, background workers, schedulers
8. Business logic in `app/services/` only
9. All timestamps UTC
10. Don't over-build

## Next Steps
Start M1 - Foundation:
1. Create project structure
2. Set up venv, requirements.txt, .gitignore, .env.example
3. Create config.py, db.py, models.py, logging_setup.py, main.py
4. Set up Alembic + initial migration
5. Create seed.py, seed/summer.json, cli.py
6. Create API routes for health, profiles, tags
7. Write tests (conftest.py, test_app.py, test_profiles.py)
8. Run init-db, start server, run tests