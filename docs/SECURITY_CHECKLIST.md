# Phase 2 security checklist — with evidence

Checked 2026-10-23 against commit history up to B8. "Test" = a named test in `tests/`; run them all with `python -m pytest`.
Items that only a real server can prove are marked **LIVE** and are repeated in `docs/DEPLOY.md` / the live milestones.

| # | Requirement (PLAN_PHASE2 §15) | Evidence | State |
|---|---|---|---|
| 1 | Platform tokens are Fernet-encrypted at rest; the API never returns them | `test_accounts.py::test_callback_stores_encrypted_tokens_and_redirects` (DB value ≠ token, decrypts back), `::test_accounts_api_never_exposes_tokens` (response has no token fields), `::test_decrypt_with_other_key_fails_cleanly`, `::test_missing_key_means_not_configured` | ✅ |
| 2 | OAuth `state` random, single-use, 10-minute expiry, bound to the platform | `test_accounts.py::test_start_redirects_with_state_and_stores_it` (`token_urlsafe(32)`), `::test_callback_state_cannot_be_reused`, `::test_callback_expired_state_is_400_and_burned`, `::test_callback_state_must_match_platform`, `::test_old_states_are_purged_on_start`. PKCE is not used: neither documented flow requires it (`docs/DECISIONS.md`) | ✅ |
| 3 | Redirect URIs only from `PUBLIC_BASE_URL`; no open redirect | `platforms/__init__.py::callback_url`; `test_accounts.py::test_callback_user_cancelled_burns_state_and_never_echoes_input`, `::test_exchange_failure_redirects_with_error_not_500` (the callback only redirects to the fixed `/accounts?msg=` page and never echoes request input) | ✅ |
| 4 | `/m/{token}`: HMAC + expiry + containment; 404 on any failure; the only unauthenticated file route | `test_media_public.py` (10): valid token, HEAD, expired / tampered / garbage / forged-id / archived / missing file / unset secret → bare 404, `::test_media_route_skips_basic_auth_but_everything_else_needs_it` (only GET/HEAD on `/m/` bypass auth; `/api/v1/assets/{id}/file` and POST `/m/` still 401), `::test_token_does_not_validate_under_another_secret`. Containment reuses `resolve_asset_file` (`test_security.py`) | ✅ |
| 5 | Platform error messages sanitized before storage/display | `test_publish_live.py::test_platform_messages_are_sanitized_before_storage`, `::test_unexpected_exception_is_a_generic_transient_error`, `test_accounts.py::test_sanitize_strips_tokens_and_query_fragments`; **mutation check 2026-10-23:** disabling `sanitize()` makes `test_hardening.py::test_no_secret_value_leaks_through_any_channel` fail ("'LONG-TOKEN-SECRET' leaked into responses") | ✅ |
| 6 | `PUBLISHING_MODE=off` honoured by worker, API, dashboard; `live` refuses to start without prerequisites | `test_publish_live.py::test_off_and_reminders_modes_refuse`, `::test_publish_endpoint_refuses_when_off`, `test_scheduler.py::test_worker_refuses_live_without_prerequisites`, `::test_worker_refuses_reminders_without_a_push_channel`, `test_engagement.py::test_send_needs_live_mode_and_an_account`, `::test_api_sync_refused_outside_live` | ✅ |
| 7 | Only `approved`/`scheduled` posts can be published or reminded; **the planner never approves** | `test_publish_live.py::test_preflight_failures_leave_the_post_untouched` (draft → `invalid_transition`), `test_dry_run_still_enforces_preflight_and_window`, `test_hardening.py::test_planner_never_approves_and_topup_creates_drafts_only` (drafts stay drafts; top-up posts are drafts with no `approved_at`), `test_reminders`-style cases in `test_phase2_regressions.py` | ✅ |
| 8 | Nothing posts, is delivered to TikTok drafts or is reminded outside the window (07:59 / 08:00 / 21:00 / 21:01 / DST) | `test_window.py` (boundaries, DST change 25 Oct 2026, midnight), `test_scheduler.py::test_nothing_happens_outside_the_window_in_any_mode[dry_run\|reminders\|live]`, `test_publish_live.py::test_scheduler_never_publishes_outside_the_window`, `::test_retry_after_evening_failure_moves_to_next_window_start`, `::test_manual_publish_outside_window_needs_override`, `test_planner.py` (every slot inside the window, 15-minute end margin in `test_phase2_regressions.py`) | ✅ |
| 9 | Reply sending needs an explicit human POST per comment | `test_engagement.py::test_the_whole_pipeline_never_sends_a_reply_by_itself` (sync + draft make zero POSTs to Instagram), `::test_send_reply_posts_once_and_records_it` (second send → 409), `::test_a_comment_already_being_sent_cannot_be_sent_again` | ✅ |
| 10 | Basic auth mandatory when `APP_ENV=production` (startup refuses); docs off | `test_hardening.py::test_production_refuses_to_start_without_basic_auth`, `::test_production_needs_both_username_and_password`, `::test_production_rejects_a_short_password_without_echoing_it` (12+ chars), `::test_production_starts_with_good_credentials_and_hides_docs` (`/docs`, `/redoc`, `/openapi.json` → 404) | ✅ |
| 11 | Notification credentials only in env; none in logs, DB, API responses or message bodies | `test_notifications.py::test_recent_rows_capped_and_secretless`, `::test_provider_request_shapes`; **end-to-end scan** `test_hardening.py::test_no_secret_value_leaks_through_any_channel`: connects via OAuth, fails a publish with a token-bearing platform error, loads 11 pages/endpoints, then scans responses, production-format JSON logs, a full SQLite dump and push bodies for the access token, ntfy topic, app secret, media secret and encryption key — none found | ✅ |
| 12 | `rclone.conf` `chmod 600`, not in git, not in plain backups; Dropbox access scoped | Documented in `docs/DEPLOY.md` §6–§7 and `docs/PLATFORM_SETUP.md` §6; `git ls-files` contains no `rclone.conf`, `.env` or `*.db` | ✅ docs · **LIVE** (permissions on the server; read-only scope set is unconfirmed) |
| 13 | Uptime monitor on `/health` documented | `docs/DEPLOY.md` §8 (keyword monitor on `"worker_ok":true`); `test_scheduler.py::test_heartbeat_makes_worker_ok_in_health_and_goes_stale` proves the flag flips after 5 minutes | ✅ docs · **LIVE** (monitor created) |

