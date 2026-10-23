# Summer AI Content Automation — Phase 2 Build Plan (v3: build-now edition)

> **Audience:** the engineer/model implementing Phase 2. Follow this document literally, exactly like `PLAN.md`.
> `PLAN.md` (Phase 1) stays in force for everything this file does not change. Where this file contradicts `PLAN.md`, this file wins — and the contradiction is listed in §1.
> Where it is silent, pick the simplest option that fits the surrounding code and write it down in `docs/DECISIONS.md`.
> **Phase 2 scope:** Instagram + TikTok (inbox mode) posting and scheduling, a **weekly random-time planner** with a hard **08:00–21:00 window**, **push notifications**, **manual-post reminders**, Dropbox → server media copy, platform authorisation, error recovery, light Instagram engagement. **X is deferred (Appendix A). Fanvue and monetisation = Phase 3.**
> **This version is organised so building starts NOW**: everything that needs no client account, domain, server or API key is built and tested offline first (milestones **B0–B8**). Everything that needs Lee is isolated in the **live milestones L1–L6** (§17) and can be done in any order as his items arrive.

### Changelog
| Version | Date | Change |
|---|---|---|
| v1 | 2026-10-17 | First plan (Instagram, TikTok, X). |
| v2 / v2.1 | 2026-10-20/21 | Client answers: X dropped; TikTok inbox first; weekly planner; push notifications; Dropbox copy via rclone; 10 posts/week **per platform, mirrored**; hard window 08:00–21:00 (tz `Europe/London`, ⚠ confirm); Creator account confirmed; Lee rents server/domain himself; Anthropic key chosen (pending). |
| **v3** | 2026-10-21 | **Build-now restructure.** New §0a readiness matrix; milestones split into **offline build B0–B8** and **live L1–L6**; new **`PUBLISHING_MODE`** (`off` / `reminders` / `dry_run` / `live`) replaces the on/off kill switch; **manual-reminder mode** (Halloween helper: push at slot time → human posts → *Mark as posted*); **dry-run mode** (worker proves the pipeline without touching any platform); **posting-window utilities** as a shared service; **API-notes rule** (verify every platform call against official docs before coding it, record in `docs/PLATFORM_API_NOTES.md`); optional **L0 developer-live path** (own test accounts + tunnel + ntfy) so live proof does not have to wait for Lee. |
| **v3.4** | 2026-10-23 | B7 as built (§10): API-published posts only, no auto-send, reply drafting reuses the 6-field schema, one digest push per hour. |
| **v3.3** | 2026-10-23 | B6 as built: staged `run_once` (one failing stage never stops the rest), `recover_stuck_claim`, token refresh throttle, planner tick state in the heartbeat row, `run_forever` + signal shutdown, persistent pre-flight failures fail visibly (`preflight_fail`). |
| **v3.2** | 2026-10-23 | B4/B5 as built: platform signatures take `external_user_id`; `bad_request` code; dry run never claims; retry returns to the schedule; JPEG-only Instagram images. |
| **v3.1** | 2026-10-22 | **Validation patch** (defects found building B1–B3): mirroring made real (shared day pattern + sibling pinning), weekly budget, window end margin, retryable notifications, reminder semantics, blank-env = unset, test isolation, Halloween deployment caveat. |

---

## 0. Starting point (as of 2026-10-21)

