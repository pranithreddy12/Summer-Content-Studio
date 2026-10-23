#!/usr/bin/env bash
# Nightly consistent copy of the SQLite database (safe while the app runs). Keeps the last 14 copies.
# Installed by summer-backup.service/.timer. Also copy .env (it holds TOKEN_ENCRYPTION_KEY!) somewhere safe by hand.
set -euo pipefail
DB=/srv/summer/app/data/app.db
OUT=/srv/summer/backups
mkdir -p "$OUT"
sqlite3 "$DB" ".backup '$OUT/app-$(date +%F).db'"
find "$OUT" -name 'app-*.db' -mtime +14 -delete