## Scans run 2026-10-23

```
git grep -n -i "sk-ant" -- . ':!tests' ':!docs/PROGRESS.md'
  -> .env.example:20 (placeholder sk-ant-REPLACE_ME) and PLAN.md text only
git grep -n -E "[0-9]{8,10}:[A-Za-z0-9_-]{30,}"           (Telegram bot-token shape)   -> no matches
git ls-files | grep -E "\.env$|\.db$|rclone\.conf"        -> no matches
git log --all -S"sk-ant-api"                              -> no commits
```
`.env` exists only on the developer's machine and is git-ignored.

## Phase 1 items that still hold
Upload allow-list + magic bytes + size limit, `safe_relpath`, cross-origin POST rejection, constant-time Basic auth, generic 500
handler, no key in any response: `tests/test_security.py` (13) and `tests/test_assets.py`, all green.

## Known gaps (not hidden)
- **Nothing was tested against a real platform**: OAuth, token refresh and publishing run against `httpx.MockTransport` and a local fake Instagram server (`tests/manual/kill_restart.py`). Unverified API details are listed in `docs/PLATFORM_API_NOTES.md`.
- **Rate limiting is per client IP and in memory**: behind Caddy it relies on `--proxy-headers` (set in the systemd unit in `docs/DEPLOY.md`); one web process only.
- **One shared dashboard login** (Basic auth) — no per-user accounts or roles.
- **SIGTERM on Linux** is handled by the same code path as Ctrl+C (event + finish current step) but was only exercised on Windows.
- **No dependency vulnerability scan** was run (no scanner is installed on the build machine); `requirements.lock` pins exact versions so a scan can be added in CI/at deploy time.

