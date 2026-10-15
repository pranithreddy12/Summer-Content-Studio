# Summer AI Content Automation — Phase 1 Build Plan

> **Audience:** the engineer/model implementing Phase 1. Follow this document literally.
> Where it gives an exact name, path, field, value or rule, use it exactly. Where it is silent, pick the simplest option that fits the surrounding code and write it down in `docs/DECISIONS.md`.
> **Source of truth for requirements:** the client spec (Phase 1). This plan turns that spec into concrete decisions.

---

## 0. Repository assessment (as of 2026-10-14)

| Item | State |
|---|---|
| Project folder | Empty. No code, no git repo. |
| Python | 3.14 (default), 3.13 and 3.11 installed via `py` launcher (Windows 11). |
| Existing deps/config | None. |
| Functionality to preserve | None. |
| Decision | Use **Python 3.13** (`py -3.13`) for the venv — most mature wheel support. Code must also run on 3.12+ (Linux VPS). |

---

## 1. Non-negotiable rules for the builder

1. **No fake functionality.** No publishing, no Dropbox API, no Instagram scraping, no X/Fanvue code. Statuses `published`/`failed` exist in the data model only.
2. **No secrets in code or git.** Only `.env` (gitignored). `.env.example` has placeholders only.
3. **Never invent image content.** Claude only sees the human-written `description` of an asset. Empty description → prompt says "no visual description provided" and Claude must write neutral copy.
4. **Every milestone ends green:** `pytest` passes and the app starts. Do not start milestone N+1 with red tests.
5. **Tests never call the real Claude API.** The real API is used only in the manual smoke test (§13).
6. **No new dependencies** beyond §3 without writing why in `docs/DECISIONS.md`.
7. **No React, Redis, Celery, HTMX, Docker, background workers, schedulers.** Plain FastAPI + Jinja2 + HTML forms.
8. **Business logic lives in `app/services/`.** API routes and dashboard routes are thin and call the same service functions. Never duplicate logic between API and dashboard.
9. **All timestamps stored in UTC**, displayed in `APP_TIMEZONE`.
10. **Don't over-build.** No repository classes, no abstract base classes, no plugin systems, no generic CRUD factories.

---

## 2. Architecture

```
Browser (dashboard, Jinja2 HTML forms)        API clients / tests (JSON)
        │                                            │
        ▼                                            ▼
  app/web/routes.py                         app/api/*.py  (/api/v1)
        └──────────────┬─────────────────────────────┘
                       ▼
              app/services/*.py   ← all business rules (validation, status transitions,
                       │             idempotency, tagging, file safety)
         ┌─────────────┼───────────────────┐
         ▼             ▼                   ▼
   app/models.py   app/services/      app/prompts/ (versioned templates)
   (SQLAlchemy)    claude_client.py ──► Anthropic API (official SDK)
         │
         ▼
   SQLite (data/app.db)  — Alembic migrations (Postgres-ready)
   Media files: data/media/ (uploads)  +  MEDIA_SYNC_DIR (read-only, e.g. Dropbox desktop folder)
```

**Key design decisions (do not revisit):**

| Topic | Decision | Why |
|---|---|---|
| Persona | Stored in DB `model_profiles`, seeded once from `app/seed/summer.json`. Dashboard edits DB. | Editable without code; multi-profile ready. |
| Generation output | Claude returns JSON constrained by `output_config.format` (JSON schema), validated again with Pydantic. | Spec requires schema validation. |
| Generate = create draft | `POST /content/generate` calls Claude **and persists** a draft post + its first variant. | Never lose paid-for output. |
| Variations | Separate table `content_variants`. Regenerate appends a variant; post fields change only on explicit "apply". | "Don't lose the original." |
| Duplicate prevention | `idempotency_key` (unique) on posts. Forms carry a hidden UUID; API accepts `Idempotency-Key` header. Same key → return existing post, no Claude call. | Double-click / resubmit safe, no double charges. |
| Status changes | Only via action endpoints + one transition table in `services/posts.py`. PATCH cannot set `status`. | Consistent, testable. |
| Dropbox | **No Dropbox API in Phase 1.** Three storage types: `upload`, `local_sync` (files in Dropbox desktop-synced folder, registered in place, not copied), `external_url` (Dropbox shared link stored as reference, no preview). | Works today with zero credentials; no duplication. |
| Tags/categories | One `tags` table; `is_category=True` for the 6 seeded categories. Many-to-many to assets and posts. | Custom campaigns without schema change. |
| Migrations | Alembic, `render_as_batch=True` (SQLite ALTER support). | SQLite now, Postgres later. |
| Auth | Optional HTTP Basic on everything except `/health`, enabled when `DASHBOARD_USERNAME`+`DASHBOARD_PASSWORD` are set. | Required before VPS exposure; zero friction locally. |
| Model | `CLAUDE_MODEL` env var, default `claude-opus-5-5`. | Configurable; one place. |

---

## 3. Dependencies

`requirements.txt`
```
fastapi>=0.115
uvicorn[standard]>=0.30
sqlalchemy>=2.0.30
alembic>=1.13
pydantic>=2.7
pydantic-settings>=2.3
jinja2>=3.1
python-multipart>=0.0.9
anthropic>=1.13,<2
tzdata>=2024.1        # zoneinfo on Windows
```
`requirements-dev.txt`
```
-r requirements.txt
pytest>=8
httpx>=0.27           # FastAPI TestClient
```
After the first successful install, run `pip freeze > requirements.lock` and commit it (exact versions for the VPS).

---

## 4. Project structure (create exactly this)

```
.
├── app/
│   ├── __init__.py
│   ├── main.py               # create_app(), exception handlers, mounts routers/static
│   ├── config.py             # Settings (pydantic-settings), get_settings() with lru_cache
│   ├── db.py                 # engine, SessionLocal, Base, get_db(), UTCDateTime type, utcnow()
│   ├── models.py             # ALL ORM models + enums
│   ├── schemas.py            # ALL Pydantic request/response models
│   ├── logging_setup.py      # JSON log formatter + configure_logging()
│   ├── security.py           # basic auth dep, origin check middleware, rate limiter, safe path helpers
│   ├── seed.py               # seed_profiles(), seed_categories() — idempotent
│   ├── cli.py                # python -m app.cli init-db | sync-media
│   ├── services/
│   │   ├── __init__.py
│   │   ├── profiles.py
│   │   ├── tags.py
│   │   ├── assets.py         # register, upload, sync, filter, archive, file resolution
│   │   ├── posts.py          # CRUD, filters, transitions, variants apply
│   │   ├── generation.py     # orchestrates: validate → prompt → Claude → persist post/variant/log
│   │   └── claude_client.py  # ClaudeClient: SDK call, error mapping, usage
│   ├── prompts/
│   │   ├── __init__.py       # PROMPT_VERSION, render_system(), render_user()
│   │   ├── platforms.py      # PLATFORM_RULES, CONTENT_TYPES
│   │   ├── system_v1.j2
│   │   └── user_v1.j2
│   ├── api/
│   │   ├── __init__.py       # api_router = APIRouter(prefix="/api/v1")
│   │   ├── health.py         # GET /health (no prefix)
│   │   ├── profiles.py
│   │   ├── assets.py
│   │   ├── content.py        # generate, regenerate
│   │   ├── posts.py
│   │   └── tags.py
│   ├── web/
│   │   ├── __init__.py
│   │   ├── routes.py         # dashboard pages + form POST handlers
│   │   ├── templates/
│   │   │   ├── base.html  overview.html  assets.html  asset_detail.html  asset_new.html
│   │   │   ├── generate.html  posts.html  post_detail.html  profile.html  _flash.html
│   │   └── static/style.css
│   └── seed/summer.json
├── migrations/               # alembic (env.py imports app.db.Base + app.models)
├── alembic.ini
├── tests/
│   ├── conftest.py
│   ├── test_app.py  test_profiles.py  test_assets.py  test_generation.py
│   ├── test_claude_client.py  test_posts.py  test_security.py  test_dashboard.py
│   └── fixtures/ tiny.png  tiny.mp4(optional)  fake.exe
├── data/                     # gitignored (app.db, media/)
├── docs/
│   ├── DECISIONS.md  DEMO.md  REFERENCE_RESEARCH.md
├── .env.example  .gitignore  README.md  requirements.txt  requirements-dev.txt  pytest.ini
```

