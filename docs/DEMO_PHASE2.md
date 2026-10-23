# Phase 2 demo scripts

Two runs: the **offline demo** works today with no accounts, no server and no API keys; the **live demo** needs the
platform accounts connected (`docs/PLATFORM_SETUP.md`) and a server (`docs/DEPLOY.md`).

## A. Offline demo (everything except the platforms)

Setup (local machine):
```
python -m app.cli init-db
uvicorn app.main:app --port 8001          # port 8000 is blocked on the developer's machine
```
In `.env`: an AI key (Mistral or Anthropic), `APP_TIMEZONE=Europe/London`, and for the reminders part
`NOTIFY_PROVIDER=ntfy` + `NTFY_TOPIC=<random string>` (install the ntfy app on a phone and subscribe to that topic).

1. **Media + drafts.** *Media library* → upload 4–6 videos (or *Sync*) → tag `halloween`. *Generate* → **Create Instagram + TikTok pair**
   (or "Surprise me") → drafts appear, one Instagram and one TikTok caption per video, sharing a group.
2. **Approve** a batch on the *Queue* page (nothing is ever approved automatically).
3. **Planner** → activate the rule for each platform (10 per week, 08:00–21:00, Europe/London) → **Plan next 7 days**.
   Show: exactly 10 slots per platform, every day has at least one, three days have two, all times between 08:00 and 21:00,
   each video's Instagram and TikTok posts are on the **same day** (different times). Click **Plan** again → nothing changes.
4. **Calendar** shows the week; open a post: it carries its planned time.
5. **Reminders.** Set `PUBLISHING_MODE=reminders`, run `python -m app.cli worker` (or `--once` to show a single pass). At a slot a push
   arrives on the phone with the full caption → open the post → **Copy caption**, **Download media** → post by hand → **Mark as posted**
   (the queue shows *published* "by hand").
6. **Dry run.** `PUBLISHING_MODE=dry_run`: the worker log and each post's *Publishing* panel show
   `dry run: would publish reel to instagram (…)`; the post stays scheduled; nothing is sent anywhere
   (proof: `python tests\manual\dryrun_proof.py` blocks all HTTP and still passes).
7. **The window.** Show that nothing is scheduled, reminded or published before 08:00 or after 21:00
   (`tests/test_scheduler.py::test_nothing_happens_outside_the_window_in_any_mode`).
8. **Phone.** Open the dashboard on a phone (or a 375 px browser): all pages fit the screen.

## B. Live demo (after the server and accounts exist)

1. **Accounts** → connect Instagram and TikTok (press Allow on each platform's page). Shows `@summerh_03`, status *connected*, expiry date.
2. **Instagram.** Approve a Reel → open it → **Publish now** (inside the window) → permalink appears, status *published (api)*.
3. **TikTok.** Schedule a video 5 minutes ahead and leave the browser → a push says *video is in your TikTok drafts* → open TikTok,
   add the AI label, **Publish** → click **Mark as posted**.
4. **Unattended.** `AUTO_PLAN_ENABLED=true`, `PUBLISHING_MODE=live`: **Plan next 7 days** → the next slot posts with nobody touching anything.
5. **Failure and recovery.** Disconnect the Instagram account, let a slot arrive → the post shows **failed** (`account_not_connected`) and a push
   arrives → **Reconnect** → **Retry** → published inside the window.
6. **Crash safety.** `systemctl kill -s KILL summer-worker` while a publish is running, let it restart → no duplicate post
   (offline proof: `python tests\manual\kill_restart.py`).
7. **Engagement.** A comment appears on the Instagram post → hourly push → **Engagement** → **Draft with AI** → edit → **Send reply**
   (only then does it go to Instagram; a second click is refused).
8. **Overview.** Counts, comments to answer, worker healthy. `PUBLISHING_MODE=off` + restart → everything stops.
