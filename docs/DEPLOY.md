# Deploying Summer to the server (DigitalOcean + Cloudflare)

Goal: the dashboard and worker running at **https://studio.itsmesummer.app** behind a login, on Lee's own DigitalOcean account, domain at Cloudflare.
Config files live in `deploy/`: `server-setup.sh`, `Caddyfile`, `summer-web.service`, `summer-worker.service`,
`summer-media-sync.service/.timer`, `summer-backup.service/.timer`, `backup.sh`. Account steps for Lee: `docs/PLATFORM_SETUP.md`.
Honest status: nothing here has been run on a real server yet (live milestone L1 in `PLAN_PHASE2.md`).

## 1. Accept the invitations and check access
- **DigitalOcean:** accept the team invite (turn on two-step verification first if Lee ticked "require a secure sign-in method"). Billing stays in Lee's name.
- **Cloudflare:** accept the invite; confirm `itsmesummer.app` is under Domains and **WHOIS redaction** is on (Lee cares about his identity not being public).

## 2. SSH key and Droplet
```bash
ssh-keygen -t ed25519 -C "summer-server" -f ~/.ssh/summer_ed25519
cat ~/.ssh/summer_ed25519.pub        # DigitalOcean → Settings → Security → SSH keys → Add
```
| Setting | Value |
|---|---|
| Image | Ubuntu LTS (latest) |
| Plan | Basic, Regular SSD, **2 GB RAM / 1 vCPU / 50 GB** (~$12/mo). Step up to 4 GB only if ffmpeg/uploads struggle. |
| Region | **London (LON1)** |
| Auth | **SSH key only** |
| Backups | **Enable** (weekly +20 %, daily +30 %) |
| Hostname | `summer-studio` |
Disk: size from Lee's file count/GB (still unknown). If large, add a DigitalOcean **Volume** and mount it at `/srv/summer/media_sync`.
Optional: Cloud Firewall (22 from your IP only, 80, 443) and a **billing alert** (~$20).

## 3. Base server (once, as root)
```bash
scp deploy/server-setup.sh root@SERVER_IP:/root/ && ssh root@SERVER_IP "bash /root/server-setup.sh"
ssh root@SERVER_IP "adduser --disabled-password --gecos '' deploy && usermod -aG sudo deploy && rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy && echo 'deploy ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/deploy"
```
The script installs Python/venv, ffmpeg, sqlite3, ufw, Caddy, unattended-upgrades, a 2 GB swap file, the no-login `summer` service user and `/srv/summer/{app,media_sync,backups}`. Use the `deploy` user from now on (optionally disable root SSH).

## 4. DNS (Cloudflare → itsmesummer.app → DNS)
| Type | Name | Value | Proxy |
|---|---|---|---|
| A | `studio` | droplet IPv4 | **DNS only (grey cloud)** — Caddy needs it for the certificate; Instagram fetches media directly |
| A | `@` | droplet IPv4 | DNS only |
Check: `nslookup studio.itsmesummer.app`.

## 5. Copy the app, configure, start
```bash
# project folder, Git Bash (commit first). Skips secrets and local state.
tar --exclude='.venv' --exclude='data' --exclude='.git' --exclude='dist' --exclude='__pycache__' --exclude='.env' -czf ../summer-app.tgz .
scp ../summer-app.tgz deploy@SERVER_IP:/tmp/
# on the server
sudo -u summer tar -xzf /tmp/summer-app.tgz -C /srv/summer/app
sudo -u summer python3 -m venv /srv/summer/venv
sudo -u summer /srv/summer/venv/bin/pip install -r /srv/summer/app/requirements.txt
```
Generate the two secrets (keep them safe — losing `TOKEN_ENCRYPTION_KEY` means reconnecting every account):
```bash
/srv/summer/venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # TOKEN_ENCRYPTION_KEY
/srv/summer/venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"                                # MEDIA_URL_SECRET
```
Create `/srv/summer/app/.env` (`chown summer`, `chmod 600`, **never in git**). Start in the safest mode (`off`):
```
APP_ENV=production                       # refuses to start without DASHBOARD_USERNAME and a 12+ char DASHBOARD_PASSWORD
DATABASE_URL=sqlite:////srv/summer/app/data/app.db
MEDIA_UPLOAD_DIR=/srv/summer/app/data/media
MEDIA_SYNC_DIR=/srv/summer/media_sync
APP_TIMEZONE=Europe/London
DASHBOARD_USERNAME=lee
DASHBOARD_PASSWORD=<long random; give Lee via a password-manager share>
LLM_PROVIDER=auto
MISTRAL_API_KEY=<key>                    # ANTHROPIC_API_KEY=<key> once Lee has one
PUBLIC_BASE_URL=https://studio.itsmesummer.app
TOKEN_ENCRYPTION_KEY=<generated>
MEDIA_URL_SECRET=<generated>
PUBLISHING_MODE=off
NOTIFY_PROVIDER=none                     # telegram / ntfy / pushover when ready (+ its credentials, see .env.example)
MIN_FREE_DISK_GB=2
```
```bash
sudo -u summer bash -c 'cd /srv/summer/app && /srv/summer/venv/bin/python -m app.cli init-db'
sudo cp /srv/summer/app/deploy/summer-web.service /srv/summer/app/deploy/summer-worker.service /etc/systemd/system/
sudo cp /srv/summer/app/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl daemon-reload && sudo systemctl enable --now summer-web && sudo systemctl reload caddy
```
Verify: `curl https://studio.itsmesummer.app/health` → `"status":"ok"` (certificate issued automatically); open it on a **phone**, log in, generate a draft, upload a file.
Logs: `sudo journalctl -u summer-web -n 50`, `sudo journalctl -u caddy -n 50`, `sudo journalctl -u summer-worker -n 50`.
After changing `.env`: `sudo systemctl restart summer-web summer-worker`.

