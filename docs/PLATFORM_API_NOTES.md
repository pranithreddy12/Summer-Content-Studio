# Platform API notes (PLAN_PHASE2 rule 10)

Every platform call in `app/platforms/` is recorded here **before** it is coded. Mock fixtures in the platform tests are copied from these notes.

Status labels: **VERIFIED-DOC** = read in the official documentation on the date shown. **SECONDARY** = official doc page was unreachable (HTTP 503), details taken from search-result excerpts of the official doc. **UNVERIFIED** = from general knowledge or a third party; must be confirmed live (it is also listed in the live checklist at the bottom).

Checked: 2026-10-23.

---

## Instagram — Instagram API with Instagram Login (host `graph.instagram.com`)

Docs: https://developers.facebook.com/docs/instagram-platform/content-publishing · https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login · https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/comment-moderation

| Item | Value | Status |
|---|---|---|
| Base | `https://graph.instagram.com/v26.0/…` (version = one constant `API_VERSION`) | VERIFIED-DOC (examples use v26.0) |
| Account | "Instagram professional account". The guide does not name Business vs Creator, and a Facebook Page is only mentioned for Page Publishing Authorization. | VERIFIED-DOC text; **Creator eligibility UNVERIFIED** |
| Scopes | `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_comments` | VERIFIED-DOC |
| Authorize | `GET https://www.instagram.com/oauth/authorize?client_id&redirect_uri&response_type=code&scope&state` (code valid 1 h, single use; strip trailing `#_`) | VERIFIED-DOC |
| Short-lived token | `POST https://api.instagram.com/oauth/access_token` form: `client_id, client_secret, grant_type=authorization_code, redirect_uri, code` → `access_token, user_id, permissions` (doc shows a `data` array; module accepts flat or `data[0]`) | VERIFIED-DOC (shape variants handled) |
| Long-lived token | `GET https://graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret&access_token` → `access_token, token_type, expires_in` (60 days, server-side only) | VERIFIED-DOC |
| Refresh | `GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token` — token must be ≥ 24 h old and unexpired; +60 days each time; an expired token cannot be refreshed | VERIFIED-DOC |
| Username lookup | `GET /me?fields=user_id,username` (the get-started page uses exactly this call; its `account_type` field returns `Business` or `Media_Creator`, so Creator accounts are recognised by this API) | VERIFIED-DOC 2026-10-23 (falls back to the token response's `user_id` and username `unknown` if it ever fails) |
| Create container | `POST /<IG_ID>/media` — image: `image_url`; reel: `media_type=REELS` + `video_url`; story: `media_type=STORIES` + `image_url` or `video_url`; `caption` (not for stories); `access_token` | VERIFIED-DOC (guide text; Reels/Stories wording partly stale per search — **confirm live, L2**) |
| Media rules | URL must be publicly reachable when Meta fetches it; **images must be JPEG** (MPO/JPS unsupported) | VERIFIED-DOC |
| Poll | `GET /<CONTAINER_ID>?fields=status_code` → `EXPIRED, ERROR, FINISHED, IN_PROGRESS, PUBLISHED`. Docs: poll **once per minute, at most 5 minutes**. | VERIFIED-DOC |
| Publish | `POST /<IG_ID>/media_publish` `creation_id=<container>` → `{"id": "<media id>"}` | VERIFIED-DOC |
| Permalink | `GET /<MEDIA_ID>?fields=permalink,timestamp` | **UNVERIFIED** (reference page returned 404; fail-soft: url=None) |
| Limit | `GET /<IG_ID>/content_publishing_limit` — "100 API-published posts per 24 h moving window" (the same guide also says 50 in the carousel section — contradictory). Response fields `quota_usage` / `config.quota_total` | limit text VERIFIED-DOC (contradiction noted); **field names UNVERIFIED**; the check is best-effort |
| Comments | `GET /<MEDIA_ID>/comments` → `data[]` with `id, text, timestamp` (+ `username` requested, **UNVERIFIED**) | VERIFIED-DOC for id/text/timestamp |
| Reply | `POST /<COMMENT_ID>/replies` JSON `{"message": "..."}` → `{"id": "<new comment id>"}` | VERIFIED-DOC |
| Metrics | `GET /<MEDIA_ID>?fields=like_count,comments_count` | **UNVERIFIED** (reference 404). Insights metrics (views/reach/saves/shares) **not implemented** until verified. |
| Access level | Own accounts added to the app work with Standard Access; docs prefer webhooks over polling for comments | VERIFIED-DOC |

Design consequences:
- Videos always publish as `REELS` (feed videos are Reels on Instagram). A `feed_post`/`story` with a **non-JPEG image** is rejected pre-flight as `media_not_publishable` ("convert to JPEG") rather than failing at Meta.
- Poll interval 60 s × 5 attempts (the doc rule). The worker blocks during a publish; acceptable for ≤ 2 posts/day. `# ponytail: sync polling, move to a state machine if volume grows.`
- `check_status` can only tell *whether* a container was published (`PUBLISHED`), not the media id; it then returns the container id as `external_post_id` and no permalink.
- The access token travels in the query/body as the docs show; `httpx` INFO request logging is silenced in `configure_logging` so tokens never reach logs.
- Deviation from PLAN §7: `publish/check_status/fetch_*` take `external_user_id` (the IG user id) because the endpoints are `/<IG_ID>/…`.

---

## TikTok — Content Posting API (host `open.tiktokapis.com`)

Docs (all returned **HTTP 503** on 2026-10-23, so details below come from search-result excerpts of those pages = SECONDARY): https://developers.tiktok.com/doc/content-posting-api-reference-upload-video · https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide · https://developers.tiktok.com/doc/content-posting-api-reference-get-video-status · https://developers.tiktok.com/doc/content-posting-api-reference-direct-post · https://developers.tiktok.com/doc/login-kit-manage-user-access-tokens

| Item | Value | Status |
|---|---|---|
| Inbox init | `POST /v2/post/publish/inbox/video/init/` JSON `{"source_info": {"source": "FILE_UPLOAD", "video_size", "chunk_size", "total_chunk_count"}}`; header `Authorization: Bearer <token>`; scope `video.upload` | SECONDARY |
| Init response | `{"data": {"publish_id", "upload_url"}, "error": {"code": "ok", ...}}` | SECONDARY (`publish_id`, `upload_url`); `error.code` shape UNVERIFIED |
| Chunk rules | each chunk ≥ 5 MB and ≤ 64 MB except the final chunk (≤ 128 MB); `total_chunk_count = floor(video_size / chunk_size)`; 1–1000 chunks; sequential; files < 5 MB = one chunk (`chunk_size = video_size`, count 1) | SECONDARY |
| Upload | `PUT <upload_url>` (use the full URL incl. query), `Content-Range: bytes <first>-<last>/<total>`, `Content-Type: <video mime>` | SECONDARY (success status codes UNVERIFIED — module accepts any 2xx) |
| Status | `POST /v2/post/publish/status/fetch/` JSON `{"publish_id"}` → `data.status` ∈ `PROCESSING_UPLOAD`, `PROCESSING_DOWNLOAD`, `SEND_TO_USER_INBOX`, `PUBLISH_COMPLETE`, `FAILED` (+ `fail_reason`). For uploads `SEND_TO_USER_INBOX` = notification delivered to the creator's inbox; `PUBLISH_COMPLETE` = the user accepted and posted. Rate limit 30 req/min per token. | SECONDARY |
| Unaudited apps | Content from unaudited clients is restricted to private viewing; direct post blocked with `unaudited_client_can_only_post_to_private_accounts` | VERIFIED-DOC (earlier session, 2026-10-17) |
| Direct post | `POST /v2/post/publish/video/init/`; call `POST /v2/post/publish/creator_info/query/` first and use one of its `privacy_level_options`; `post_info.is_aigc` | creator_info + privacy rule VERIFIED-DOC (2026-10-17); init path and `is_aigc` field **UNVERIFIED**; mock-only |
| Photo posts | `PULL_FROM_URL` only, requires a verified domain/URL prefix | VERIFIED-DOC (2026-10-17); **not built** (inbox = video only) |
| Authorize | `GET https://www.tiktok.com/v2/auth/authorize/?client_key&scope&response_type=code&redirect_uri&state` | SECONDARY (path confirmed by a doc excerpt); `scope` comma-separated and PKCE **UNVERIFIED** |
| Token | `POST https://open.tiktokapis.com/v2/oauth/token/` form `client_key, client_secret, code, grant_type=authorization_code, redirect_uri` → `access_token, expires_in, refresh_token, refresh_expires_in, open_id, scope, token_type` | **UNVERIFIED** (search could not surface the v2 page) |
| Refresh | same endpoint, `grant_type=refresh_token`, `refresh_token`, `client_key`, `client_secret` | **UNVERIFIED** |
| Username | `GET /v2/user/info/?fields=open_id,display_name` | **UNVERIFIED** (fail-soft → `display_name` falls back to `open_id`) |
| Metrics / comments / replies | not available to this app type | — (`unsupported`) |

Design consequences:
- Inbox mode is the deliverable: success = `SEND_TO_USER_INBOX` → post status `sent_to_drafts`, never `published`.
- Chunking: ≥ 5 MB files use 10 MB chunks with the remainder folded into the final chunk (≤ 128 MB), per the floor rule.
- Videos only. `photo_post` or an image asset → `media_not_publishable`.
- TikTok credentials/fields marked UNVERIFIED are exactly what L3 must prove; if any differs, fix the module and update this file.

---

## Error mapping (shared)

| Condition | `PlatformError.code` | transient |
|---|---|---|
| `httpx.TimeoutException` | `timeout` | yes |
| other `httpx.TransportError` | `connection_error` | yes |
| HTTP ≥ 500 | `upstream_error` | yes |
| HTTP 429, Graph codes 4/17/32/613, TikTok `rate_limit_exceeded` | `rate_limited` | yes |
| Graph code 190 / HTTP 401 | `token_expired` | no |
| IG quota reached | `platform_quota` | yes |
| container `ERROR`/`EXPIRED`, TikTok `FAILED` | `media_rejected` | no |
| TikTok unaudited | `unaudited_client` | no |
| other 4xx | `bad_request` | no (**added to the PLAN §8 table**) |
| missing capability | `unsupported` | no |

## Live checklist (UNVERIFIED items to prove in L2/L3)
Instagram: Creator account eligibility **for publishing** (the account type is recognised, publishing itself unproven) and Facebook Page need · Reels and Stories containers · permalink fetch · `content_publishing_limit` field names · comment `username` field · `like_count`/`comments_count`.
TikTok: v2 authorize scope separator/PKCE · token + refresh request/response · `/user/info/` · init `error.code` shape · upload response codes · direct-post init path and `is_aigc`.

## Dropbox / rclone (docs/DEPLOY.md, PLATFORM_SETUP.md) - checked 2026-10-23
rclone's Dropbox page: `rclone config` -> `dropbox`; empty key/secret = rclone's shared app; own app needs redirect `http://localhost:53682/`; its own-app scope list names file **write** as the minimum - a read-only scope set for download-only `rclone copy` is **UNVERIFIED** (try read-only first, widen only if rclone refuses). The page does not document `--min-size` or `copy` semantics (general rclone knowledge: `copy` never deletes at the destination) - both **UNVERIFIED** here, proven in L5.
