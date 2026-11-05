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

---

## Fanvue (PLAN_PHASE3 rule P3-1) - checked 2026-11-06

Sources: `https://api.fanvue.com/docs/openapi-v1.json` (spec `info.version` 0.1, server `https://api.fanvue.com`) and the guides `authentication/implementation-guide`, `get-started/build-your-first-app`, `get-started/choose-your-app-type`, `get-started/test-your-app`, `authentication/scopes`, `authentication/rate-limits`, `versions/overview`, `tutorials/uploading-media`, `tutorials/automated-replies`, `tutorials/tracking-links`. Everything below is **VERIFIED-DOC** unless marked.

| Item | Value | Status |
|---|---|---|
| Every request | `Authorization: Bearer <access>`, `X-Fanvue-API-Version: 2025-06-26` (header missing = default, unknown = 400, sunset = 410 with `nextVersion`); paths under `/v1/` | VERIFIED-DOC |
| App | Developer Area `https://www.fanvue.com/dev`, type **API only** (no listing, no review), creator account with verified email; client secret shown once | VERIFIED-DOC |
| Authorize | `GET https://auth.fanvue.com/oauth2/auth?client_id&response_type=code&redirect_uri&scope&state&code_challenge&code_challenge_method=S256`; scope string space-separated, always includes `openid offline_access offline`; redirect must be `https://` (or http on localhost) and registered exactly | VERIFIED-DOC |
| Token | `POST https://auth.fanvue.com/oauth2/token`, form `grant_type=authorization_code, code, redirect_uri, code_verifier`; **client id/secret only as HTTP Basic** (`client_secret_basic`; body credentials -> `invalid_client`) -> `access_token, refresh_token, id_token, token_type=Bearer, expires_in, scope` | VERIFIED-DOC (`expires_in` shown as a placeholder: parse with `int()`) |
| Refresh | same URL + Basic header, form `grant_type=refresh_token, refresh_token` -> new access **and** new refresh token. Refresh tokens are single-use, rotate every time, reuse is detected -> persist both before use | VERIFIED-DOC |
| Granted scopes | response `scope` may be narrower than requested; store it | VERIFIED-DOC |
| Scopes requested | `read:self read:creator read:post write:post read:media write:media read:chat write:chat read:fan read:insights read:tracking_links write:tracking_links` (never `write:creator`) | names VERIFIED-DOC |
| Rate limit | 200 points/60 s per user per OAuth client, 6 requests in flight; headers `X-RateLimit-Limit/Remaining/Reset`; `429`/`503` carry `Retry-After` | VERIFIED-DOC |
| Sandbox | none - every call is live | VERIFIED-DOC |
| Me | `GET /v1/users/me` -> `uuid, handle, displayName, isCreator, isAiCreator, email, fanCounts{followersCount, subscribersCount}, …`. Store `uuid` + `handle` only (never `email`) | VERIFIED-DOC |
| Upload 1 | `POST /v1/media/uploads {name, filename, mediaType: image\|video\|audio\|document, sizeBytes}` -> `{mediaUuid, uploadId, partSize, maxParts, totalParts}`; max 1.5 GiB | VERIFIED-DOC |
| Upload 2 | `GET /v1/media/uploads/{uploadId}/parts/urls?from=1&to=N` -> `{partSize, parts[{partNumber, url}]}` | VERIFIED-DOC |
| Upload 3 | `PUT <url>` raw bytes, **no** Authorization header; keep response header `ETag` | VERIFIED-DOC |
| Upload 4 | `PATCH /v1/media/uploads/{uploadId} {parts[{PartNumber, ETag}]}` (capitalised keys) -> `{status}` (`processing` expected) | VERIFIED-DOC |
| Upload 5 | `GET /v1/media/{uuid}` -> `status: created\|processing\|ready\|error`, `errorReason: unsupported_format\|video_too_long\|…\|processing_failed` | VERIFIED-DOC; poll interval not specified (we use 5 s, max 10 min) |
| Create post | `POST /v1/posts {text, mediaUuids[], mediaPreviewUuid, price (cents, needs media), audience: subscribers\|followers-and-subscribers (required), publishAt, expiresAt, collectionUuids}` -> `201 {uuid, createdAt, text, price, audience, publishAt, publishedAt, expiresAt}` | VERIFIED-DOC; whether `publishedAt` is set immediately when `publishAt` is omitted is **UNVERIFIED** (fall back to `createdAt`) |
| Get/list posts | `GET /v1/posts/{uuid}`; `GET /v1/posts?cursor&size&includeUnpublished&startDate&endDate` -> `data[{uuid, mediaUuids[], likesCount, commentsCount, tips, publishedAt, …}], nextCursor` | VERIFIED-DOC; shape of `tips` object **UNVERIFIED** (store as-is) |
| Chats | `GET /v1/chats?filter=not_answered&cursor&size` (filter is a repeatable enum incl. `unread`, `not_answered`) -> `data[{user{uuid, handle, displayName}, unreadMessagesCount, lastMessageAt, isRead}], nextCursor` | VERIFIED-DOC; exact meaning of `not_answered` (fan spoke last) **UNVERIFIED** - prove in L9, fall back to `unread` |
| Messages | `GET /v1/chats/{userUuid}/messages?limit=1..50&markAsRead=false` -> `data[{uuid, text, sentAt, sender{uuid, handle}, type, status: SENT\|UNSENT\|MODERATED}]`, newest first; `type` values include `AUTOMATED_*`, `BROADCAST`, `TIP`, `SINGLE_RECIPIENT` | VERIFIED-DOC |
| Send | `POST /v1/chats/{userUuid}/message {text, mediaUuids[], mediaPreviewUuid, price}` -> `201 {messageUuid}`; with `price` + media it is pay-to-view | VERIFIED-DOC |
| Tracking links | `POST /v1/tracking-links {name, externalSocialPlatform: instagram\|tiktok\|…}` -> `{uuid, name, linkUrl, clicks, engagement{acquiredSubscribers, acquiredFollowers, …}, earnings{totalGross, totalNet} \| null}` (USD cents); `GET /v1/tracking-links?limit&cursor` same items in `data[]` | VERIFIED-DOC |
| Earnings | `GET /v1/insights/earnings/summary?startDate&endDate&granularity=day\|week&timezone` -> `totals{allTime, thisMonth}, breakdownBySource{subs, messages, posts, tips, referrals, renewals, other}, overTime[], period{…}` | VERIFIED-DOC; inner fields of each source object **UNVERIFIED** (render gross/net only when present) |
| Subscribers | `GET /v1/insights/subscribers?startDate&endDate&cursor&size` -> `data[{date, total, newSubscribersCount, cancelledSubscribersCount}]` | VERIFIED-DOC |

Error mapping used by `app/platforms/fanvue.py` (same codes as the table above): 401 -> `token_expired`; 403 -> `missing_scope` (new code, non-transient); 410 -> `api_version_sunset` (new code, non-transient); 429/503 -> `rate_limited` (transient, honour `Retry-After` up to 120 s); ≥500 -> `upstream_error`; media `error` -> `media_rejected`; other 4xx -> `bad_request`.

Live checklist (Fanvue, L7-L9): token `expires_in` real value · `publishedAt` when published immediately · `tips` and earnings source object shapes · `not_answered` semantics · an `out` message sent by the app shows our `appUuid` · PPV message as seen by a test fan.