## 6. Dropbox → server copy (rclone)
Lee's Dropbox is a share link the app cannot read; the files must be on the server. `rclone copy` never deletes.
```bash
curl https://rclone.org/install.sh | sudo bash
# Lee authorises on HIS computer (no browser on the server):  rclone authorize "dropbox"   → he sends you the token privately
sudo -u summer -H rclone config              # n) new remote "dropbox", type dropbox, leave client id/secret empty, paste the token
sudo chmod 600 /srv/summer/.config/rclone/rclone.conf
sudo -u summer -H rclone lsd dropbox:         # find the folder name; then test a copy by hand once:
sudo -u summer -H rclone copy dropbox:Summer /srv/summer/media_sync --min-size 1k --transfers 2 -P
sudo -u summer bash -c 'cd /srv/summer/app && /srv/summer/venv/bin/python -m app.cli sync-media'
```
Edit the folder name in `deploy/summer-media-sync.service`, then install the 15-minute timer:
```bash
sudo cp /srv/summer/app/deploy/summer-media-sync.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now summer-media-sync.timer
```
`rclone.conf` holds a Dropbox token: `chmod 600`, owned by `summer`, **never in git, never in plain backups**. `cli sync-media` refuses when free disk < `MIN_FREE_DISK_GB` (and sends a `disk_low` push). Least privilege: a Dropbox app with read-only scopes (`docs/PLATFORM_SETUP.md` §6).

## 7. Backups
- **DigitalOcean backups** (enabled in step 2) cover the whole server weekly/daily.
- **Nightly database copy** (keeps 14): `sudo cp /srv/summer/app/deploy/summer-backup.{service,timer} /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now summer-backup.timer` (script: `deploy/backup.sh`).
- **By hand, somewhere safe and private:** a copy of `.env` (it contains `TOKEN_ENCRYPTION_KEY`) and, encrypted, `rclone.conf`. Without the key the stored platform tokens are unreadable and accounts must be reconnected.
- Test a restore once: copy a backup over `data/app.db` on a scratch box and start the app.

## 8. Monitoring
A dead worker cannot warn anyone, so use an external check: a free **UptimeRobot** (or similar) **keyword monitor** on `https://studio.itsmesummer.app/health`, alert if the page does not contain `"worker_ok":true`, interval 5 minutes, alert to Lee's phone/email. `/health` is public and contains no secrets.

## 9. Going live, step by step
Change one thing at a time; restart `summer-web summer-worker` after each `.env` edit.
1. **`PUBLISHING_MODE=off`** — dashboard works; Lee can log in from his phone; generate and approve drafts.
2. **Push channel:** set `NOTIFY_PROVIDER` + credentials (Telegram once Lee has the bot), then `python -m app.cli notify-test`; also *Accounts → Send test notification*.
3. **`PUBLISHING_MODE=reminders`** (+ worker running): a push at each scheduled slot inside 08:00–21:00; Lee posts by hand and clicks *Mark as posted*. This is the safety net.
4. **Planner:** activate the posting rules (Planner page) once the time zone and "all videos?" are confirmed; "Plan next 7 days"; later `AUTO_PLAN_ENABLED=true`.
5. **Connect accounts** (Accounts page; Lee presses *Allow*): needs `PUBLIC_BASE_URL`, `TOKEN_ENCRYPTION_KEY`, `MEDIA_URL_SECRET` and `INSTAGRAM_APP_ID/SECRET` or `TIKTOK_CLIENT_KEY/SECRET`.
6. **`PUBLISHING_MODE=dry_run`** for a day: the worker logs "would publish" attempts and sends nothing to any platform. Check them.
7. **`PUBLISHING_MODE=live`**: first publish one Lee-approved test post (Publish now, inside the window); record the permalink in `docs/LIVE_PROOF.md`. Then let the schedule run.
8. **Kill switch:** `PUBLISHING_MODE=off` + restart stops all automation instantly.

## 10. Costs (approximate; prices change — check when you buy)
| Item | About |
|---|---|
| DigitalOcean 2 GB droplet | ~$12/month, + backups 20–30 % (≈ $14–16) |
| Hetzner alternative | ≈ €6–12/month (prices/stock vary) |
| Domain `.app` (Cloudflare) | ≈ $13–15/year |
| AI captions | cents per draft; set a monthly limit at the provider |
| Telegram/ntfy, Meta, TikTok, Dropbox/rclone | free |

## 11. Updating or rolling back
Update: repeat the `tar`/`scp` copy (step 5), `sudo -u summer /srv/summer/venv/bin/pip install -r requirements.txt`, `sudo -u summer bash -c 'cd /srv/summer/app && /srv/summer/venv/bin/python -m app.cli init-db'` (runs migrations), then `sudo systemctl restart summer-web summer-worker`.
Roll back: restore the previous archive, and the latest `data/app.db` backup if a migration went wrong.

## 12. Still needed for the public developer-app pages
TikTok and Meta developer apps typically ask for **Privacy Policy** and **Terms** URLs on your domain — add simple `/privacy` and `/terms` pages (apex domain) before submitting those apps; check the exact fields in each portal.
