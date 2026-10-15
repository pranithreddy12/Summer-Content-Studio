# Build Decisions

Decisions from the build plan (§2) plus every deviation made during construction.

## Inherited from the plan (do not revisit)

| Topic | Decision |
|---|---|
| Persona storage | DB `model_profiles`, seeded once from `app/seed/summer.json`; dashboard edits DB only |
| Generation output | Claude returns JSON constrained by `output_config.format` (json_schema), re-validated by `GeneratedContent` Pydantic model |
| Generate = create draft | `POST /content/generate` calls Claude AND persists draft + first variant in one commit |
| Variations | Separate `content_variants` table; regenerate appends; post changes only on explicit "apply" |
| Duplicate prevention | unique `idempotency_key` on posts; hidden UUID in forms; `Idempotency-Key` header on API; same key → existing post, no Claude call (race-safe via IntegrityError catch) |
| Status changes | Single transition table in `services/posts.py`; action endpoints only; PATCH cannot set `status` (schema `extra="forbid"`) |
| Dropbox | No Dropbox API in Phase 1. Three storage types: `upload`, `local_sync` (registered in place, never copied), `external_url` (link only, no preview) |
| Tags/categories | One `tags` table, `is_category=True` for the 6 seeded categories; M2M to assets and posts |
| Migrations | Alembic with `render_as_batch=True` |
| Auth | Optional HTTP Basic on everything except `/health`, enabled when both `DASHBOARD_USERNAME`+`DASHBOARD_PASSWORD` set (`secrets.compare_digest`) |
| Model | `CLAUDE_MODEL` env var, default `claude-opus-5-5` |
| No fake functionality | No publishing/scraping/OAuth; `published`/`failed` statuses exist in the data model only |

## Build decisions & deviations made

1. **JSON columns use SQLAlchemy `JSON` type, not `Text`.** The original M1 code stored lists/dicts as `Text` with manual `json.dumps`/`json.loads`, which caused `sqlite3.ProgrammingError: dict not supported` and made Jinja iterate over JSON *strings* in prompts. Native `JSON` is Postgres-compatible (typed as `json` later) and skips app-level serialization entirely. Migration `6fe077f2856a_0002_json_columns`.
2. **Service errors use one `AppError(code, message, status)`** (in `app/services/__init__.py`) converted to the `ErrorOut` shape by a handler in `main.py`. Plan left the mechanism open; string-matching `ValueError` in routes would have been fragile.
3. **`generation_log.post_id` set via `db.flush()`** before linking the success log row and the first variant, then one commit (plan: "set after success").
4. **`init-db` runs Alembic first** (`upgrade head`), with `create_all` + `stamp` only as a legacy fallback for DBs created before migrations existed. M1 initially did `create_all` then upgrade, which made `upgrade` fail on existing tables.
5. **Rate-limiter store is cleared per test** in `conftest.py` — the in-memory sliding window persisted across tests within one process and caused order-dependent 429s.
6. **Starlette 1.8 compatibility:** `Jinja2Templates.TemplateResponse` requires the new `(request=, name=, context=)` signature; all dashboard routes use a small `_template_response()` wrapper (plan assumed the old two-arg style).
7. **`PostOut.variant_count`** is served from a `ContentPost.variant_count` property (derived, not stored).
8. **`planned_publish_at` transition rules implemented exactly per §11**: draft stays draft; approved → scheduled; scheduled with `null` → approved; content edits reset approved/scheduled → draft with `approved_at=None`; past dates rejected with 422.
9. **Dashboard generate/regenerate routes inject the Claude client through `Depends(get_claude_client)`** (bug found by tests: the original code read `app.dependency_overrides` manually and could silently call the real Anthropic API during tests; one real 401 call occurred before the fix, see PROGRESS).
10. **`check_output` drops hashtags containing whitespace entirely** (plan wording "reject tags containing spaces (remove them)").
11. **Multipart upload route is `async` and streams in 1 MB chunks**; generation endpoints are sync `def` so FastAPI threadpools them.
12. **`requirements.lock` committed** via `pip freeze` after first successful install (per plan §3).

## Known limitations (honest list)

- No publishing anywhere — approved/scheduled posts must be posted manually.
- No Dropbox API (desktop sync folder + link registration only).
- Claude never sees images; only the human-written `description` is sent.
- Single-user Basic auth; in-memory rate limiter (single uvicorn worker only).
- Reference accounts were not auto-analyzed; `style_notes` filled by humans.
- AI output requires human review; platforms require AI-content labels — applied when posting manually.
- X account pending (Phase 2).
