# Build Decisions



Decisions from the build plan (Â§2) plus every deviation made during construction.



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

8. **`planned_publish_at` transition rules implemented exactly per Â§11**: draft stays draft; approved → scheduled; scheduled with `null` → approved; content edits reset approved/scheduled → draft with `approved_at=None`; past dates rejected with 422.

9. **Dashboard generate/regenerate routes inject the Claude client through `Depends(get_claude_client)`** (bug found by tests: the original code read `app.dependency_overrides` manually and could silently call the real Anthropic API during tests; one real 401 call occurred before the fix, see PROGRESS).

10. **`check_output` drops hashtags containing whitespace entirely** (plan wording "reject tags containing spaces (remove them)").

11. **Multipart upload route is `async` and streams in 1 MB chunks**; generation endpoints are sync `def` so FastAPI threadpools them.

12. **`requirements.lock` committed** via `pip freeze` after first successful install (per plan Â§3).



## Known limitations (honest list)



- No publishing anywhere — approved/scheduled posts must be posted manually.

- No Dropbox API (desktop sync folder + link registration only).

- Claude never sees images; only the human-written `description` is sent.

- Single-user Basic auth; in-memory rate limiter (single uvicorn worker only).

- Reference accounts were not auto-analyzed; `style_notes` filled by humans.

- AI output requires human review; platforms require AI-content labels — applied when posting manually.

- X account pending (Phase 2).



## Mistral provider (added 2026-10-15)

- Requested because Claude API credits were unavailable for testing. `MistralClient` calls Mistral's REST API through `httpx` (no extra SDK); `LLMClient` routes per `LLM_PROVIDER`.

- Deviation from PLAN §21/§3: `httpx` is now a runtime dependency (it was dev-only). Fallback only on provider failures (auth, permission, model, rate limit, timeout, connection, 5xx) - never on refusal/invalid output/bad request, to avoid double paid calls.

- `GenerationLog.provider`/`model` record the provider that actually served (or failed) the request.

- Persona/prompt are unchanged; Mistral gets the JSON key list appended to the system prompt and uses `response_format=json_object` (not schema-enforced), so output is validated by Pydantic exactly as for Claude.


## Frontend redesign — dark editorial / sidebar (added 2026-10-16)
- Dashboard restyled to a dark editorial theme with a fixed left sidebar shell (client-requested direction). Still no JS framework, no CDN, no new dependency and no external fonts — serif headings use the system `ui-serif` stack, so nothing is fetched at runtime.
- Deviation from PLAN 13: `style.css` is ~850 lines rather than "~150". The larger size buys a full token set (surfaces/type/accent/status), the sidebar shell, and responsive behavior at 1080px/900px; it remains single hand-written CSS with no build step.
- PLAN 13's rule set is otherwise intact: plain HTML forms, Post/Redirect/Get with `?msg=&kind=` flash, inline `<script>` only (confirm on archive, submit-button disable, plus the already-existing tab/char-count/tag-suggest helpers), autoescape unchanged, no route or schema change.
- Default Mistral model is `ministral-8b-latest`: on the free tier `mistral-small/medium-latest` returned 429 (0 req/min) and `mistral-large-latest` 403; ministral-8b returned valid drafts for Instagram and TikTok (see `docs/LIVE_PROOF.md`). Use a larger model on a paid tier for better copy.

## Image input (added 2026-10-18)
- Requested so captions match the real media. Deviation from PLAN §1/§21 ("descriptions only"): `app/services/frames.py` sends images to the AI - the image itself for photos, 3 frames (20/50/80% of duration) for videos - downscaled to max 1024px JPEG via `ffmpeg` (no new Python dependency). Without ffmpeg, small originals (<=3.5 MB) are still sent; videos then fall back to description only.
- Only `upload` and `local_sync` assets (through `resolve_asset_file` containment). `external_url` (Dropbox) is never fetched: shared links are not stable direct-download URLs.
- Prompt version bumped to `v2` (image-aware accuracy rules; `v1` files kept). Works with both providers (Claude image blocks, Mistral `image_url` data URIs); `ministral-8b-latest` supports vision. Verified live on a real 7.6 MB video: 3 frames (66-81 KB each), caption matched the scene.
- Privacy: frames are sent to the configured AI provider. Disable with `ENABLE_IMAGE_INPUT=false`.
- Also fixed: Media Library showed "0 uses" for every asset (page never filled `usage_count`); added a "Used in posts" filter.

## Random draft batch (added 2026-10-18)
- `app/services/random_draft.py`: random pick among active, unused, uploaded/synced assets whose file exists (placeholder videos < 1 KB skipped); one `generate_draft` per asset (so image/frame input, idempotency and logging are reused). Max 5 per request; stops at the first provider error and keeps earlier drafts; a `batch_key` makes a resubmitted form reuse existing drafts instead of paying again.
- Still human-in-the-loop: drafts only, nothing approved or published automatically (consistent with PLAN section 10: no unreviewed automation).
- Dropbox: random selection needs files on disk (`MEDIA_SYNC_DIR`). Reading a Dropbox folder through its share link would need the Dropbox API (app key + token) - not built; candidate for Phase 2.

## Permanent asset deletion (added 2026-10-20)
- `delete_asset_permanently` (services/assets.py). Allowed for `upload` (row + uploaded file, path containment-checked) and `external_url` (row only). `local_sync` is refused (409 `synced_asset`): the Dropbox folder is read-only to the app and a deleted row would be re-registered by the next Sync; archive keeps the relpath registered so it is skipped.
- Assets used by non-archived posts need `force=true` (409 `asset_in_use` otherwise); posts and generation logs are detached (`media_asset_id` -> NULL), never deleted. Default `DELETE` still archives, so PLAN section 12 behaviour is unchanged.
