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


## Phase 2 B4/B5 decisions (2026-10-23)
- **Platform calls follow docs/PLATFORM_API_NOTES.md.** Instagram (Instagram Login) was verified against Meta's pages; the TikTok pages returned 503, so TikTok details are marked SECONDARY/UNVERIFIED and are proven live in L3. Nothing may be coded from memory without being marked.
- **Instagram images must be JPEG** (Meta's rule); PNG/WebP is rejected at pre-flight with `media_not_publishable` rather than failing at Meta. Videos always publish as Reels.
- **Polling is synchronous** (60 s x 5 for Instagram, 3 s x 20 for TikTok); the worker blocks during a publish. Fine for <= 2 posts/day per platform.
- **OAuth without PKCE**: only a random single-use `state` (10 min). Neither documented flow requires PKCE.
- **Tokens**: Fernet (`cryptography`), key in `TOKEN_ENCRYPTION_KEY`; `AccountOut` has no token fields; the log formatter and `sanitize()` strip `access_token=` style fragments; httpx INFO logging is silenced.
- **`/m/{token}`**: HMAC + 1 h expiry, GET/HEAD only, the sole route without Basic auth (besides /health).
- **Dry run never claims** and writes one idempotent attempt per (post, planned time); **retry** only re-schedules, the worker publishes inside the window.
- **Failure visibility over silent skipping**: a missing media file or expired token fails the post and pushes `publish_failed`.

## Phase 2 B6 decisions (2026-10-23)
- **Recovery never publishes.** A post stuck in `publishing` is checked against the platform using the `platform_ref` saved by `on_ref` (container id / publish_id); only an absent or unknown ref sends it back through the normal backoff. Known gap: a TikTok upload that is still processing when the check runs can later also appear in drafts after the retry (drafts only, never public).
- **Persistent pre-flight problems fail the post** (live mode only) rather than skipping it forever; dry-run and reminders never change state.
- **Planner tick state** (last local day per rule) is stored in the single heartbeat row's JSON instead of a new table; `_heartbeat` carries it forward. Planner ticks do not run in `off` mode.
- **Throttles are in memory** (token refresh retry every 30 min): the worker is a single process; a restart simply tries once more.
- **Shutdown**: SIGINT/SIGTERM/SIGBREAK set an event; the running iteration finishes, the sleep is interruptible.

## Phase 2 B7 decisions (2026-10-23)
- **No new LLM client code for replies**: the existing 6-field schema carries the reply in `caption`; works with Claude (JSON schema) and Mistral (JSON object) alike. `generation_logs` rows use `purpose='reply'`, prompt version `reply_v1`, and never store prompt/response text.
- **Replies are never automatic**: sync and draft cannot write to Instagram; sending is one POST per comment, live mode only, atomic claim so a double click cannot send twice.
- **Only API-published Instagram posts are tracked** for metrics/comments; TikTok has no comment/metric access for this app type (UI says so).
- **Digest is hourly by key** (`comments:instagram:YYYYMMDDHH`): simple and restart-safe, at the cost of delaying later comments in the same hour.
- **Layout fix found during the 375 px check**: `.shell { grid-template-columns: 1fr }` let any wide table stretch the whole page (overview was 641 px wide on a 375 px phone). Now `minmax(0, 1fr)`.

## Phase 2 B8 decisions (2026-10-23)
- **Production means authenticated**: `APP_ENV=production` refuses to start without `DASHBOARD_USERNAME` and a 12+ character `DASHBOARD_PASSWORD`, and serves no `/docs`, `/redoc` or `/openapi.json`. The only unauthenticated routes are `/health` and the signed `/m/{token}` media links.
- **Disk guard returns 507** (`disk_low`) and sends one push per day; nothing is registered or hashed while the disk is below `MIN_FREE_DISK_GB` (default 2 GB). Tests set the threshold to 0 so they never depend on the machine's free space.
- **Docs are written for a non-technical client**, state what is unconfirmed, and mark every menu path as "may have moved"; TikTok setup steps are explicitly unconfirmed (the official pages were unreachable).
- **Version 0.2.0** (`/health`, OpenAPI title).
- **Footer/sidebar text follows the mode** instead of claiming "Phase 1 / nothing is published automatically".

## Deployment files (2026-10-23)
- `deploy/` (server setup, Caddy, systemd, backup) and `docs/DEPLOY.md` are the single deployment reference (`/srv/summer` layout). They were authored outside this build log; reviewed and kept.
- The server installs from `requirements.lock` (exact tested versions) and the lock must stay portable: no `name @ file:///...` entries (checked by `tests/manual/build_delivery_zip.py`).
- Server-side scripts and units are kept LF by `.gitattributes`.

## Public pages (2026-10-24)
- Privacy Policy, Terms and the landing page are **static files on the apex domain, served by Caddy** from `/var/www/summer-site`, deliberately outside the app: no login, no cookies, no scripts, strict CSP. The app keeps no `/privacy` or `/terms` routes (tested) so nothing there can be exposed by mistake.
- Texts are **drafts with explicit `[[PLACEHOLDERS]]`** that a test pins to the documented list; a visible placeholder is safer than invented legal details. Whoever operates the service decides the legal name, contact address, deletion promise and governing law.
- Policy statements are tied to the code where possible (named processors, the 14-day backup rotation) so a change in one without the other fails a test.

## Phase 3 C0 decisions (2026-11-06)
Defaults used until Lee answers H3-H10 (his requirements doc ended at H2). Each is a setting or data row, so changing it later is not a rebuild.
- **H3 AI messaging:** Fanvue's Community Guidelines allow AI-generated media with clear disclosure and say nothing about automated messaging; Fanvue publishes an official "Reply to unread chats automatically" tutorial. Still: **replies are AI-drafted and human-sent.** The Acceptable Use Policy has not been read.
- **H4 order:** posting -> bio tracking links -> fan inbox -> PPV in replies -> weekly report. Fan grouping, mass messages, promotions, free-trial links and prices are left to Fanvue's own app.
- **H5 escalation:** a fixed safety list that the client cannot remove (age/minor, "are you real/AI/a bot", meeting/address/phone, refund/chargeback/dispute, self-harm, legal threats) plus Lee's own topics. An escalated chat gets no AI draft, only a push to Lee.
- **H6 PPV price:** Lee types it; it must be between `FANVUE_PPV_MIN_CENTS` and `FANVUE_PPV_MAX_CENTS` (300-5000). The AI never proposes a price.
- **H7 Fanvue-only media:** `media_assets.fanvue_only`, refused for Instagram/TikTok at approve and at publish.
- **H8 data:** UK GDPR assumed; store fan uuid, handle, display name and message text only; message text deleted after `FAN_DATA_RETENTION_DAYS=90`. `/users/me` `email` is never stored.
- **H9 report:** weekly Telegram digest (Monday 09:00 Europe/London) + `/fanvue` page fetched live, nothing stored.
- **H10 payments:** no `write:creator` scope, so the app cannot change prices, promotions or payouts.
- **Fanvue posts:** need media; caption without hashtags; same hard 08:00-21:00 window; own posting rule (inactive), never mirrored with IG/TikTok; our worker owns timing (`publishAt` never sent).
- **Inbox by polling, not webhooks:** one creator, `GET /v1/chats?filter=not_answered` every 120 s, about 30 requests per hour against a 200/min budget, and no new public endpoint. Webhooks only if volume makes polling cost more than ~20 req/min.
- **Only the worker refreshes Fanvue tokens:** refresh tokens rotate with reuse detection and web + worker are separate processes; a race would revoke the connection. The web side reads the token and answers `503 worker_not_running` if it has expired.
- **Bio-link attribution is per link, not per post:** an Instagram/TikTok bio holds one link. Whether those platforms down-rank links to subscription sites is unverified; Lee decides where to paste them.

### Auto-send of fan replies: designed, not built (PLAN_PHASE3 §9)
Gate: Lee confirms H3/H5 in writing **and** at least 50 human-approved drafts have been sent with no more than 10 % heavily edited. Then `FANVUE_AUTO_REPLY=off|safe`: `safe` sends drafts only for non-escalated, text-only chats, after a 10-minute delay, inside the posting window. PPV is never automatic. Why wait: the risks are account-level (misleading fans about who replies, replying to things a human must handle), and Fanvue has no sandbox to try it safely.