| Item | State |
|---|---|
| Code | Phase 1 complete **plus** post-handover additions: Mistral/Claude router (`llm_client.py`, `LLM_PROVIDER`), **image/video-frame input to the AI** (`frames.py`, prompt `v2`, needs `ffmpeg`), **random draft batch** (`random_draft.py`, `/generate/random`), **asset permanent delete**, library "used" filter, dashboard redesign. |
| Tests | **193 passing** (last recorded run, 2026-10-20). Baseline for every milestone summary. |
| Git | Last commit `3b01a21`. **~46 uncommitted files** (everything above + this plan + `dist/`). |
| Live proof | Mistral live generation proven (`docs/LIVE_PROOF.md`); vision proven on a real video. **Claude live generation unproven** (Lee's Anthropic key pending). |
| Publishing | None. `published`/`failed` statuses exist in the model only; no code sets them. |
| Media | Lee's media is in Dropbox (share link only). No local Dropbox folder, no `MEDIA_SYNC_DIR` on the dev machine. Synced files are never deleted by the app (Phase 1 rule). |
| Available to the builder **today** | Python 3.13 venv, ffmpeg, Mistral key (developer shell), the whole repo, official platform docs on the web. |
| **NOT** available yet | VPS, domain/HTTPS, Meta developer app, TikTok developer app, Telegram bot token, Anthropic key, Dropbox app, Lee's time for "Allow" sessions. |

### Client decisions already in hand (do not re-ask)
Instagram + TikTok only · TikTok **inbox** mode first (direct later) · **10 posts/week on each platform**, ≥ 1/day, 3 days with 2 posts, random times, **same 10 posts mirrored** on both platforms · **only 08:00–21:00** ("GMT" → default `Europe/London`, ⚠ confirm) · Instagram is a **Creator** account · Lee approves from his phone, sole user · accepts AI-label rules · wants push notifications for anything needing him · Halloween (31 Oct) is a priority and automation will **not** be live by then.

### Still open with Lee (cannot block the offline build)
Time-zone meaning (UK clock vs fixed GMT) · are the weekly posts all videos (TikTok inbox = video only) · server + domain (he is renting them) · Dropbox file count/GB · TikTok → creator account · Meta developer-account problem · Telegram · Anthropic key · "voice" (audio vs caption tone) · examples of comments/DMs to automate · Fanvue answers (Phase 3).

---

## 0a. Readiness matrix — what can be built and proven today

| Capability | Needs from client? | Build + test offline now? | How it is proven offline | Live proof later |
|---|---|---|---|---|
| Data model, migration, transitions, posting-window utilities | No | **Yes** | pytest, migration tests | — |
| **Weekly planner** (10/wk, ≥1/day, 3 doubles, 08:00–21:00, mirroring) | tz meaning only | **Yes** | property tests over many seeds, DST week | Lee confirms the plan "looks right" |
| **Manual-reminder mode** + *Mark as posted* (Halloween helper) | No | **Yes** | pytest + local browser demo | real push on a phone (ntfy now, Telegram when Lee is ready) |
| **Push notifications** (Telegram / ntfy / Pushover) | Telegram token (later) | **Yes** | `MockTransport`; live via **ntfy on the developer's own phone** | Telegram on Lee's phone |
| Public signed media URL | No | **Yes** | pytest | HTTPS on the server |
| Platform modules (Instagram, TikTok) | App keys + "Allow" | **Yes — against documented API shapes** | `MockTransport` fixtures copied from official docs | real post (L2/L3) |
| OAuth connect flow | App keys + domain | **Yes** | pytest with mock token endpoints | real connect (L2/L3) |
| Publishing service, retries, stuck-claim recovery, window enforcement | No | **Yes** | pytest, fake platform | unattended post (L4) |
| **Dry-run mode** (worker runs end-to-end, sends nothing) | No | **Yes** | pytest + run on real approved drafts | — |
| Dropbox → server copy | Dropbox app auth | Code + docs offline | disk-guard/sync tests; rclone documented | real folder (L5) |
| Instagram engagement | IG connected | **Yes** (mocked) | pytest | real comment/reply (L6) |
| Deployment docs, platform setup guides | No | **Yes** | review | — |

**Rule of thumb:** if a milestone needs Lee, it is a live milestone. Never let a live dependency stall an offline one.

---

## 1. Rules for the builder (additions to PLAN.md §1)

1. **Still no fake functionality.** A post becomes `published` only (a) after a platform API returned a real post id, or (b) when a **human** clicks *Mark as posted* (recorded as `published_via='manual'`). The worker never marks anything published on its own except from a real API id. Never optimistic.
2. **Tests never call a real platform API, notification service or LLM.** All HTTP goes through one injectable `httpx.Client`; tests use `httpx.MockTransport`. (The developer's shell contains a real `MISTRAL_API_KEY`: conftest must keep blanking every provider key and notification credential.)
3. **One background process, no frameworks.** Phase 1 rule "no background workers/schedulers" is **replaced** by: exactly one worker, `python -m app.cli worker`, a plain `while True` loop with `time.sleep`. No Celery, Redis, APScheduler, cron libraries.
4. **One new Python dependency allowed:** `cryptography` (Fernet, for tokens at rest). `httpx` is already a runtime dependency. Nothing else. (`rclone`, Caddy, systemd are server tools, not Python dependencies.)
5. **Tokens and keys are secrets.** Platform tokens are encrypted in the DB; notification credentials live only in env. Never logged, never returned by the API, never rendered in templates, never in exception messages, never inside a notification body.
6. **Human approval stays mandatory.** Only `approved`/`scheduled` posts can be published or reminded. The **planner may schedule approved posts and may create *drafts*** (§9a) but **never approves** anything; AI never publishes a draft and never sends a reply without a human clicking a button.
7. **No DMs, no auto-replies, no follow/like automation, no scraping.** Engagement = read comments + metrics, AI-drafted replies that a human sends (§10). *Lee's "as much automation as possible" is met by scheduling, drafting, reminders and notifications; auto-replies/DMs need written examples and a separate decision.*
8. **Business logic in `app/services/` only** (unchanged). Platform modules are plain functions, no base classes, no plugin registry.
9. **Notifications are best-effort and never block work.** `notify()` never raises.
10. **API-notes rule (new).** Before writing any platform call, open the **official** documentation, and record the endpoint, required params, scopes, a sample request/response, the doc URL and the date checked in `docs/PLATFORM_API_NOTES.md`, marked `VERIFIED-DOC` or `UNVERIFIED`. Mock fixtures in tests are copied from those docs. Anything marked `UNVERIFIED` is also listed in the live-proof checklist (§17 L-milestones). Do **not** invent endpoints or fields from memory.
11. **Posting window is a hard rule.** Nothing is published, delivered to TikTok drafts, or reminded outside the rule's window (default 08:00–21:00 local) unless a human explicitly overrides a manual *Publish now* (§8). One shared implementation: `services/window.py`.
12. **Modes, not flags.** `PUBLISHING_MODE` (§5) is the only switch; every code path that could post checks it through one helper `publishing_mode()`.

---

## 2. Architecture

```
Browser (dashboard)          API clients / tests              Worker (python -m app.cli worker)
      │                             │                                     │ every WORKER_INTERVAL_SECONDS
      ▼                             ▼                                     ▼
 app/web/routes.py          app/api/*.py (/api/v1)          services/scheduler.py::run_once()
      └───────────────┬─────────────┘                                     │
                      ▼                                                   │
              app/services/*.py  ◄────────────────────────────────────────┘
                      │
     ┌────────────────┼─────────────┬──────────────┬───────────────┬──────────────┬──────────────┐
     ▼                ▼             ▼              ▼               ▼              ▼              ▼
 publishing.py   accounts.py   engagement.py   planner.py     notifications.py  window.py   generation.py /
 (claim →        (OAuth,       (IG metrics,    (weekly        (Telegram / ntfy  (in-window,  random_draft.py
  publish →       tokens)       comments,       random slots   / Pushover push)   next-window) (unchanged +
  record)                       replies)        → schedule)                                   both_platforms)
     │
     ▼
 app/platforms/instagram.py · tiktok.py   ── httpx ──► Instagram API / TikTok Content Posting API
     ▲
     │ Instagram fetches media from
 GET /m/{token}  (signed, expiring public media URL; the only unauthenticated file route)

Dropbox ──(rclone copy, systemd timer, read-only token)──► server folder = MEDIA_SYNC_DIR ──(cli sync-media)──► media_assets
```

**Publishing modes (the heart of "build now"):**

| `PUBLISHING_MODE` | Worker behaviour | Needs accounts/HTTPS? | Use |
|---|---|---|---|
| `off` (default) | Does nothing automated. Phase 1 behaviour; planner/notifications still available manually. | No | safe default / kill switch |
| `reminders` | At each scheduled slot inside the window, sends a **`post_due` push** ("Time to post to Instagram: caption …") with a link. The post stays `scheduled`; a human posts by hand and clicks **Mark as posted**. | No | **Halloween helper**, works today |
| `dry_run` | Runs the full due/claim/pre-flight/payload-building pipeline, **sends nothing to any platform**, writes a `publish_attempts` row `trigger='dry_run'` ("would publish"), leaves the post `scheduled`. | No | prove scheduling on real approved drafts; demos |
| `live` | Real publishing through the platform modules. Requires connected accounts + `PUBLIC_BASE_URL` + keys, else refuses to start the loop with a clear message. | **Yes** | after live milestones |

**Key design decisions (do not revisit):**

| Topic | Decision | Why |
|---|---|---|
| Scheduler | Separate worker process polling the DB every 60 s. Web app never publishes on a timer. | No new infra; survives web restarts; testable via `run_once(now)`. |
| Double-publish prevention | Claim with one conditional `UPDATE content_posts SET status='publishing' WHERE id=? AND status IN ('scheduled','approved')`; rowcount 0 = someone else has it. | Atomic in SQLite and Postgres. |
| Stuck claims | A post in `publishing` for > 15 min is checked against the platform (stored container/publish id) before any retry. Never blindly re-publish. | A crash must not create a duplicate public post. |
| Token storage | Fernet-encrypted columns, key from `TOKEN_ENCRYPTION_KEY`. Missing key → connections page "not configured", `live` refuses to start. | Tokens = full account control. |
| Public media | `GET /m/{token}`: HMAC-signed, expires after 1 h, bound to one asset id. Needs `PUBLIC_BASE_URL` (https). | Instagram must fetch media from a public URL. |
| Which assets can publish | `upload` always; `local_sync` only if the file exists on the server; `external_url` never (→ `media_not_publishable`). | Cannot serve a Dropbox share link as a media file. |
| Retries | Transient: up to 3 attempts, backoff 5 → 15 → 60 min, **moved to the next window start if outside the window**. Permanent: straight to `failed`. | Retry only what can succeed; respect "nothing outside 08:00–21:00". |
| Platforms | **Instagram + TikTok only.** `Platform.x` not added. X design in Appendix A. | Lee's answer. |
| TikTok | **Inbox mode** (video → TikTok drafts; Lee taps Publish). Direct mode code-complete but mock-only until TikTok audits the app. | Unaudited apps are private-only. |
| Planner | `services/planner.py`: per (profile, platform) rule → 7-day slot plan with random times in the window → assigns **approved, unscheduled** posts to slots; mirrors siblings by `group_key`. Idempotent. Never approves. Optional AI draft top-up (off by default). | Lee's pattern. |
| Notifications | One `notify()`; provider by `NOTIFY_PROVIDER`; deduped by `event_key`. | Lee: "push notifications to me". |
| Media from Dropbox | `rclone copy` (never deletes) on a systemd timer into `MEDIA_SYNC_DIR`, then `cli sync-media`. Read-only Dropbox scopes. | Share links can't be read; files must be on the server. |
| AI labels | TikTok direct: `is_aigc=true`. TikTok inbox: Lee adds the label in-app (note in the push). Instagram: manual reminder in the UI. ⚠ verify at build time. | Platform rules (Lee accepted). |
| Hosting | Lee rents the VPS + domain (Option A) and invites the developer. One box: web + worker + Caddy. | Lee's answer. |

---

## 3. Dependencies

`requirements.txt` — add only:
```
cryptography>=43     # Fernet encryption for platform tokens
```
Update `requirements.lock` after install; record in `docs/DECISIONS.md`. **Server tools (not Python deps):** `ffmpeg` (already needed for AI vision), `rclone`, Caddy, systemd.

---

## 4. Project structure (new/changed files only)

```
app/
├── platforms/
│   ├── __init__.py          # PUBLISHERS dict, PublishResult/PlatformError, http_client()
│   ├── instagram.py
│   └── tiktok.py
├── services/
│   ├── window.py            # in_window(), next_window_start(), clamp_to_window()  (pure, tz-aware)
│   ├── planner.py           # weekly random-slot planner (§9a)
│   ├── notifications.py     # notify() + providers (§9b)
│   ├── publishing.py        # publish_post(), claim, record attempt, error classification, mode handling
│   ├── scheduler.py         # run_once(now) – due posts / reminders / dry-run, retries, stuck claims, token refresh, planner tick
│   ├── accounts.py          # OAuth start/callback, token encrypt/decrypt/refresh, disconnect
│   ├── engagement.py        # IG metrics + comments sync, reply drafts, send reply
│   └── random_draft.py      # (existing) + both_platforms option
├── api/
│   ├── planner.py  notifications.py  publishing.py  accounts.py  engagement.py  media_public.py
├── prompts/
│   └── reply_v1.j2
├── web/templates/
│   ├── planner.html  calendar.html  accounts.html  engagement.html   (+ edits to post_detail, posts, overview, base)
├── cli.py                   # + worker, plan-week, notify-test, refresh-tokens; sync-media gains disk guard
migrations/versions/0003_phase2_publishing.py
tests/
├── test_window.py  test_planner.py  test_notifications.py  test_publishing.py  test_scheduler.py
├── test_platform_instagram.py  test_platform_tiktok.py  test_accounts.py  test_engagement.py
├── test_media_public.py  test_media_sync.py  test_reminders.py
docs/ PLATFORM_API_NOTES.md (rule 10), PLATFORM_SETUP.md (click-by-click for a non-technical client), DEPLOY.md, DEMO_PHASE2.md, LIVE_PROOF.md (extend)
```

---

## 5. Configuration (additions to PLAN.md §5)

| Env var | Type | Default | Notes |
|---|---|---|---|
| `PUBLISHING_MODE` | str | `off` | `off` / `reminders` / `dry_run` / `live` (§2). Invalid value → startup error. `live` without accounts/URL/keys → worker refuses with a clear message. |
| `PUBLIC_BASE_URL` | `str \| None` | None | e.g. `https://studio.example.com`. Must start with `https://`. Required for `live` and OAuth. Used for links in pushes too (when unset, pushes omit the link). |
| `TOKEN_ENCRYPTION_KEY` | `SecretStr \| None` | None | `Fernet.generate_key()`; README shows the one-liner. Losing it = reconnect all accounts. |
| `MEDIA_URL_SECRET` | `SecretStr \| None` | None | HMAC key for `/m/{token}`. Required for `live`. |
| `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET` | `str` / `SecretStr` | None | Instagram API with Instagram Login. |
| `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET` | `str` / `SecretStr` | None | |
| `TIKTOK_POST_MODE` | str | `inbox` | `inbox` or `direct` (direct = mock-only until audited). |
| `WORKER_INTERVAL_SECONDS` | int | `60` | |
| `PUBLISH_MAX_ATTEMPTS` | int | `3` | |
| `PLATFORM_TIMEOUT_SECONDS` | float | `60` | per HTTP call |
| `AUTO_PLAN_ENABLED` | bool | `false` | Worker runs the planner daily (§9a). Manual "Plan next 7 days" always available. |
| `PLANNER_RUN_HOUR` | int | `6` | Local hour (rule timezone) of the daily plan run. |
| `PLANNER_LEAD_MINUTES` | int | `30` | Never plan a slot closer than this to now. |
| `PLANNER_MAX_TOPUP_PER_DAY` | int | `3` | Cap on AI drafts the planner may create per day. 0 = never. |
| `NOTIFY_PROVIDER` | str | `none` | `none` / `telegram` / `ntfy` / `pushover`. |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | `SecretStr` / `str` | None | Bot created via BotFather by Lee. |
| `NTFY_SERVER` / `NTFY_TOPIC` | `str` / `SecretStr` | `https://ntfy.sh` / None | Topic = effectively a password: long random string. **Use ntfy on the developer's own phone to live-test pushes today.** |
| `PUSHOVER_APP_TOKEN` / `PUSHOVER_USER_KEY` | `SecretStr` | None | |
| *(all credential vars)* | | | **Blank value (`KEY=`) = unset** (validator); tests set them blank, `PUBLISHING_MODE=off`, `NOTIFY_PROVIDER=none` for the whole session (v3.1). |
| `MIN_FREE_DISK_GB` | float | `2` | `cli sync-media` refuses (and notifies) below this. |

Posting rules (posts per week, window, timezone) are **data** (`posting_rules` table, §6), not env.

`/health` adds `"publishing_mode": str, "worker_last_seen": iso|null, "worker_ok": bool` (`worker_ok` = heartbeat < 5 min old). Still no secrets.

SQLite: in `db.py` connect hook add `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` (web + worker share the file).

---

## 6. Database (migration `0003_phase2_publishing`, hand-reviewed autogenerate)

### 6.1 Enum changes
```
Platform:     instagram, tiktok                (x NOT added)
PostStatus:   + publishing              (transient claim state)
              + sent_to_drafts          (TikTok inbox mode: uploaded, human must post in app)
AccountStatus (new): connected, expired, revoked, error
```
`ContentType` unchanged.

### 6.2 New tables

**`platform_accounts`**
| column | type | notes |
|---|---|---|
| model_profile_id | FK not null, index | |
| platform | String(20) not null | |
| external_user_id | String(100) not null | IG user id / TikTok open_id |
| username | String(100) not null | display only |
| access_token_enc | Text not null | Fernet |
| refresh_token_enc | Text nullable | Fernet |
| token_expires_at / refresh_expires_at | UTCDateTime nullable | |
| scopes | JSON list[str] | |
| status | String(20) not null default `connected` | AccountStatus |
| last_error | String(300) nullable | sanitized, no tokens |
| created_at, updated_at | | |
- UniqueConstraint(`model_profile_id`, `platform`).

**`oauth_states`**: `state` String(64) PK, `platform`, `model_profile_id`, `code_verifier` String(128) nullable (PKCE), `created_at`. Deleted on use; rows > 10 min old invalid.

**`publish_attempts`**
| column | type | notes |
|---|---|---|
| post_id | FK content_posts ON DELETE CASCADE, index | |
| account_id | FK platform_accounts nullable | |
| attempt_no | Integer | 1-based |
| trigger | String(20) | `schedule` / `manual` / `retry` / `dry_run` |
| started_at, finished_at | UTCDateTime | finished nullable |
| success | Boolean nullable | null while running **and for dry runs** |
| error_code / error_message | String(40) / String(500) nullable | sanitized |
| platform_ref | String(200) nullable | container id / publish_id / media id — stuck-claim checks |
| http_status | Integer nullable | |

**`post_metrics`**: `post_id` FK, `fetched_at`, `likes`, `comments`, `shares`, `views`, `saves` (Integer nullable), `raw` JSON. Append-only. (Instagram only.)

**`comments`**: `post_id` FK, `platform`, `external_id` String(100), `author_username` String(100), `text` Text, `commented_at` UTCDateTime, `reply_status` String(20) default `none` (`none`/`drafted`/`sent`/`ignored`), `reply_text` Text default "", `reply_external_id` nullable, `created_at`. UniqueConstraint(`platform`, `external_id`).

**`worker_heartbeats`**: single row `id=1`, `last_seen` UTCDateTime, `last_result` JSON.

**`posting_rules`** — one row per (profile, platform):
| column | type | notes |
|---|---|---|
| model_profile_id, platform | FK / String(20) | UniqueConstraint(profile, platform) |
| posts_per_week | Integer not null default 10 | 1–28 |
| min_per_day / max_per_day | Integer not null default 1 / 2 | `max_per_day ≤ 4` |
| window_start / window_end | String(5) not null | local `HH:MM`, default **`08:00` / `21:00`** |
| enforce_window | Boolean not null default true | §1 rule 11 |
| min_gap_minutes | Integer not null default 180 | between two posts the same day |
| timezone | String(40) not null | IANA; default `Europe/London` (⚠ CLIENT: UK clock time vs fixed GMT); falls back to `APP_TIMEZONE` |
| auto_topup | Boolean not null default false | planner may create AI drafts for unfilled slots |
| is_active | Boolean not null default true | |
| created_at, updated_at | | |
- Seeded for Summer × instagram and tiktok: 10/week, min 1, max 2, window 08:00–21:00, **`is_active=false`** until the owner activates it in the dashboard.

**`notifications`** — delivery log only: `id`, `event` String(30), `event_key` String(120) **unique**, `provider` String(20), `success` Boolean, `error_category` String(40) nullable, `created_at`. No message bodies with secrets.

### 6.3 `content_posts` new columns
| column | type | notes |
|---|---|---|
| platform_account_id | FK nullable | resolved at publish time if null |
| published_at | UTCDateTime nullable | from platform response, or the human's click for manual |
| **published_via** | String(10) nullable | `api` / `manual` |
| external_post_id | String(100) nullable, index | |
| external_url | String(500) nullable | permalink |
| publish_attempt_count | Integer default 0 | |
| next_attempt_at | UTCDateTime nullable, index | retry time (already clamped into the window) |
| last_publish_error | String(500) nullable | sanitized |
| claimed_at | UTCDateTime nullable | set with `publishing` |
| tiktok_privacy_level | String(30) nullable | direct mode only |
| planned_by | String(10) nullable | `planner` / `user` |
| group_key | String(40) nullable, index | links the Instagram + TikTok siblings of one asset |
| reminded_at | UTCDateTime nullable | `reminders` mode: when the `post_due` push was sent |

---

## 6a. Posting-window service (`services/window.py`) — pure functions, built first

```python
@dataclass
class Window: start: time; end: time; tz: ZoneInfo
def in_window(rule, now_utc) -> bool                     # now converted to rule tz; start <= local time <= end
def next_window_start(rule, now_utc) -> datetime          # next 08:00 local (UTC aware); today if before start, else tomorrow
def clamp_to_window(rule, when_utc) -> datetime           # when if inside window else next_window_start(when)
```
Rules: `end` is **inclusive** (21:00:00 allowed, 21:00:01 not — pick this and test it); DST-safe via `zoneinfo`; nonexistent local times shift forward; ambiguous local times use the first occurrence. `enforce_window=False` → `in_window` always True. Tests: `test_window.py` (in/out of window, boundary seconds, DST change in `Europe/London` — UK clocks go back **25 Oct 2026** —, next-window across midnight, clamp idempotent).

---

## 7. Platform modules (`app/platforms/`)

`__init__.py`:
```python
@dataclass
class PublishResult:
    external_post_id: str
    external_url: str | None
    published_at: datetime | None
    platform_ref: str | None        # container/publish id

class PlatformError(Exception):
    def __init__(self, code: str, message: str, transient: bool, http_status: int | None = None,
                 platform_ref: str | None = None): ...

def http_client(settings) -> httpx.Client   # module-level, overridable in tests (like get_claude_client)
PUBLISHERS = {"instagram": instagram, "tiktok": tiktok}
```
Every platform module exposes exactly these functions (no class); **each carries a docstring with the official doc URL + date checked (rule 10)**:
```python
def authorize_url(settings, state: str, code_challenge: str | None) -> str
def exchange_code(http, settings, code: str, code_verifier: str | None) -> TokenSet
def refresh(http, settings, account) -> TokenSet
def publish(http, token, external_user_id, post, media_url, media_path, on_ref=None, caption=None) -> PublishResult
def check_status(http, token, external_user_id, platform_ref) -> PublishResult | None   # stuck-claim check
def fetch_metrics(http, token, external_user_id, external_post_id) -> dict
def fetch_comments(http, token, external_user_id, external_post_id) -> list[dict]
def send_reply(http, token, external_user_id, external_comment_id, text) -> str
# v3.2 (B4): `external_user_id` added (IG endpoints are /<IG_ID>/...); refresh(account) needs .access_token/.refresh_token/.scopes/.external_user_id/.username
# `PUBLISHERS` is the lazy `app.platforms.publisher(name)`; `caption` = render_caption(post)
```
`TokenSet` = dataclass(access_token, refresh_token, expires_at, refresh_expires_at, scopes, external_user_id, username). TikTok implements metrics/comments/reply as `PlatformError("unsupported", ..., transient=False)`.

Caption sent = `caption` + `\n\n` + `" ".join("#"+h for h in hashtags)`; built in `publishing.py::render_caption(post)`, one place.

### 7.1 Instagram (`instagram.py`) — Instagram API with Instagram Login
- Professional account required. **Lee's account is a Creator account (screenshot, 2026-10-21).** ⚠ confirm at setup that Creator accounts qualify for the chosen API flavour and whether a linked Facebook Page is required; record in `PLATFORM_SETUP.md`.
- Scopes: `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_comments`.
- Own-account apps need no App Review (Standard Access). Document in `PLATFORM_SETUP.md`.
- **Meta developer account blocker:** Lee's developer signup failed (identity verification / creator issue). Order of attack: (1) get the exact error from Lee and help verify; (2) **fallback:** create the Meta app under the **developer's** account in development mode; Lee accepts the **Instagram Tester** invite and authorises via OAuth. Record the ownership trade-off in `DECISIONS.md` and tell Lee plainly.
- Tokens: short-lived → long-lived (60 days) → refresh before expiry (worker). No refresh token; `refresh()` uses the current long-lived token.
- Publish flow: `POST /{ig_user_id}/media` (container: `image_url` for feed_post; `media_type=REELS` + `video_url` for reel; `media_type=STORIES` + `image_url`/`video_url` for story; `caption` except stories) → store container id as `platform_ref` → poll `GET /{container_id}?fields=status_code` every 10 s, max 5 min, until `FINISHED` (`ERROR`/`EXPIRED` → permanent `media_rejected`) → `POST /{ig_user_id}/media_publish creation_id=` → `GET /{media_id}?fields=permalink,timestamp`. ⚠ **Verify Reels/Stories support for the chosen flavour in the official docs first** (rule 10); if unsupported, record it and adjust the plan.
- Limit: 100 API posts/24 h/account (check `content_publishing_limit`; at limit → transient `platform_quota`). Lee's pattern (≤ 2/day) is far below it.
- Media reachable at `media_url` (§11). Feed post requires media → `media_required` (also at approve time).

### 7.2 TikTok (`tiktok.py`) — Content Posting API (**inbox mode is the Phase 2 deliverable**)
- Scopes: `user.info.basic`, `video.upload` (inbox), `video.publish` (direct, later). Tokens: access 24 h, refresh long-lived; refresh when < 2 h left.
- **Mode `inbox`:** `POST /v2/post/publish/inbox/video/init/` with `FILE_UPLOAD` (chunked upload, no public URL). The video lands in the creator's TikTok inbox; Lee adds caption/AI label and posts in the app. Success → status `sent_to_drafts` (never `published`), `platform_ref = publish_id`, `tiktok_ready` push. Post detail: "Sent to TikTok drafts — finish in the app, then click *Mark as posted*". **Planner caveat:** the planned time = delivery to drafts (inside the window); the public time = when Lee taps Publish.
- **Mode `direct` (later):** code + mocks only. Unaudited apps can only post `SELF_ONLY`. Call `creator_info/query` first; let the human pick `privacy_level`; send `is_aigc=true`.
- **Videos only** in inbox mode. `photo_post` → `media_not_publishable` ("post manually") until the verified-domain stretch (`PULL_FROM_URL`) exists.
- Poll `publish/status/fetch` until `PUBLISH_COMPLETE` / `FAILED` (max 10 min).
- Metrics/comments unavailable → `unsupported`; UI says so.

---

## 8. Publishing service (`services/publishing.py`)

`publish_post(db, post_id, trigger, now, http, override_window=False) -> ContentPost`
1. `publishing_mode()`: `off` → `AppError("publishing_disabled", 409)`; `reminders` → `AppError("publishing_reminders_only", 409)` (publish is manual); `dry_run` → run steps 2–3 and 6a then stop (below); `live` → continue.
2. Pre-flight `check_publishable(post)` (also used at approve time and shown in UI): status ∈ {approved, scheduled}; account connected for (profile, platform); media rules (§7); caption limits; `external_url` asset → `media_not_publishable`; **posting window** — outside the window the **worker defers** (post stays `scheduled`), and a **manual Publish now** returns 409 `outside_posting_window` unless `override_window=True` (dashboard confirm). Failure → 409/422 with a code, **post status unchanged**.
3. Claim: conditional UPDATE → `publishing`, `claimed_at=now`. rowcount 0 → `AppError("already_publishing", 409)`.
4. Insert `publish_attempts` (attempt_no = count+1), commit.
5. Ensure fresh token (`accounts.get_valid_token`, refresh if < 5 min left).
6. Build `media_url` (§11) and/or `media_path`; call `PUBLISHERS[platform].publish(...)`. Save `platform_ref` on the attempt as soon as known (callback `on_ref(ref)` → commit) so a crash after container creation is recoverable.
6a. **Dry run (v3.2, as built):** runs pre-flight + payload check (file exists, caption renders), **sends no HTTP and never claims** (status, `publish_attempt_count`, `claimed_at` untouched), and writes ONE `publish_attempts` row per (post, planned time): `trigger='dry_run'`, `success=NULL`, `platform_ref='dry:<planned_publish_at>'` (the idempotency marker), `error_message='dry run: would publish <type> to <platform> (<n> chars, media <file>)'`. No connected account is needed. The worker's `dry_runs` counter counts NEW attempt rows only.
7. Success → `published` (TikTok inbox: `sent_to_drafts` + `tiktok_ready` push), `published_via='api'`, `published_at`, `external_post_id`, `external_url`, `next_attempt_at=None`, `last_publish_error=None`; attempt success. One commit.
8. `PlatformError` → attempt failed with code/message;
   - `transient` and `publish_attempt_count < PUBLISH_MAX_ATTEMPTS` → back to `scheduled`, `next_attempt_at = clamp_to_window(now + backoff[n])` (5, 15, 60 min).
   - else → `failed`, `last_publish_error`, **`publish_failed` push**.
9. Any other exception → transient `internal_error` (logged with traceback, generic message).

**Error codes** (`PlatformError.code`):
| code | transient | meaning |
|---|---|---|
| `token_expired` | no* | refresh failed → account `expired` + `account_attention` push; post → `failed` "Reconnect account" |
| `account_not_connected` | no | |
| `rate_limited` / `platform_quota` | yes | |
| `timeout` / `connection_error` / `upstream_error` | yes | |
| `media_rejected` | no | platform could not process media |
| `media_not_publishable` / `media_required` | no | |
| `caption_too_long` | no | |
| `policy_rejected` | no | platform refused content |
| `unaudited_client` | no | TikTok direct without audit |
| `unsupported` | no | feature unavailable for that platform/app type |
| `outside_posting_window` | n/a | pre-flight only (see step 2) |
| `internal_error` | yes | |
| `bad_request` | no | v3.2: any other 4xx the platform returns |

Messages stored/displayed pass `sanitize(msg)`: strip anything matching the account's tokens, truncate 500.

**Transition table additions (`services/posts.py`, the only place statuses change):**
| action | from | to | notes |
|---|---|---|---|
| claim (service) | approved, scheduled | publishing | conditional UPDATE |
| publish_success (service) | publishing | published | `published_via='api'` |
| inbox_success (service) | publishing | sent_to_drafts | TikTok inbox |
| **mark_posted (user)** | **approved, scheduled, sent_to_drafts** | **published** | `published_via='manual'`, optional `external_url` (https); works in **every** `PUBLISHING_MODE` (the human asserts they posted it) |
| publish_retry (service) | publishing | scheduled | `next_attempt_at` set |
| publish_fail (service) | publishing | failed | |
| retry (user) | failed | scheduled | v3.2: planned time kept if still in the future, else set to now; `publish_attempt_count=0`, error cleared. Nothing is sent from the action: the worker publishes it inside the posting window (hard rule). |
| edit (user) | failed | draft | same as other content edits |
| archive | + failed | archived | |
| plan (planner, service) | approved | scheduled | sets `planned_publish_at`, `planned_by='planner'` |
| any user action | publishing | — | 409 `invalid_transition` |
| any edit | published | — | 409 |

Still: **no endpoint accepts `status` in a body.** Approving a post whose `planned_publish_at` is in the past = schedule for "now" (worker picks it up next loop, if inside the window). A user editing the planned time sets `planned_by='user'` (the planner never moves user-set times).

---

## 9. Worker (`services/scheduler.py`, `cli.py worker`)

```python
def run_once(db, now, http) -> dict:   # returns counts, used by tests and heartbeat
    mode = publishing_mode()
    1. if mode == "off": heartbeat only, return
    2. if mode == "live": stuck claims: status=publishing and claimed_at < now-15min →
         platform_ref present: check_status(); found → mark published; else → retry path
         no platform_ref → retry path (nothing reached the platform)
    3. due posts: status=scheduled AND planned_publish_at <= now AND (next_attempt_at IS NULL OR <= now)
         AND now inside the rule's window (enforce_window) ; order by planned_publish_at, limit 10
         mode live     → publish_post(trigger="schedule"/"retry")
         mode dry_run  → publish_post (dry-run branch)
         mode reminders→ if reminded_at IS NULL: notify("post_due", key=f"due:{post_id}") and set reminded_at **only if the push was sent** (failed push → retried next loop; post stays scheduled). Push body = full `render_caption(post)` (+ "Mark as posted" hint) so it is usable from a phone without the dashboard. No push channel configured → nothing sent, counted as `skipped`, and `cli worker` **refuses to start** in `reminders` mode without one.
       one failure never stops the loop
    4. token refresh (live only): accounts expiring within 24 h (IG: within 7 days) → refresh; failure → status expired + last_error + account_attention push
    5. planner tick (if AUTO_PLAN_ENABLED): once per local day at PLANNER_RUN_HOUR per active rule → planner.plan_week(...) (§9a)
    6. engagement sync every 6th loop (live only): IG metrics + comments for posts published in the last 14 days; new-comments digest push (§9b)
    7. upsert worker_heartbeats
```
`cli worker`: `configure_logging()`, loop `run_once(SessionLocal(), utcnow(), http_client())` with try/except per iteration (log + continue), sleep `WORKER_INTERVAL_SECONDS`, clean exit on Ctrl+C/SIGTERM. Run **one** worker only. `cli plan-week --profile 1 --platform instagram`, `cli notify-test`, `cli refresh-tokens` run single steps by hand.

Dashboard overview shows a red banner when `worker_last_seen` is older than 5 min and there are scheduled posts: "Scheduler is not running — scheduled posts will not publish." (A dead worker cannot push; point an external uptime monitor at `/health`.)

---

## 9a. Weekly planner (`services/planner.py`)

**Client pattern (confirmed):** **10 posts/week on each platform**, ≥ 1/day, **3 days with 2 posts** (7 days → 4 × 1 + 3 × 2 = 10), random times, **only 08:00–21:00** (hard, §1 rule 11), **the same 10 posts on each platform** (mirroring).

```python
@dataclass
class Slot: start_utc: datetime; local_day: date
def make_slots(rule, start_day: date, now: datetime, rng: random.Random) -> list[Slot]
def plan_week(db, rule, now, rng=None, start_day=None) -> PlanResult   # PlanResult(slots, filled, unfilled, topup_drafts)
```
**`make_slots`** (pure, deterministic for a given `rng`; what the tests hammer):
1. 7 consecutive local days from `start_day` (default: tomorrow in the rule tz; "today" counts only for slots after `now + PLANNER_LEAD_MINUTES`).
2. Per-day counts: every day gets `min_per_day`; the remaining `posts_per_week − 7·min_per_day` go to **distinct random days** (≤ `max_per_day` each). With Lee's numbers: 3 random days get 2.
3. For a day with `n` posts draw `n` random minutes uniformly in `[window_start, window_end]` (minute precision), retry (max 50) until every gap ≥ `min_gap_minutes`; fallback: evenly spaced. All slots strictly inside the window (also leave room for retry/backoff to land inside).
4. Local → UTC via `zoneinfo` (DST-safe).
5. Drop slots earlier than `now + PLANNER_LEAD_MINUTES`.

**`plan_week`** (idempotent — running twice never double-schedules):
1. Count posts already `scheduled`/`published`/`sent_to_drafts`/`publishing` for (profile, platform) per local day; reduce that day's target.
2. `make_slots` for the remainder.
3. Assign **`approved` posts with no `planned_publish_at`** (oldest approved first) to slots in time order via the `plan` transition → `scheduled`, `planned_by='planner'`.
4. Slots left without a post = `unfilled`. If `rule.auto_topup` and cap > 0: call `random_draft.generate_random_drafts(...)` (Phase 1 service; random **unused** uploaded/synced assets; AI sees them) — **drafts only, never approved** — then `approval_needed` push. Cap is per local day.
5. Unfilled slots within 48 h of the first one → `slots_unfilled` push (deduped per day).
6. Never touches posts where `planned_by='user'` or status ≥ `publishing`.
7. **Mirroring (v3.1 — how it actually works):** the 7-day *day pattern* (which days get 2) is drawn from `Random(f"{profile_id}:{start_day}")`, **not** from the caller's rng, so every platform of a profile gets the same pattern for the same week; groups are planned oldest-group-first before singletons; a post whose sibling is already planned is **pinned to the sibling's local day** (fallback to the next free slot with a log warning). Each platform keeps its own random times. Test: 10 IG+TikTok pairs → every pair on one local day. `random_draft` gains `both_platforms`: for each random **video** asset create an Instagram + TikTok pair sharing a `group_key` (captions generated per platform), still **drafts only**. Images produce an Instagram draft only (TikTok inbox = video only) until photo posts exist.
7a. **Weekly budget (v3.1):** per-day targets = min_per_day first, then the pattern, then up to max_per_day, **capped so already-occupied + new ≤ `posts_per_week`** for the 7-day window. Re-planning a part-filled week can never exceed 10. The week is **rolling**: the daily run plans the 7 days starting tomorrow; occupied posts in that window count against the budget.
7b. **End margin (v3.1):** slots are drawn from `[window_start, window_end − 15 min]` (`END_MARGIN_MINUTES`) so a 60 s worker poll + publish time never crosses 21:00; `validate_rule_values` accounts for it.

**Rule validation** (service + API, 422): `1 ≤ posts_per_week ≤ 28`; `min_per_day ≤ max_per_day ≤ 4`; `posts_per_week ≥ 7·min_per_day` and `≤ 7·max_per_day`; `window_start < window_end`; window long enough for `max_per_day` posts with the gap; valid IANA timezone.

**Dashboard (`/planner`):** per-platform rule form (per week, min/max per day, window, gap, timezone, auto top-up, active), **Plan next 7 days** button (PRG + `?msg=` with counts), week grid of planned/published slots with unfilled slots marked, and a plain TikTok note ("planned time = delivered to your drafts; you publish in the app").

---

## 9b. Push notifications (`services/notifications.py`)

```python
def notify(db, event: str, key: str, title: str, body: str = "", url: str | None = None, http=None) -> bool
# never raises; True if a push was sent
```
- **Dedupe (v3.1):** one `notifications` row per `event_key`. A key whose push was **sent** (or logged `disabled`) is final → `False`. A key whose send **failed** is retried on the next call (same row), so an outage never loses a `post_due`/`publish_failed` push; deterministic keys still stop a restart from re-sending a delivered push.
- **Providers** (one small `httpx` function each, `MockTransport`-tested):
  - `telegram`: `POST https://api.telegram.org/bot<token>/sendMessage` `{chat_id, text, disable_web_page_preview: true}`
  - `ntfy`: `POST {NTFY_SERVER}/{NTFY_TOPIC}` body = text, headers `Title`, `Click` (url), `Priority`
  - `pushover`: `POST https://api.pushover.net/1/messages.json` `{token, user, title, message, url}`
  - `none`: log only (`notifications` row `success=false, error_category="disabled"`).
- **Body rules:** short; may include the first 60 chars of a caption; **never** tokens/keys/credentials; link = `PUBLIC_BASE_URL` + dashboard path (omit if unset).
- **Events** (key, trigger):
  | event | key | trigger |
  |---|---|---|
  | **`post_due`** | `due:{post_id}` | `reminders` mode: slot reached inside the window — "Time to post to {platform}: «caption start»" + link to the post page |
  | `tiktok_ready` | `tiktok:{post_id}` | inbox upload finished — "Video is in your TikTok drafts: add the AI label and Publish" |
  | `publish_failed` | `failed:{post_id}:{attempt_no}` | post moved to `failed` |
  | `approval_needed` | `approval:{local_date}` | planner top-up created drafts, or the daily digest (09:00 local) when drafts are waiting |
  | `slots_unfilled` | `slots:{profile}:{platform}:{local_date}` | §9a step 5 |
  | `account_attention` | `acct:{account_id}:{status}:{local_date}` | token expired/expiring |
  | `new_comments` | `comments:{platform}:{yyyymmddHH}` | hourly digest |
  | `disk_low` | `disk:{local_date}` | `sync-media` refused |
- The window rule governs *posting and post reminders*, not information: `publish_failed`, `account_attention` and `disk_low` are still sent outside the window. ⚠ confirm quiet hours with Lee later.
- Dashboard: `POST /api/v1/notifications/test` + "Send test notification" on the Accounts page; `GET /api/v1/notifications` = last 50 log rows (no secrets).
- **Live-test today with ntfy on the developer's own phone** (install the ntfy app, subscribe to a random topic). Telegram goes live when Lee creates the bot (recommended channel: free, private).

---

## 10. Engagement (`services/engagement.py`) — Instagram only

- **Metrics:** per published Instagram post (`published_via='api'`, or manual with an `external_url` we can map), snapshot likes/comments/shares/views/saves into `post_metrics`. Post detail shows the latest snapshot; overview shows 7-day totals. TikTok: not available (UI says so).
- **Comments:** fetch new comments on published Instagram posts → `comments` (dedupe by external id) → hourly `new_comments` push.
- **Reply drafts:** "Draft reply" → LLM with `reply_v1.j2` (persona + restricted topics; `{"reply": str}` ≤ 300 chars; never mention adult platforms; never continue sexual/political/minor-related threads → `{"reply": ""}` + `ignored` suggestion). Uses `LLMClient`, logged in `generation_logs` with `purpose="reply"`.
- **Send:** a human edits and clicks "Send reply" → `send_reply` → `reply_status=sent`. Rate-limited. No bulk/auto send, no DMs.
- `/engagement`: unanswered comments newest first; Draft / Send / Ignore.
- **Not built:** auto-replies and DMs. Before any such work Lee must supply **3–4 concrete examples**; then evaluate against current Meta policy as a separate approved change (`AUTO_REPLY_MODE` would default `off`). Note it in `DECISIONS.md`.

---

## 11. Public media URL (`api/media_public.py`, `security.py`)

- `make_media_token(asset_id, expires_at)` = `base64url(f"{asset_id}.{exp_unix}") + "." + base64url(hmac_sha256(MEDIA_URL_SECRET, payload))`.
- `GET /m/{token}`: `hmac.compare_digest`, expiry, asset active, `resolve_asset_file` (existing containment check) → `FileResponse`. Any failure → 404 (no detail). Exempt from Basic auth; **the only** unauthenticated route besides `/health`.
- TTL 1 h; minted only inside `publish_post` (live). Never shown in the dashboard.
- Needs HTTPS at `PUBLIC_BASE_URL` → works on the VPS (or an L0 dev tunnel), **not** on a laptop behind NAT.

---

## 11a. Dropbox → server media copy (`docs/DEPLOY.md`, `cli.py sync-media`)

- **Mechanism:** `rclone copy dropbox:<folder> $MEDIA_SYNC_DIR --min-size 1k --transfers 2` on a **systemd timer every 15 minutes**, followed by `python -m app.cli sync-media` (registers new files, auto-tags by sub-folder name — Phase 1). `rclone copy` **never deletes**.
- **Dropbox access (⚠ CLIENT):** least privilege — Lee creates a Dropbox app (guided, `PLATFORM_SETUP.md`) with **read-only** scopes `files.metadata.read` + `files.content.read` and authorises once via rclone's OAuth flow in his browser. Fallback: rclone's default app (broader access — say so plainly).
- `rclone.conf` holds a Dropbox token: `chmod 600`, service-user owned, never in git, only in encrypted backups.
- **Disk guard:** `cli sync-media` first checks `shutil.disk_usage(MEDIA_SYNC_DIR).free ≥ MIN_FREE_DISK_GB`; below → exit non-zero, log, `disk_low` push. Size the VPS disk from Lee's file count/GB.
- Empty/placeholder files ignored (Phase 1: videos < 1 KB skipped; the copy uses `--min-size 1k`).
- First big copy runs once by hand during setup; the timer takes over.
- Tests (`test_media_sync.py`): disk guard refuses below threshold and registers nothing; registers new files and skips existing; never deletes a source file; a removed source file leaves the DB row untouched. (`rclone` itself isn't unit-tested; a manual check is documented.)

---

## 12. API additions (`/api/v1`, same `ErrorOut` shape)

| Method & path | Success | Errors |
|---|---|---|
| GET `/planner/rules` · PUT `/planner/rules/{id}` | rules (validated per §9a) | 404, 422 |
| POST `/planner/plan` `{"profile_id","platform","start_day"?}` | 200 `{"slots","filled","unfilled","topup_drafts"}` | 404, 409 (rule inactive), 422 |
| GET `/planner/week?profile_id&platform&start` | slots + assigned post ids | |
| POST `/posts/{id}/mark-posted` `{"external_url"?}` | 200 (`published_via='manual'`) | 404, 409, 422 |
| POST `/notifications/test` · GET `/notifications` | 200 | 503 `not_configured` |
| GET `/accounts` | list (id, profile_id, platform, username, status, token_expires_at, scopes) — **never tokens** | |
| GET `/oauth/{platform}/start?profile_id=` | 302 to platform | 400 `not_configured`, 404 |
| GET `/oauth/{platform}/callback?code&state` | 302 to `/accounts?msg=...` | invalid/expired state → 400 |
| POST `/accounts/{id}/disconnect` | 200 (tokens deleted, status `revoked`) | 404 |
| POST `/posts/{id}/publish` `{"override_window"?}` | 200 PostOut | 404, 409, 422 |
| POST `/posts/{id}/retry` | 200 | 404, 409 |
| GET `/posts/{id}/attempts` · `/posts/{id}/metrics` | list / latest + history | 404 |
| GET `/comments?platform&post_id&reply_status` | `{"items","total"}` | |
| POST `/comments/{id}/draft-reply` · `/send-reply` `{"text"}` · `/ignore` | 200 | gen errors, 429, 409 already sent |
| POST `/content/generate-random` (existing) | gains `both_platforms` | |
| GET `/m/{token}` (no prefix, no auth) | file | 404 |

OAuth callback routes live under `/oauth/...` (no `/api/v1` prefix) because the redirect URI is registered with the platform: `{PUBLIC_BASE_URL}/oauth/{platform}/callback`. They are dashboard routes: Basic auth applies, so the browser that started the flow completes it.

---

## 13. Dashboard additions

| Page | Route | Content |
|---|---|---|
| Planner | `GET /planner` | §9a. |
| Calendar | `GET /calendar?week=` | 7-column week grid of scheduled + published posts (server-rendered, no JS lib), prev/next week, click → post detail; planner-chosen times marked; out-of-window posts flagged. |
| Post detail | (edit) | **Manual-post panel** (all modes): caption + hashtags with **Copy** buttons (inline JS clipboard), **Download media** (authenticated file route), **Mark as posted** (optional URL). Publish panel (`live`): pre-flight reasons, "Publish now" (confirm; outside-window override; IG "add AI label manually" reminder), attempts table, external link, Retry. TikTok inbox: "Sent to drafts" + Mark as posted. Dry-run line: "Dry run: would publish at …". Banner text per mode: `off` = Phase 1 text; `reminders` = "You'll get a reminder at the planned time; post it yourself and click Mark as posted"; `dry_run` = "Dry run — nothing is sent to any platform"; `live` = "Scheduled posts publish automatically while the scheduler runs". |
| Accounts | `GET /accounts` | Card per platform: username, status, token expiry, Connect / Reconnect / Disconnect; missing env var **names** only; "Send test notification" + provider. |
| Engagement | `GET /engagement` | §10. |
| Queue | (edit) | Filter adds `publishing`, `sent_to_drafts`, `failed`; failed rows show the error code; published rows link out. |
| Overview | (edit) | Cards: failed (red), published last 7 days, unfilled slots next 7 days; mode badge; worker banner (§9); engagement totals. |
| Generator | (edit) | "Surprise me" card gains **"Create Instagram + TikTok pair"** (`both_platforms`). |

Forms stay plain HTML + PRG + `?msg=`. Inline JS only for confirm dialogs, clipboard copy, and the **submit-event** button disabling pattern (never disable a submit button in a click handler — Phase 1 lesson). The dashboard must be **usable on a phone** (Lee approves from his phone): single-column below ~700 px, tap targets ≥ 44 px; verify with a 375 px viewport **and record a screenshot path or Playwright check in `PROGRESS.md` — no evidence, not done (B2 claimed this without any).**

---

## 14. External prerequisites (⚠ CLIENT) → which **live** milestone they unlock

| # | Needed | Status (2026-10-21) | Unlocks |
|---|---|---|---|
| 1 | **VPS + domain + HTTPS** | Lee is renting both himself (Option A) and asked for recommendations: DigitalOcean (London, 2 GB, backups) or Hetzner; domain via Cloudflare/Namecheap; then invites the developer. Still need approx. file count + GB. | L1 |
| 2 | Instagram professional account | ✓ **Creator** (screenshot). | L2 |
| 3 | Meta developer account + app + IG tester | **Blocked:** Lee's identity verification / creator issue; he is investigating. Fallback app under the developer's account (§7.1). | L2 |
| 4 | TikTok developer app + redirect + verified domain | Lee is switching TikTok to a creator account, then creates the dev app; needs #1 first. Inbox mode only. | L3 |
| 5 | ~~X~~ | Dropped. | — |
| 6 | Engagement scope | Default delivered: IG metrics + human-sent replies + digests. Need 3–4 automation examples before any auto-reply work. | L6 |
| 7 | AI-persona disclosure rules | ✓ accepted. | go-live |
| 8 | Funded AI key | Lee chose **Anthropic**, will do later (needs the walkthrough). Mistral already works. | Claude live proof |
| 9 | Posting rules | ✓ 10/wk per platform, mirrored, 08:00–21:00. Confirm: UK clock vs fixed GMT; weekly posts all videos? | L4 activation |
| 10 | Notification channel | Lee will create a Telegram account, then a bot token is needed. | L5 (Telegram) |
| 11 | Dropbox → server | Lee: "all in Dropbox". Need Dropbox app/auth, folder name, file count + GB. | L5 |
| 12 | Persona "voice" | Lee asked about voices from Higgsfield / elsewhere — may mean an **audio** voice for videos, not caption tone. Clarify. If audio: the app only posts finished videos (any tool is fine). If captions: need 5–10 sample captions. | quality at go-live |
| 13 | Link-in-bio target | None yet; CTAs say "link in bio" only. | Phase 3 |

Developer apps are created **by Lee** under his own identity where possible; the Meta fallback is the documented exception. We write exact clicks in `docs/PLATFORM_SETUP.md`; we never ask for account passwords.

---

## 15. Security checklist (additions to PLAN.md §14)

- [ ] Tokens Fernet-encrypted; `git grep`/log scan shows no token values; `AccountOut` has no token fields (test asserts).
- [ ] OAuth `state` random (`secrets.token_urlsafe(32)`), single-use, 10-min expiry; PKCE `S256` where required.
- [ ] Redirect URIs built only from `PUBLIC_BASE_URL`; no open redirect.
- [ ] `/m/{token}` HMAC + expiry + containment; 404 on any failure; no other unauthenticated route.
- [ ] Platform error messages sanitized before storage/display.
- [ ] `PUBLISHING_MODE=off` honoured by worker, API and dashboard (kill switch); `live` refuses to start without prerequisites.
- [ ] Publishing/reminders require `approved`/`scheduled`; a draft can never be published (test). **Planner never approves** (test).
- [ ] **Nothing posts, delivers to TikTok drafts or reminds outside the window** (test with the clock at 07:59, 08:00, 21:00, 21:01, across the DST change).
- [ ] Reply sending requires an explicit human POST per comment.
- [ ] Basic auth mandatory when `APP_ENV=production` (startup refuses without it) — Phase 2 makes the app public-facing.
- [ ] Notification credentials only in env; none in logs, DB, API responses or message bodies (test greps captured requests/logs).
- [ ] `rclone.conf` `chmod 600`, not in git, not in plain backups; Dropbox app read-only scoped.
- [ ] Uptime monitor on `/health` documented.

---

## 16. Tests

Fixtures (`conftest.py` additions): `fake_http` = `httpx.Client(transport=httpx.MockTransport(handler))` with a per-test route table, installed via dependency override of `http_client`; `connected_account(platform)` inserts an account with encrypted fake tokens (`TOKEN_ENCRYPTION_KEY` from `Fernet.generate_key()` in the settings fixture); `frozen_now` passed explicitly to `run_once`/`publish_post`/`plan_week` (no time-freezing library); the settings fixture **blanks every provider key and notification credential** and sets `PUBLISHING_MODE=off` unless a test sets another.

| File | Tests |
|---|---|
| **test_window.py** | §6a cases (boundaries, DST, midnight, clamp idempotent, `enforce_window=False`) |
| **test_planner.py** | **property tests over many seeds:** exactly `posts_per_week` slots; every day ≥ `min_per_day`, ≤ `max_per_day`; with Lee's rule exactly 3 days have 2 slots; all local times inside 08:00–21:00; same-day gaps ≥ `min_gap_minutes`; none before `now + lead`; same seed → same slots; different seeds differ; **DST week** (Europe/London across 25 Oct 2026) correct UTC; **idempotent**; already-scheduled posts reduce the target; oldest approved first; `planned_by='user'` and drafts untouched; unfilled reported; **mirroring puts siblings on the same local day**; `auto_topup` creates only drafts (never approved), respects the daily cap (fake LLM); rule validation 422 cases; inactive rule → 409; `slots_unfilled` push deduped per day |
| **test_reminders.py** | `reminders` mode: due post inside window → one `post_due` push + `reminded_at`, status unchanged; outside window → nothing; second loop → no duplicate; `mark_posted` from `scheduled` → `published`, `published_via='manual'`; works in every mode; draft cannot be reminded or marked |
| **test_notifications.py** | each provider builds the right request (URL, payload, headers) via MockTransport; `none` sends nothing; duplicate key → one push; failed HTTP returns False, never raises; **secret values never appear** in request body, logs or `/api/v1/notifications`; events fire at the right points; test endpoint → 503 when unconfigured |
| test_publishing.py | draft cannot publish (409); mode `off`/`reminders` → 409 codes; `dry_run` sends **zero** HTTP calls, writes one dry-run attempt, leaves status `scheduled`, idempotent; `live`: external_url asset → `media_not_publishable`; no account → `account_not_connected`; success → published + external id + `published_via='api'` + attempt row; transient failure → `scheduled` with `next_attempt_at` +5 min **inside the window** (a failure at 20:58 → next 08:00), count 1; 3rd transient → `failed` + push; permanent → `failed`; concurrent claim → `already_publishing`; edit/approve while `publishing` → 409; retry from failed; edit failed → draft; manual Publish now outside the window → `outside_posting_window`, with `override_window` → proceeds; published edit → 409; no endpoint accepts `status` |
| test_scheduler.py | due post published, future untouched; `next_attempt_at` respected; **window respected in every mode**; one failing post doesn't stop others; stuck claim with `platform_ref` + platform says published → published (no second publish call); stuck claim without ref → retried; mode `off` → nothing claimed; heartbeat written; token near expiry refreshed; planner tick once per local day at `PLANNER_RUN_HOUR`, only when `AUTO_PLAN_ENABLED` |
| test_platform_instagram.py | fixtures copied from the official docs (`PLATFORM_API_NOTES.md`): feed/reel/story container params; polls until FINISHED; ERROR → `media_rejected`; permalink; quota → transient `platform_quota`; 5xx → transient; hashtags in caption; comments + metrics parsed |
| test_platform_tiktok.py | inbox chunked upload → `sent_to_drafts` (never `published`); mark-posted → `published`; metrics/comments → `unsupported`; direct (mock only) sends privacy + `is_aigc=true`; unaudited → `unaudited_client`; `photo_post` → `media_not_publishable`; status FAILED → permanent |
| test_accounts.py | URL with state (+ PKCE where used); bad/expired/reused state → 400; code exchange stores encrypted tokens (DB value ≠ token); reconnect updates the same row; disconnect wipes tokens; `/api/v1/accounts` never contains token strings; missing `TOKEN_ENCRYPTION_KEY` → not_configured; refresh success updates tokens; refresh 401 → `expired` + push |
| test_engagement.py | IG metrics snapshot; comments deduped; digest push; draft reply (fake LLM, `purpose="reply"`); send requires text, calls platform once, marks sent; second send → 409; TikTok → unsupported message |
| test_media_public.py | valid token serves without Basic auth; expired/tampered/other id/archived → 404; path escape impossible |
| test_media_sync.py | §11a cases |
| test_dashboard.py (extend) | planner, calendar, accounts, engagement pages 200 with real data; "Plan next 7 days" shows counts; manual-post panel shows Copy/Download/Mark as posted; banner text per mode; worker banner when heartbeat stale; submit buttons not disabled in click handlers; `/generate/random` with `both_platforms` creates a pair sharing `group_key` |
| test_app.py (extend) | `alembic upgrade head` on empty DB, no autogenerate diff; upgrade from a Phase-1 DB keeps data |

Command unchanged: `python -m pytest`. Real counts in each milestone summary (baseline **193**).

---

## 17. Milestones

> Order matters. **B-milestones need nothing from Lee.** Do them in order; each ends green. L-milestones are done whenever the matching items arrive — they never block a B-milestone.
> **Halloween priority (31 Oct):** B0 → B1 → B2 → B3 first. After B3 the system schedules posts inside Lee's window and **pushes a reminder at each slot**, which makes manual posting for Halloween painless — without any account, server or key.

### Offline build (no client needed)

**B0 — Close-out.** Commit the outstanding work in sensible chunks (AI providers + vision; random drafts + delete; dashboard; docs/plans), add `dist/` to `.gitignore`, record the baseline. **Done when:** clean `git status`, `python -m pytest` green (193), count recorded in `docs/PROGRESS.md`.

**B1 — Foundations.** `cryptography`, all §5 config incl. `PUBLISHING_MODE`, WAL pragma, migration `0003` (enums, all §6 tables/columns, `posting_rules` seeded inactive), transition-table additions (`plan`, `mark_posted`, service-only claims), `services/window.py` + `test_window.py`, `check_publishable` (pre-flight incl. window), `/health` additions, `mark-posted` API + the manual-post panel on post detail (Copy / Download / Mark as posted). **Done when:** a Phase-1 DB upgrades cleanly; you can approve a post and mark it posted by hand; window tests green; no autogenerate diff.

**B2 — Planner.** `services/planner.py`, `posting_rules` API + `/planner` page + `cli plan-week`, calendar page, mirroring (`group_key`) and `random_draft both_platforms` + the "Create Instagram + TikTok pair" button. **Done when:** `test_planner.py` property tests green; on a scratch DB with ~20 approved drafts "Plan next 7 days" yields exactly 10 slots/platform matching Lee's pattern, all inside 08:00–21:00; running it twice changes nothing; the week grid renders on a 375 px viewport.

**B3 — Notifications + reminder mode (Halloween helper).** `services/notifications.py` (all providers), dedupe table, `cli notify-test`, notification API, `post_due` reminders in the worker (`reminders` mode) + the minimal `cli worker` loop (heartbeat, modes `off`/`reminders`/`dry_run` skeleton), overview mode badge + worker banner. **Halloween caveat (v3.1):** reminders need the worker running 24/7 and a phone-reachable dashboard to click *Mark as posted*. A sleeping laptop sends nothing — before 31 Oct either run the worker on an always-on machine/cheap VPS (L1 can be a bare server with Basic auth, no domain needed for ntfy-only use) or accept that the push (with the full caption) is the whole helper and *Mark as posted* is done later. **Done when:** tests green; **live today:** a real push arrives on the developer's phone via **ntfy** and a scratch-DB scheduled post triggers exactly one `post_due` push at its slot (inside the window, none outside). Telegram path is mock-proven; live later (L5).

**B4 — Platform modules + API notes.** First `docs/PLATFORM_API_NOTES.md` (rule 10: fetch the official Instagram and TikTok docs, record endpoints/params/samples/URLs/dates, mark each `VERIFIED-DOC`/`UNVERIFIED`), then `platforms/{instagram,tiktok}.py` (all §7 functions), `PlatformError` mapping, and platform tests with doc-derived fixtures. **Done when:** platform tests green; every `UNVERIFIED` item is listed in the L-milestone checklists.

**B5 — Accounts (OAuth) + publishing service.** `services/accounts.py`, `/oauth/*`, accounts page, `cli refresh-tokens`; `services/publishing.py` (all §8 incl. dry run, retries clamped to the window, transitions); publish/retry/attempts API; post-detail publish panel; signed media route + `test_media_public`. **Done when:** tests green incl. every §8 case; with `PUBLISHING_MODE=dry_run` on a scratch DB the worker produces "would publish" attempts for due posts and sends **zero** HTTP. **Built 2026-10-23:** pre-flight needs a connected account only in `live`; a vanished media file fails the post visibly (`media_not_publishable` + `publish_failed` push) instead of being skipped forever; OAuth uses `state` only (no PKCE: neither documented flow requires it); `GET /m/{token}` skips Basic auth for GET/HEAD only; the log formatter redacts `access_token=…`-style fragments.

**B6 — Worker complete.** `services/scheduler.py` full `run_once` (stuck-claim recovery, token refresh, planner tick, engagement hook), graceful shutdown, `/health.worker_ok`. **Done when:** `test_scheduler.py` green (window respected in all modes, no double publish, recovery); a kill-and-restart scratch run neither loses nor duplicates a (fake-platform) post. **Built 2026-10-23:** `run_once` stages (recover_stuck → reminders|publish → refresh_tokens → planner), each wrapped so one failure is logged, listed in `result['errors']` and never stops the others; stuck claims (> 15 min in `publishing`) are checked with the platform via the saved `platform_ref` before any retry (a post without a ref, or that the platform does not know, is re-queued through the normal backoff — recovery itself never publishes); token refresh: Instagram within 7 days, TikTok within 24 h, failures retried at most every 30 min; planner tick (`AUTO_PLAN_ENABLED`, not in `off` mode) plans each active rule once per local day from `PLANNER_RUN_HOUR`, its per-rule last-day state lives in the heartbeat row (`planner_ran`) so restarts never re-plan; `run_forever(stop_event)` + SIGINT/SIGTERM/SIGBREAK handlers finish the iteration and exit, the sleep is interruptible. **Persistent pre-flight problems** (`account_not_connected`, `media_required`, `media_not_publishable`, `caption_too_long`) now FAIL the post in `live` mode (attempt row + `publish_failed` push, transition `preflight_fail`) instead of being skipped silently every minute; the user fixes the cause and clicks Retry. Engagement sync joins in B7.

**B7 — Engagement (Instagram, mocked).** `services/engagement.py`, `reply_v1.j2`, metrics/comments sync, digest push, `/engagement` page, metrics on post detail/overview. **Done when:** `test_engagement.py` green. **Built 2026-10-23:** `services/engagement.py`, `api/engagement.py`, `reply_v1.j2`, `/engagement` page, worker stage (live only, every 6 min, last run kept in the heartbeat row as `engagement_at`). Tracked = Instagram posts with `published_via='api'` published in the last 14 days (a manual post has no media id to look up). Reply drafting reuses the existing 6-field output schema (the reply goes in `caption`, other fields empty) so no LLM client changed; the draft is trimmed to 300 chars, an empty draft = the model declined → `suggest_ignore`. The commenter's text is untrusted: angle brackets are stripped, it is wrapped in a `<comment>` tag and the system prompt says never to follow it. Sending: live mode + connected account only, one send per comment via an atomic `none/drafted/ignored → sending → sent` claim, status restored on failure so it can be retried; a rejected token expires the account. Digest: one `new_comments` push per hour (key `comments:instagram:YYYYMMDDHH`), snippets only — comments arriving later in the same hour wait for the next hour's push (known ceiling). Metrics are `like_count`/`comments_count` only (insights not implemented, see PLATFORM_API_NOTES).

**B8 — Docs, hardening, handover.** `docs/PLATFORM_SETUP.md` (Meta incl. fallback, TikTok, Dropbox app, Telegram bot, Anthropic key — click-by-click for a non-technical client), `docs/DEPLOY.md` (VPS sizing, systemd units for web + worker + rclone timer, Caddy HTTPS, uptime monitor, backups incl. encryption key), `docs/DEMO_PHASE2.md`, README (modes, planner, notifications, window rule, costs), `docs/DECISIONS.md`, security checklist §15 with evidence, `requirements.lock`, secret scan, phone-viewport check, rebuild the client delivery zip + results report. **Done when:** every "Build-complete" row of §19 has evidence.

### Live (needs Lee) — any order, as items arrive

**L0 (optional, developer-only accelerator).** Use **your own** test Instagram Creator + TikTok accounts and a free tunnel (e.g. Cloudflare quick tunnel) as a temporary `PUBLIC_BASE_URL`, plus ntfy, to live-test OAuth, an Instagram post and a TikTok inbox upload **before** Lee's server exists. Record results in `docs/LIVE_PROOF.md`. (Needs your own Meta developer app and test accounts — skip if unavailable.)

**L1 — Server up.** Lee's VPS + domain (§14 #1): deploy per `DEPLOY.md`, HTTPS, systemd web + worker, backups, uptime monitor, Basic auth on. **Done when:** `/health` OK over HTTPS and the worker heartbeat is live.

**L2 — Instagram connected + first post.** (§14 #2–3) Meta app (Lee's, or the fallback), keys in `.env`, Lee clicks **Allow** on a call. Publish one Lee-approved test post (`PUBLISHING_MODE=live`, Publish now, inside the window). **Done when:** permalink recorded in `LIVE_PROOF.md`; every Instagram `UNVERIFIED` item resolved (Creator eligibility, Reels/Stories support, Facebook Page need) and `PLATFORM_API_NOTES.md` updated.

**L3 — TikTok connected + inbox delivery.** (§14 #4) Dev app, verified domain, Lee clicks Allow; send one video to his drafts. **Done when:** the video is in Lee's TikTok drafts, the `tiktok_ready` push arrived, *Mark as posted* works.

**L4 — Unattended scheduling.** Activate Lee's rules (tz confirmed), `AUTO_PLAN_ENABLED=true`, `PUBLISHING_MODE=live`. **Done when:** a post scheduled ~5 min ahead publishes with nobody touching it; "Plan next 7 days" on Lee's real approved batch gives 10 slots/platform in 08:00–21:00 and Lee confirms the times look right; nothing ever lands outside the window.

**L5 — Telegram + Dropbox copy.** Lee's Telegram bot token + chat; Dropbox read-only app + rclone timer. **Done when:** a Telegram push reaches Lee's phone; a file dropped in his Dropbox folder appears in the Media Library within ~20 minutes; disk guard verified.

**L6 — Engagement live.** Metrics for a real Instagram post; one human-approved reply sent (only if Lee agrees); `new_comments` push received. Claude live proof when the Anthropic key exists.

After each milestone append to `docs/PROGRESS.md` (files, features, real test count, live evidence, blockers, next).

---

## 18. Demo scripts

**Offline demo (works today, no accounts):** (1) Generator → "Surprise me" with *Instagram + TikTok pair* → drafts. (2) Approve a batch. (3) Planner → "Plan next 7 days" → 10 slots per platform, 08:00–21:00, 3 double days, calendar view. (4) `PUBLISHING_MODE=reminders`: at a slot a push arrives on the phone → open the post → Copy caption / Download media → post by hand → **Mark as posted**. (5) `PUBLISHING_MODE=dry_run`: worker log + "would publish" attempts, nothing sent anywhere. (6) Show nothing is ever scheduled or reminded outside 08:00–21:00.

**Live demo (after L1–L5):** Accounts → Instagram and TikTok connected; Publish now → Instagram permalink; TikTok video lands in drafts with a push; schedule 5 min ahead → publishes unattended; force a failure → Failed + push → reconnect → Retry → published; comment → digest push → drafted, edited, sent reply; Overview with counts, metrics, healthy worker; `PUBLISHING_MODE=off` → everything stops.

---

## 19. Definition of Done (Phase 2)

### A. Build-complete — provable with no client input
| # | Criterion | Evidence |
|---|---|---|
| 1 | Phase-1 data upgrades without loss | migration test |
| 2 | Weekly planner matches Lee's pattern, mirrors siblings, stays in the window, never approves, idempotent | `test_planner.py` + scratch run |
| 3 | Window is a hard rule everywhere (planner, worker, retries, TikTok delivery, reminders, manual override) | `test_window.py` + scheduler/publishing tests |
| 4 | Reminder mode: slot push → manual post → *Mark as posted* | `test_reminders.py` + live ntfy push |
| 5 | Dry-run mode proves the pipeline with zero platform calls | tests + scratch run |
| 6 | Notifications: dedupe, no secrets, all events, three providers (mocked) | `test_notifications.py` |
| 7 | Platform modules match the documented APIs; unverified items listed | `PLATFORM_API_NOTES.md` + platform tests |
| 8 | Accounts/OAuth secure; tokens never exposed | `test_accounts.py` + scans |
| 9 | No double publishing on crash/concurrency; failures visible and retryable | scheduler/publishing tests |
| 10 | Human approval required for every publish and reply | tests |
| 11 | IG engagement (mocked) | `test_engagement.py` |
| 12 | Dashboard works on a phone viewport | manual check recorded |
| 13 | Docs: DEPLOY, PLATFORM_SETUP, PLATFORM_API_NOTES, DEMO_PHASE2, README, DECISIONS | files present |

### B. Live-complete — needs Lee (record in `docs/LIVE_PROOF.md`)
| # | Criterion | Evidence |
|---|---|---|
| 14 | Server + HTTPS + worker running | L1 |
| 15 | Instagram connected; real post | L2 permalink |
| 16 | TikTok inbox delivery + Mark as posted | L3 |
| 17 | Unattended scheduled post; plan approved by Lee; nothing outside 08:00–21:00 | L4 |
| 18 | Telegram push + Dropbox copy live | L5 |
| 19 | Engagement live (agreed scope) | L6 |
| 20 | Claude live generation (when key exists) | LIVE_PROOF |

---

## 20. Explicitly NOT in Phase 2
**X / Twitter publishing (deferred — Appendix A)**; TikTok **direct** public posting and TikTok metrics/comments; TikTok photo posts until the domain is verified (stretch); Fanvue anything, payments/monetisation (Phase 3); **DMs and automatic replies** (needs the §10 decision); likes/follows automation; scraping; multi-user accounts/roles; AI image/video/**audio-voice** generation; unreviewed auto-publishing (the planner only schedules *approved* posts and only creates *drafts*); best-time-to-post *optimisation* (random times in a window, nothing learned); analytics beyond per-post Instagram metrics; Postgres migration; Docker; any queue/scheduler library.

*(Phase 1 already ships image/video-frame input to the AI, random drafts and asset deletion.)*

---

## Appendix A — X (Twitter) design, **deferred, not built**
Kept so X can be added later without re-research (Lee dropped X on 2026-10-20). Would add `Platform.x`, `ContentType.post`, `app/platforms/x.py`, `X_CLIENT_ID/SECRET`, `test_platform_x.py`, error code `platform_billing`.
- API v2, OAuth 2.0 PKCE; scopes `tweet.read tweet.write users.read media.write offline.access`. Access token 2 h; refresh token rotates on each refresh — always save the new one.
- Media: v2 media upload (chunked for video: INIT/APPEND/FINALIZE + STATUS poll); attach `media_ids` to `POST /2/tweets`.
- Text = caption + hashtags ≤ 280 chars; over → permanent `caption_too_long` (also at approve time). X prompt rules: ≤ 280 incl. hashtags, 0–2 hashtags, no links, hook-first.
- Billing: pay-per-use prepaid credits (reported ≈ $0.015 per plain post, ≈ $0.20 with a link; verify in the X Developer Console). Out-of-credits → permanent `platform_billing`.
- Client prerequisites if revived: X account, developer app, purchased credits with a spending cap.