---

## 5. Configuration (`app/config.py`, `.env.example`)

`Settings(BaseSettings)` with `model_config = SettingsConfigDict(env_file=".env", extra="ignore")`.

| Env var | Type | Default | Notes |
|---|---|---|---|
| `APP_ENV` | str | `development` | `development` / `production` |
| `LOG_LEVEL` | str | `INFO` | |
| `DATABASE_URL` | str | `sqlite:///./data/app.db` | |
| `ANTHROPIC_API_KEY` | `SecretStr \| None` | None | Never logged/returned. Missing → generation returns `not_configured`. |
| `CLAUDE_MODEL` | str | `claude-opus-5-5` | Cheaper alternative: `claude-sonnet-5-5` (client's choice). |
| `CLAUDE_EFFORT` | str | `medium` | one of `low/medium/high` |
| `CLAUDE_MAX_TOKENS` | int | `16000` | Thinking counts toward this; don't lower. |
| `CLAUDE_TIMEOUT_SECONDS` | float | `120` | |
| `CLAUDE_MAX_RETRIES` | int | `2` | SDK retries only transient errors (429/5xx/connection). |
| `MEDIA_UPLOAD_DIR` | Path | `./data/media` | created on startup |
| `MEDIA_SYNC_DIR` | `Path \| None` | None | e.g. `C:\Users\<you>\Dropbox\Summer`. Read-only use. |
| `MAX_IMAGE_MB` | int | `25` | |
| `MAX_VIDEO_MB` | int | `500` | |
| `APP_TIMEZONE` | str | `UTC` | e.g. `Europe/London`; validate with `zoneinfo.ZoneInfo` |
| `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` | `str \| None` / `SecretStr \| None` | None | Both set → Basic auth on. |
| `GENERATION_RATE_LIMIT_PER_MINUTE` | int | `10` | |

`.env.example` = every var above with a placeholder or default and a one-line comment. `ANTHROPIC_API_KEY=sk-ant-REPLACE_ME`. Note in comments: *a Claude.ai subscription does not include API credits; create a key + add credits at console.anthropic.com.*

`.gitignore`: `.env`, `.venv/`, `__pycache__/`, `*.pyc`, `data/`, `.pytest_cache/`, `*.db`, `*.sqlite*`, `.idea/`, `.vscode/`, `*.log`.

---

## 6. Database (`app/db.py`, `app/models.py`)

### 6.1 `db.py`
- `engine = create_engine(url, connect_args={"check_same_thread": False} if sqlite else {})`.
- For SQLite add an `event.listens_for(engine, "connect")` that runs `PRAGMA foreign_keys=ON`.
- `SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)`.
- `get_db()` FastAPI dependency yields a session, closes in `finally`.
- `utcnow()` → `datetime.now(timezone.utc)`.
- `UTCDateTime(TypeDecorator)` impl `DateTime`: on bind, convert aware → UTC → naive; reject naive input (raise ValueError). On result, attach `timezone.utc`. Use this type for **every** datetime column.
- Engine/session must be creatable from a URL (function `make_engine(url)`), so tests can point at a temp DB.

### 6.2 Enums (`str, enum.Enum`, stored as `String(20)`, not DB enums)
```
Platform:     instagram, tiktok                (x added in Phase 2 — do not add now)
ContentType:  feed_post, reel, story, video, photo_post
PostStatus:   draft, approved, scheduled, published, failed, archived
MediaType:    image, video
StorageType:  upload, local_sync, external_url
PostSource:   ai, manual
```
Valid platform/content-type combos (in `prompts/platforms.py`, enforced in service):
- instagram: `feed_post`, `reel`, `story`
- tiktok: `video`, `photo_post`

### 6.3 Tables

All tables: `id` Integer PK autoincrement. `created_at` / `updated_at` = `UTCDateTime`, `default=utcnow`, `onupdate=utcnow` for updated_at.

**`model_profiles`**
| column | type | notes |
|---|---|---|
| slug | String(50) unique not null | `summer` |
| name | String(100) not null | `Summer` |
| handles | JSON not null default `{}` | `{"instagram":"summerh_03","tiktok":"its_me_summerh","fanvue":"its_me_summer"}` |
| persona_description | Text not null | |
| tone_style | Text not null | |
| communication_rules | JSON list[str] not null | |
| restricted_topics | JSON list[str] not null | |
| style_notes | Text default "" | learnings from reference accounts (filled by humans) |
| cta_guidelines | Text default "" | |
| is_active | Boolean default True, index | |
| created_at, updated_at | | |

**`tags`**: `name` String(60) not null, `slug` String(60) unique not null (lowercase, `[a-z0-9-]`), `is_category` Boolean default False, `created_at`.

**`asset_tags`**: `asset_id` FK media_assets.id ON DELETE CASCADE, `tag_id` FK tags.id ON DELETE CASCADE, composite PK.
**`post_tags`**: `post_id`, `tag_id`, same pattern.

**`media_assets`**
| column | type | notes |
|---|---|---|
| model_profile_id | FK model_profiles.id nullable, index | |
| filename | String(255) not null | display name (original name) |
| media_type | String(20) not null, index | MediaType |
| mime_type | String(100) nullable | |
| storage_type | String(20) not null | StorageType |
| source_ref | String(1000) nullable | Dropbox link / original location text. Never used to open files. |
| local_relpath | String(500) nullable | relative to the root of its storage_type (upload → MEDIA_UPLOAD_DIR, local_sync → MEDIA_SYNC_DIR). Always forward slashes. |
| sha256 | String(64) nullable, index | dedupe |
| size_bytes | Integer nullable | |
| description | Text default "" | human-written; the ONLY visual info sent to Claude |
| is_active | Boolean default True, index | archive = False |
| imported_at | UTCDateTime not null | when registered |
| created_at, updated_at | | |
- UniqueConstraint(`storage_type`, `local_relpath`) (NULLs allowed multiple times).
- "Used" status is **derived** (count of non-archived posts referencing it), not stored.

**`content_posts`**
| column | type | notes |
|---|---|---|
| model_profile_id | FK not null, index | |
| media_asset_id | FK nullable, index, ON DELETE SET NULL | |
| platform | String(20) not null | |
| content_type | String(30) not null | |
| campaign | String(60) not null, index | tag slug, e.g. `halloween` |
| hook | Text default "" | |
| caption | Text not null | |
| hashtags | JSON list[str] not null default [] | stored without `#` |
| call_to_action | Text default "" | |
| content_notes | Text default "" | |
| status | String(20) not null default `draft` | |
| planned_publish_at | UTCDateTime nullable, index | |
| source | String(10) not null | ai / manual |
| generation_input | JSON nullable | the GenerateRequest used (for regenerate) |
| idempotency_key | String(64) nullable, **unique** | |
| approved_at, archived_at | UTCDateTime nullable | |
| created_at, updated_at | | |
- Index(`status`, `platform`).

**`content_variants`**
| column | type | notes |
|---|---|---|
| post_id | FK content_posts.id ON DELETE CASCADE, index | |
| hook, caption, hashtags, call_to_action, content_notes, content_format | same types as post | |
| generation_log_id | FK nullable | |
| created_at | | |

**`generation_logs`**
| column | type | notes |
|---|---|---|
| model_profile_id | FK not null | |
| media_asset_id | FK nullable | |
| post_id | FK nullable | set after success |
| provider | String(30) | `anthropic` |
| model | String(100) | |
| prompt_version | String(20) | |
| purpose | String(20) | `generate` / `regenerate` |
| success | Boolean | |
| error_category | String(40) nullable | see §8.4 |
| input_tokens, output_tokens | Integer nullable | |
| duration_ms | Integer | |
| request_id | String(100) nullable | Anthropic `_request_id`, for support |
| created_at | | |
**Never store** prompt text, response text, API key, or headers in this table.

Relationships: `MediaAsset.tags`, `ContentPost.tags` (secondary tables, `lazy="selectin"`), `ContentPost.asset`, `ContentPost.variants` (order_by created_at), `ContentPost.profile`.

### 6.4 Migrations & seed
- `alembic init migrations`; in `env.py` set `target_metadata = Base.metadata`, read URL from `get_settings().database_url`, `render_as_batch=True`.
- Generate **one** initial revision `0001_initial` via autogenerate, then review it by hand.
- `python -m app.cli init-db`: creates `data/` + media dir, runs `alembic upgrade head` (via `alembic.command.upgrade`), then `seed_profiles()` + `seed_categories()`. Idempotent (safe to run twice).
- `seed_profiles()` inserts `summer.json` only if slug `summer` doesn't exist (never overwrites edits).
- `seed_categories()` upserts by slug: `halloween` (Halloween), `lifestyle` (General lifestyle), `fashion` (Fashion & appearance), `playful` (Playful / teasing), `reels` (Reels & short-form video), `promo` (Promotional). `is_category=True`.

### 6.5 `app/seed/summer.json`
```json
{
  "slug": "summer",
  "name": "Summer",
  "handles": {"instagram": "summerh_03", "tiktok": "its_me_summerh", "fanvue": "its_me_summer"},
  "persona_description": "Summer is a cheeky, flirtatious and playful young woman with an innocent-looking, girl-next-door presentation. She is confident and seductive without losing her warm, approachable tone.",
  "tone_style": "Natural, conversational and engaging. Light playful teasing. Short attention-grabbing hooks when they fit. Never stiff, corporate or overly salesy.",
  "communication_rules": [
    "Sound like a real person talking to her followers, not a brand.",
    "Vary sentence openings, structure and length between posts.",
    "Use playful teasing and flirtation that stays suggestive, never explicit.",
    "Use emojis sparingly and only where they add personality.",
    "Calls to action should feel like an invitation, not an advert.",
    "Do not invent personal experiences, biography, locations or events."
  ],
  "restricted_topics": [
    "Politics, elections, politicians and political causes",
    "Religion debates",
    "Explicit sexual content",
    "Anything involving minors",
    "Drugs, self-harm, violence",
    "Medical, legal or financial advice"
  ],
  "style_notes": "",
  "cta_guidelines": "On Instagram and TikTok never name or link adult-subscription platforms in captions; point people to 'link in bio' or to follow/turn on notifications. Not every post needs a CTA."
}
```

---

## 7. Pydantic schemas (`app/schemas.py`)

Use `ConfigDict(from_attributes=True)` on all `*Out` models. Rules: strip strings; reject unknown fields on inputs (`extra="forbid"`).

- `ProfileOut` — all profile fields.
- `ProfileUpdate` — all editable fields optional; `name` 1–100 chars; lists max 30 items, each 1–300 chars.
- `TagOut` — id, name, slug, is_category.
- `AssetCreate` (JSON registration, no file): `filename` (1–255, required), `media_type` (required), `storage_type` (`external_url` or `local_sync` only), `source_ref` (required for external_url, must start with `https://`), `local_relpath` (required for local_sync; validated by `safe_relpath`, §10), `description` (≤2000), `tags: list[str]` (≤20), `model_profile_id` optional.
- `AssetUpdate` — filename, description, tags, model_profile_id, is_active (all optional).
- `AssetOut` — fields + `tags`, `preview_url` (`/api/v1/assets/{id}/file` or null for external_url), `usage_count`.
- `GenerateRequest`: `profile_id` int, `platform` Platform, `content_type` ContentType, `campaign` str (tag slug, required), `asset_id` int|None, `asset_context` str ≤1000 (extra context typed by user, optional), `seasonal_context` str ≤500, `tone_variation` str ≤200, `cta_request` str ≤200 (`""` = let model decide), `extra_restrictions` list[str] ≤10, `idempotency_key` str ≤64 optional. Validator: platform/content_type combo valid.
- `RegenerateRequest`: `tone_variation` optional, `extra_instructions` str ≤500 optional.
- `GeneratedContent` (the Claude output schema — model only produces these fields):
  `content_format: str`, `hook: str`, `caption: str`, `hashtags: list[str]`, `call_to_action: str`, `content_notes: str`. All required (empty string allowed for hook/CTA).
- `PostCreate` (manual post): profile_id, platform, content_type, campaign, asset_id?, hook, caption (1–2200 required), hashtags, call_to_action, content_notes, tags, planned_publish_at?, idempotency_key?.
- `PostUpdate`: hook, caption, hashtags, call_to_action, content_notes, campaign, tags, asset_id, planned_publish_at (explicit `null` clears it — use `model_fields_set` to tell "absent" from "null"). **No `status` field.**
- `PostOut`: all fields + `tags`, `asset` (AssetOut|None), `variant_count`, `is_published=False` never invented — just `status`.
- `VariantOut`.
- `ErrorOut`: `{"error": {"code": str, "message": str}}`.
- `planned_publish_at` inputs must be timezone-aware ISO strings in the API (naive → 422). Dashboard converts `datetime-local` using `APP_TIMEZONE`.

---

## 8. Claude integration

### 8.1 `app/services/claude_client.py`

```python
class GenerationError(Exception):
    def __init__(self, category: str, message: str, http_status: int): ...

@dataclass
class ClaudeResult:
    content: GeneratedContent
    model: str
    input_tokens: int | None
    output_tokens: int | None
    request_id: str | None

class ClaudeClient:
    def __init__(self, settings): build anthropic.Anthropic(api_key=..., timeout=..., max_retries=...) lazily;
        if no key -> self.client = None
    def generate(self, system: str, user: str) -> ClaudeResult
```

The call (use `messages.create` with a JSON schema — do not use beta APIs):
```python
response = self.client.messages.create(
    model=settings.claude_model,
    max_tokens=settings.claude_max_tokens,
    system=system,
    messages=[{"role": "user", "content": user}],
    output_config={
        "effort": settings.claude_effort,
        "format": {"type": "json_schema", "schema": GENERATED_CONTENT_SCHEMA},
    },
)
```
- `GENERATED_CONTENT_SCHEMA`: hand-written dict: `type: object`, the 6 properties (strings; `hashtags` = array of strings), `required` = all 6, `additionalProperties: False`. **No min/max/pattern constraints in the schema** — enforce limits in Python after parsing.
- Do not send `thinking`, `temperature`, `top_p` (rejected on current models). Do not send images.
- Handle `stop_reason` before reading content:
  - `"refusal"` → `GenerationError("refusal", "Claude declined this request. Adjust the description/context and try again.", 422)`
  - `"max_tokens"` → `GenerationError("truncated", ..., 502)`
- Take the first block where `block.type == "text"` (thinking blocks may come first). None → `invalid_output`.
- `GeneratedContent.model_validate_json(text)`; `ValidationError` / JSON error → `invalid_output` (502). Log the error category only, never the text.
- Usage: `response.usage.input_tokens`, `response.usage.output_tokens`; `response._request_id`.
- No app-level retry loop. The SDK's `max_retries` covers transient failures. Invalid output = the user clicks Regenerate (prevents runaway charges).
- Refusal fallback to another model (`fallbacks` beta) is **intentionally not enabled**: it would silently switch the persona's model and add cost. Revisit if refusals turn out frequent.

### 8.2 Error mapping (catch most-specific first)
| Exception | category | HTTP | user message |
|---|---|---|---|
| no API key configured | `not_configured` | 503 | "ANTHROPIC_API_KEY is not set. See README → Configure Claude." |
| `anthropic.AuthenticationError` | `auth_error` | 502 | "Anthropic rejected the API key." |
| `anthropic.PermissionDeniedError` | `permission_denied` | 502 | "API key lacks permission / credits." |
| `anthropic.NotFoundError` | `model_not_found` | 502 | "Model '{model}' not found — check CLAUDE_MODEL." |
| `anthropic.RateLimitError` | `rate_limited` | 429 | "Anthropic rate limit reached, try again shortly." |
| `anthropic.BadRequestError` | `bad_request` | 502 | "Anthropic rejected the request." (log `e.message` server-side; check it doesn't contain the key) |
| `anthropic.APITimeoutError` | `timeout` | 504 | |
| `anthropic.APIConnectionError` | `connection_error` | 503 | |
| `anthropic.APIStatusError` (other, ≥500) | `upstream_error` | 503 | |
| refusal / truncated / invalid_output | see 8.1 | | |

Note: `APITimeoutError` is a subclass of `APIConnectionError` — catch it first.

### 8.3 Post-parse validation (`services/generation.py`, function `check_output`)
- Strip leading `#` and whitespace from hashtags; drop empties and duplicates (case-insensitive); reject tags containing spaces (remove them).
- Limits: caption ≤ 2200 chars (both platforms); hashtags: Instagram ≤ 15 kept (hard cap 30 by platform), TikTok ≤ 8. Truncate the hashtag list to the cap (don't fail). Caption over limit → `invalid_output`.
- Caption empty → `invalid_output`.
- Political keyword guard: **do not** build a keyword filter (false positives). Political avoidance is a prompt rule + human review.

### 8.4 Generation flow (`services/generation.py`)
`generate_draft(db, req, client) -> ContentPost`
1. If `req.idempotency_key` and a post with that key exists → return it (no API call).
2. Load profile (404 if missing, 409 if inactive). Load asset if given (404 if missing/inactive). Campaign must be an existing tag slug, else 422.
3. `system = render_system(profile, platform, content_type)`, `user = render_user(req, asset)`.
4. `t0`; call `client.generate`; on `GenerationError` write a failed `GenerationLog` (commit) and re-raise.
5. `check_output`. On failure log `invalid_output` and raise.
6. Create `ContentPost` (status draft, source ai, `generation_input=req.model_dump(mode="json")`, tags = [campaign]), `ContentVariant` from the same output, success `GenerationLog` with post_id. Single commit. If the commit hits `IntegrityError` on idempotency_key (race), rollback and return the existing post.

`regenerate(db, post_id, req, client) -> ContentVariant`
- Post must exist and status ∈ {draft, approved, scheduled}; archived → 409.
- Rebuild request from `post.generation_input` (manual posts: build from post fields; caption used as context), overlay `tone_variation`/`extra_instructions`, plus tell the prompt to differ from the existing caption (include current caption in `<avoid_repeating>`).
- Append a variant + log. **Do not change the post.**

`apply_variant(db, post_id, variant_id)` (in `services/posts.py`) — copies variant fields onto the post; if post was approved/scheduled → back to `draft` (content changed, needs re-approval).

### 8.5 Rate limiting
`security.py`: in-memory sliding window keyed by client IP for `/api/v1/content/*` and dashboard generate POSTs. Exceed → 429 `rate_limited`. Comment: `# in-memory, single process; move to DB/proxy if running multiple workers`.

---

## 9. Prompts (`app/prompts/`)

`PROMPT_VERSION = "v1"` (stored in every GenerationLog). Changing a template = new file `system_v2.j2` + bump version; never edit a released version silently.

### 9.1 `platforms.py`
```python
CONTENT_TYPES = {
  "instagram": ["feed_post", "reel", "story"],
  "tiktok": ["video", "photo_post"],
}
HASHTAG_CAP = {"instagram": 15, "tiktok": 8}
PLATFORM_RULES = {
 "instagram": {
   "feed_post": "Instagram feed photo caption. First line is the hook (visible before 'more'). 1–4 short lines total. 3–10 relevant hashtags returned separately, mix of niche and broad.",
   "reel": "Instagram Reel caption. Very short punchy hook that works with the video. 1–3 short lines. 3–8 relevant hashtags.",
   "story": "Instagram Story text overlay idea + short caption. Keep it under 120 characters. 0–3 hashtags.",
 },
 "tiktok": {
   "video": "TikTok caption. Hook-first, casual, lowercase-friendly, under 150 characters before hashtags. 3–6 hashtags; trend-style tags only if relevant. content_notes: suggest an on-screen text hook for the first 2 seconds.",
   "photo_post": "TikTok photo-mode caption. Short and playful. 3–6 hashtags.",
 },
}
GENERAL_PLATFORM_RULES = [
  "Content must stay within Instagram and TikTok community guidelines: suggestive and flirty is fine, explicit is not.",
  "Never mention or link adult-subscription sites by name on Instagram or TikTok; use 'link in bio' style CTAs.",
]
```

### 9.2 `system_v1.j2` (render with Jinja2 `Environment(loader=PackageLoader("app","prompts"), autoescape=False, undefined=StrictUndefined)`)
```
You write social media copy for {{ profile.name }}, an AI-generated creator persona.

<persona>
{{ profile.persona_description }}
</persona>

<tone_and_style>
{{ profile.tone_style }}
</tone_and_style>

<communication_rules>
{% for r in profile.communication_rules %}- {{ r }}
{% endfor %}</communication_rules>

<restricted_topics>
Never write about these. If the brief touches them, write neutral copy that ignores them:
{% for t in profile.restricted_topics %}- {{ t }}
{% endfor %}- Politics of any kind is always off-limits, even if requested.
</restricted_topics>
{% if profile.style_notes %}
<style_notes_from_reference_accounts>
{{ profile.style_notes }}
Learn from the approach; never copy wording from any real account.
</style_notes_from_reference_accounts>
{% endif %}
<cta_guidelines>
{{ profile.cta_guidelines }}
Only include a call to action when it fits the post. An empty call_to_action is fine.
</cta_guidelines>

<platform>
Platform: {{ platform }} · Format: {{ content_type }}
{{ platform_rule }}
{% for r in general_rules %}- {{ r }}
{% endfor %}</platform>

<accuracy>
- You cannot see the media. Only describe what the provided media description states. If no description is given, write copy that does not describe the visual content specifically.
- Do not invent activities, places, events, purchases, relationships or personal history.
- Do not claim to be human or deny being AI if the copy touches on it.
</accuracy>

<output>
Return only the JSON object required by the schema:
- content_format: the format you wrote for (e.g. "instagram reel caption")
- hook: the opening line/on-screen hook (may repeat the caption's first line)
- caption: full caption text WITHOUT hashtags
- hashtags: relevant tags without '#', no keyword stuffing
- call_to_action: the CTA used in the caption, or ""
- content_notes: 1–3 short notes for the human reviewer (e.g. posting tips, on-screen text idea, anything to double-check). Always remind that AI-generated content should carry the platform's AI label.
Write fresh copy each time: vary hook style (question, tease, statement, playful challenge) and sentence structure.
</output>
```

### 9.3 `user_v1.j2`
```
Write one {{ platform }} {{ content_type }} post.

<campaign>{{ campaign_name }}{% if seasonal_context %} — {{ seasonal_context }}{% endif %}</campaign>

<media>
{% if asset %}Type: {{ asset.media_type }}
Description (written by the team, the only reliable info about the media): {{ asset.description or "No description provided." }}{% else %}No media attached (text-first post).{% endif %}
{% if asset_context %}Extra context: {{ asset_context }}{% endif %}
</media>
{% if tone_variation %}<tone_variation>{{ tone_variation }}</tone_variation>{% endif %}
{% if cta_request %}<requested_cta>{{ cta_request }}</requested_cta>{% endif %}
{% if extra_restrictions %}<extra_restrictions>{% for r in extra_restrictions %}
- {{ r }}{% endfor %}
</extra_restrictions>{% endif %}
{% if avoid_repeating %}<avoid_repeating>
Write a clearly different alternative to this existing caption:
{{ avoid_repeating }}
</avoid_repeating>{% endif %}
{% if extra_instructions %}<extra_instructions>{{ extra_instructions }}</extra_instructions>{% endif %}
```
User-supplied text is placed inside tags as data. Keep the system prompt free of per-request values except platform/format.

---

## 10. Media & assets (`app/services/assets.py`, `app/security.py`)

**Allowed types** (extension → mime → media_type):
`.jpg/.jpeg image/jpeg`, `.png image/png`, `.webp image/webp`, `.gif image/gif`, `.mp4 video/mp4`, `.mov video/quicktime`, `.webm video/webm`. Everything else → 415 `unsupported_file_type`.

**Magic-byte check** (first 16 bytes): jpeg `FF D8 FF`; png `89 50 4E 47`; gif `GIF8`; webp `RIFF....WEBP`; mp4/mov `....ftyp` (bytes 4–8); webm `1A 45 DF A3`. Mismatch → 415.

**Upload** (`POST /api/v1/assets/upload`, multipart: `file`, `description`, `tags` (comma-separated), `model_profile_id`):
1. Check extension, then stream the `UploadFile` in 1 MB chunks into `MEDIA_UPLOAD_DIR/tmp-<uuid>`, computing sha256 and size; abort + delete when exceeding `MAX_IMAGE_MB`/`MAX_VIDEO_MB` → 413.
2. Magic check on first chunk.
3. If sha256 already exists on an active asset → delete temp, return 409 `duplicate_asset` with existing id.
4. Rename to `<uuid4hex><ext>`; `local_relpath` = that name; `filename` = sanitized original name (`Path(name).name`, ≤255).

**Local sync** (`sync_media_dir(db)`, used by `python -m app.cli sync-media`, `POST /api/v1/assets/sync`, and a dashboard button):
- If `MEDIA_SYNC_DIR` unset → 400 `sync_not_configured`.
- `rglob` files with allowed extensions; skip hidden files and Dropbox placeholders (size 0). For each, `relpath = path.relative_to(root).as_posix()`. If (`local_sync`, relpath) already registered → skip. Else register (sha256 hash, size, mime from extension, filename = name). Also auto-tag: if any folder name in relpath matches an existing tag slug (case-insensitive, e.g. a `Halloween/` folder) → add that tag.
- Never copies, moves or deletes source files. Returns `{"added": n, "skipped": m}`.
- Note for docs: with Dropbox "online-only" files, Windows downloads the file when it's hashed — tell the client to mark the Summer folder "Available offline".

**External URL** registration: stores the link only. No preview, no download. Dashboard shows "Open in Dropbox" link.

**Safe path helpers (`security.py`):**
- `safe_relpath(s) -> str`: reject empty, absolute paths, drive letters (`C:`), `..` segments, backslashes converted to `/` first, NUL bytes. Returns normalized posix relpath.
- `resolve_asset_file(asset) -> Path`: `root = upload_dir or sync_dir` by storage type; `p = (root / asset.local_relpath).resolve()`; require `p.is_relative_to(root.resolve())` and `p.is_file()`, else 404. **The only way files are served.** No endpoint accepts a filesystem path to read.

**Serving:** `GET /api/v1/assets/{id}/file` → `FileResponse(path, media_type=asset.mime_type)` (supports range requests for video). Dashboard uses `<img loading="lazy">` / `<video controls preload="metadata">`.

**Filtering** (`list_assets`): `q` (filename/description `ilike`), `tag` (repeatable; asset must have ALL given tags), `media_type`, `storage_type`, `unused` (bool: no non-archived posts), `include_inactive` (default false), `limit` (≤100, default 50), `offset`. Order `imported_at desc`.

**Delete:** `DELETE /api/v1/assets/{id}` = archive (`is_active=False`), returns 200 with the asset. No hard delete, no file deletion.

Tags input everywhere: list of names → `get_or_create_tags(db, names)` slugifies (`lower`, non-alnum → `-`, strip `-`, max 60; empty → 422).

---

## 11. Content queue & status rules (`app/services/posts.py`)

**Transition table (the only place statuses change):**
| action | allowed from | to | side effects |
|---|---|---|---|
| approve | draft | approved → **scheduled if planned_publish_at is set** | approved_at = now |
| archive | draft, approved, scheduled, failed | archived | archived_at = now |
| restore | archived | draft | archived_at = None |
| set planned_publish_at (PATCH) | draft, approved, scheduled | draft stays draft; approved → scheduled; scheduled with null → approved | |
| content edit (PATCH caption/hook/hashtags/cta/notes/asset) or apply_variant | draft, approved, scheduled | draft | approved_at = None |
| any edit | archived, published | — | 409 `invalid_transition` |

Invalid action → 409 `{"error":{"code":"invalid_transition","message":"Cannot approve a post with status 'archived'."}}`.
`published`/`failed` are never set in Phase 1. No code path sets them. Add a test asserting no endpoint accepts `status` in a body.
`planned_publish_at` in the past → 422 when setting it (allow unchanged values on unrelated edits).

**Listing** (`list_posts`): filters `platform`, `status` (repeatable), `campaign`, `asset_id`, `profile_id`, `planned_from`, `planned_to`; default excludes archived unless `status=archived` requested; order `planned_publish_at asc nulls last, created_at desc`; limit/offset.

**Manual create** (`POST /api/v1/posts`): source manual, idempotency like generate.

---

## 12. API (`app/api/`)

All JSON errors use `ErrorOut`. Register handlers in `main.py`:
- `RequestValidationError` → 422 `validation_error`, message = joined `loc: msg` (never echo input values).
- `GenerationError` → its status/category/message.
- custom `AppError(code, message, status)` raised by services → that status.
- `Exception` → 500 `internal_error`, "Unexpected error" (full traceback only in the server log).

Docs: keep `/docs` on in development; set `docs_url=None, redoc_url=None` when `APP_ENV=production`.

| Method & path | Body/query | Success | Errors |
|---|---|---|---|
| GET `/health` | — | 200 `{"status":"ok","database":"ok","claude_configured":bool,"version":"0.1.0"}` | 503 if DB `SELECT 1` fails |
| GET `/api/v1/profiles` | `active` bool? | 200 list[ProfileOut] | |
| GET `/api/v1/profiles/{id}` | | 200 | 404 `not_found` |
| PATCH `/api/v1/profiles/{id}` | ProfileUpdate | 200 | 404, 422 |
| GET `/api/v1/tags` | `category_only` bool | 200 | |
| GET `/api/v1/assets` | filters §10 | 200 `{"items":[...],"total":n}` | 422 |
| POST `/api/v1/assets` | AssetCreate (reference registration) | 201 | 409 duplicate relpath, 422 |
| POST `/api/v1/assets/upload` | multipart | 201 | 413, 415, 409, 422 |
| POST `/api/v1/assets/sync` | — | 200 `{"added","skipped"}` | 400 |
| GET `/api/v1/assets/{id}` | | 200 | 404 |
| GET `/api/v1/assets/{id}/file` | | file | 404 |
| PATCH `/api/v1/assets/{id}` | AssetUpdate | 200 | 404, 422 |
| DELETE `/api/v1/assets/{id}` | | 200 (archived) | 404 |
| POST `/api/v1/content/generate` | GenerateRequest; header `Idempotency-Key` overrides body | 201 PostOut (200 if idempotent replay) | 404/409/422/429/502/503/504 |
| POST `/api/v1/content/{post_id}/regenerate` | RegenerateRequest | 201 VariantOut | 404, 409, gen errors |
| GET `/api/v1/posts` | filters §11 | 200 `{"items","total"}` | |
| POST `/api/v1/posts` | PostCreate | 201 (200 on replay) | 404, 422 |
| GET `/api/v1/posts/{id}` | | 200 | 404 |
| PATCH `/api/v1/posts/{id}` | PostUpdate | 200 | 404, 409, 422 |
| GET `/api/v1/posts/{id}/variants` | | 200 list | 404 |
| POST `/api/v1/posts/{id}/variants/{vid}/apply` | | 200 PostOut | 404, 409 |
| POST `/api/v1/posts/{id}/approve` | | 200 | 404, 409 |
| POST `/api/v1/posts/{id}/archive` | | 200 | 404, 409 |
| POST `/api/v1/posts/{id}/restore` | | 200 | 404, 409 |

Differences from client spec (document in README): added `/assets/upload`, `/assets/sync`, `/assets/{id}/file`, `/tags`, `/posts/{id}/variants*`, `/posts/{id}/restore`; DELETE asset = archive.

Generation routes are `def` (sync) endpoints — FastAPI runs them in a threadpool; the Anthropic sync client is fine. Inject the client with a dependency `get_claude_client()` (returns a module-level singleton built from settings) so tests override it with `app.dependency_overrides`.

---

## 13. Dashboard (`app/web/`)

Server-rendered Jinja2, plain HTML forms, Post/Redirect/Get with flash messages (store flash in a signed cookie? **No** — pass `?msg=...&kind=ok|error` on redirect; escape on render). Small hand-written `style.css` (CSS grid, system font, ~150 lines). No JS frameworks; inline `<script>` only for `confirm()` on archive and disabling the submit button on click.

**CSRF mitigation:** middleware in `security.py` — for unsafe methods on non-`/api` paths, if an `Origin` or `Referer` header is present and its host ≠ request host → 403. (API is protected by Basic auth + no cookies.)

**Idempotency in forms:** generate & new-post forms include `<input type="hidden" name="idempotency_key" value="{{ uuid4 }}">` generated at render time.

| Page | Route | Content |
|---|---|---|
| Overview | `GET /` | Cards: total active assets, total posts (non-archived), drafts awaiting approval, approved, scheduled. Table: counts by platform × status. Recent 10 posts. "Upcoming" = next 7 scheduled. All from DB queries in `services/posts.py::stats()`. |
| Media library | `GET /assets` | Filter form (q, tag dropdown incl. Halloween first, media type, unused only). Grid of cards: preview, filename, tags, usage count, "Generate" button → `/generate?asset_id=`. Buttons: "Upload", "Register Dropbox link", "Sync Dropbox folder" (POST, shown only if MEDIA_SYNC_DIR set). |
| New asset | `GET/POST /assets/new` | Two forms: upload file; register external URL / local_sync path. |
| Asset detail | `GET/POST /assets/{id}` | Large preview, metadata edit form (filename, description with hint *"Describe what's actually in the image — Claude only knows what you write here"*, tags comma-separated), archive button, list of posts using it. |
| Generator | `GET/POST /generate` | Fields: profile (active only), asset (select, filtered by `?tag=`; preselect from query), platform, content type (all options listed; server validates combo), campaign (category/tag select, Halloween default), seasonal context, tone variation, CTA request, extra restrictions (one per line). Submit → service → redirect to post detail. On error re-render the form with values kept and a red error box showing the message (e.g. missing API key). |
| Queue | `GET /posts` | Filters platform/status/campaign. Table: thumbnail, platform, type, campaign, status badge, created, planned (APP_TIMEZONE), caption first 100 chars, actions: Open, Approve, Archive. |
| Post detail | `GET/POST /posts/{id}` | Asset preview. Edit form (hook, caption with live char count, hashtags space/comma separated, CTA, notes, campaign, tags, planned publish `datetime-local`). Buttons: Save, Approve, Archive/Restore, "Generate alternative" (tone variation + extra instructions inputs). Variants list (newest first) each with "Use this version". Status banner text: *"Approved/scheduled posts are NOT published automatically in Phase 1. Post manually."* |
| Profile | `GET/POST /profiles/{id}` (+ `/profiles` list) | Name, persona description, tone/style, communication rules (one per line), restricted topics (one per line), style notes, CTA guidelines, handles (read-only display), active checkbox. |

Form POST handlers convert form data → the same Pydantic schemas → same services. Validation errors → re-render with message.

---

## 14. Security checklist (all must be true)

- [ ] `.env` gitignored; `.env.example` placeholders only; `git grep -i "sk-ant"` returns only the placeholder.
- [ ] `ANTHROPIC_API_KEY` is `SecretStr`; never in logs, responses, templates, GenerationLog, exception messages.
- [ ] `/health` returns `claude_configured: bool` only.
- [ ] Generic 500 handler; no tracebacks in responses.
- [ ] Upload: extension allow-list + magic bytes + size limit + server-generated filenames.
- [ ] `safe_relpath` + `resolve_asset_file` containment check; no endpoint reads a user-supplied path directly.
- [ ] Optional Basic auth (use `secrets.compare_digest`) on all routes except `/health`; README says it's mandatory before VPS exposure (plus HTTPS via reverse proxy).
- [ ] Origin/Referer check for dashboard POSTs.
- [ ] Rate limit on generation.
- [ ] Jinja2 autoescape ON for HTML templates (default with `.html`); prompt templates use a separate env with autoescape off.
- [ ] No social account passwords stored anywhere.

---

## 15. Logging (`app/logging_setup.py`)

- stdlib `logging`, one `StreamHandler` to stdout with a `JsonFormatter` (`json.dumps` of `ts`, `level`, `logger`, `msg`, plus any `extra=` keys).
- Middleware logs one line per request: method, path, status, duration_ms (no query strings, no bodies).
- Generation logs: `event=generation`, profile_id, platform, success, error_category, tokens, duration_ms, request_id.
- Exception handler logs with `exc_info=True`.

---

## 16. Tests (`tests/`)

`pytest.ini`: `testpaths = tests`, `addopts = -q`.

**`conftest.py`**
- `settings` fixture: env overrides via `monkeypatch.setenv` → `DATABASE_URL=sqlite:///{tmp_path}/test.db`, `MEDIA_UPLOAD_DIR={tmp_path}/media`, `MEDIA_SYNC_DIR={tmp_path}/sync`, `ANTHROPIC_API_KEY=sk-ant-test-SECRET123`; clear `get_settings.cache_clear()`.
- `app` fixture: `create_app()` after settings; `Base.metadata.create_all(engine)`; run seeds.
- `client` fixture: `TestClient(app)`.
- `FakeClaude` class with `.result` or `.error` attribute and a `.calls` counter; `fake_claude` fixture installs it via `app.dependency_overrides[get_claude_client]`.
- `halloween_asset` fixture: uploads `tests/fixtures/tiny.png` tagged `halloween` with a description.

For `test_claude_client.py` build real SDK exceptions:
```python
req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
anthropic.RateLimitError("rate", response=httpx.Response(429, request=req), body=None)
anthropic.APIConnectionError(request=req)
anthropic.APITimeoutError(request=req)
```
and mock `client.client.messages.create` with `unittest.mock.MagicMock` returning a `SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json_str)], usage=SimpleNamespace(input_tokens=10, output_tokens=20), _request_id="req_1", model="m")`.

**Required test cases** (one function each, names indicative):

| File | Tests |
|---|---|
| test_app.py | app starts; `/health` 200 with `claude_configured`; DB init creates all tables; `alembic upgrade head` on empty temp DB succeeds and `alembic check`/autogenerate shows no diff vs models; init-db twice is idempotent |
| test_profiles.py | list returns Summer; get Summer has persona fields; PATCH tone persists; PATCH invalid (empty name) 422; GET 999 → 404 JSON error shape; unknown field → 422 |
| test_assets.py | register external_url 201; register local_sync with valid relpath; upload png 201 + `/file` returns bytes; PATCH description/tags; filter by tag=halloween returns only tagged; filter q; filter unused; upload `.exe` → 415; png extension with non-png bytes → 415; oversize (set MAX_IMAGE_MB=0 via env… use tiny limit) → 413; duplicate upload → 409; missing filename → 422; archive hides from default list; sync registers files in tmp sync dir, auto-tags `Halloween/` folder, second sync adds 0 |
| test_generation.py | invalid platform/content_type combo → 422; unknown campaign → 422; prompt contains persona description, restricted topics, "Politics", platform rule text for instagram reel vs tiktok video (call `render_system` directly); user prompt contains asset description and "No description provided" when empty; valid fake output → post persisted with draft status + 1 variant + success log; hashtags normalized (`#Tag` → `Tag`, dedupe, capped); FakeClaude raising GenerationError("rate_limited") → 429, failure log written, no post; missing API key (unset env, real ClaudeClient) → 503 `not_configured`; same idempotency_key twice → one post, FakeClaude called once; regenerate adds variant and leaves post caption unchanged; regenerate archived → 409 |
| test_claude_client.py | valid JSON parsed into GeneratedContent + usage captured; malformed JSON → invalid_output; missing field → invalid_output; thinking block before text handled; stop_reason refusal → refusal; max_tokens → truncated; RateLimitError → rate_limited; APIConnectionError → connection_error; APITimeoutError → timeout; AuthenticationError message doesn't include key |
| test_posts.py | manual create + get; persists after "restart" (dispose engine, new app on same DB file, GET still returns it); PATCH caption; approve draft → approved; approve with planned time → scheduled; set time on approved → scheduled; clear → approved; edit approved → draft; archive → archived; approve archived → 409 `invalid_transition`; PATCH with `status` field → 422; past planned time → 422; filter platform; filter status; apply variant copies fields; no endpoint ever yields `published` |
| test_security.py | API key string `sk-ant-test-SECRET123` not in any response body of: /health, profiles, a forced 500 (route raising), a generation auth error; 500 response has no traceback; `safe_relpath` rejects `../x`, `/etc/passwd`, `C:\\x`, `a/../../b`, `\\\\server\\share`; local_sync asset with relpath escaping root → 422; Basic auth on: no creds → 401, right creds → 200, `/health` open; cross-origin dashboard POST → 403 |
| test_dashboard.py | each page GET returns 200 and contains real data (e.g. asset filename, post caption, counts match API); generate form with FakeClaude redirects to post page; generate with missing key shows error text on page |

Command: `python -m pytest`. Report the real pass/fail counts in each milestone summary.

---

## 17. Milestones (do in order; each has a "done when")

### M1 — Foundation
Tasks: venv, requirements, `.gitignore`, `.env.example`, `git init`; `config.py`, `db.py`, `models.py`, `logging_setup.py`, `main.py` (+ error handlers), Alembic + `0001_initial`, `seed.py`, `seed/summer.json`, `cli.py init-db`, `/health`, profiles API, tags API, `conftest.py`, `test_app.py`, `test_profiles.py`.
**Done when:** `python -m app.cli init-db` works twice; `uvicorn app.main:app --reload` serves `/health`; `GET /api/v1/profiles` shows Summer; tests green.

### M2 — AI service
Tasks: `prompts/*`, `claude_client.py`, `generation.py` (generate + regenerate), `content.py` routes, minimal `posts` model usage needed by generation, rate limiter, `test_claude_client.py`, `test_generation.py`.
**Done when:** tests green with fakes; **manual smoke** with a real key: one Instagram reel + one TikTok video draft generated via `/docs` (cost ≈ cents). If no API key/credits yet → record as blocker, continue (everything else is testable with fakes).

### M3 — Assets
Tasks: `security.py` path/file helpers, `services/assets.py` (upload, register, sync, filters, archive), assets API + `/file`, `cli.py sync-media`, `test_assets.py`, path tests in `test_security.py`.
**Done when:** upload/preview/filter/sync work via API; tests green.

### M4 — Content queue
Tasks: `services/posts.py` (CRUD, transitions, variants apply, stats), posts API, `test_posts.py`.
**Done when:** full lifecycle draft → approved → scheduled → archived → restored via API; restart persistence test green.

### M5 — Dashboard
Tasks: templates, `web/routes.py`, `style.css`, Basic auth + origin check, `test_dashboard.py`, rest of `test_security.py`.
**Done when:** every page in §13 works in a browser with real data; error states visible (unset API key → red message on generator page).

### M6 — Validation & handover
Tasks: full test run; end-to-end Halloween demo (§18) on a fresh DB with a real key; fix defects; README, `docs/DEMO.md`, `docs/DECISIONS.md`, `docs/REFERENCE_RESEARCH.md`; `pip freeze > requirements.lock`; secret scan.
**Done when:** every item in §20 is checked with evidence.

**After each milestone write a summary** (append to `docs/PROGRESS.md`): files created/changed, features done, exact test command + real result, known issues, next milestone.

---

## 18. Halloween demo script (`docs/DEMO.md`)

1. `python -m app.cli init-db`, start server, open `http://127.0.0.1:8000`.
2. Media library → Upload 2–3 Halloween images/videos (or Sync Dropbox folder) → tag `halloween` → write a factual description for each.
3. Filter library by tag Halloween → click "Generate" on an image.
4. Instagram · feed_post · campaign Halloween · seasonal context "Halloween week" → Generate → lands on draft.
5. "Generate alternative" with tone variation "more teasing" → variant appears; original remains.
6. Edit caption → Save → "Use this version" on a variant if preferred.
7. Approve → set planned publish date → status becomes Scheduled (banner confirms nothing is auto-published).
8. Repeat for a video asset on TikTok · video.
9. Queue → filter campaign Halloween → shows both; Overview counts match.
10. Archive one draft → disappears from default queue, visible under status=archived.

---

## 19. Documentation deliverables

- **README.md**: overview; requirements (Python 3.12+); Windows + Linux setup commands; configure Anthropic key (console.anthropic.com → API keys → add credits; subscription ≠ API credits); choose model (`CLAUDE_MODEL`); `init-db`; run (`uvicorn app.main:app --host 127.0.0.1 --port 8000`); Dropbox options (upload / desktop sync folder / link-only) and their limits; run tests; project structure; architecture (copy §2 diagram + decisions table); API endpoint list + differences from spec; implemented features; known limitations & external dependencies (below); VPS notes (set Basic auth, run behind Caddy/Nginx with HTTPS, single uvicorn worker because of in-memory rate limiter + SQLite, back up `data/`); Postgres path (`DATABASE_URL=postgresql+psycopg://...`, add `psycopg[binary]`, `alembic upgrade head`).
- **docs/REFERENCE_RESEARCH.md**: the six reference URLs; states Instagram profiles were **not** scraped (login wall, ToS); a template table for a human to fill (hook styles, caption length, emoji use, CTA patterns, posting cadence, formats) and an instruction to paste the summary into Summer's `style_notes` via the Profile page.
- **docs/DECISIONS.md**: decisions from §2 + any deviations made during build.

**Known limitations to state honestly:** no publishing (Phase 2); no Dropbox API (needs app key + OAuth; desktop sync folder recommended meanwhile); Claude doesn't see images (descriptions written by humans; image-input is a possible Phase 2 add); single-user, Basic-auth only; in-memory rate limiter; reference accounts not auto-analyzed; AI output must be human-reviewed; platforms require AI-content labels — apply manually when posting; X account pending.

---

## 20. Definition of Done (map to client spec §16)

| # | Criterion | Evidence |
|---|---|---|
| 1 | Starts from README | fresh clone → commands → `/health` ok |
| 2 | DB initializes & retains data | init-db + restart test |
| 3 | Persona retrieve/edit | profiles tests + Profile page |
| 4 | Assets register/tag/browse/associate | assets tests + library page |
| 5 | Instagram draft via Claude | smoke test with real key (post id + log row) |
| 6 | TikTok draft via Claude | same |
| 7 | Tone + no politics | prompt tests + manual review of smoke output |
| 8 | Halloween asset → campaign draft | DEMO.md run |
| 9 | Edit/save/retrieve/approve/archive | posts tests |
| 10 | Planned dates, no fake publishing | transition tests + banner |
| 11 | Dashboard shows real data | dashboard tests |
| 12 | Automated tests incl. mocked AI | `python -m pytest` output |
| 13 | .env.example/.gitignore/docs/deps | files present |
| 14 | No secrets committed | `git grep -i "sk-ant"` |
| 15 | New profile without code duplication | add 2nd profile via a seed JSON or SQL insert, generate with it in a test |

**External blockers to report if hit:** Anthropic API key + credits (needed only for criteria 5–7 live check); real Halloween assets from the Dropbox folder (demo uses whatever is provided — never invent descriptions).

---

## 21. Explicitly NOT in Phase 1
Publishing (IG/TikTok/X), platform OAuth, DMs/conversations, Fanvue anything, payments, analytics, calendar view, multi-tenant/user accounts, background jobs/schedulers, automatic multi-post batch generation, Instagram scraping, image input to Claude, refusal-fallback models.
