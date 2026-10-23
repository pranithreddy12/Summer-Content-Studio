#!/usr/bin/env bash
# One-time base setup for a fresh Ubuntu LTS droplet.   Run as root:   bash server-setup.sh
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get -y upgrade
apt-get -y install python3-venv python3-pip ffmpeg git ufw unattended-upgrades curl gpg \
    debian-keyring debian-archive-keyring apt-transport-https sqlite3

# 2 GB swap: small droplet, ffmpeg frame extraction + large uploads
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# service user (no login shell) + app folders
id summer >/dev/null 2>&1 || useradd --system --create-home --home-dir /srv/summer --shell /usr/sbin/nologin summer
mkdir -p /srv/summer/app /srv/summer/media_sync /srv/summer/backups
chown -R summer:summer /srv/summer

# Caddy from the official repository (automatic HTTPS)
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
apt-get update
apt-get -y install caddy

# firewall: SSH + web only (also add the same rules in the DigitalOcean Cloud Firewall)
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# automatic security updates
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "Base server ready. Next: docs/DEPLOY.md step 5 (copy the app)."
