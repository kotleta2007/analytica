#!/usr/bin/env sh
# Nightly copy of the database into data/backups/, keeping the last 30.
#
# Uses SQLite's online backup (`.backup`), which is safe while the app is
# running and writing — a plain `cp` of a WAL-mode database is not. Runs on
# the host, where data/ is bind-mounted; needs the sqlite3 CLI
# (`apt install sqlite3`). Cron line, see the README:
#
#   0 4 * * * /opt/analytica/deploy/backup.sh
set -eu

cd "$(dirname "$0")/.."

db=data/analytica.db
dir=data/backups
keep=30

mkdir -p "$dir"
out="$dir/analytica-$(date +%Y-%m-%d).db"
sqlite3 "$db" ".backup '$out'"
sqlite3 "$out" "PRAGMA quick_check" | grep -qx ok || { echo "backup $out failed quick_check" >&2; exit 1; }

# Newest first; everything past the 30th goes.
ls -1t "$dir"/analytica-*.db | tail -n +$((keep + 1)) | xargs -r rm --
