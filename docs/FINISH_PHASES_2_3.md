# Finishing Phase 2 and Phase 3: what is left, in order

The code for all three phases is built and tested offline (690 tests). What remains is **setup and live proof**: put the app on a server, connect the real accounts, and prove each step once with Lee. Nothing below needs new code unless a live API behaves differently from its documentation.

**Who:** **You** = Pranith (developer). **Lee** = client (owns every account; he clicks "Allow", you never get his passwords).
**Golden rules:** secrets are typed on the server with `nano`, never pasted in chat or email (use a password-manager share link). Change one thing at a time. After each `.env` edit run `sudo systemctl restart summer-web summer-worker`. Kill switch at any time: `PUBLISHING_MODE=off`, then restart.
**Record every proof** (date, what you saw, link or screenshot) in `docs/LIVE_PROOF.md` and a short entry in `docs/PROGRESS.md`.

Detailed clicks live in `docs/DEPLOY.md` (server) and `docs/PLATFORM_SETUP.md` (accounts). This page is the order and the checklist.

---

## Step 0: Before anything (you, on your laptop, ~30 min)

- [ ] **Merge `phase3` into `master`** (open a PR on GitHub, merge it). Deploy from `master`, so the server runs the tested code.
- [ ] **Run the tests once more:** `.venv\Scripts\python -m pytest`. They must be green.
- [ ] **Send Lee one message** asking for everything in Step 1. Ask it all at once so he can work through it.

## Step 1: What you need from Lee (one message, he can answer over a few days)

| # | Item | Needed for | Notes |
|---|---|---|---|
| 1 | DigitalOcean team invite + Cloudflare invite (DNS) | Step 2 | Already sent on 2026-10-23; check you still have access. |
| 2 | **Legal page details:** operator name (a brand/trade name is fine), a contact email that is **not personal**, how many days to delete data on request (e.g. 30), governing law (e.g. England and Wales) | Step 2 (public pages), TikTok and Meta review | Lee cares about privacy; suggest a brand name and a separate mailbox. Not legal advice. |
| 3 | Lee reviews the **Terms**: they currently forbid "sexually explicit content", which may not fit Fanvue | Before go-live | His call; you change the wording he asks for. |
| 4 | Telegram: create a bot with @BotFather, send the **bot token** + press Start on the bot | Step 3 | `PLATFORM_SETUP.md` §3a. ntfy works meanwhile (§3b). |
| 5 | Anthropic API key with a spend limit (~$10) | Step 3 | `PLATFORM_SETUP.md` §2. Mistral works until then. |
| 6 | Meta developer app (or permission to use the fallback under your account) | Step 5 | `PLATFORM_SETUP.md` §4a / §4b. |
| 7 | TikTok: the DNS TXT text for domain verification; app `Summer Content` with Login Kit + Content Posting API | Step 6 | `PLATFORM_SETUP.md` §5. Do **not** submit for review yet. |
| 8 | Dropbox folder name + roughly how many files and GB | Steps 2 and 7 | Sets the disk size. |
| 9 | Fanvue developer app: Client ID + secret | Step 10 | `PLATFORM_SETUP.md` §7 (exact scopes and redirect URI). |
| 10 | Fanvue answers: never-say list, topics to escalate, ~5 example fan messages with how Summer would answer, PPV price range, which media are Fanvue-only | Steps 11-12 | Defaults are already set; these improve quality. |
| 11 | Confirms Summer's Fanvue bio has a clear "AI-generated" statement | Step 11 | Fanvue's guidelines accept a bio statement. |
| 12 | OK for one real test post on each platform and one real test message to a fan account he controls | Steps 5, 6, 11, 12 | |
| 13 | Confirm: "GMT" = UK clock time (`Europe/London`, BST in summer)? Are the 10 weekly posts all videos? | Step 8 | TikTok inbox mode takes videos only. |

---

# Phase 2: live milestones L1-L6

## Step 2 (L1): Server online (you, ~2-3 hours) - `DEPLOY.md` §1-5, 7, 8, 12
*Already done?* Open `https://studio.itsmesummer.app/health`. If it answers `"status":"ok"`, skip to the parts below you have not done.

