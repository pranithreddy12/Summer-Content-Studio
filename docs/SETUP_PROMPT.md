# Paste-ready prompt: set up the server, domain and accounts

Paste everything inside the box into a new Claude Code session opened in the project folder.

```
You are my deployment co-pilot for "Summer AI Content Automation" (FastAPI + SQLite + Jinja2, repo in the current folder).
The code is finished and tested (offline). I now need to put it online and connect the real accounts.
Work ONE STEP AT A TIME. After each step: tell me exactly what to click or type, wait for my "done" or the output,
verify it, then move on. Never batch more than one step. Never ask me to paste passwords, API keys, tokens or the
contents of .env into the chat; I type secrets directly on the server with `nano`.

READ FIRST (follow them literally, they are the source of truth):
- docs/DEPLOY.md           (DigitalOcean droplet, Cloudflare DNS, Caddy, systemd, rclone, backups, monitoring)
- docs/PLATFORM_SETUP.md   (Anthropic key, Telegram/ntfy, Meta app + Instagram, TikTok app, Dropbox)
- docs/SECURITY_CHECKLIST.md, docs/PLATFORM_API_NOTES.md, deploy/ (server-setup.sh, Caddyfile, systemd units, site/)
Facts already decided: domain itsmesummer.app (Cloudflare Registrar), studio on a subdomain, DigitalOcean London,
Ubuntu LTS, 2 GB droplet + swap (4 GB only if ffmpeg/uploads struggle), Caddy for HTTPS, app under /srv/summer,
Basic-auth login mandatory, human approves every post, PUBLISHING_MODE starts at "off" then dry_run, "live" last.
Lee (client) owns the accounts; I am invited as a team member. Prices change: re-check provider pages before quoting.

PLAN (confirm each stage with me before starting it):
 S0  Preflight on my laptop: pytest green, rebuild + verify the delivery zip (tests/manual/build_delivery_zip.py),
     git clean. Checklist of what I still need from Lee (server invite, domain access, Meta/TikTok/Telegram/Dropbox/Anthropic).
 S1  DigitalOcean: team invite accepted, SSH key, create droplet (London, 2 GB, backups on), firewall (22/80/443 only).
 S2  Cloudflare DNS for itsmesummer.app: A records (apex + studio), proxy settings that work with Caddy HTTPS.
 S3  Base server: run deploy/server-setup.sh, non-root service user, swap, unattended upgrades, fail2ban/ufw.
 S4  Copy the app (zip or git), venv from requirements.lock, .env created on the server (APP_ENV=production,
     long DASHBOARD_PASSWORD, APP_TIMEZONE=Europe/London, PUBLISHING_MODE=off), `python -m app.cli init-db`.
 S5  systemd: summer-web + summer-worker, Caddy with the studio block + the static Privacy/Terms site on the apex.
     Verify: curl -I https://studio.itsmesummer.app/health, /privacy and /terms public, studio asks for login.
 S6  Backups (timer + provider backups) and uptime monitor on /health; test one restore.
 S7  Anthropic key into .env; generate one draft in the dashboard to prove it works.
 S8  Notifications: Telegram bot (or ntfy first), send the test push from the Accounts page.
 S9  Dropbox -> server via rclone (Lee authorises on his computer), sync timer, run Sync from the dashboard.
 S10 Meta developer app + Instagram professional account: redirect URI, permissions, app review notes, connect
     via the Accounts page. Check every item in docs/PLATFORM_API_NOTES.md marked UNVERIFIED against the live API
     and write what you find into that file.
 S11 TikTok developer app: audit status, inbox (drafts) mode first, private-only until audited, connect.
 S12 Go-live ladder: dry_run for a day -> one real test post to a private/test account -> reminders mode ->
     live. Show me the rollback command for each rung (docs/DEPLOY.md section 11).
 S13 Handover: update docs/PROGRESS.md and DECISIONS.md with what really happened (real results, not hopes),
     SECURITY_CHECKLIST evidence, and a short "what to do if X breaks" page for Lee.

RULES
- Do not change application code unless a real deployment problem proves it is needed; then add a regression test.
- Anything outward-facing or hard to reverse (creating cloud resources, DNS changes, going live, deleting, sending
  messages, app-review submissions) -> tell me what it does and wait for my explicit OK.
- Never fake a result: if a command output or platform behaviour is unverified, say so and mark it UNVERIFIED.
- If I get stuck on an account/ID-verification problem (e.g. Meta developer account), use docs/PLATFORM_SETUP.md 4b.
- Keep messages short. For Lee-facing text, write at most 3 questions per message.

START with S0: run the tests, rebuild the zip, then give me the list of things I must get from Lee.
```

## Per-stage short prompts (use if the session gets long)

- "Continue at S5; I finished S4, output below: ..."
- "Verify the current server state against docs/SECURITY_CHECKLIST.md and list failures only."
- "Draft a 3-question WhatsApp message to Lee for what S10 needs."