# Phase 3 additions (PLAN_PHASE3 §14) — checked 2026-11-06

| # | Requirement | Evidence | State |
|---|---|---|---|
| P1 | Fanvue tokens Fernet-encrypted; the rotated refresh token is persisted with the access token | `test_platform_fanvue.py::test_worker_refreshes_and_persists_both_rotated_tokens`, `::test_connect_flow_end_to_end_with_pkce` (DB holds ciphertext, decrypts to the new refresh token) | ✅ |
| P2 | PKCE S256 verifier: server-side only, single use, 10-minute state | `test_platform_fanvue.py::test_connect_flow_end_to_end_with_pkce` (challenge = S256(verifier), verifier never in the redirect, state burned); mutation "wrong verifier" fails it | ✅ |
| P3 | Client secret only in the Basic header, never the body | `::test_exchange_uses_basic_auth_and_verifier_and_keeps_only_uuid_and_handle`; mutation "secret in body" fails 2 tests | ✅ |
| P4 | Only the worker refreshes rotating tokens (no reuse-detection revocation) | `::test_web_side_never_refreshes_fanvue`, `test_refresh_tokens_cli_skips_fanvue`; mutation "web may refresh" fails it | ✅ |
| P5 | No `write:creator` scope: the app cannot change prices, promotions or payouts | `test_phase3_rules.py::test_default_scopes_never_include_write_creator`, `::test_write_creator_scope_is_refused` (start-up refuses it) | ✅ |
| P6 | Fanvue-only media never reaches Instagram/TikTok | `test_phase3_rules.py` (create, edit, generate, approve, publish pre-flight, random drafts); mutation removes the guard -> 5 fail | ✅ |
| P7 | Every fan reply and PPV is a human POST; one send per waiting chat | `test_fan_inbox.py::test_send_reply_once`, `::test_worker_stage_syncs_drafts_and_respects_the_poll_interval` (drafts, never sends), `test_fans_ui.py::test_dashboard_posts_are_csrf_protected` | ✅ |
| P8 | Escalations get no AI draft; drafts are re-checked | `test_fan_inbox.py::test_safety_escalations` (22 phrasings), `::test_ordinary_messages_do_not_escalate`, `::test_ai_escalation_and_bad_drafts_become_escalations`; mutations "sync never escalates" / "drafts unchecked" fail | ✅ |
| P9 | Fan text never in logs, pushes, notification or generation logs; Fanvue secrets nowhere | **end-to-end scan** `test_hardening.py::test_phase3_no_fanvue_secret_or_fan_text_leaks` (sync, AI escalation, hostile Fanvue error echoing the token, 6 pages/endpoints; scans responses, JSON logs, a full DB dump, the log tables and push bodies); mutations "send error unsanitised" and "AI reason in push" each fail it | ✅ |
| P10 | Fan message text deleted after 90 days | `test_fan_inbox.py::test_purge_deletes_old_text_and_empty_chats_but_keeps_escalations`, `::test_inbox_stage_runs_in_live_mode_only_and_purge_runs_daily`; Privacy Policy states the same number (`test_site_pages.py` pins it to the setting) | ✅ |
| P11 | Fan-controlled text is escaped and never inside inline JS | `test_fans_ui.py::test_fans_pages_render_and_escape_fan_text`, `::test_handle_never_reaches_inline_javascript` | ✅ |
| P12 | Public Privacy Policy describes the Fanvue processing | `deploy/site/privacy.html` (Fanvue named as a processor, fan chat data, AI drafts, 90-day deletion, fan deletion requests); `test_site_pages.py::test_privacy_policy_covers_what_the_service_really_does` | ✅ draft · **Lee/legal review** |

**Phase 3 gaps:** nothing was sent to a real Fanvue account (no sandbox); fan-message escalation is keyword-based (broad on purpose, but a determined fan can phrase around it — every reply is still human-sent); the earnings report holds a web request while Fanvue answers (up to `PLATFORM_TIMEOUT_SECONDS`).