- [ ] Accept the invites, enable 2FA on DigitalOcean (§1).
- [ ] Create the Droplet: Ubuntu LTS, **London**, 2 GB, backups on, SSH key only (§2). Firewall: 22, 80, 443.
- [ ] Run `deploy/server-setup.sh` (§3). Optional hardening: set `PasswordAuthentication no` and install fail2ban.
- [ ] Cloudflare DNS: `A` records for `@` and `studio` pointing to the Droplet, **grey cloud** (DNS only) so Caddy can get the certificate (§4).
- [ ] Copy the app, create the venv from `requirements.lock`, write `.env` with `APP_ENV=production`, a long `DASHBOARD_PASSWORD`, `PUBLIC_BASE_URL=https://studio.itsmesummer.app`, generated `TOKEN_ENCRYPTION_KEY` + `MEDIA_URL_SECRET`, **`PUBLISHING_MODE=off`**. Run `python -m app.cli init-db` (§5).
- [ ] Fill the `[[...]]` placeholders in `deploy/site/privacy.html` and `terms.html` with Lee's answers (Step 1 #2), then copy them to `/var/www/summer-site` (§12).
- [ ] Start `summer-web` **and** `summer-worker`, reload Caddy.
- [ ] Backups timer on (§7). **Also copy `.env` somewhere safe offline**: losing `TOKEN_ENCRYPTION_KEY` means reconnecting every account. The nightly backup covers the database only; uploaded media relies on DigitalOcean's weekly backup.
- [ ] Uptime monitor on `/health` (§8).

**Done when:** `/health` is OK over HTTPS and shows the worker heartbeat; `https://itsmesummer.app/privacy` and `/terms` open without login; the studio asks for a login; Lee logs in **on his phone**, generates a draft and uploads a file.

## Step 3: Push notifications + AI key (you, ~20 min once Lee sends them)
- [ ] `.env`: `NOTIFY_PROVIDER=telegram`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (`PLATFORM_SETUP.md` §3a). Restart, then `python -m app.cli notify-test` and *Accounts → Send test notification*.
- [ ] `.env`: `ANTHROPIC_API_KEY=...`. Generate one draft in the dashboard. Record in `LIVE_PROOF.md`: **this is the Phase 1 Claude proof** that is still missing (also try one draft from a real image or video of Lee's).

**Done when:** a Telegram push reaches Lee's phone; one Claude draft exists.

## Step 4: Safety net, reminder mode (you + Lee, 1 day)
- [ ] `PUBLISHING_MODE=reminders`. Approve a few posts with times inside 08:00-21:00.
- [ ] Lee gets a push at each slot, posts by hand, presses **Mark as posted**.

**Done when:** Lee has done this once end to end. From here on, nothing is ever missed even if a platform step is slow.

## Step 5 (L2): Instagram connected + first real post (you + Lee on a call, ~1 hour)
- [ ] Meta app (Lee's, or the fallback under yours), redirect URI `https://studio.itsmesummer.app/oauth/instagram/callback`, Instagram tester added (`PLATFORM_SETUP.md` §4).
- [ ] `.env`: `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET`. Restart.
- [ ] Lee: *Accounts → Connect Instagram* → **Allow**.
- [ ] `PUBLISHING_MODE=dry_run` for a few hours: check the "would publish" lines.
- [ ] With Lee's OK: `PUBLISHING_MODE=live`, **Publish now** on one approved post inside the window.
- [ ] Write in `PLATFORM_API_NOTES.md` what the live API showed for each Instagram item marked UNVERIFIED (Creator accounts, Reels/Stories, Facebook Page need).

**Done when:** the permalink is in `LIVE_PROOF.md`.

## Step 6 (L3): TikTok connected + video in Lee's drafts (you + Lee, then 1-2+ weeks of TikTok review)
- [ ] DNS TXT record for TikTok domain verification in Cloudflare, Lee presses **Verify** (`PLATFORM_SETUP.md` §5).
- [ ] App redirect URI `https://studio.itsmesummer.app/oauth/tiktok/callback`, scopes `user.info.basic` + `video.upload`. Consider renaming the app from `its_me_summer` to a neutral name.
- [ ] `.env`: `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, keep `TIKTOK_POST_MODE=inbox`. Lee connects in sandbox/test mode.
- [ ] Send one video to Lee's TikTok drafts; he finishes it in the app and presses **Mark as posted**.
- [ ] Record a ~2 min demo video (screen share with Lee), then submit the TikTok app for review with the scope explanations.

**Done when:** the video is in Lee's TikTok drafts, the "TikTok ready" push arrived, Mark as posted works. Until TikTok approves, TikTok stays on reminders.

## Step 7 (L5): Dropbox copy (you + Lee, ~30 min) - `DEPLOY.md` §6, `PLATFORM_SETUP.md` §6
- [ ] Lee authorises rclone (read-only) on his computer; you put the config on the server.
- [ ] Enable `summer-media-sync.timer`.

**Done when:** a file Lee drops in his Dropbox folder appears in the Media Library within ~20 minutes.

## Step 8 (L4): Unattended scheduling (you + Lee, a few days)
- [ ] Planner page: activate Lee's rules (10/week per platform, 08:00-21:00, `Europe/London` once he confirms Step 1 #13).
- [ ] "Plan next 7 days" on his real approved batch; Lee checks the times.
- [ ] `AUTO_PLAN_ENABLED=true`, `PUBLISHING_MODE=live`.
- [ ] Schedule one post ~5 minutes ahead and touch nothing.

**Done when:** that post publishes by itself; the week shows 10 slots per platform, all inside 08:00-21:00.

## Step 9 (L6): Engagement (you + Lee, ~30 min)
- [ ] Metrics appear for a real Instagram post; a `new_comments` push arrives.
- [ ] Only if Lee agrees: send one AI-drafted reply he approved.

**Done when:** recorded in `LIVE_PROOF.md`. **Phase 2 is complete.**

---

# Phase 3: Fanvue, live milestones L7-L10

Fanvue has **no test mode**: everything happens on Summer's real account, so do these on a call with Lee.

## Step 10 (L7): Connect Fanvue, read-only proof (you + Lee, ~30 min) - `PLATFORM_SETUP.md` §7
- [ ] Lee creates the app (type **API only**), redirect URI `https://studio.itsmesummer.app/oauth/fanvue/callback`, the listed scopes, **not** `write:creator`.
- [ ] `.env`: `FANVUE_CLIENT_ID`, `FANVUE_CLIENT_SECRET`. Restart **both** services (only the worker refreshes Fanvue tokens).
- [ ] Lee: *Accounts → Connect Fanvue* → **Allow**.

**Done when:** the Accounts page shows `@its_me_summer`; the Fanvue report page shows real numbers; after ~1 hour the worker log shows a token refresh.

## Step 11 (L8): One real Fanvue post (you + Lee)
- [ ] With Lee's OK, publish one **subscribers-only** test post (or one he actually wants), then check it in the Fanvue app.
- [ ] Mark any Fanvue-only media with the "Fanvue only" checkbox (Step 1 #10).

**Done when:** post uuid + Lee's confirmation in `LIVE_PROOF.md`; any field that behaved differently is fixed and noted in `PLATFORM_API_NOTES.md`.

## Step 12 (L9): Fan inbox live (you + Lee)
- [ ] Put Lee's never-say and escalate lists into the profile's fan rules (Step 1 #10): Profiles → Summer → the three "Fanvue chat" boxes, or Lee types them himself. The fixed safety escalations (minors, "are you real", meeting, refunds, self-harm, legal) always apply anyway.
- [ ] Sync real chats; let the AI draft ~5 replies; Lee edits and sends at least one (and one paid-media reply to his own test fan account if he has one).
- [ ] From a test fan account send "are you real?" and check it is escalated (push, no AI draft).

**Done when:** messages arrive in Fanvue, Lee reviewed the drafts, one escalation was seen.

## Step 13 (L10): Bio links + weekly digest (Lee, then wait a week)
- [ ] *Fanvue → Create bio links*; Lee pastes them into Summer's Instagram and TikTok bios (after checking each platform's rules on subscription links).

**Done when:** link clicks > 0, and the weekly digest arrives on Telegram. **Phase 3 is complete.**

---

## Finish
- [ ] `docs/LIVE_PROOF.md` has a dated entry for L1-L10 and anything that behaved differently from the docs.
- [ ] Rebuild the delivery zip (`tests/manual/build_delivery_zip.py`) if any code changed during go-live, and hand over: login (via password-manager share), the kill switch, and where backups live.

## Already known, not blockers
Small items left on purpose (see `docs/AUDIT_OVERALL_2026-11-07.md`): `/health` does not check the schema version; a link can set a banner message; the AI top-up limit is per run, not per day; no login throttling; old metric rows are not pruned; an Instagram post recovered after a crash keeps the wrong id (needs a live look in Step 5).
